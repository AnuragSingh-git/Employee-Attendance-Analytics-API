"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""

import os
import re
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal, Annotated
from calendar import monthrange

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError


load_dotenv()

client = MongoClient(os.getenv("MongoDB_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


def create_indexes():
    """Create required indexes. Safe to run repeatedly."""
    db.employees.create_index(
        [("emp_code", ASCENDING)],
        unique=True,
        name="uq_employee_code",
    )

    db.employees.create_index(
        [("department", ASCENDING), ("joined_on", ASCENDING)],
        name="idx_department_joined",
    )

    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="uq_attendance_employee_date",
    )

    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)],
        name="idx_attendance_date_employee",
    )

    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING),
         ("status", ASCENDING)],
        name="idx_attendance_employee_date_status",
    )


@app.on_event("startup")
def startup():
    create_indexes()


# ----------------------------- Helpers ---------------------------------

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

PRESENCE_STATUSES = {"PRESENT", "WFH", "ON_DUTY"}
ALL_STATUSES = {"PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"}


def validate_date_string(value: str) -> str:
    """Validate a YYYY-MM-DD calendar date."""
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        raise HTTPException(422, "Date must be YYYY-MM-DD")
    if len(value) != 10:
        raise HTTPException(422, "Date must be YYYY-MM-DD")
    return value


def validate_month_string(value: str) -> tuple[int, int]:
    """Return year and month from YYYY-MM."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise HTTPException(422, "Month must be YYYY-MM")
    year, month = map(int, value.split("-"))
    return year, month


def parse_hhmm(value: str) -> time:
    """Convert an HH:MM string into a time."""
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        raise HTTPException(422, "Time must be HH:MM")
    h, m = map(int, value.split(":"))
    return time(h, m)


def truncate_seconds(value: datetime) -> datetime:
    """Discard microseconds before calculations."""
    return value.replace(microsecond=0)


def from_epoch_ms(value: int) -> datetime:
    """Convert epoch milliseconds into a timezone-aware UTC instant."""
    return datetime.fromtimestamp(value / 1000, tz=UTC).replace(
        microsecond=0
    )


def to_epoch_ms(value: Optional[datetime]) -> Optional[int]:
    """Convert a stored MongoDB datetime into epoch milliseconds."""
    if value is None:
        return None

    # PyMongo commonly returns naive datetimes representing UTC.
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)

    value = truncate_seconds(value)
    return int(value.timestamp() * 1000)


def mongo_datetime(value: datetime) -> datetime:
    """Store an instant as a naive UTC datetime for PyMongo."""
    if value.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware")
    return value.astimezone(UTC).replace(microsecond=0, tzinfo=None)


def local_datetime(value: datetime) -> datetime:
    """Interpret a MongoDB naive datetime as UTC, then convert to IST."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(IST)


def shift_start_for_punch(punch_in: datetime, shift_start: str,
                          shift_end: str) -> datetime:
    """Find the correct shift start, including overnight shifts."""
    local_in = punch_in.astimezone(IST)
    start_time = parse_hhmm(shift_start)
    end_time = parse_hhmm(shift_end)

    start_day = local_in.date()

    # Overnight shift: early-morning punch-in belongs to yesterday's shift.
    if end_time <= start_time and local_in.time().replace(
        tzinfo=None
    ) < end_time:
        start_day -= timedelta(days=1)

    return datetime.combine(start_day, start_time, tzinfo=IST)


def shift_end_for_record(attendance_date: str,
                         shift_start: str,
                         shift_end: str) -> datetime:
    """Return the scheduled shift end as an IST datetime."""
    day = date.fromisoformat(attendance_date)
    start_time = parse_hhmm(shift_start)
    end_time = parse_hhmm(shift_end)

    if end_time <= start_time:
        day += timedelta(days=1)

    return datetime.combine(day, end_time, tzinfo=IST)


def compute_late_minutes(punch_in: datetime, shift_start: str,
                         shift_end: str) -> int:
    """Apply the 10-minute grace period, then count from shift start."""
    local_in = punch_in.astimezone(IST)
    scheduled_start = shift_start_for_punch(
        punch_in, shift_start, shift_end
    )
    seconds_late = (
        local_in - scheduled_start
    ).total_seconds()

    if seconds_late <= 600:
        return 0

    return int(seconds_late // 60)


def compute_work_hours(punch_in: datetime,
                       punch_out: datetime) -> float:
    """Calculate work duration with two-decimal half-up rounding."""
    seconds = (punch_out - punch_in).total_seconds()
    hours = Decimal(str(seconds)) / Decimal("3600")
    rounded = hours.quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return float(rounded)


def compute_overtime(punch_out: datetime, shift_end: str,
                     attendance_date: str, shift_start: str) -> int:
    """Apply the 30-minute minimum to overtime."""
    local_out = punch_out.astimezone(IST)
    scheduled_end = shift_end_for_record(
        attendance_date, shift_start, shift_end
    )
    seconds_after = (
        local_out - scheduled_end
    ).total_seconds()

    if seconds_after < 1800:
        return 0

    return int(seconds_after // 60)


def round_half_up(value, places=2):
    """Round a number using the assessment's half-up rule."""
    quantum = Decimal("1").scaleb(-places)
    return float(
        Decimal(str(value)).quantize(
            quantum, rounding=ROUND_HALF_UP
        )
    )


def public_employee(doc: dict) -> dict:
    """Remove MongoDB's internal ID and convert created_at."""
    doc = dict(doc)
    doc.pop("_id", None)
    if "created_at" in doc:
        doc["created_at"] = to_epoch_ms(doc["created_at"])
    return doc


def public_attendance(doc: dict) -> dict:
    """Convert an attendance document to the API's response format."""
    doc = dict(doc)
    doc.pop("_id", None)

    for field in ("punch_in", "punch_out"):
        doc[field] = to_epoch_ms(doc.get(field))

    doc.setdefault("work_hours", None)
    doc.setdefault("late_minutes", 0)
    doc.setdefault("overtime_minutes", 0)
    doc.setdefault("half_day", False)
    doc.setdefault("history", [])

    history = []
    for entry in doc["history"]:
        item = dict(entry)
        item["at"] = to_epoch_ms(item.get("at"))
        changes = {}
        for field, change in item.get("changes", {}).items():
            change = dict(change)
            if field in {"punch_in", "punch_out"}:
                change["from"] = to_epoch_ms(change.get("from"))
                change["to"] = to_epoch_ms(change.get("to"))
            changes[field] = change
        item["changes"] = changes
        history.append(item)

    doc["history"] = history
    return doc


# ------------------------------ Models ----------------------------------

class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(
        min_length=3,
        max_length=120,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30")
    shift_end: str = Field(default="18:30")
    joined_on: str

    @field_validator("shift_start", "shift_end")
    @classmethod
    def validate_shift_time(cls, value):
        parse_hhmm(value)
        return value

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value):
        validate_date_string(value)
        return value

    @model_validator(mode="after")
    def validate_shift(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self


class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def validate_timestamp(cls, value):
        if value is not None and not (
            100_000_000_000 <= value <= 4_102_444_800_000
        ):
            raise ValueError("punched_at must be epoch milliseconds")
        return value


class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emp_code: str
    punched_at: Optional[StrictInt] = None

    @field_validator("punched_at")
    @classmethod
    def validate_timestamp(cls, value):
        if value is not None and not (
            100_000_000_000 <= value <= 4_102_444_800_000
        ):
            raise ValueError("punched_at must be epoch milliseconds")
        return value


class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Optional[
        Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]
    ] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator("punch_in", "punch_out")
    @classmethod
    def validate_timestamp(cls, value):
        if value is not None and not (
            100_000_000_000 <= value <= 4_102_444_800_000
        ):
            raise ValueError("Timestamp must be epoch milliseconds")
        return value

    @model_validator(mode="after")
    def validate_not_both_times_absent(self):
        # Explicit null and omitted values are allowed at model level.
        # The endpoint validates the final state against the stored record.
        return self




# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "database ok"}
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )



@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()

    doc["created_at"] = mongo_datetime(
        datetime.now(UTC).replace(microsecond=0)
    )

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    return public_employee(doc)




@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = {}
    if department is not None:
        query["department"] = department

    skip = (page - 1) * page_size

    total = db.employees.count_documents(query)

    cursor = (
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip(skip)
        .limit(page_size)
    )

    items = [public_employee(doc) for doc in cursor]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }



@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "Employee not found")

    now = datetime.now(UTC) if body.punched_at is None else (
        from_epoch_ms(body.punched_at)
    )
    now = truncate_seconds(now)

    local_now = now.astimezone(IST)
    shift_start_time = parse_hhmm(emp["shift_start"])
    shift_end_time = parse_hhmm(emp["shift_end"])

    attendance_day = local_now.date()

    # Early-morning punches for an overnight shift belong to yesterday.
    if (
        shift_end_time <= shift_start_time
        and local_now.time().replace(tzinfo=None) < shift_end_time
    ):
        attendance_day -= timedelta(days=1)

    date_str = attendance_day.isoformat()

    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": mongo_datetime(now),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            now, emp["shift_start"], emp["shift_end"]
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Already punched in for this attendance date",
        )

    return public_attendance(doc)



@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = {}

    if emp_code is not None:
        query["emp_code"] = emp_code

    if date_from is not None:
        validate_date_string(date_from)

    if date_to is not None:
        validate_date_string(date_to)

    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must be <= date_to")

    if date_from or date_to:
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from
        if date_to:
            query["date"]["$lte"] = date_to

    if status is not None:
        if status not in ALL_STATUSES:
            raise HTTPException(422, "Invalid attendance status")
        query["status"] = status

    total = db.attendance_logs.count_documents(query)

    cursor = (
        db.attendance_logs.find(query, {"_id": 0})
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    items = [public_attendance(doc) for doc in cursor]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/attendance/punch-out", status_code=200)
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "Employee not found")

    punched_out_at = (
        datetime.now(UTC)
        if body.punched_at is None
        else from_epoch_ms(body.punched_at)
    )
    punched_out_at = truncate_seconds(punched_out_at)

    # A suitable punch-in must exist at or before punch-out.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "status": {"$in": list(PRESENCE_STATUSES)},
            "punch_in": {
                "$ne": None,
                "$lte": mongo_datetime(punched_out_at),
            },
        },
        sort=[("punch_in", DESCENDING)],
    )

    if record is None:
        raise HTTPException(404, "No punch-in found")

    if record.get("punch_out") is not None:
        raise HTTPException(409, "Already punched out")

    punch_in_at = record["punch_in"]
    if punch_in_at.tzinfo is None:
        punch_in_at = punch_in_at.replace(tzinfo=UTC)

    duration = (
        punched_out_at - punch_in_at
    ).total_seconds()

    if duration <= 0 or duration > 24 * 60 * 60:
        raise HTTPException(
            422,
            "Punch-out must be after punch-in and within 24 hours",
        )

    work_hours = compute_work_hours(
        punch_in_at, punched_out_at
    )

    overtime_minutes = compute_overtime(
        punched_out_at,
        emp["shift_end"],
        record["date"],
        emp["shift_start"],
    )

    half_day = work_hours < 4.50

    # Atomic compare-and-set: only one simultaneous request can win.
    updated = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": mongo_datetime(punched_out_at),
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    if updated is None:
        raise HTTPException(
            409,
            "Attendance record was already punched out",
        )

    return public_attendance(updated)


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeIn,
):
    validate_date_string(date)

    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "Employee not found")

    old = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date}
    )
    if old is None:
        raise HTTPException(404, "Attendance record not found")

    # Omitted fields stay unchanged.
    changes_requested = body.model_dump(
        exclude_unset=True,
        exclude={"reason", "regularized_by"},
    )

    if not changes_requested:
        raise HTTPException(422, "No changes supplied")

    new_status = changes_requested.get("status", old["status"])
    new_in_ms = changes_requested.get(
        "punch_in", to_epoch_ms(old.get("punch_in"))
    )
    new_out_ms = changes_requested.get(
        "punch_out", to_epoch_ms(old.get("punch_out"))
    )

    if new_status in {"ABSENT", "LEAVE"}:
        if "punch_in" in changes_requested or "punch_out" in changes_requested:
            raise HTTPException(
                422,
                "ABSENT and LEAVE cannot include punch times",
            )
        new_in_ms = None
        new_out_ms = None

    elif new_in_ms is None:
        raise HTTPException(
            422,
            "A presence status requires punch_in",
        )

    new_in = from_epoch_ms(new_in_ms) if new_in_ms is not None else None
    new_out = from_epoch_ms(new_out_ms) if new_out_ms is not None else None

    # A supplied punch-in must belong to this attendance date in IST.
    if new_in is not None:
        if new_in.astimezone(IST).date().isoformat() != date:
            # For overnight shifts, early morning belongs to previous shift day.
            shift_start_time = parse_hhmm(emp["shift_start"])
            shift_end_time = parse_hhmm(emp["shift_end"])
            local_time = new_in.astimezone(IST).time().replace(tzinfo=None)

            valid_overnight_date = (
                shift_end_time <= shift_start_time
                and local_time < shift_end_time
                and (
                    new_in.astimezone(IST).date()
                    - timedelta(days=1)
                ).isoformat() == date
            )

            if not valid_overnight_date:
                raise HTTPException(
                    422,
                    "punch_in does not belong to this attendance date",
                )

    if new_in is not None and new_out is not None:
        duration = (new_out - new_in).total_seconds()
        if duration <= 0 or duration > 24 * 60 * 60:
            raise HTTPException(
                422,
                "punch_out must be after punch_in and within 24 hours",
            )

    # Build the final record values before calculating derived fields.
    if new_status in {"ABSENT", "LEAVE"}:
        derived = {
            "late_minutes": 0,
            "work_hours": None,
            "overtime_minutes": 0,
            "half_day": False,
        }
    else:
        derived = {
            "late_minutes": compute_late_minutes(
                new_in, emp["shift_start"], emp["shift_end"]
            ),
            "work_hours": None,
            "overtime_minutes": 0,
            "half_day": False,
        }

        if new_out is not None:
            derived["work_hours"] = compute_work_hours(new_in, new_out)
            derived["overtime_minutes"] = compute_overtime(
                new_out, emp["shift_end"], date, emp["shift_start"]
            )
            derived["half_day"] = derived["work_hours"] < 4.50

    final_values = {
        "status": new_status,
        "punch_in": mongo_datetime(new_in) if new_in else None,
        "punch_out": mongo_datetime(new_out) if new_out else None,
        **derived,
    }

    # Record only fields whose stored values actually change.
    changes = {}
    for field, new_value in final_values.items():
        old_value = old.get(field)
        if field in {"punch_in", "punch_out"}:
            if old_value is not None and old_value.tzinfo is None:
                old_value = old_value.replace(tzinfo=UTC)
            if old_value is not None:
                old_value = mongo_datetime(old_value)

        # Missing derived fields have their documented defaults.
        if field == "late_minutes" and field not in old:
            old_value = 0
        if field == "overtime_minutes" and field not in old:
            old_value = 0
        if field == "half_day" and field not in old:
            old_value = False
        if field == "history":
            continue

        if old_value != new_value:
            changes[field] = {
                "from": old_value,
                "to": new_value,
            }

    if not changes:
        raise HTTPException(422, "Request makes no changes")

    history_entry = {
        "at": mongo_datetime(datetime.now(UTC)),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    # Conditional update prevents silently overwriting concurrent corrections.
    result = db.attendance_logs.update_one(
        {
            "_id": old["_id"],
            "history": old.get("history", []),
            "status": old.get("status"),
            "punch_in": old.get("punch_in"),
            "punch_out": old.get("punch_out"),
        },
        {
            "$set": final_values,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            409,
            "Record changed concurrently; reload and retry",
        )

    updated = db.attendance_logs.find_one({"_id": old["_id"]})
    return public_attendance(updated)

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly_analytics(
    emp_code: str,
    month: str = Query(..., description="Month in YYYY-MM format"),
):
    year, month_number = validate_month_string(month)

    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    first_day = date(year, month_number, 1)
    last_day = date(year, month_number, monthrange(year, month_number)[1])

    joined_on = date.fromisoformat(employee["joined_on"])
    effective_start = max(first_day, joined_on)

    # Weekdays from the later of the month start and joining date.
    working_days = 0
    if effective_start <= last_day:
        current = effective_start
        while current <= last_day:
            if current.weekday() < 5:
                working_days += 1
            current += timedelta(days=1)

    logs = list(db.attendance_logs.aggregate([
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": first_day.isoformat(),
                    "$lte": last_day.isoformat(),
                },
            }
        },
        {
            "$addFields": {
                "_dateParts": {"$dateFromString": {"dateString": "$date"}}
            }
        },
        {
            "$group": {
                "_id": None,
                "total_records": {"$sum": 1},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", list(PRESENCE_STATUSES)]},
                                    {"$lt": [{"$dayOfWeek": "$_dateParts"}, 7]},
                                    {"$gt": [{"$dayOfWeek": "$_dateParts"}, 1]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "late_days": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
                "total_work_hours": {
                    "$sum": {"$ifNull": ["$work_hours", 0]}
                },
                "half_days": {
                    "$sum": {"$cond": [{"$eq": ["$half_day", True]}, 1, 0]}
                },
            }
        },
    ]))

    stats = logs[0] if logs else {
        "total_records": 0,
        "present_days": 0,
        "late_days": 0,
        "total_overtime_minutes": 0,
        "total_work_hours": 0,
        "half_days": 0,
    }

    attendance_rate = (
        stats["present_days"] / working_days if working_days else 0
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "department": employee["department"],
        "working_days": working_days,
        "present_days": stats["present_days"],
        "late_days": stats["late_days"],
        "total_overtime_minutes": stats["total_overtime_minutes"],
        "total_work_hours": round_half_up(stats["total_work_hours"], 2),
        "half_days": stats["half_days"],
        "attendance_rate": round_half_up(attendance_rate, 4),
    }

@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., description="Month in YYYY-MM format"),
):
    year, month_number = validate_month_string(month)
    month_end = date(year, month_number, monthrange(year, month_number)[1])
    month_start = date(year, month_number, 1)

    # The employee collection establishes the full set of departments
    # and headcount, including employees without attendance records.
    employees = list(db.employees.aggregate([
        {
            "$match": {
                "joined_on": {"$lte": month_end.isoformat()}
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "employee_codes": {"$push": "$emp_code"},
            }
        },
        {"$sort": {"_id": 1}},
    ]))

    result = []

    for department in employees:
        codes = department["employee_codes"]

        attendance = list(db.attendance_logs.aggregate([
            {
                "$match": {
                    "emp_code": {"$in": codes},
                    "date": {
                        "$gte": month_start.isoformat(),
                        "$lte": month_end.isoformat(),
                    },
                }
            },
            {
                "$group": {
                    "_id": None,
                    "present_records": {
                        "$sum": {
                            "$cond": [
                                {"$in": ["$status", list(PRESENCE_STATUSES)]},
                                1,
                                0,
                            ]
                        }
                    },
                    "late_records": {
                        "$sum": {
                            "$cond": [
                                {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                                1,
                                0,
                            ]
                        }
                    },
                    "overtime_minutes": {
                        "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                    },
                    "work_hours": {
                        "$sum": {"$ifNull": ["$work_hours", 0]}
                    },
                }
            },
        ]))

        stats = attendance[0] if attendance else {
            "present_records": 0,
            "late_records": 0,
            "overtime_minutes": 0,
            "work_hours": 0,
        }

        result.append({
            "department": department["_id"],
            "headcount": department["headcount"],
            "present_records": stats["present_records"],
            "late_records": stats["late_records"],
            "overtime_minutes": stats["overtime_minutes"],
            "work_hours": round_half_up(stats["work_hours"], 2),
        })

    return {"month": month, "departments": result}

@app.get("/analytics/leaderboard/late")
def late_arrival_leaderboard(
    date_from: str = Query(...),
    date_to: str = Query(...),
    limit: int = Query(default=10, ge=1, le=100),
):
    validate_date_string(date_from)
    validate_date_string(date_to)

    if date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must be less than or equal to date_to",
        )

    pipeline = [
        {
            "$match": {
                "date": {"$gte": date_from, "$lte": date_to},
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "late_days": {"$sum": 1},
                "total_late_minutes": {"$sum": "$late_minutes"},
            }
        },
        {
            "$sort": {
                "late_days": -1,
                "total_late_minutes": -1,
                "_id": 1,
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {
                    "late_days": -1,
                    "total_late_minutes": -1,
                },
                "output": {
                    "rank": {"$rank": {}}
                },
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {
            "$unwind": {
                "path": "$employee",
                "preserveNullAndEmptyArrays": True,
            }
        },
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "late_days": 1,
                "total_late_minutes": 1,
                "rank": 1,
            }
        },
    ]

    items = list(db.attendance_logs.aggregate(pipeline))
    return {
        "date_from": date_from,
        "date_to": date_to,
        "limit": limit,
        "items": items,
    }

@app.get("/analytics/departments/{department}/trend")
def department_daily_trend(
    department: str,
    date_from: str = Query(...),
    date_to: str = Query(...),
):
    validate_date_string(date_from)
    validate_date_string(date_to)

    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)

    if start > end:
        raise HTTPException(
            status_code=422,
            detail="date_from must be less than or equal to date_to",
        )

    days = (end - start).days + 1
    if days > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 calendar days",
        )

    # Restrict attendance records to employees in the requested department.
    employee_codes = [
        item["emp_code"]
        for item in db.employees.find(
            {"department": department},
            {"_id": 0, "emp_code": 1},
        )
    ]

    grouped = list(db.attendance_logs.aggregate([
        {
            "$match": {
                "emp_code": {"$in": employee_codes},
                "date": {"$gte": date_from, "$lte": date_to},
            }
        },
        {
            "$group": {
                "_id": "$date",
                "present": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", list(PRESENCE_STATUSES)]},
                            1,
                            0,
                        ]
                    }
                },
                "late": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
            }
        },
        {"$sort": {"_id": 1}},
    ]))

    by_date = {item["_id"]: item for item in grouped}
    trend = []

    current = start
    while current <= end:
        day_key = current.isoformat()
        item = by_date.get(day_key, {})

        trend.append({
            "date": day_key,
            "present": item.get("present", 0),
            "late": item.get("late", 0),
            "overtime_minutes": item.get("overtime_minutes", 0),
        })
        current += timedelta(days=1)

    return {
        "department": department,
        "date_from": date_from,
        "date_to": date_to,
        "trend": trend,
    }

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: str):
    """
    Return MongoDB execution statistics for a representative query
    used by a supported API endpoint.
    """

    # These are representative database filters for each API.
    # Keep the endpoint names aligned with the assessment's contract.
    explain_queries = {
        "employees": (
            db.employees,
            {"emp_code": "EMP0001"},
        ),
        "attendance": (
            db.attendance_logs,
            {"emp_code": "EMP0001", "date": "2026-10-15"},
        ),
        "attendance-punch-in": (
            db.attendance_logs,
            {"emp_code": "EMP0001", "date": "2026-10-15"},
        ),
        "attendance-punch-out": (
            db.attendance_logs,
            {"emp_code": "EMP0001", "date": "2026-10-15"},
        ),
        "analytics-monthly": (
            db.attendance_logs,
            {"emp_code": "EMP0001", "date": {"$gte": "2026-10-01", "$lte": "2026-10-31"}},
        ),
        "analytics-department-summary": (
            db.employees,
            {"department": "Engineering", "joined_on": {"$lte": "2026-10-31"}},
        ),
        "analytics-late-leaderboard": (
            db.attendance_logs,
            {"date": {"$gte": "2026-10-01", "$lte": "2026-10-31"}, "late_minutes": {"$gt": 0}},
        ),
        "analytics-department-trend": (
            db.attendance_logs,
            {"date": {"$gte": "2026-10-01", "$lte": "2026-10-31"}},
        ),
    }

    if endpoint not in explain_queries:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Unknown endpoint",
                "supported_endpoints": sorted(explain_queries.keys()),
            },
        )

    collection, query = explain_queries[endpoint]

    try:
        # This runs a real explain with execution statistics.
        explanation = collection.find(query).explain("executionStats")
    except PyMongoError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not explain query: {exc.__class__.__name__}",
        )

    def find_stages(node):
        """Recursively collect stage names from nested query-plan objects."""
        stages = []
        if isinstance(node, dict):
            if isinstance(node.get("stage"), str):
                stages.append(node["stage"])
            for value in node.values():
                stages.extend(find_stages(value))
        elif isinstance(node, list):
            for value in node:
                stages.extend(find_stages(value))
        return stages

    winning_plan = (
        explanation.get("queryPlanner", {})
        .get("winningPlan", {})
    )
    stages = find_stages(winning_plan)

    return {
        "endpoint": endpoint,
        "query": query,
        "execution_stats": explanation.get("executionStats", {}),
        "winning_plan": winning_plan,
        "stages": sorted(set(stages)),
        "uses_index": any(stage == "IXSCAN" for stage in stages),
        "has_collection_scan": any(stage == "COLLSCAN" for stage in stages),
    }


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
