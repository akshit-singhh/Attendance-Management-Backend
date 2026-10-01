from fastapi import APIRouter, Depends, Query
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from sqlalchemy import func
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

from app.core.database import get_session
from app.api.deps import require_admin
from app.models.user import User
from app.models.attendance import AttendanceAuditLog

router = APIRouter()

# --- Schemas ---
class AuditLogItemResponse(BaseModel):
    id: int
    attendance_record_id: int
    changed_by_name: str
    changed_by_email: str
    old_status: str
    new_status: str
    reason: Optional[str]
    timestamp: datetime

class PaginatedAuditLogsResponse(BaseModel):
    items: List[AuditLogItemResponse]
    total: int
    page: int
    size: int

# --- Endpoints ---
@router.get("", response_model=PaginatedAuditLogsResponse, tags=["Admin - Audit Logs"])
async def get_audit_logs(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=100),
    user_id: Optional[int] = Query(default=None, description="Filter logs by the user who made the change"),
    record_id: Optional[int] = Query(default=None, description="Filter logs by a specific attendance record"),
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_session)
):
    """Fetch paginated audit logs of attendance modifications."""
    
    count_stmt = select(func.count(AttendanceAuditLog.id))
    stmt = select(AttendanceAuditLog, User).join(User, AttendanceAuditLog.changed_by_id == User.id)

    if user_id:
        count_stmt = count_stmt.where(AttendanceAuditLog.changed_by_id == user_id)
        stmt = stmt.where(AttendanceAuditLog.changed_by_id == user_id)
        
    if record_id:
        count_stmt = count_stmt.where(AttendanceAuditLog.attendance_record_id == record_id)
        stmt = stmt.where(AttendanceAuditLog.attendance_record_id == record_id)

    total_logs = (await db.exec(count_stmt)).one()

    skip = (page - 1) * size
    
    # We now know the exact column is named 'changed_at'
    stmt = stmt.order_by(AttendanceAuditLog.changed_at.desc()).offset(skip).limit(size)
    result = await db.exec(stmt)

    logs = []
    for log, user in result.all():
        logs.append(AuditLogItemResponse(
            id=log.id,
            attendance_record_id=log.attendance_record_id,
            changed_by_name=user.full_name,
            changed_by_email=user.email,
            old_status=log.previous_status, # We now know it's 'previous_status'
            new_status=log.new_status,
            reason=log.reason,
            timestamp=log.changed_at        # We now know it's 'changed_at'
        ))

    return PaginatedAuditLogsResponse(
        items=logs,
        total=total_logs,
        page=page,
        size=size
    )