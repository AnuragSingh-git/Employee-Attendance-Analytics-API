"""Environment-backed application settings."""
import os
from dotenv import load_dotenv
load_dotenv()
class Settings:
    MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    MONGO_DB: str = os.getenv("MONGO_DB", "attendance_db")
    MONGO_TIMEOUT_MS: int = int(os.getenv("MONGO_TIMEOUT_MS", "3000"))
settings = Settings()
