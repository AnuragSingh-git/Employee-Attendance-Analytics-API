# HROne Attendance & Analytics API

A modular FastAPI + MongoDB implementation of the supplied candidate-kit contract. Routes are separated from shared domain helpers, request schemas, and database setup. MongoDB owns filtering, sorting, pagination, unique constraints, and analytics aggregation.

## Quick start (local)

Requirements: Python 3.11+ and MongoDB 6/7 running locally.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Windows PowerShell: Copy-Item .env.example .env
uvicorn app.main:app --reload --port 8000
```

Open http://127.0.0.1:8000/docs for interactive API docs. Health: http://127.0.0.1:8000/health. Never commit `.env` or real credentials.

## Run with Docker

```bash
docker compose up --build
```

This starts MongoDB and the API. Data persists in the `mongo_data` volume.

## Folder structure and what every file does

```text
app/
  main.py                    Create FastAPI app, lifespan, include routers
  core/
    config.py                Read environment configuration
    constants.py             IST/UTC and status constants
    database.py              Reusable Mongo client, collections, indexes
    utils.py                 Date/time rules, calculations, serialization, shared helpers
  api/routes/
    employees.py             Health + employee create/list routes
    attendance.py            Punch-in/out, attendance listing, correction routes
    analytics.py             Monthly, department, leaderboard, trend reports
    admin.py                 MongoDB explain-plan diagnostics endpoint
  schemas/
    requests.py              Pydantic request validation models
  services/                  Reserved boundary for extracting business use-cases as the app grows
  repositories/              Reserved boundary for data-access classes if query complexity grows
  tests/                     Automated tests belong here
openapi.yaml                 Original API contract
DATA_MODEL.md                Original data model and business rules
PROBLEM_STATEMENT.docx       Original assignment statement
sample_seed.py               Provided sample-data helper
sample_data/                 Provided sample fixtures (if supplied)
Dockerfile                   Container image recipe
docker-compose.yml           Local API + MongoDB orchestration
.env.example                 Safe template for local settings
requirements.txt             Python dependencies
.gitignore                   Prevent committing secrets/cache/build artifacts
```

### Request flow

1. A client sends HTTP request to a route in `app/api/routes/`.
2. FastAPI validates the request with `app/schemas/requests.py`; malformed data normally returns HTTP 422.
3. Route code applies the assignment's business rules using `app/core/utils.py`.
4. The route queries or updates MongoDB via `app/core/database.py`. Unique indexes and atomic updates protect important race conditions.
5. The response is serialized to the contract's JSON representation (timestamps are epoch milliseconds).

### Important files explained

- **`app/main.py`**: only app assembly and startup/shutdown. Startup checks MongoDB and creates indexes before serving requests.
- **`app/core/config.py`**: central place for environment variables, so configuration is not scattered around route code.
- **`app/core/database.py`**: one reusable MongoClient per process and idempotent indexes. The unique `(emp_code, date)` attendance index prevents two simultaneous punch-ins for one attendance date.
- **`app/core/constants.py`**: canonical timezone and attendance status sets, avoiding subtly different copies across modules.
- **`app/core/utils.py`**: reusable timestamp conversion, attendance-date logic (including overnight shifts), lateness/overtime/work-hour calculations, pagination validation and response serialization.
- **`app/schemas/requests.py`**: request body types. Pydantic validates employee codes, shift times, statuses, and strict epoch-millisecond integer inputs before the route runs. Client-supplied derived metrics are ignored.
- **`app/api/routes/employees.py`**: creates employees and lists them with database-side filtering and pagination; duplicate employee codes become 409.
- **`app/api/routes/attendance.py`**: attendance lifecycle. Punch-in relies on a unique database index rather than a check-then-insert race; punch-out uses an atomic update; regularization appends audit history and uses compare-and-set to avoid silently overwriting a concurrent edit.
- **`app/api/routes/analytics.py`**: reporting endpoints. Data-intensive grouping is pushed into MongoDB aggregation rather than loading the whole collection into Python.
- **`app/api/routes/admin.py`**: returns execution plans for supported queries to inspect index use. Despite the route prefix, it is a diagnostics endpoint—not authentication.
- **`Dockerfile` / `docker-compose.yml`**: reproducible container packaging and local MongoDB setup.
- **`.env.example` / `.gitignore`**: safe configuration template and secret/cache exclusions.
- **`openapi.yaml`, `DATA_MODEL.md`, `PROBLEM_STATEMENT.docx`**: original assignment documents retained as reference.
- **`services/`, `repositories/`, `tests/`**: architectural seams for the next iteration. Move complex use-cases into services and Mongo query code into repositories as the application grows; add unit/integration tests here.

## API overview

- `GET /health`
- `POST /employees`, `GET /employees`
- `POST /attendance/punch-in`, `POST /attendance/punch-out`, `GET /attendance`
- `PATCH /attendance/{emp_code}/{date}`
- `GET /analytics/employees/{emp_code}/monthly`
- `GET /analytics/departments/summary`
- `GET /analytics/leaderboard/late`
- `GET /analytics/departments/{department}/trend`
- `GET /admin/explain/{endpoint}`

## Scale and security notes

- Use multiple API workers/replicas behind a load balancer for throughput; MongoDB indexes and atomic writes preserve key invariants across workers.
- For production, use MongoDB Atlas/replica set, TLS, credentials from a secret manager, connection-pool settings, structured logs, metrics, tracing, rate limits, backups, and CI tests.
- The assignment contract does not define authentication/authorization. `regularized_by` is audit metadata supplied by the caller, not proof of identity; secure the correction and explain routes before real deployment.
- Department trend uses MongoDB features such as `$densify` and `$setWindowFields`; use a MongoDB version that supports them (MongoDB 5.1+).
- This is a refactor of the provided starter implementation. Run against MongoDB and the supplied tests/contract before submission; passing syntax compilation alone does not prove every business edge case.
