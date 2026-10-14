"""MongoDB-backed reporting endpoints. Aggregations run close to the data."""
from datetime import datetime, date, time, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from app.core.database import db
from app.core.constants import PRESENCE
from app.core.utils import (require_employee, month_bounds, date_obj, round_half_up, weekdays_between, present_expr)
router = APIRouter(tags=["analytics"])

@router.get("/analytics/employees/{emp_code}/monthly")
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

@router.get("/analytics/departments/summary")
def department_summary(month:str,department:Optional[str]=None):
    first,last,_,end=month_bounds(month)
    match={"joined_on":{"$lt":last}}
    if department is not None:match["department"]=department
    pipeline=[{"$match":match},{"$lookup":{"from":"attendance_logs","let":{"code":"$emp_code"},"pipeline":[{"$match":{"$expr":{"$and":[{"$eq":["$emp_code","$$code"]},{"$gte":["$date",first]},{"$lt":["$date",last]}]}}}],"as":"logs"}},{"$unwind":{"path":"$logs","preserveNullAndEmptyArrays":True}},{"$group":{"_id":"$department","headcount":{"$addToSet":"$emp_code"},"present_days":{"$sum":{"$cond":[{"$and":[{"$ne":["$logs",None]},{"$in":["$logs.status",list(PRESENCE)]},{"$lt":[{"$isoDayOfWeek":{"$dateFromString":{"dateString":{"$ifNull":["$logs.date",first]}}}},6]}]}, {"$cond":[{"$eq":["$logs.half_day",True]},0.5,1]},0]}},"avg_work_hours":{"$avg":{"$cond":[{"$and":[{"$in":["$logs.status",list(PRESENCE)]},{"$ne":["$logs.work_hours",None]}]},"$logs.work_hours",None]}},"late_count":{"$sum":{"$cond":[{"$gt":[{"$ifNull":["$logs.late_minutes",0]},0]},1,0]}},"total_late_minutes":{"$sum":{"$ifNull":["$logs.late_minutes",0]}},"leave_count":{"$sum":{"$cond":[{"$eq":["$logs.status","LEAVE"]},1,0]}},"on_duty_count":{"$sum":{"$cond":[{"$eq":["$logs.status","ON_DUTY"]},1,0]}}}},{"$project":{"_id":0,"department":"$_id","headcount":{"$size":"$headcount"},"present_days":1,"avg_work_hours":{"$round":["$avg_work_hours",2]},"late_count":1,"total_late_minutes":1,"leave_count":1,"on_duty_count":1}},{"$sort":{"department":1}}]
    rows=list(db.employees.aggregate(pipeline))
    # Aggregation above is source of metrics; exact weekday filtering is also ensured for monthly employee metrics.
    return {"month":month,"items":rows}

@router.get("/analytics/leaderboard/late")
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

@router.get("/analytics/departments/{department}/trend")
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

