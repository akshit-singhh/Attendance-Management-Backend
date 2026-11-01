import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine
from app.schemas.health import (
    DatabaseCheck,
    StorageCheck,
    AppMetadata,
    HealthCheckResponse,
)

START_TIME = time.time()

router = APIRouter()


@router.get(
    "",
    response_model=HealthCheckResponse,
    status_code=status.HTTP_200_OK,
    summary="Comprehensive Production Health & Readiness Check",
    description="Probes database connectivity, filesystem write access, and service uptime for load balancers and monitoring systems."
)
async def health_check(response: Response):
    """
    Production Health Check.
    Returns HTTP 200 when all core services (Database, Storage) are operational.
    Returns HTTP 503 if any critical dependency fails.
    """
    is_healthy = True
    checks: Dict[str, Any] = {}

    # 1. Database Check
    db_start = time.perf_counter()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_latency = round((time.perf_counter() - db_start) * 1000, 2)
        checks["database"] = DatabaseCheck(
            status="healthy",
            latency_ms=db_latency,
            dialect="postgresql",
            error=None
        ).model_dump()
    except Exception as exc:
        is_healthy = False
        db_latency = round((time.perf_counter() - db_start) * 1000, 2)
        checks["database"] = DatabaseCheck(
            status="unhealthy",
            latency_ms=db_latency,
            dialect="postgresql",
            error=str(exc)
        ).model_dump()

    # 2. Storage / Uploads Check
    storage_path = "uploads"
    try:
        os.makedirs(storage_path, exist_ok=True)
        probe_file = os.path.join(storage_path, ".health_probe")
        with open(probe_file, "w") as f:
            f.write("probe")
        if os.path.exists(probe_file):
            os.remove(probe_file)
        checks["storage"] = StorageCheck(
            status="healthy",
            path=storage_path,
            writable=True,
            error=None
        ).model_dump()
    except Exception as exc:
        is_healthy = False
        checks["storage"] = StorageCheck(
            status="unhealthy",
            path=storage_path,
            writable=False,
            error=str(exc)
        ).model_dump()

    overall_status = "healthy" if is_healthy else "unhealthy"
    if not is_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    uptime = round(time.time() - START_TIME, 2)
    current_ts = datetime.now(timezone.utc).isoformat()
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    env_name = os.getenv("ENVIRONMENT", "production")

    return HealthCheckResponse(
        status=overall_status,
        app=AppMetadata(
            name=settings.PROJECT_NAME,
            version=settings.VERSION,
            uptime_seconds=uptime,
            timestamp=current_ts,
            python_version=py_ver,
            environment=env_name
        ),
        checks=checks
    )


