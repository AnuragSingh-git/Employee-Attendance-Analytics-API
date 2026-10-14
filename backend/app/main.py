"""ASGI application entry point. Keep this module intentionally small."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pymongo.errors import PyMongoError
from app.core.database import client, create_indexes
from app.api.routes import employees, attendance, analytics, admin

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast if MongoDB is not available, rather than accepting traffic that cannot work.
    client.admin.command("ping")
    create_indexes()
    yield
    client.close()

app = FastAPI(title="HROne Employee Attendance & Analytics API", version="2.1.0", lifespan=lifespan)
app.include_router(employees.router)
app.include_router(attendance.router)
app.include_router(analytics.router)
app.include_router(admin.router)

@app.get("/health", tags=["health"])
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok", "database": "connected"}
    except PyMongoError:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="MongoDB unavailable")
