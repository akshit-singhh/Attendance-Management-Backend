# app/schemas/health.py

from typing import Any, Dict, Optional
from pydantic import BaseModel


class DatabaseCheck(BaseModel):
    status: str
    latency_ms: Optional[float] = None
    dialect: Optional[str] = "postgresql"
    error: Optional[str] = None


class StorageCheck(BaseModel):
    status: str
    path: str
    writable: bool
    error: Optional[str] = None


class AppMetadata(BaseModel):
    name: str
    version: str
    uptime_seconds: float
    timestamp: str
    python_version: str
    environment: str


class HealthCheckResponse(BaseModel):
    status: str
    app: AppMetadata
    checks: Dict[str, Any]
