from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.api.deps import get_current_user
from app.models.user import User, RoleEnum
# Adjust these imports based on where you placed the Leave models
from app.models.attendance import (
    LeaveRequest,
    LeaveStatus,
    AttendanceRecord,
    AttendanceAuditLog,
    AttendanceStatus
)

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
        
    # 2. Lock the Request[cite: 3]
    leave = await db.get(LeaveRequest, leave_id)
    if not leave or leave.status != LeaveStatus.PENDING:
        raise HTTPException(status_code=404, detail="Pending leave request not found.")
        
    # 3. Update Request Status[cite: 3]
    leave.status = LeaveStatus.APPROVED
    leave.reviewed_by_id = current_user.id
    db.add(leave)
    
    # 4. Fetch Historical Absences[cite: 3]
    absences_stmt = select(AttendanceRecord).where(
        (AttendanceRecord.student_id == leave.student_id) &
        (AttendanceRecord.date >= leave.start_date) &
        (AttendanceRecord.date <= leave.end_date) &
        (AttendanceRecord.status == AttendanceStatus.ABSENT)
    )
    absences = (await db.exec(absences_stmt)).all()
    
    # 5. The Audit Loop[cite: 3]
    for record in absences:
        # Insert Audit Log
        audit_log = AttendanceAuditLog(
            attendance_record_id=record.id,
            changed_by_id=current_user.id,
            previous_status=record.status,
            new_status=AttendanceStatus.LEAVE,
            reason=f"Approved Medical Leave Request #{leave.id}"
        )
        db.add(audit_log)
        
        # Update Record
        record.status = AttendanceStatus.LEAVE
        record.is_overridden = True
        db.add(record)
        
    # 6. Commit the entire transaction atomically[cite: 3]
    await db.commit()
    
    # 7. Scheduled Notifications (To be wired to email/FCM later)[cite: 3]
    # background_tasks.add_task(notification_service.send_leave_update, ...)
    
    return {
        "status": "success", 
        "message": f"Leave approved. {len(absences)} absence(s) automatically converted to leave.",
        "records_updated": len(absences)
    }