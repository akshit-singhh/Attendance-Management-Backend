from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from typing import List
from datetime import date, datetime
from sqlalchemy.exc import IntegrityError

from app.core.database import get_session
from app.api.deps import require_teacher
from app.models.user import User, StudentProfile
from app.models.academic import CourseOffering, Subject, Section, StudentSubjectMap, SubstituteGrant
from app.models.attendance import (
    AttendanceSession,
    AttendanceRecord,
    SessionStatus,
    OfflineSyncPayload,
    StudentRosterResponse,
    CourseScheduleResponse
)

router = APIRouter()

@router.get("/schedule", response_model=List[CourseScheduleResponse])
async def get_daily_schedule(
    term_id: int, 
    teacher_id: int = Depends(require_teacher),
    db: AsyncSession = Depends(get_session)
):
    """Fetch all classes assigned to this specific teacher for the current term."""
    
    statement = (
        select(CourseOffering, Subject, Section)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .join(Section, CourseOffering.section_id == Section.id)
        .where(CourseOffering.teacher_id == teacher_id)
        .where(CourseOffering.term_id == term_id)
    )
    
    results = await db.exec(statement)
    
    schedule = []
    for offering, subject, section in results:
        schedule.append(CourseScheduleResponse(
            course_offering_id=offering.id,
            subject_name=subject.name,
            subject_code=subject.course_code,
            section_name=section.name
        ))
        
    return schedule


@router.get("/roster/{course_offering_id}", response_model=List[StudentRosterResponse])
async def get_class_roster(
    course_offering_id: int,
    teacher_id: int = Depends(require_teacher),
    db: AsyncSession = Depends(get_session)
):
    """Fetch the exact list of students enrolled in this specific course offering."""
    
    offering = await db.get(CourseOffering, course_offering_id)
    if not offering or offering.teacher_id != teacher_id:
        raise HTTPException(status_code=403, detail="You do not have access to this roster.")

    statement = (
        select(User, StudentProfile)
        .join(StudentProfile, User.id == StudentProfile.user_id)
        .join(StudentSubjectMap, StudentProfile.id == StudentSubjectMap.student_id)
        .where(StudentSubjectMap.course_offering_id == course_offering_id)
    )
    
    results = await db.exec(statement)
    
    roster = []
    for user, profile in results:
        roster.append(StudentRosterResponse(
            student_id=user.id,
            roll_number=profile.roll_number,
            full_name=user.full_name
        ))
        
    return roster


@router.post("/sync", status_code=status.HTTP_200_OK)
async def sync_offline_attendance(
    payload: OfflineSyncPayload,
    teacher_id: int = Depends(require_teacher),
    db: AsyncSession = Depends(get_session)
):
    """Idempotent endpoint for synchronizing offline attendance captures."""
    
    # 1. Idempotency Check (Session Level)
    existing_session_stmt = select(AttendanceSession).where(AttendanceSession.session_uuid == payload.session_uuid)
    existing_session = (await db.exec(existing_session_stmt)).first()
    
    if existing_session:
        return {
            "status": "success", 
            "message": "Session already synchronized.", 
            "session_id": existing_session.id
        }

    # 2. Assignment & Substitute Verification
    offering = await db.get(CourseOffering, payload.course_offering_id)
    if not offering:
        raise HTTPException(status_code=404, detail="Course offering not found.")

    is_authorized = offering.teacher_id == teacher_id
    if not is_authorized:
        sub_stmt = select(SubstituteGrant).where(
            SubstituteGrant.course_offering_id == payload.course_offering_id,
            SubstituteGrant.substitute_teacher_id == teacher_id,
            SubstituteGrant.start_date <= payload.date,
            SubstituteGrant.end_date >= payload.date
        )
        active_sub = (await db.exec(sub_stmt)).first()
        if not active_sub:
            raise HTTPException(
                status_code=403, 
                detail="Not authorized to sync this session. Assignment removed or substitute grant expired."
            )

    # 3. Duplicate Session Check (Same target and date, but different UUID)
    duplicate_check_stmt = select(AttendanceSession).where(
        AttendanceSession.course_offering_id == payload.course_offering_id,
        AttendanceSession.date == payload.date
    )
    duplicate_session = (await db.exec(duplicate_check_stmt)).first()
    
    session_flagged = False
    session_flag_reason = None
    if duplicate_session:
        session_flagged = True
        session_flag_reason = "Duplicate session marked for the same date and class."

    # 4. Fetch current real-time enrollment to detect Stale Lists
    enrolled_stmt = select(StudentSubjectMap.student_id).where(
        StudentSubjectMap.course_offering_id == payload.course_offering_id
    )
    enrolled_students = set((await db.exec(enrolled_stmt)).all())

    # 5. Create Parent AttendanceSession
    new_session = AttendanceSession(
        session_uuid=payload.session_uuid,
        course_offering_id=payload.course_offering_id,
        teacher_id=teacher_id,
        date=payload.date,
        duration_periods=payload.duration_periods,
        mode=payload.mode,
        is_held=payload.is_held,
        reason_not_held=payload.reason_not_held,
        status=SessionStatus.SUBMITTED,
        submitted_at=datetime.utcnow()
    )
    db.add(new_session)
    await db.commit() 
    await db.refresh(new_session)

    # 6. Process Individual Student Records
    server_now = datetime.utcnow()
    db_records = []
    
    for item in payload.records:
        is_flagged = session_flagged
        flag_reason = session_flag_reason

        # Stale List Check
        if item.student_id not in enrolled_students:
            is_flagged = True
            flag_reason = "Student no longer enrolled in this class."
        
        # Clock Skew Check (Flag if device clock is > 24 hours off from server time)
        clock_diff = abs((server_now - item.captured_at).total_seconds())
        if clock_diff > 86400: 
            is_flagged = True
            existing_reason = flag_reason + " | " if flag_reason else ""
            flag_reason = existing_reason + "Device clock heavily skewed outside tolerance."

        db_record = AttendanceRecord(
            record_uuid=item.record_uuid,
            session_id=new_session.id,
            student_id=item.student_id,
            status=item.status,
            captured_at=item.captured_at,
            synced_at=server_now,
            is_flagged=is_flagged,
            flag_reason=flag_reason
        )
        db_records.append(db_record)

    db.add_all(db_records)
    await db.commit()

    return {
        "status": "success",
        "message": f"Successfully synchronized {len(db_records)} records.",
        "session_id": new_session.id
    }