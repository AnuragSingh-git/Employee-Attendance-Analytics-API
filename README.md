
# Employee Attendance and Analytics API

A REST API built with FastAPI and MongoDB for employee management, attendance tracking, attendance regularization, and attendance analytics.

## Requirements

- Python 3.11+
- Git
- MongoDB 6.0 or newer.

MongoDB can run locally or through a MongoDB Atlas cluster.

## Setup and Run

Create and activate a virtual environment:

```bash
python -m venv .venv
```

Activate it:

- Windows: `.venv\Scripts\activate`
- macOS/Linux: `source .venv/bin/activate`

Install dependencies:

```bash
pip install -r requirements.txt
```

Configure the MongoDB connection using the environment variables expected by the application. Start the API:

```bash
uvicorn app.main:app --reload
```

Interactive API documentation is available at `http://127.0.0.1:8000/docs`.

## Features

- Employee creation and listing with pagination.
- Attendance punch-in and punch-out.
- Attendance listing and regularization.
- Monthly employee attendance analytics.
- Department attendance summaries.
- Late-arrival leaderboard.
- Daily department attendance trends.
- MongoDB indexes and query-plan inspection through the explain endpoint.

## Technical Overview

- **FastAPI** handles HTTP routes and request validation.
- **Pydantic** validates incoming request data.
- **PyMongo** provides database access and MongoDB aggregation pipelines.
- **MongoDB** stores employee and attendance records and enforces unique indexes.

The API uses epoch milliseconds for timestamps at the API boundary and UTC datetimes for database storage. Attendance dates and shift calculations use India Standard Time (UTC+05:30).

## Project Files

- `app/main.py` — API endpoints and application logic.
- `requirements.txt` — Python dependencies.
- `DECISIONS.md` — technical decisions and trade-offs.
- `REVIEW.md` — code review findings and verification status.
- `openapi.yaml` — API contract, if included in the repository.
- `DATA_MODEL.md` — data model documentation, if included in the repository.