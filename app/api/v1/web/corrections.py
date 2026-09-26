from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from datetime import datetime, timedelta
from typing import List, Optional
from pydantic import BaseModel

from app.core.database import get_session
from app.api.deps import require_hod, get_current_user
from app.models.user import User, RoleEnum
from app.models.attendance import (
    CorrectionRequest, 
    CorrectionStatus,
    AttendanceRecord, 
    AttendanceAuditLog,
    AttendanceStatus
)

router = APIRouter()

# --- SCHEMAS ---
class CorrectionDecisionPayload(BaseModel):
    decision: str # "APPROVE" or "REJECT"
    rejection_reason: Optional[str] = None # Required if decision is REJECT

class CorrectionQueueResponse(BaseModel):
    id: int
    attendance_record_id: int
    requested_by_name: str
    suggested_status: str
    reason: str
    requested_at: datetime


# --- ENDPOINTS ---
@router.get("/pending", response_model=List[CorrectionQueueResponse], tags=["HOD - Corrections Queue"])
async def get_pending_corrections(
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    """Fetch all pending correction requests for the dashboard."""
    statement = (
        select(CorrectionRequest, User.full_name)
        .join(User, CorrectionRequest.submitted_by_id == User.id)
        .where(CorrectionRequest.status == CorrectionStatus.PENDING)
    )
    results = await db.exec(statement)
    
    queue = []
    for request, teacher_name in results:
        queue.append(CorrectionQueueResponse(
            id=request.id,
            attendance_record_id=request.attendance_record_id,
            requested_by_name=teacher_name,
            suggested_status=request.suggested_status.value,
            reason=request.reason,
            requested_at=request.applied_on
        ))
    return queue


@router.post("/{request_id}/resolve", status_code=status.HTTP_200_OK, tags=["HOD - Corrections Queue"])
async def resolve_correction(
    request_id: int,
    payload: CorrectionDecisionPayload,
    current_admin: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session)
):
    """Approve or reject a correction request, enforcing the audit trail and time windows[cite: 1]."""
    
    if current_admin.role_assignments[0].role not in [RoleEnum.HOD, RoleEnum.COORDINATOR, RoleEnum.ADMIN]:
        raise HTTPException(status_code=403, detail="Not authorized to resolve corrections.")

    # 1. Fetch Request
    correction = await db.get(CorrectionRequest, request_id)
    if not correction or correction.status != CorrectionStatus.PENDING:
        raise HTTPException(status_code=404, detail="Pending correction request not found.")

    # 2. Fetch the target attendance record
    record = await db.get(AttendanceRecord, correction.attendance_record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Original attendance record not found.")

    # 3. Enforce the 7-day Escalation Window
    if (datetime.utcnow().date() - record.date) > timedelta(days=7):
        # Escalate to HOD/Admin if past 7 days
        if current_admin.role_assignments[0].role == RoleEnum.COORDINATOR:
            raise HTTPException(status_code=403, detail="Window expired. Only HOD or Admin can approve this.")

    # 4. Handle Rejection
    if payload.decision.upper() == "REJECT":
        if not payload.rejection_reason:
            raise HTTPException(status_code=400, detail="Rejection reason is mandatory.")
        
        correction.status = CorrectionStatus.REJECTED
        # Append the rejection reason so it is not lost
        correction.reason = f"{correction.reason} | REJECTED: {payload.rejection_reason}"
        
        db.add(correction)
        await db.commit()
        return {"status": "success", "message": "Correction rejected."}

    # 5. Handle Approval & Audit Trail
    if payload.decision.upper() == "APPROVE":
        # Create Audit Log[cite: 1]
        audit_log = AttendanceAuditLog(
            attendance_record_id=record.id,
            changed_by_id=current_admin.id,
            previous_status=record.status,
            new_status=correction.suggested_status,
            reason=f"Approved Correction Request #{correction.id}: {correction.reason}"
        )
        db.add(audit_log)

        # Update actual record[cite: 1]
        record.status = correction.suggested_status
        record.is_overridden = True
        db.add(record)

        # Mark request as approved
        correction.status = CorrectionStatus.APPROVED
        db.add(correction)

        await db.commit()
        return {"status": "success", "message": "Correction approved and audit log generated."}

    raise HTTPException(status_code=400, detail="Invalid decision. Must be APPROVE or REJECT.")