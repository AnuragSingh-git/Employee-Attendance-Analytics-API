"""MongoDB client and collection handles. One client is reused per process."""
from pymongo import MongoClient, ASCENDING, DESCENDING
from app.config import settings
client = MongoClient(settings.MONGO_URI, serverSelectionTimeoutMS=settings.MONGO_TIMEOUT_MS, tz_aware=False)
db = client[settings.MONGO_DB]

def create_indexes() -> None:
    """Create idempotent indexes; unique indexes protect against concurrent duplicates."""
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="uq_employee_code")
    db.employees.create_index([("department", ASCENDING), ("emp_code", ASCENDING)], name="ix_employee_department_code")
    db.employees.create_index([("joined_on", ASCENDING), ("department", ASCENDING)], name="ix_employee_joined_department")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uq_attendance_employee_date")
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="ix_attendance_date_employee")
    db.attendance_logs.create_index([("date", ASCENDING), ("status", ASCENDING), ("emp_code", ASCENDING)], name="ix_attendance_date_status_employee")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("punch_in", DESCENDING)], name="ix_attendance_employee_punchin")
