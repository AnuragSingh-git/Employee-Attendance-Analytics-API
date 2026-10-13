# HROne Employee Attendance & Analytics API

FastAPI + MongoDB implementation of the candidate assignment.

## What is included

- Employee creation/listing with database-enforced unique employee codes.
- Punch-in and punch-out with IST attendance dates, overnight shifts, lateness, overtime, work-hours and half-day calculations.
- Attendance filtering, sorting and pagination in MongoDB.
- Manual regularization with an append-only history record and optimistic concurrency protection.
- Monthly employee analytics, department summaries, late leaderboard, daily department trend, and MongoDB explain endpoint.
- MongoDB indexes created idempotently at application startup.

## Run locally

Requires Python 3.10+ and MongoDB 7.0+ (MongoDB 6.0+ for most features; `$densify`/window analytics need a compatible server).

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Start MongoDB locally, or set MONGO_URI to your MongoDB Atlas connection string.
python sample_seed.py
uvicorn app.main:app --port 8000 --reload
```

Open `http://127.0.0.1:8000/docs` for interactive API documentation. `/health` pings MongoDB and returns 503 if it is unavailable.

## Configuration

- `MONGO_URI`: MongoDB connection URI.
- `MONGO_DB`: database name (defaults to `attendance_db`).

Never commit `.env` or database credentials.

## Important behavior

- All API timestamps are integer epoch milliseconds; stored instants are BSON datetimes.
- `date` is an IST attendance date, including the previous shift-start date for overnight shifts.
- Punch-in/punch-out are not approval workflows. PATCH is the manual correction endpoint and records `regularized_by` and `reason` in the audit trail.
- This assignment's OpenAPI contract defines no authentication or authorization. Add real authentication/role checks before exposing admin or correction routes in production.
- Derived fields are server-calculated. Clients cannot override work hours, lateness, overtime, or half-day values.

## Submission note

The implementation is based on `openapi.yaml` and `DATA_MODEL.md`. Run it against MongoDB and exercise the endpoints in `/docs` before submission, especially aggregation behavior against the grader's MongoDB version and large dataset.
