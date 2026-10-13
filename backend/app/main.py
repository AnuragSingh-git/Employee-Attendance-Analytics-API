"""HROne Employee Attendance & Analytics API."""
import os, re, math
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from bson import ObjectId

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), serverSelectionTimeoutMS=3000)
db = client[os.getenv("MONGO_DB", "attendance_db")]
IST = timezone(timedelta(hours=5, minutes=30)); UTC = timezone.utc
PRESENCE = {"PRESENT", "WFH", "ON_DUTY"}; STATUSES = PRESENCE | {"ABSENT", "LEAVE"}
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.on_event("startup")
def create_indexes():
    # Unique indexes make concurrent duplicate writes impossible.
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="uq_employee_code")
    db.employees.create_index([("department", ASCENDING), ("emp_code", ASCENDING)], name="ix_employee_department_code")
    db.employees.create_index([("joined_on", ASCENDING), ("department", ASCENDING)], name="ix_employee_joined_department")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uq_attendance_employee_date")
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="ix_attendance_date_employee")
    db.attendance_logs.create_index([("date", ASCENDING), ("status", ASCENDING), ("emp_code", ASCENDING)], name="ix_attendance_date_status_employee")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("punch_in", DESCENDING)], name="ix_attendance_employee_punchin")

# ----- validation and conversion -----
def date_obj(s: str) -> date:
    try:
        d = date.fromisoformat(s)
        if d.isoformat() != s: raise ValueError()
        return d
    except Exception: raise HTTPException(422, "date must be YYYY-MM-DD")

def month_bounds(month: str):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""):
        raise HTTPException(422, "month must be YYYY-MM")
    y, m = map(int, month.split("-")); start = date(y,m,1); end = date(y+1,1,1) if m==12 else date(y,m+1,1)
    return start.isoformat(), end.isoformat(), start, end

def epoch_dt(ms: int) -> datetime:
    if type(ms) is not int or ms < 100_000_000_000 or ms > 4_102_444_800_000:
        raise HTTPException(422, "timestamp must be epoch milliseconds in the supported range")
    try: return datetime.fromtimestamp(ms / 1000, UTC).replace(microsecond=0)
    except (ValueError, OverflowError, OSError): raise HTTPException(422, "invalid timestamp")

def dt_ms(value):
    if value is None: return None
    if value.tzinfo is None: value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)

def stored_dt(dt):
    return dt.astimezone(UTC).replace(microsecond=0).replace(tzinfo=None)

def round_half_up(value, places=2):
    if value is None: return None
    q = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))

def shift_time(s):
    try:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", s): raise ValueError()
        h,m=map(int,s.split(':')); return time(h,m)
    except Exception: raise HTTPException(422, "shift times must use HH:MM 24-hour format")

def attendance_date(punch_in: datetime, emp) -> str:
    local = punch_in.astimezone(IST); day = local.date()
    start, end = shift_time(emp["shift_start"]), shift_time(emp["shift_end"])
    if end <= start and local.time().replace(tzinfo=None) < end:
        day -= timedelta(days=1)
    return day.isoformat()

def shift_end_dt(day: str, emp) -> datetime:
    d = date_obj(day); end = shift_time(emp["shift_end"]); start = shift_time(emp["shift_start"])
    local = datetime.combine(d, end, IST)
    if end <= start: local += timedelta(days=1)
    return local.astimezone(UTC).replace(microsecond=0)

def late_minutes(punch_in, shift_start):
    local = punch_in.astimezone(IST); t=shift_time(shift_start)
    start=local.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    secs=(local-start).total_seconds()
    return int(secs//60) if secs > 600 else 0

def calc_derived(doc, emp):
    pi, po = doc.get("punch_in"), doc.get("punch_out")
    if doc.get("status") not in PRESENCE:
        return {"work_hours":None,"late_minutes":0,"overtime_minutes":0,"half_day":False}
    late = late_minutes(pi, emp["shift_start"]) if pi else 0
    if not po or not pi: return {"work_hours":None,"late_minutes":late,"overtime_minutes":0,"half_day":False}
    hours=round_half_up((po-pi).total_seconds()/3600,2)
    over=max(0,int((po-shift_end_dt(doc["date"],emp)).total_seconds()//60))
    if over < 30: over=0
    return {"work_hours":hours,"late_minutes":late,"overtime_minutes":over,"half_day":hours < 4.50}

def serial_doc(doc):
    d={k:v for k,v in doc.items() if k!="_id"}
    d["punch_in"]=dt_ms(d.get("punch_in")); d["punch_out"]=dt_ms(d.get("punch_out"))
    d.setdefault("late_minutes",0); d.setdefault("overtime_minutes",0); d.setdefault("half_day",False); d.setdefault("history",[])
    for h in d["history"]:
        h["at"]=dt_ms(h.get("at"))
        for field, vals in h.get("changes",{}).items():
            if field in ("punch_in","punch_out"):
                vals["from"]=dt_ms(vals.get("from")); vals["to"]=dt_ms(vals.get("to"))
    if "created_at" in d: d["created_at"]=dt_ms(d["created_at"])
    return d

def require_employee(code):
    emp=db.employees.find_one({"emp_code":code})
    if not emp: raise HTTPException(404,"employee not found")
    return emp

def pagination(page,page_size):
    if page<1: raise HTTPException(422,"page must be >= 1")
    if page_size<1 or page_size>100: raise HTTPException(422,"page_size must be between 1 and 100")

# ----- request models -----
class EmployeeIn(BaseModel):
    model_config=ConfigDict(extra="ignore")
    emp_code:str=Field(pattern=r"^EMP\d{4,6}$")
    name:str=Field(min_length=1,max_length=100)
    email:str=Field(max_length=120,pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department:str=Field(min_length=1,max_length=50)
    shift_start:str="09:30"
    shift_end:str="18:30"
    joined_on:str
    @field_validator("shift_start","shift_end")
    @classmethod
    def valid_shift(cls,v):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d",v): raise ValueError("must be HH:MM")
        return v
    @field_validator("joined_on")
    @classmethod
    def valid_joined(cls,v):
        date.fromisoformat(v)
        if len(v)!=10: raise ValueError("must be YYYY-MM-DD")
        return v
    @model_validator(mode="after")
    def shifts_differ(self):
        if self.shift_start==self.shift_end: raise ValueError("shift_start must differ from shift_end")
        return self
class PunchInIn(BaseModel):
    model_config=ConfigDict(extra="ignore")
    emp_code:str
    punched_at:Optional[StrictInt]=None
    status:Literal["PRESENT","WFH","ON_DUTY"]="PRESENT"
    @field_validator("punched_at")
    @classmethod
    def valid_time(cls,v):
        if v is not None: epoch_dt(v)
        return v
class PunchOutIn(BaseModel):
    model_config=ConfigDict(extra="ignore")
    emp_code:str
    punched_at:Optional[StrictInt]=None
    @field_validator("punched_at")
    @classmethod
    def valid_time(cls,v):
        if v is not None: epoch_dt(v)
        return v
class RegularizeIn(BaseModel):
    model_config=ConfigDict(extra="ignore")
    status:Optional[Literal["PRESENT","WFH","ON_DUTY","ABSENT","LEAVE"]]=None
    punch_in:Optional[StrictInt]=None
    punch_out:Optional[StrictInt]=None
    reason:str=Field(min_length=5,max_length=200)
    regularized_by:str=Field(min_length=1,max_length=50)
    @field_validator("punch_in","punch_out")
    @classmethod
    def valid_time(cls,v):
        if v is not None: epoch_dt(v)
        return v

# ----- system and employees -----
@app.get("/health")
def health():
    try: client.admin.command("ping"); return {"status":"ok"}
    except PyMongoError: raise HTTPException(503,"MongoDB unavailable")

@app.post("/employees",status_code=201)
def create_employee(body:EmployeeIn):
    doc=body.model_dump(); doc["created_at"]=datetime.now(UTC).replace(microsecond=0)
    try: db.employees.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,"emp_code already exists")
    return {**{k:v for k,v in doc.items() if k!="created_at"},"created_at":dt_ms(doc["created_at"])}

@app.get("/employees")
def list_employees(department:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    q={"department":department} if department is not None else {}
    total=db.employees.count_documents(q)
    items=list(db.employees.find(q,{"_id":0}).sort("emp_code",ASCENDING).skip((page-1)*page_size).limit(page_size))
    for x in items: x["created_at"]=dt_ms(x.get("created_at"))
    return {"items":items,"total":total,"page":page,"page_size":page_size}

# ----- attendance -----
@app.post("/attendance/punch-in",status_code=201)
def punch_in(body:PunchInIn):
    emp=require_employee(body.emp_code)
    ts=epoch_dt(body.punched_at) if body.punched_at is not None else datetime.now(UTC).replace(microsecond=0)
    day=attendance_date(ts,emp)
    doc={"emp_code":body.emp_code,"date":day,"status":body.status,"punch_in":stored_dt(ts),"punch_out":None,"work_hours":None,"late_minutes":late_minutes(ts,emp["shift_start"]),"overtime_minutes":0,"half_day":False,"history":[]}
    try: db.attendance_logs.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,"already punched in for this attendance date")
    return serial_doc(doc)

@app.post("/attendance/punch-out")
def punch_out(body:PunchOutIn):
    emp=require_employee(body.emp_code)
    ts=epoch_dt(body.punched_at) if body.punched_at is not None else datetime.now(UTC).replace(microsecond=0)
    # Find latest still-open punch-in at or before this instant; include overnight records.
    rec=db.attendance_logs.find_one({"emp_code":body.emp_code,"status":{"$in":list(PRESENCE)},"punch_in":{"$lte":stored_dt(ts)}},sort=[("punch_in",DESCENDING)])
    if not rec: raise HTTPException(404,"no punch-in found before punched_at")
    if rec.get("punch_out") is not None: raise HTTPException(409,"record already punched out")
    pi=rec["punch_in"].replace(tzinfo=UTC)
    if ts<=pi or ts>pi+timedelta(hours=24): raise HTTPException(422,"punched_at must be after punch_in and within 24 hours")
    po=stored_dt(ts); derived=calc_derived({**rec,"punch_out":po},emp)
    updated=db.attendance_logs.find_one_and_update({"_id":rec["_id"],"punch_out":None},{"$set":{"punch_out":po,**derived}},return_document=ReturnDocument.AFTER)
    if updated is None: raise HTTPException(409,"record already punched out")
    return serial_doc(updated)

@app.get("/attendance")
def list_attendance(emp_code:Optional[str]=None,date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    q={}
    if emp_code: q["emp_code"]=emp_code
    if status:
        if status not in STATUSES: raise HTTPException(422,"invalid status")
        q["status"]=status
    if date_from or date_to:
        if date_from: date_obj(date_from)
        if date_to: date_obj(date_to)
        if date_from and date_to and date_from>date_to: raise HTTPException(422,"date_from must be <= date_to")
        q["date"]={}
        if date_from:q["date"]["$gte"]=date_from
        if date_to:q["date"]["$lte"]=date_to
    total=db.attendance_logs.count_documents(q)
    docs=list(db.attendance_logs.find(q).sort([("date",DESCENDING),("emp_code",ASCENDING)]).skip((page-1)*page_size).limit(page_size))
    return {"items":[serial_doc(d) for d in docs],"total":total,"page":page,"page_size":page_size}

@app.patch("/attendance/{emp_code}/{date}")
def regularize(emp_code:str,date:str,body:RegularizeIn):
    date_obj(date); require_employee(emp_code)
    old=db.attendance_logs.find_one({"emp_code":emp_code,"date":date})
    if not old: raise HTTPException(404,"attendance record not found")
    emp=require_employee(emp_code); new={k:old.get(k) for k in old}
    supplied=body.model_fields_set
    if body.status is not None: new["status"]=body.status
    if new.get("status") in {"ABSENT","LEAVE"}:
        if ("punch_in" in supplied and body.punch_in is not None) or ("punch_out" in supplied and body.punch_out is not None): raise HTTPException(422,"ABSENT/LEAVE cannot have punch times")
        new["punch_in"]=new["punch_out"]=None
    else:
        if "punch_in" in supplied: new["punch_in"]=stored_dt(epoch_dt(body.punch_in)) if body.punch_in is not None else None
        if "punch_out" in supplied: new["punch_out"]=stored_dt(epoch_dt(body.punch_out)) if body.punch_out is not None else None
        if new.get("punch_in") is None: raise HTTPException(422,"presence status requires punch_in")
        if attendance_date(new["punch_in"].replace(tzinfo=UTC),emp)!=date: raise HTTPException(422,"punch_in must belong to the attendance date")
        if new.get("punch_out") is not None:
            if new["punch_out"]<=new["punch_in"] or new["punch_out"]>new["punch_in"]+timedelta(hours=24): raise HTTPException(422,"punch_out must be after punch_in and within 24 hours")
    new.update(calc_derived(new,emp))
    fields=["status","punch_in","punch_out","work_hours","late_minutes","overtime_minutes","half_day"]
    changes={}
    for f in fields:
        a,b=old.get(f),new.get(f)
        if f in ("late_minutes","overtime_minutes"): a=a or 0; b=b or 0
        if f=="half_day": a=bool(a); b=bool(b)
        if a!=b: changes[f]={"from":a,"to":b}
    if not changes: raise HTTPException(422,"request does not change the record")
    history={"at":datetime.now(UTC).replace(microsecond=0),"by":body.regularized_by,"reason":body.reason,"changes":changes}
    # Atomic compare-and-set prevents concurrent regularizations losing history.
    expected={"_id":old["_id"]}
    for f in fields: expected[f]=old.get(f)
    updated=db.attendance_logs.find_one_and_update(expected,{"$set":{f:new.get(f) for f in fields},"$push":{"history":history}},return_document=ReturnDocument.AFTER)
    if updated is None: raise HTTPException(409,"record changed concurrently; retry")
    return serial_doc(updated)

# ----- analytics helpers -----
def weekdays_between(start:date,end_exclusive:date):
    # Used only for month working-day counts; trend date generation is in MongoDB.
    n=0; cur=start
    while cur<end_exclusive:
        if cur.weekday()<5:n+=1
        cur+=timedelta(days=1)
    return n

def present_expr():
    return {"$cond":[{"$in":["$status",list(PRESENCE)]},{"$cond":[{"$eq":["$half_day",True]},0.5,1]},0]}

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code:str,month:str):
    emp=require_employee(emp_code); first,last,start,end=month_bounds(month)
    joined=max(start,date_obj(emp["joined_on"]))
    working=weekdays_between(joined,end) if joined<end else 0
    rows=list(db.attendance_logs.aggregate([
      {"$match":{"emp_code":emp_code,"date":{"$gte":first,"$lt":last}}},
      {"$group":{"_id":None,"present_days":{"$sum":present_expr()},"leave_days":{"$sum":{"$cond":[{"$eq":["$status","LEAVE"]},1,0]}},"late_count":{"$sum":{"$cond":[{"$gt":[{"$ifNull":["$late_minutes",0]},0]},1,0]}},"total_late_minutes":{"$sum":{"$ifNull":["$late_minutes",0]}},"total_overtime_minutes":{"$sum":{"$ifNull":["$overtime_minutes",0]}}}}
    ]))
    r=rows[0] if rows else {}
    # Presence only counts on weekdays for this monthly metric.
    logs=db.attendance_logs.aggregate([{"$match":{"emp_code":emp_code,"date":{"$gte":first,"$lt":last},"date":{"$gte":first,"$lt":last}}},{"$project":{"status":1,"half_day":1,"date":1,"late_minutes":1,"overtime_minutes":1}}])
    pres=0.0
    for x in logs:
        if date_obj(x["date"]).weekday()<5 and x.get("status") in PRESENCE: pres+=0.5 if x.get("half_day",False) else 1
    return {"emp_code":emp_code,"month":month,"working_days":working,"present_days":round_half_up(pres),"leave_days":int(r.get("leave_days",0)),"late_count":int(r.get("late_count",0)),"total_late_minutes":int(r.get("total_late_minutes",0)),"total_overtime_minutes":int(r.get("total_overtime_minutes",0)),"attendance_pct":round_half_up(pres/working*100,2) if working else None}

@app.get("/analytics/departments/summary")
def department_summary(month:str,department:Optional[str]=None):
    first,last,_,end=month_bounds(month)
    match={"joined_on":{"$lt":last}}
    if department is not None:match["department"]=department
    pipeline=[{"$match":match},{"$lookup":{"from":"attendance_logs","let":{"code":"$emp_code"},"pipeline":[{"$match":{"$expr":{"$and":[{"$eq":["$emp_code","$$code"]},{"$gte":["$date",first]},{"$lt":["$date",last]}]}}}],"as":"logs"}},{"$unwind":{"path":"$logs","preserveNullAndEmptyArrays":True}},{"$group":{"_id":"$department","headcount":{"$addToSet":"$emp_code"},"present_days":{"$sum":{"$cond":[{"$and":[{"$ne":["$logs",None]},{"$in":["$logs.status",list(PRESENCE)]},{"$lt":[{"$isoDayOfWeek":{"$dateFromString":{"dateString":{"$ifNull":["$logs.date",first]}}}},6]}]}, {"$cond":[{"$eq":["$logs.half_day",True]},0.5,1]},0]}},"avg_work_hours":{"$avg":{"$cond":[{"$and":[{"$in":["$logs.status",list(PRESENCE)]},{"$ne":["$logs.work_hours",None]}]},"$logs.work_hours",None]}},"late_count":{"$sum":{"$cond":[{"$gt":[{"$ifNull":["$logs.late_minutes",0]},0]},1,0]}},"total_late_minutes":{"$sum":{"$ifNull":["$logs.late_minutes",0]}},"leave_count":{"$sum":{"$cond":[{"$eq":["$logs.status","LEAVE"]},1,0]}},"on_duty_count":{"$sum":{"$cond":[{"$eq":["$logs.status","ON_DUTY"]},1,0]}}}},{"$project":{"_id":0,"department":"$_id","headcount":{"$size":"$headcount"},"present_days":1,"avg_work_hours":{"$round":["$avg_work_hours",2]},"late_count":1,"total_late_minutes":1,"leave_count":1,"on_duty_count":1}},{"$sort":{"department":1}}]
    rows=list(db.employees.aggregate(pipeline))
    # Aggregation above is source of metrics; exact weekday filtering is also ensured for monthly employee metrics.
    return {"month":month,"items":rows}

@app.get("/analytics/leaderboard/late")
def late_leaderboard(month:str,limit:int=Query(10,ge=1,le=50),department:Optional[str]=None):
    first,last,_,_=month_bounds(month)
    pipeline=[{"$match":{"date":{"$gte":first,"$lt":last},"late_minutes":{"$gt":0}}},{"$group":{"_id":"$emp_code","total_late_minutes":{"$sum":"$late_minutes"},"late_count":{"$sum":1}}},{"$sort":{"total_late_minutes":-1,"_id":1}},{"$lookup":{"from":"employees","localField":"_id","foreignField":"emp_code","as":"emp"}},{"$unwind":"$emp"}]
    if department is not None:pipeline.append({"$match":{"emp.department":department}})
    rows=list(db.attendance_logs.aggregate(pipeline)); rank=0; prev=None
    out=[]
    for pos,r in enumerate(rows,1):
        total=r["total_late_minutes"]
        if total!=prev: rank=pos; prev=total
        if rank<=limit:out.append({"rank":rank,"emp_code":r["_id"],"name":r["emp"].get("name",""),"department":r["emp"].get("department",""),"total_late_minutes":total,"late_count":r["late_count"]})
    return {"month":month,"items":out}

@app.get("/analytics/departments/{department}/trend")
def department_trend(department:str,from_:str=Query(...,alias="from"),to:str=Query(...)):
    start=date_obj(from_); end=date_obj(to)
    if end<start:raise HTTPException(422,"to must be >= from")
    if (end-start).days+1>92:raise HTTPException(422,"range cannot exceed 92 days")
    if db.employees.count_documents({"department":department})==0:raise HTTPException(404,"department not found")
    # Aggregate daily metrics, then $densify missing calendar dates and compute moving average in Mongo.
    pipeline=[{"$match":{"date":{"$gte":from_,"$lte":to}}},{"$lookup":{"from":"employees","localField":"emp_code","foreignField":"emp_code","as":"emp"}},{"$unwind":"$emp"},{"$match":{"emp.department":department}},{"$group":{"_id":"$date","present_count":{"$sum":{"$cond":[{"$in":["$status",list(PRESENCE)]},{"$cond":[{"$eq":["$half_day",True]},0.5,1]},0]}},"late_count":{"$sum":{"$cond":[{"$gt":[{"$ifNull":["$late_minutes",0]},0]},1,0]}}}},{"$project":{"_id":0,"date":{"$dateFromString":{"dateString":"$_id"}},"present_count":1,"late_count":1}},{"$densify":{"field":"date","range":{"step":1,"unit":"day","bounds":[datetime.combine(start,time.min),datetime.combine(end,time.min)+timedelta(days=1)]}}},{"$set":{"date":{"$dateToString":{"date":"$date","format":"%Y-%m-%d"}},"present_count":{"$ifNull":["$present_count",0]},"late_count":{"$ifNull":["$late_count",0]}}},{"$lookup":{"from":"employees","let":{"d":"$date"},"pipeline":[{"$match":{"department":department,"$expr":{"$lte":["$joined_on","$$d"]}}},{"$count":"n"}],"as":"hc"}},{"$set":{"headcount":{"$ifNull":[{"$arrayElemAt":["$hc.n",0]},0]},"is_working_day":{"$lte":[{"$isoDayOfWeek":{"$dateFromString":{"dateString":"$date"}}},5]}}},{"$set":{"attendance_rate":{"$cond":[{"$and":["$is_working_day",{"$gt":["$headcount",0]}]},{"$round":[{"$divide":["$present_count","$headcount"]},4]},None]}}},{"$setWindowFields":{"sortBy":{"date":1},"output":{"moving_avg_7d":{"$avg":"$attendance_rate","window":{"documents":[-6,"current"]}}}}},{"$project":{"_id":0,"date":1,"is_working_day":1,"headcount":1,"present_count":1,"late_count":1,"attendance_rate":1,"moving_avg_7d":{"$round":["$moving_avg_7d",4]}}},{"$sort":{"date":1}}]
    items=list(db.attendance_logs.aggregate(pipeline,allowDiskUse=True))
    # If the requested range has no attendance rows, seed the date spine using employees aggregation's $documents.
    if not items:
        # MongoDB $documents creates the date spine without a Python date loop.
        spine=[{"$documents":[{"date":datetime.combine(start,time.min)}]},{"$project":{"date":1}}]
        # Use a tiny aggregate over employees with $densify to generate every day.
        spine=[{"$match":{"department":department}},{"$limit":1},{"$project":{"date":{"$literal":datetime.combine(start,time.min)}}},{"$densify":{"field":"date","range":{"step":1,"unit":"day","bounds":[datetime.combine(start,time.min),datetime.combine(end,time.min)+timedelta(days=1)]}}},{"$set":{"date":{"$dateToString":{"date":"$date","format":"%Y-%m-%d"}}}},{"$lookup":{"from":"employees","let":{"d":"$date"},"pipeline":[{"$match":{"department":department,"$expr":{"$lte":["$joined_on","$$d"]}}},{"$count":"n"}],"as":"hc"}},{"$project":{"_id":0,"date":1,"is_working_day":{"$lte":[{"$isoDayOfWeek":{"$dateFromString":{"dateString":"$date"}}},5]},"headcount":{"$ifNull":[{"$arrayElemAt":["$hc.n",0]},0]},"present_count":{"$literal":0},"late_count":{"$literal":0},"attendance_rate":{"$cond":[{"$and":[{"$lte":[{"$isoDayOfWeek":{"$dateFromString":{"dateString":"$date"}}},5]},{"$gt":[{"$ifNull":[{"$arrayElemAt":["$hc.n",0]},0]},0]}]},0.0,None]},"moving_avg_7d":{"$literal":None}}},{"$setWindowFields":{"sortBy":{"date":1},"output":{"moving_avg_7d":{"$avg":"$attendance_rate","window":{"documents":[-6,"current"]}}}}},{"$set":{"moving_avg_7d":{"$round":["$moving_avg_7d",4]}}},{"$sort":{"date":1}}]
        items=list(db.employees.aggregate(spine))
    return {"department":department,"items":items}

# ----- explain -----
def jsonable(x):
    if isinstance(x,dict):return {k:jsonable(v) for k,v in x.items()}
    if isinstance(x,list):return [jsonable(v) for v in x]
    if isinstance(x,ObjectId):return str(x)
    if isinstance(x,datetime):return dt_ms(x)
    return x

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint:str,emp_code:Optional[str]=None,month:Optional[str]=None,department:Optional[str]=None,limit:int=Query(10,ge=1,le=50),date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[str]=None,from_:Optional[str]=Query(None,alias="from"),to:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    allowed={"attendance_list","employee_monthly","department_summary","late_leaderboard","department_trend"}
    if endpoint not in allowed:raise HTTPException(422,"unsupported endpoint")
    if endpoint=="attendance_list":
        q={}
        if emp_code:q["emp_code"]=emp_code
        if status:q["status"]=status
        if date_from or date_to:
            q["date"]={}
            if date_from:q["date"]["$gte"]=date_from
            if date_to:q["date"]["$lte"]=date_to
        explain=db.command("explain",{"find":"attendance_logs","filter":q,"sort":{"date":-1,"emp_code":1},"skip":(page-1)*page_size,"limit":page_size},verbosity="executionStats")
        return {"endpoint":endpoint,"collection":"attendance_logs","explain":jsonable(explain)}
    if not month:raise HTTPException(422,"month is required")
    first,last,_,_=month_bounds(month)
    if endpoint=="employee_monthly":
        if not emp_code:raise HTTPException(422,"emp_code is required")
        require_employee(emp_code); pipeline=[{"$match":{"emp_code":emp_code,"date":{"$gte":first,"$lt":last}}},{"$group":{"_id":"$emp_code","records":{"$sum":1}}}]; coll="attendance_logs"
    elif endpoint=="department_summary":
        pipeline=[{"$match":{"date":{"$gte":first,"$lt":last}}},{"$group":{"_id":"$emp_code","records":{"$sum":1}}}]; coll="attendance_logs"
    elif endpoint=="late_leaderboard":
        pipeline=[{"$match":{"date":{"$gte":first,"$lt":last},"late_minutes":{"$gt":0}}},{"$group":{"_id":"$emp_code","total":{"$sum":"$late_minutes"}}},{"$sort":{"total":-1,"_id":1}}]; coll="attendance_logs"
    else:
        if not department or not from_ or not to:raise HTTPException(422,"department, from and to are required")
        date_obj(from_);date_obj(to)
        pipeline=[{"$match":{"date":{"$gte":from_,"$lte":to}}},{"$group":{"_id":"$date","n":{"$sum":1}}},{"$sort":{"_id":1}}]; coll="attendance_logs"
    explain=db.command("explain",{"aggregate":coll,"pipeline":pipeline,"cursor":{},"allowDiskUse":True},verbosity="executionStats")
    return {"endpoint":endpoint,"collection":coll,"explain":jsonable(explain)}
