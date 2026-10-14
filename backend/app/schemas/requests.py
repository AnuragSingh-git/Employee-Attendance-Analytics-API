"""Validated request bodies. Pydantic rejects malformed input before business logic."""
import re
from datetime import date
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from app.core.utils import epoch_dt


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

