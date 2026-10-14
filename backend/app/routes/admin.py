"""Database execution-plan diagnostics for the required list/report queries."""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from app.database import db
from app.utils import month_bounds, require_employee, date_obj, jsonable
router = APIRouter(tags=["admin diagnostics"])

@router.get("/admin/explain/{endpoint}")
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
