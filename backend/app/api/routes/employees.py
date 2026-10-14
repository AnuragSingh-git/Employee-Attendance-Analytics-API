"""Health check and employee CRUD endpoints."""
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError
from app.core.database import client, db
from app.core.constants import UTC
from app.core.utils import dt_ms
from app.schemas.requests import EmployeeIn
router = APIRouter(tags=["employees", "health"])

@router.post("/employees",status_code=201)
def create_employee(body:EmployeeIn):
    doc=body.model_dump(); doc["created_at"]=datetime.now(UTC).replace(microsecond=0)
    try: db.employees.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,"emp_code already exists")
    return {**{k:v for k,v in doc.items() if k!="created_at"},"created_at":dt_ms(doc["created_at"])}

@router.get("/employees")
def list_employees(department:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    q={"department":department} if department is not None else {}
    total=db.employees.count_documents(q)
    items=list(db.employees.find(q,{"_id":0}).sort("emp_code",ASCENDING).skip((page-1)*page_size).limit(page_size))
    for x in items: x["created_at"]=dt_ms(x.get("created_at"))
    return {"items":items,"total":total,"page":page,"page_size":page_size}

