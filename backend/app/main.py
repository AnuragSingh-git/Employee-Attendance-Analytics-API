"""Application entry point: configure FastAPI and attach endpoint groups."""
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pymongo.errors import PyMongoError

from app.database import client, create_indexes
from app.routes import employees, attendance, analytics, admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Check MongoDB and create indexes when the API starts."""
    client.admin.command("ping")
    create_indexes()
    yield
    client.close()


app = FastAPI(
    title="HROne Employee Attendance & Analytics API",
    version="2.1.0",
    lifespan=lifespan,
)
app.include_router(employees.router)
app.include_router(attendance.router)
app.include_router(analytics.router)
app.include_router(admin.router)


@app.get("/health", tags=["health"])
def health():
    """Return service health, including MongoDB connectivity."""
    try:
        client.admin.command("ping")
        return {"status": "ok", "database": "connected"}
    except PyMongoError:
        raise HTTPException(status_code=503, detail="MongoDB unavailable")
