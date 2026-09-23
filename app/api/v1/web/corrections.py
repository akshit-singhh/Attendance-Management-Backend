from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from datetime import datetime, timedelta
from typing import List
from pydantic import BaseModel

from app.core.database import get_session
from app.api.deps import require_hod, get_current_user
from app.models.user import User, RoleEnum
from app.models.attendance import (
    CorrectionRequest, 
    AttendanceRecord, 
    AttendanceAuditLog, 
    AttendanceSession
)

router = APIRouter()

# --- SCHEMAS ---
class CorrectionDecisionPayload(BaseModel):
    decision: str # "APPROVE" or "REJECT"
    rejection_reason: str = None # Required if decision is REJECT

class CorrectionQueueResponse(BaseModel):
    id: int
    session_id: int
    record_id: int
    requested_by_name: str
    proposed_status: str
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
        .join(User, CorrectionRequest.requested_by_id == User.id)
        .where(CorrectionRequest.status == "PENDING")
    )
    results = await db.exec(statement)
    
    queue = []
    for request, teacher_name in results:
        queue.append(CorrectionQueueResponse(
            id=request.id,
            session_id=request.session_id,
            record_id=request.record_id,
            requested_by_name=teacher_name,
            proposed_status=request.proposed_status,
            reason=request.reason,
            requested_at=request.resolved_at or datetime.utcnow() # Fallback for display
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
    if not correction or correction.status != "PENDING":
        raise HTTPException(status_code=404, detail="Pending correction request not found.")

    # 2. Enforce the 7-day Escalation Window[cite: 1]
    session = await db.get(AttendanceSession, correction.session_id)
    if session.submitted_at and (datetime.utcnow() - session.submitted_at) > timedelta(days=7):
        # Escalate to HOD/Admin if past 7 days
        if current_admin.role_assignments[0].role == RoleEnum.COORDINATOR:
            raise HTTPException(status_code=403, detail="Window expired. Only HOD or Admin can approve this.")

    # 3. Handle Rejection
    if payload.decision.upper() == "REJECT":
        if not payload.rejection_reason:
            raise HTTPException(status_code=400, detail="Rejection reason is mandatory.")
        
        correction.status = "REJECTED"
        correction.rejection_reason = payload.rejection_reason
        correction.approved_by_id = current_admin.id
        correction.resolved_at = datetime.utcnow()
        
        db.add(correction)
        await db.commit()
        return {"status": "success", "message": "Correction rejected."}

    # 4. Handle Approval & Audit Trail
    if payload.decision.upper() == "APPROVE":
        record = await db.get(AttendanceRecord, correction.record_id)
        if not record:
            raise HTTPException(status_code=404, detail="Original attendance record not found.")

        # Create Audit Log[cite: 1]
        audit_log = AttendanceAuditLog(
            attendance_record_id=record.id,
            changed_by_id=current_admin.id,
            previous_status=record.status,
            new_status=correction.proposed_status,
            reason=f"Approved Correction Request #{correction.id}: {correction.reason}"
        )
        db.add(audit_log)

        # Update actual record[cite: 1]
        record.status = correction.proposed_status
        record.is_overridden = True
        db.add(record)

        # Mark request as approved
        correction.status = "APPROVED"
        correction.approved_by_id = current_admin.id
        correction.resolved_at = datetime.utcnow()
        db.add(correction)

        await db.commit()
        return {"status": "success", "message": "Correction approved and audit log generated."}

    raise HTTPException(status_code=400, detail="Invalid decision. Must be APPROVE or REJECT.")