from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from sqlalchemy.orm import aliased
from datetime import datetime, timedelta
from typing import List, Optional

from app.core.database import get_session
from app.api.deps import require_hod, get_current_user
from app.models.user import User, RoleEnum, StudentProfile
from app.models.academic import CourseOffering, Subject
from app.models.attendance import (
    CorrectionRequest, 
    CorrectionStatus,
    AttendanceRecord, 
    AttendanceAuditLog,
    AttendanceStatus
)
from app.schemas.corrections import (
    CorrectionDecisionPayload,
    BulkCorrectionDecisionPayload,
    CorrectionQueueResponse,
)

router = APIRouter()


async def fetch_corrections_list(
    db: AsyncSession,
    status_filter: Optional[str] = None
) -> List[CorrectionQueueResponse]:
    TeacherUser = aliased(User, name="teacher")
    StudentUser = aliased(User, name="student")

    stmt = (
        select(
            CorrectionRequest,
            TeacherUser.full_name.label("teacher_name"),
            AttendanceRecord,
            StudentUser.full_name.label("student_name"),
            StudentProfile.roll_number.label("student_roll"),
            Subject.course_code,
            Subject.name.label("subject_name"),
        )
        .join(TeacherUser, CorrectionRequest.submitted_by_id == TeacherUser.id)
        .join(AttendanceRecord, CorrectionRequest.attendance_record_id == AttendanceRecord.id)
        .outerjoin(StudentUser, AttendanceRecord.student_id == StudentUser.id)
        .outerjoin(StudentProfile, StudentProfile.user_id == StudentUser.id)
        .outerjoin(CourseOffering, AttendanceRecord.course_offering_id == CourseOffering.id)
        .outerjoin(Subject, CourseOffering.subject_id == Subject.id)
    )

    if status_filter and status_filter.upper() != "ALL":
        try:
            target_status = CorrectionStatus(status_filter.upper())
            stmt = stmt.where(CorrectionRequest.status == target_status)
        except ValueError:
            pass

    stmt = stmt.order_by(CorrectionRequest.applied_on.desc())
    results = (await db.exec(stmt)).all()

    queue = []
    today = datetime.utcnow().date()
    for corr, t_name, rec, s_name, s_roll, c_code, c_name in results:
        is_escalated = (today - rec.date) > timedelta(days=7) if rec.date else False
        curr_status = rec.status.value if hasattr(rec.status, "value") else str(rec.status)
        sug_status = corr.suggested_status.value if hasattr(corr.suggested_status, "value") else str(corr.suggested_status)
        req_status = corr.status.value if hasattr(corr.status, "value") else str(corr.status)

        queue.append(
            CorrectionQueueResponse(
                id=corr.id,
                attendance_record_id=corr.attendance_record_id,
                requested_by_name=t_name or "Faculty",
                student_name=s_name or "Student",
                student_roll=s_roll or "N/A",
                subject_code=c_code or "N/A",
                subject_name=c_name or "N/A",
                class_date=rec.date.isoformat() if rec.date else None,
                current_status=curr_status,
                suggested_status=sug_status,
                status=req_status,
                reason=corr.reason,
                requested_at=corr.applied_on,
                is_escalated=is_escalated,
            )
        )
    return queue


@router.get("", response_model=List[CorrectionQueueResponse], tags=["HOD - Corrections Queue"])
async def list_corrections(
    status: Optional[str] = None,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    """Fetch correction requests for the admin/HOD dashboard with optional status filter (ALL, PENDING, APPROVED, REJECTED)."""
    return await fetch_corrections_list(db, status_filter=status)


@router.get("/pending", response_model=List[CorrectionQueueResponse], tags=["HOD - Corrections Queue"])
async def get_pending_corrections(
    status: Optional[str] = "PENDING",
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    """Fetch pending correction requests for backward compatibility or with status override."""
    return await fetch_corrections_list(db, status_filter=status)


@router.post("/{request_id}/resolve", status_code=status.HTTP_200_OK, tags=["HOD - Corrections Queue"])
async def resolve_correction(
    request_id: int,
    payload: CorrectionDecisionPayload,
    current_admin: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session)
):
    """Approve or reject a single correction request, enforcing the audit trail and time windows."""
    user_roles = {ra.role for ra in current_admin.role_assignments}
    if not (user_roles & {RoleEnum.HOD, RoleEnum.COORDINATOR, RoleEnum.ADMIN}):
        raise HTTPException(status_code=403, detail="Not authorized to resolve corrections.")

    decision_clean = payload.decision.strip().upper()
    if decision_clean in ["APPROVE", "APPROVED"]:
        decision_clean = "APPROVE"
    elif decision_clean in ["REJECT", "REJECTED"]:
        decision_clean = "REJECT"
    else:
        raise HTTPException(status_code=400, detail="Invalid decision. Must be APPROVE or REJECT.")

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
        if RoleEnum.ADMIN not in user_roles and RoleEnum.HOD not in user_roles:
            raise HTTPException(status_code=403, detail="Window expired (7 days). Only HOD or Admin can approve this.")

    # 4. Handle Rejection
    if decision_clean == "REJECT":
        if not payload.rejection_reason or not payload.rejection_reason.strip():
            raise HTTPException(status_code=400, detail="Rejection reason is mandatory.")

        correction.status = CorrectionStatus.REJECTED
        correction.reason = f"{correction.reason} | REJECTED: {payload.rejection_reason.strip()}"
        db.add(correction)
        await db.commit()
        return {"status": "success", "message": "Correction rejected."}

    # 5. Handle Approval & Audit Trail
    if decision_clean == "APPROVE":
        audit_log = AttendanceAuditLog(
            attendance_record_id=record.id,
            changed_by_id=current_admin.id,
            previous_status=record.status,
            new_status=correction.suggested_status,
            reason=f"Approved Correction Request #{correction.id}: {correction.reason}"
        )
        db.add(audit_log)

        record.status = correction.suggested_status
        record.is_overridden = True
        db.add(record)

        correction.status = CorrectionStatus.APPROVED
        db.add(correction)

        await db.commit()
        return {"status": "success", "message": "Correction approved and audit log generated."}


@router.post("/bulk-resolve", status_code=status.HTTP_200_OK, tags=["HOD - Corrections Queue"])
async def bulk_resolve_corrections(
    payload: BulkCorrectionDecisionPayload,
    current_admin: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session)
):
    """Batch approve or reject multiple correction requests."""
    user_roles = {ra.role for ra in current_admin.role_assignments}
    if not (user_roles & {RoleEnum.HOD, RoleEnum.COORDINATOR, RoleEnum.ADMIN}):
        raise HTTPException(status_code=403, detail="Not authorized to resolve corrections.")

    decision_clean = payload.decision.strip().upper()
    if decision_clean in ["APPROVE", "APPROVED"]:
        decision_clean = "APPROVE"
    elif decision_clean in ["REJECT", "REJECTED"]:
        decision_clean = "REJECT"
    else:
        raise HTTPException(status_code=400, detail="Invalid decision. Must be APPROVE or REJECT.")

    if decision_clean == "REJECT" and (not payload.rejection_reason or not payload.rejection_reason.strip()):
        raise HTTPException(status_code=400, detail="Rejection reason is mandatory for bulk rejection.")

    resolved_ids: List[int] = []
    errors: List[str] = []
    today = datetime.utcnow().date()

    for req_id in payload.request_ids:
        correction = await db.get(CorrectionRequest, req_id)
        if not correction or correction.status != CorrectionStatus.PENDING:
            errors.append(f"Request #{req_id} is not pending or not found.")
            continue

        record = await db.get(AttendanceRecord, correction.attendance_record_id)
        if not record:
            errors.append(f"Original record for request #{req_id} not found.")
            continue

        if (today - record.date) > timedelta(days=7):
            if RoleEnum.ADMIN not in user_roles and RoleEnum.HOD not in user_roles:
                errors.append(f"Request #{req_id} exceeds 7-day window. HOD/Admin approval required.")
                continue

        if decision_clean == "REJECT":
            correction.status = CorrectionStatus.REJECTED
            correction.reason = f"{correction.reason} | REJECTED: {payload.rejection_reason.strip()}"
            db.add(correction)
            resolved_ids.append(req_id)
        elif decision_clean == "APPROVE":
            audit_log = AttendanceAuditLog(
                attendance_record_id=record.id,
                changed_by_id=current_admin.id,
                previous_status=record.status,
                new_status=correction.suggested_status,
                reason=f"Approved in batch: Request #{correction.id}: {correction.reason}"
            )
            db.add(audit_log)
            record.status = correction.suggested_status
            record.is_overridden = True
            db.add(record)
            correction.status = CorrectionStatus.APPROVED
            db.add(correction)
            resolved_ids.append(req_id)

    await db.commit()
    return {
        "status": "success",
        "resolved_count": len(resolved_ids),
        "total_requested": len(payload.request_ids),
        "resolved_ids": resolved_ids,
        "errors": errors
    }
