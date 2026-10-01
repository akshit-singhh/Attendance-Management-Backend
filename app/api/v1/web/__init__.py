
from fastapi import APIRouter
from .management import router as management_router
from .dashboard import router as dashboard_router
from .audit import router as audit_router # <-- Import your new router
# ... other imports ...

router = APIRouter()

router.include_router(management_router, prefix="/management")
router.include_router(dashboard_router, prefix="/dashboard")
router.include_router(audit_router, prefix="/audit-logs") # <-- Register it