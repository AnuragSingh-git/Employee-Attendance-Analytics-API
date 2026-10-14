"""Punching, attendance search, and audited manual correction endpoints."""
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError
from app.database import db
from app.constants import UTC, PRESENCE, STATUSES
from app.utils import (epoch_dt, stored_dt, attendance_date, late_minutes, calc_derived, serial_doc, require_employee, date_obj, dt_ms)
from app.schemas import PunchInIn, PunchOutIn, RegularizeIn
router = APIRouter(tags=["attendance"])

@router.post("/attendance/punch-in",status_code=201)
def punch_in(body:PunchInIn):
    emp=require_employee(body.emp_code)
    ts=epoch_dt(body.punched_at) if body.punched_at is not None else datetime.now(UTC).replace(microsecond=0)
    day=attendance_date(ts,emp)
    doc={"emp_code":body.emp_code,"date":day,"status":body.status,"punch_in":stored_dt(ts),"punch_out":None,"work_hours":None,"late_minutes":late_minutes(ts,emp["shift_start"]),"overtime_minutes":0,"half_day":False,"history":[]}
    try: db.attendance_logs.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,"already punched in for this attendance date")
    return serial_doc(doc)

@router.post("/attendance/punch-out")
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

@router.get("/attendance")
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

@router.patch("/attendance/{emp_code}/{date}")
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

