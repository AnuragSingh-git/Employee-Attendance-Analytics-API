# HROne Attendance & Analytics Backend

A simple modular FastAPI + MongoDB implementation of the supplied HROne assignment. It uses a small number of files and **does not use Docker**.

## Folder structure

```text
hrone_backend_simple/
├── app/
│   ├── main.py              # Starts FastAPI and registers routes
│   ├── config.py            # Reads settings from .env / environment
│   ├── constants.py         # IST/UTC and valid attendance statuses
│   ├── database.py          # MongoDB client, collections, indexes
│   ├── schemas.py           # Validates incoming request bodies
│   ├── utils.py             # Shared timestamp, shift and calculation helpers
│   └── routes/
│       ├── employees.py     # Create/list employees
│       ├── attendance.py    # Punch-in/out, list and regularize attendance
│       ├── analytics.py     # Monthly, department, leaderboard and trend reports
│       └── admin.py         # Query explain endpoint
├── tests/test_utils.py      # Basic tests for timestamp and rounding helpers
├── sample_data/             # Example employees and attendance logs
├── sample_seed.py           # Loads supplied sample data into MongoDB
├── openapi.yaml             # Assignment API contract
├── DATA_MODEL.md            # Assignment data model and rules
├── PROBLEM_STATEMENT.docx   # Original assignment statement
├── REVIEW.md                # Review notes
├── DECISIONS.md             # Implementation decisions
├── .env.example             # Safe template for local configuration
└── requirements.txt         # Python packages
```

## What each code file does

- **`main.py`**: Creates the FastAPI app, checks the database on startup, creates indexes, and attaches the route modules. `/health` checks MongoDB connectivity.
- **`config.py`**: Reads `MONGO_URI`, `MONGO_DB`, and the MongoDB connection timeout from environment variables. `python-dotenv` loads a local `.env` file when present.
- **`constants.py`**: Defines IST, UTC, and the attendance statuses that count as presence.
- **`database.py`**: Creates one reusable MongoDB client, exposes the database/collections, and creates indexes. Unique indexes protect employee codes and one-record-per-employee/date attendance against concurrent duplicate inserts.
- **`schemas.py`**: Pydantic request models validate employee, punch-in, punch-out and correction inputs. Invalid request data is rejected before the route performs work.
- **`utils.py`**: Shared functions convert epoch milliseconds, determine IST attendance dates (including overnight shifts), calculate lateness/overtime/work hours/half-days, serialize MongoDB records, and validate pagination/date ranges.
- **`routes/employees.py`**: Employee creation and listing endpoints.
- **`routes/attendance.py`**: Attendance punch-in, punch-out, listing, and manual correction with audit history.
- **`routes/analytics.py`**: Employee monthly statistics, department summary, late leaderboard, and department daily trend.
- **`routes/admin.py`**: Provides the assignment's MongoDB explain endpoint for inspecting query plans and index use.
- **`tests/test_utils.py`**: Small unit tests for timestamp validation and rounding. Run the tests after installing dependencies.

The route files currently access MongoDB directly for simplicity. If the application grows, business logic and database access can later be split into separate service/repository files.

## Local setup (no Docker)

Requirements: Python 3.10+ and a reachable MongoDB instance.

1. Create and activate a virtual environment:

   **Windows PowerShell**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   **macOS/Linux**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install packages:
   ```bash
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env`, then set your MongoDB connection:
   ```env
   MONGO_URI=mongodb://localhost:27017
   MONGO_DB=attendance_db
   MONGO_TIMEOUT_MS=3000
   ```
   Do not commit `.env` or real credentials.

4. Start the API from the project root:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

5. Open interactive API docs at http://127.0.0.1:8000/docs. Health check: http://127.0.0.1:8000/health.

6. Run tests:
   ```bash
   pytest -q
   ```

## Notes

- MongoDB must be running before the API starts; startup fails if the database cannot be reached.
- This assignment contract does not define authentication/authorization. A `regularized_by` value is audit metadata, not proof that the caller is an authorized admin.
- Verify all endpoints against a running MongoDB instance and the assignment's test cases before submission.
