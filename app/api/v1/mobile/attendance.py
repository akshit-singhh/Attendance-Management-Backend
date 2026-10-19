from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from typing import List, Optional
from datetime import date, datetime, timezone
from sqlalchemy.exc import IntegrityError

from app.core.database import get_session
from app.api.deps import require_teacher
from app.models.user import User, StudentProfile
from app.models.academic import CourseOffering, Subject, Section, StudentSubjectMap, SubstituteGrant, TimetableEntry, DayOfWeek
from app.models.attendance import (
    AttendanceSession,
    AttendanceRecord,
    SessionStatus,
    CorrectionRequest, 
    CorrectionStatus, 
)
from app.schemas.attendance import (
    OfflineSyncPayload,
    StudentRosterResponse,
    CourseScheduleResponse,
    SessionHistoryResponse,
)
from app.schemas.corrections import CorrectionRequestCreate

router = APIRouter()

@router.get("/schedule", response_model=List[CourseScheduleResponse])
async def get_daily_schedule(
    term_id: Optional[int] = None, 
    day: Optional[str] = None,
    teacher_id: int = Depends(require_teacher),
    db: AsyncSession = Depends(get_session)
):
    """Fetch all classes assigned to this specific teacher for the current term and day."""
    
    # Get the current or requested day of the week to filter timetable entries
    target_day_str = (day or datetime.now().strftime("%a")).upper()
    try:
        current_day = DayOfWeek[target_day_str]
    except KeyError:
        current_day = DayOfWeek.MON

    teacher = await db.get(User, teacher_id)
    matched_teacher_ids = [teacher_id]
    if teacher:
        same_name_ids = (await db.exec(select(User.id).where(User.full_name == teacher.full_name))).all()
        matched_teacher_ids = list(set(matched_teacher_ids + list(same_name_ids)))

    statement = (
        select(CourseOffering, Subject, Section, TimetableEntry)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .join(Section, CourseOffering.section_id == Section.id)
        .join(TimetableEntry, CourseOffering.id == TimetableEntry.course_offering_id)
        .where(CourseOffering.teacher_id.in_(matched_teacher_ids))
        .where(TimetableEntry.day_of_week == current_day)
    )
    if term_id:
        statement = statement.where(CourseOffering.term_id == term_id)
    
    results = (await db.exec(statement)).all()

    # Fallback: if term_id was provided but returned empty or incomplete, query without term_id constraint
    if not results and term_id:
        fallback_stmt = (
            select(CourseOffering, Subject, Section, TimetableEntry)
            .join(Subject, CourseOffering.subject_id == Subject.id)
            .join(Section, CourseOffering.section_id == Section.id)
            .join(TimetableEntry, CourseOffering.id == TimetableEntry.course_offering_id)
            .where(CourseOffering.teacher_id.in_(matched_teacher_ids))
            .where(TimetableEntry.day_of_week == current_day)
        )
        results = (await db.exec(fallback_stmt)).all()
    
    schedule = []
    # Results is now a tuple of 4 objects due to the 4 selected tables
    for offering, subject, section, timetable_entry in results:
        schedule.append(CourseScheduleResponse(
            course_offering_id=offering.id,
            subject_name=subject.name,
            subject_code=subject.course_code,
            section_name=section.name,
            # Serialize the time objects to strings as defined by the Pydantic model
            start_time=timetable_entry.start_time.strftime("%H:%M:%S"),
            end_time=timetable_entry.end_time.strftime("%H:%M:%S"),
            room=timetable_entry.room,
            credits=subject.credits or 3,
            course_type=offering.course_type or subject.course_type or "LECTURE",
            weekly_hours=offering.weekly_hours,
        ))
        
    return schedule


@router.get("/roster/{course_offering_id}", response_model=List[StudentRosterResponse])
async def get_class_roster(
    course_offering_id: int,
    teacher_id: int = Depends(require_teacher),
    db: AsyncSession = Depends(get_session)
):
    """Fetch the exact list of students enrolled in this specific course offering."""
    
    teacher = await db.get(User, teacher_id)
    matched_teacher_ids = [teacher_id]
    if teacher:
        same_name_ids = (await db.exec(select(User.id).where(User.full_name == teacher.full_name))).all()
        matched_teacher_ids = list(set(matched_teacher_ids + list(same_name_ids)))

    offering = await db.get(CourseOffering, course_offering_id)
    if not offering or offering.teacher_id not in matched_teacher_ids:
        raise HTTPException(status_code=403, detail="You do not have access to this roster.")

    statement = (
        select(User, StudentProfile)
        .join(StudentProfile, User.id == StudentProfile.user_id)
        .join(StudentSubjectMap, User.id == StudentSubjectMap.student_id)
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

    teacher = await db.get(User, teacher_id)
    matched_teacher_ids = [teacher_id]
    if teacher:
        same_name_ids = (await db.exec(select(User.id).where(User.full_name == teacher.full_name))).all()
        matched_teacher_ids = list(set(matched_teacher_ids + list(same_name_ids)))

    is_authorized = offering.teacher_id in matched_teacher_ids
    if not is_authorized:
        sub_stmt = select(SubstituteGrant).where(
            SubstituteGrant.course_offering_id == payload.course_offering_id,
            SubstituteGrant.substitute_teacher_id.in_(matched_teacher_ids),
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
    # Use naive UTC time to match PostgreSQL's default timestamp expectation
    server_now = datetime.now(timezone.utc).replace(tzinfo=None)
    db_records = []
    
    for item in payload.records:
        is_flagged = session_flagged
        flag_reason = session_flag_reason

        # Stale List Check
        if item.student_id not in enrolled_students:
            is_flagged = True
            flag_reason = "Student no longer enrolled in this class."
        
        # Normalize the device time to a naive UTC datetime to match DB schema
        if item.captured_at.tzinfo:
            captured_at_utc = item.captured_at.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            captured_at_utc = item.captured_at

        # Clock Skew Check (Flag if device clock is > 24 hours off from server time)
        clock_diff = abs((server_now - captured_at_utc).total_seconds())
        if clock_diff > 86400: 
            is_flagged = True
            existing_reason = flag_reason + " | " if flag_reason else ""
            flag_reason = existing_reason + "Device clock heavily skewed outside tolerance."

        db_record = AttendanceRecord(
            record_uuid=item.record_uuid,
            session_id=new_session.id,
            student_id=item.student_id,
            course_offering_id=payload.course_offering_id,
            marked_by_id=teacher_id,
            date=payload.date,
            status=item.status,
            captured_at=captured_at_utc,
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
    
@router.get("/history", response_model=List[SessionHistoryResponse])
async def get_teacher_history(
    teacher_id: int = Depends(require_teacher),
    db: AsyncSession = Depends(get_session)
):
    """Fetch a distinct list of past classes this teacher has already submitted."""
    
    # Query AttendanceSession instead of AttendanceRecord to access course and teacher data
    statement = (
        select(
            AttendanceSession.course_offering_id,
            AttendanceSession.date,
            Subject.name.label("subject_name"),
            Section.name.label("section_name")
        )
        .join(CourseOffering, AttendanceSession.course_offering_id == CourseOffering.id)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .join(Section, CourseOffering.section_id == Section.id)
        .where(AttendanceSession.teacher_id == teacher_id)
        .distinct()
        .order_by(AttendanceSession.date.desc())
    )
    
    result = await db.exec(statement)
    
    history = []
    for row in result:
        history.append(SessionHistoryResponse(
            course_offering_id=row.course_offering_id,
            date=row.date,
            subject_name=row.subject_name,
            section_name=row.section_name
        ))
        
    return history

@router.post("/corrections", status_code=status.HTTP_201_CREATED)
async def submit_correction(
    payload: CorrectionRequestCreate,
    teacher_id: int = Depends(require_teacher),
    db: AsyncSession = Depends(get_session)
):
    """Submit a request to modify an already finalized attendance record."""
    
    # 1. Verify the original record exists
    record = await db.get(AttendanceRecord, payload.attendance_record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Attendance record not found.")
        
    # 2. Check the parent session to verify this teacher marked it
    session = await db.get(AttendanceSession, record.session_id)
    if not session or session.teacher_id != teacher_id:
        raise HTTPException(status_code=403, detail="You can only submit corrections for records you marked.")
        
    if record.status == payload.suggested_status:
        raise HTTPException(status_code=400, detail="Suggested status is identical to the current status.")

    # 3. Prevent duplicate pending requests for the same record
    existing_req = await db.exec(
        select(CorrectionRequest).where(
            (CorrectionRequest.attendance_record_id == payload.attendance_record_id) &
            (CorrectionRequest.status == CorrectionStatus.PENDING)
        )
    )
    if existing_req.first():
        raise HTTPException(status_code=400, detail="A pending correction request already exists for this record.")

    # 4. Create the request
    correction = CorrectionRequest(
        attendance_record_id=payload.attendance_record_id,
        suggested_status=payload.suggested_status,
        reason=payload.reason,
        submitted_by_id=teacher_id
    )
    
    db.add(correction)
    await db.commit()
    
    return {"status": "success", "message": "Correction request submitted for HOD approval."}