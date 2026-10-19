from fastapi import APIRouter, Depends, Query, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from sqlalchemy import func
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from datetime import datetime, date

from app.core.database import get_session
from app.api.deps import require_admin, require_coordinator
from app.models.user import User, StudentProfile
from app.models.attendance import (
    AttendanceAuditLog,
    AttendanceSession,
    AttendanceRecord,
    AttendanceStatus,
)
from app.models.academic import CourseOffering, Subject, Section, AcademicTerm

router = APIRouter()

# --- Schemas (imported from app.schemas) ---
from app.schemas.audit import (
    AuditLogItemResponse,
    PaginatedAuditLogsResponse,
)
from app.schemas.attendance import (
    AttendanceSessionItemResponse,
    PaginatedAttendanceSessionsResponse,
    SessionStudentRecord,
    SessionDetailResponse,
)


# --- Endpoints ---

@router.get("/sessions", response_model=PaginatedAttendanceSessionsResponse, tags=["Admin - Attendance Sessions"])
async def get_attendance_sessions(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=100),
    target_date: Optional[str] = Query(default=None, alias="date"),
    teacher_id: Optional[int] = Query(default=None),
    course_offering_id: Optional[int] = Query(default=None),
    search: Optional[str] = Query(default=None),
    admin_id: int = Depends(require_coordinator),
    db: AsyncSession = Depends(get_session)
):
    """Fetch paginated live attendance sessions marked by teachers."""
    base_stmt = (
        select(
            AttendanceSession,
            User,
            Subject,
            Section,
            AcademicTerm
        )
        .join(User, AttendanceSession.teacher_id == User.id)
        .join(CourseOffering, AttendanceSession.course_offering_id == CourseOffering.id)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .join(Section, CourseOffering.section_id == Section.id)
        .outerjoin(AcademicTerm, CourseOffering.term_id == AcademicTerm.id)
    )

    count_stmt = (
        select(func.count(AttendanceSession.id))
        .join(User, AttendanceSession.teacher_id == User.id)
        .join(CourseOffering, AttendanceSession.course_offering_id == CourseOffering.id)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .join(Section, CourseOffering.section_id == Section.id)
    )

    if target_date and isinstance(target_date, str):
        try:
            parsed_d = datetime.strptime(target_date.strip(), "%Y-%m-%d").date()
            base_stmt = base_stmt.where(AttendanceSession.date == parsed_d)
            count_stmt = count_stmt.where(AttendanceSession.date == parsed_d)
        except ValueError:
            pass

    if teacher_id and isinstance(teacher_id, int):
        base_stmt = base_stmt.where(AttendanceSession.teacher_id == teacher_id)
        count_stmt = count_stmt.where(AttendanceSession.teacher_id == teacher_id)

    if course_offering_id and isinstance(course_offering_id, int):
        base_stmt = base_stmt.where(AttendanceSession.course_offering_id == course_offering_id)
        count_stmt = count_stmt.where(AttendanceSession.course_offering_id == course_offering_id)

    if search and isinstance(search, str) and search.strip():
        s_term = f"%{search.strip().lower()}%"
        search_filter = (
            func.lower(User.full_name).like(s_term) |
            func.lower(User.email).like(s_term) |
            func.lower(Subject.name).like(s_term) |
            func.lower(Subject.course_code).like(s_term) |
            func.lower(Section.name).like(s_term)
        )
        base_stmt = base_stmt.where(search_filter)
        count_stmt = count_stmt.where(search_filter)

    total_sessions = (await db.exec(count_stmt)).one()

    skip = (page - 1) * size
    stmt = base_stmt.order_by(AttendanceSession.date.desc(), AttendanceSession.id.desc()).offset(skip).limit(size)
    results = (await db.exec(stmt)).all()

    items = []
    for sess, teacher, subject, section, term in results:
        rec_stmt = select(AttendanceRecord.status).where(AttendanceRecord.session_id == sess.id)
        statuses = (await db.exec(rec_stmt)).all()

        total_st = len(statuses)
        pres_ct = sum(1 for st in statuses if st == AttendanceStatus.PRESENT)
        abs_ct = sum(1 for st in statuses if st == AttendanceStatus.ABSENT)
        leave_ct = sum(1 for st in statuses if st == AttendanceStatus.LEAVE)
        pct = round((pres_ct / total_st * 100.0), 1) if total_st > 0 else 0.0

        items.append(AttendanceSessionItemResponse(
            id=sess.id,
            session_uuid=sess.session_uuid,
            date=str(sess.date),
            teacher_id=teacher.id,
            teacher_name=teacher.full_name,
            teacher_email=teacher.email,
            course_offering_id=sess.course_offering_id,
            subject_code=subject.course_code,
            subject_name=subject.name,
            section_name=section.name,
            term_name=term.name if term else None,
            duration_periods=sess.duration_periods,
            mode=sess.mode.value if hasattr(sess.mode, "value") else str(sess.mode),
            status=sess.status.value if hasattr(sess.status, "value") else str(sess.status),
            total_students=total_st,
            present_count=pres_ct,
            absent_count=abs_ct,
            leave_count=leave_ct,
            attendance_percentage=pct,
            submitted_at=sess.submitted_at,
            created_at=sess.created_at,
        ))

    # Summary metrics for today
    today_date = date.today()
    today_sessions = (await db.exec(select(AttendanceSession.id).where(AttendanceSession.date == today_date))).all()
    today_rec_stmt = select(AttendanceRecord.status).where(AttendanceRecord.date == today_date)
    today_statuses = (await db.exec(today_rec_stmt)).all()
    today_tot = len(today_statuses)
    today_pres = sum(1 for s in today_statuses if s == AttendanceStatus.PRESENT)
    today_pct = round((today_pres / today_tot * 100.0), 1) if today_tot > 0 else 0.0

    return PaginatedAttendanceSessionsResponse(
        items=items,
        total=total_sessions,
        page=page,
        size=size,
        summary_today={
            "sessions_today": len(today_sessions),
            "students_marked_today": today_tot,
            "present_today": today_pres,
            "overall_percentage_today": today_pct,
        }
    )


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse, tags=["Admin - Attendance Sessions"])
async def get_session_detail(
    session_id: int,
    admin_id: int = Depends(require_coordinator),
    db: AsyncSession = Depends(get_session)
):
    """Fetch details and student-by-student roster of a specific attendance session."""
    stmt = (
        select(AttendanceSession, User, Subject, Section, AcademicTerm)
        .join(User, AttendanceSession.teacher_id == User.id)
        .join(CourseOffering, AttendanceSession.course_offering_id == CourseOffering.id)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .join(Section, CourseOffering.section_id == Section.id)
        .outerjoin(AcademicTerm, CourseOffering.term_id == AcademicTerm.id)
        .where(AttendanceSession.id == session_id)
    )
    res = (await db.exec(stmt)).first()
    if not res:
        raise HTTPException(status_code=404, detail="Attendance session not found")

    sess, teacher, subject, section, term = res

    rec_stmt = (
        select(AttendanceRecord, User, StudentProfile)
        .join(User, AttendanceRecord.student_id == User.id)
        .outerjoin(StudentProfile, User.id == StudentProfile.user_id)
        .where(AttendanceRecord.session_id == session_id)
        .order_by(StudentProfile.roll_number.asc(), User.full_name.asc())
    )
    records_res = (await db.exec(rec_stmt)).all()

    student_records = []
    total_st = len(records_res)
    pres_ct = 0
    abs_ct = 0
    leave_ct = 0

    for rec, student_user, profile in records_res:
        if rec.status == AttendanceStatus.PRESENT:
            pres_ct += 1
        elif rec.status == AttendanceStatus.ABSENT:
            abs_ct += 1
        elif rec.status == AttendanceStatus.LEAVE:
            leave_ct += 1

        student_records.append(SessionStudentRecord(
            record_id=rec.id,
            student_id=student_user.id,
            roll_number=profile.roll_number if profile else f"ID-{student_user.id}",
            student_name=student_user.full_name,
            student_email=student_user.email,
            status=rec.status.value if hasattr(rec.status, "value") else str(rec.status),
            captured_at=rec.captured_at,
            is_flagged=rec.is_flagged,
            flag_reason=rec.flag_reason,
        ))

    pct = round((pres_ct / total_st * 100.0), 1) if total_st > 0 else 0.0

    session_item = AttendanceSessionItemResponse(
        id=sess.id,
        session_uuid=sess.session_uuid,
        date=str(sess.date),
        teacher_id=teacher.id,
        teacher_name=teacher.full_name,
        teacher_email=teacher.email,
        course_offering_id=sess.course_offering_id,
        subject_code=subject.course_code,
        subject_name=subject.name,
        section_name=section.name,
        term_name=term.name if term else None,
        duration_periods=sess.duration_periods,
        mode=sess.mode.value if hasattr(sess.mode, "value") else str(sess.mode),
        status=sess.status.value if hasattr(sess.status, "value") else str(sess.status),
        total_students=total_st,
        present_count=pres_ct,
        absent_count=abs_ct,
        leave_count=leave_ct,
        attendance_percentage=pct,
        submitted_at=sess.submitted_at,
        created_at=sess.created_at,
    )

    return SessionDetailResponse(
        session=session_item,
        records=student_records
    )


@router.get("", response_model=PaginatedAuditLogsResponse, tags=["Admin - Audit Logs"])
async def get_audit_logs(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=100),
    user_id: Optional[int] = Query(default=None, description="Filter logs by the user who made the change"),
    record_id: Optional[int] = Query(default=None, description="Filter logs by a specific attendance record"),
    admin_id: int = Depends(require_coordinator),
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
    stmt = stmt.order_by(AttendanceAuditLog.changed_at.desc()).offset(skip).limit(size)
    result = await db.exec(stmt)

    logs = []
    for log, user in result.all():
        logs.append(AuditLogItemResponse(
            id=log.id,
            attendance_record_id=log.attendance_record_id,
            changed_by_name=user.full_name,
            changed_by_email=user.email,
            old_status=log.previous_status,
            new_status=log.new_status,
            reason=log.reason,
            timestamp=log.changed_at
        ))

    return PaginatedAuditLogsResponse(
        items=logs,
        total=total_logs,
        page=page,
        size=size
    )