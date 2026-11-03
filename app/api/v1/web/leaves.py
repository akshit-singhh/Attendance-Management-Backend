import logging
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.api.deps import get_current_user
from app.models.user import User, RoleEnum
from app.models.attendance import (
    LeaveRequest,
    LeaveStatus,
    AttendanceRecord,
    AttendanceAuditLog,
    AttendanceStatus
)
from app.api.v1.notifications import notify_users

logger = logging.getLogger("attendance.notifications")

router = APIRouter()

@router.post("/{leave_id}/approve", status_code=status.HTTP_200_OK, tags=["HOD - Leaves Queue"])
async def approve_leave_transaction(
    leave_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session)
):
    """Massive database transaction to approve a leave, rewrite history, and log audits."""
    
    # 1. Authorize Coordinator/HOD
    if current_user.role_assignments[0].role not in [RoleEnum.COORDINATOR, RoleEnum.HOD, RoleEnum.ADMIN]:
        raise HTTPException(status_code=403, detail="Not authorized to approve leaves.")
        
    # 2. Lock the Request
    leave = await db.get(LeaveRequest, leave_id)
    if not leave or leave.status != LeaveStatus.PENDING:
        raise HTTPException(status_code=404, detail="Pending leave request not found.")
        
    # 3. Update Request Status
    leave.status = LeaveStatus.APPROVED
    leave.reviewed_by_id = current_user.id
    db.add(leave)
    
    # 4. Fetch Historical Absences
    absences_stmt = select(AttendanceRecord).where(
        (AttendanceRecord.student_id == leave.student_id) &
        (AttendanceRecord.date >= leave.start_date) &
        (AttendanceRecord.date <= leave.end_date) &
        (AttendanceRecord.status == AttendanceStatus.ABSENT)
    )
    absences = (await db.exec(absences_stmt)).all()
    
    # 5. The Audit Loop
    for record in absences:
        audit_log = AttendanceAuditLog(
            attendance_record_id=record.id,
            changed_by_id=current_user.id,
            previous_status=record.status,
            new_status=AttendanceStatus.LEAVE,
            reason=f"Approved Medical Leave Request #{leave.id}"
        )
        db.add(audit_log)
        
        record.status = AttendanceStatus.LEAVE
        record.is_overridden = True
        db.add(record)
        
    # 6. Commit the entire transaction atomically
    await db.commit()
    
    # 7. Notify student of approval
    try:
        start_str = leave.start_date.strftime("%d %b %Y") if hasattr(leave.start_date, "strftime") else str(leave.start_date)
        end_str = leave.end_date.strftime("%d %b %Y") if hasattr(leave.end_date, "strftime") else str(leave.end_date)
        await notify_users(
            db=db,
            background_tasks=background_tasks,
            sender_id=current_user.id,
            user_ids=[leave.student_id],
            title="Leave Request Approved",
            message=f"Your leave request from {start_str} to {end_str} has been approved.",
            notification_type="LEAVE_UPDATE",
            priority="NORMAL"
        )
    except Exception:
        logger.exception("Failed to dispatch leave approval notification")
    
    return {
        "status": "success", 
        "message": f"Leave approved. {len(absences)} absence(s) automatically converted to leave.",
        "records_updated": len(absences)
    }


@router.post("/{leave_id}/reject", status_code=status.HTTP_200_OK, tags=["HOD - Leaves Queue"])
async def reject_leave_transaction(
    leave_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session)
):
    """Reject a pending leave request and notify student."""
    if current_user.role_assignments[0].role not in [RoleEnum.COORDINATOR, RoleEnum.HOD, RoleEnum.ADMIN]:
        raise HTTPException(status_code=403, detail="Not authorized to reject leaves.")

    leave = await db.get(LeaveRequest, leave_id)
    if not leave or leave.status != LeaveStatus.PENDING:
        raise HTTPException(status_code=404, detail="Pending leave request not found.")

    leave.status = LeaveStatus.REJECTED
    leave.reviewed_by_id = current_user.id
    db.add(leave)
    await db.commit()

    try:
        start_str = leave.start_date.strftime("%d %b %Y") if hasattr(leave.start_date, "strftime") else str(leave.start_date)
        end_str = leave.end_date.strftime("%d %b %Y") if hasattr(leave.end_date, "strftime") else str(leave.end_date)
        await notify_users(
            db=db,
            background_tasks=background_tasks,
            sender_id=current_user.id,
            user_ids=[leave.student_id],
            title="Leave Request Rejected",
            message=f"Your leave request from {start_str} to {end_str} has been rejected.",
            notification_type="LEAVE_UPDATE",
            priority="NORMAL"
        )
    except Exception:
        logger.exception("Failed to dispatch leave rejection notification")

    return {
        "status": "success",
        "message": "Leave request rejected."
    }
