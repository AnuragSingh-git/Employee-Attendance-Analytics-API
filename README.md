
# Employee Attendance and Analytics API

A FastAPI application backed by MongoDB for employee management, attendance tracking, attendance regularization, and analytics.

## Requirements

- Python 3.11+
- Git
- MongoDB 6.0+ (local instance or MongoDB Atlas)

## Setup

1. Clone the repository.
2. Create and activate a virtual environment.
3. Install dependencies.
4. Configure the MongoDB connection using environment variables expected by the application.
5. Start the API.

```bash
python -m venv .venv
```

Activate the environment:

- Windows: `.venv\Scripts\activate`
- macOS/Linux: `source .venv/bin/activate`

Install dependencies and run the application:

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs` to explore and test the API.

## Implementation

The application is implemented in `app/main.py`. It uses FastAPI for HTTP endpoints, Pydantic for request validation, and PyMongo for MongoDB access and aggregation.

The project includes employee management, attendance operations, analytics, database indexes, and a MongoDB explain endpoint, to the extent completed below.

## Verification

Tests and manual checks performed:

- [List the endpoints and scenarios you tested.]
- [Mention whether analytics were checked against expected results.]
- [Mention whether the explain endpoint was tested against the supplied dataset.]

## Known limitations / Missing work

**Update this section honestly before submission.** List any incomplete endpoints, known bugs, missing tests, or requirements you have not verified.

- [Write `None known` only if you have actually checked all required behaviour.]
- [Otherwise, describe what is incomplete and what remains to be tested.]

## Technical notes

- `DECISIONS.md` documents implementation choices and trade-offs.
- `REVIEW.md` documents review findings, fixes, and verification status.
- `requirements.txt` lists the Python dependencies.
