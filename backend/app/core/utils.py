"""Shared pure helpers for timestamp conversion, shift rules, and serialization."""
import re
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from bson import ObjectId
from app.core.database import db
from app.core.constants import IST, UTC, PRESENCE, STATUSES

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


def weekdays_between(start:date,end_exclusive:date):
    # Used only for month working-day counts; trend date generation is in MongoDB.
    n=0; cur=start
    while cur<end_exclusive:
        if cur.weekday()<5:n+=1
        cur+=timedelta(days=1)
    return n

def present_expr():
    return {"$cond":[{"$in":["$status",list(PRESENCE)]},{"$cond":[{"$eq":["$half_day",True]},0.5,1]},0]}


def jsonable(x):
    if isinstance(x,dict):return {k:jsonable(v) for k,v in x.items()}
    if isinstance(x,list):return [jsonable(v) for v in x]
    if isinstance(x,ObjectId):return str(x)
    if isinstance(x,datetime):return dt_ms(x)
    return x

