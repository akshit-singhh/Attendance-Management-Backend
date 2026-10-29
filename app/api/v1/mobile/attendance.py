from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from app.services.notification_service import notification_service
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from typing import List, Optional
from datetime import date, datetime, timezone, timedelta
import uuid
from sqlalchemy.exc import IntegrityError

from app.core.database import get_session
from app.api.deps import require_teacher
from app.models.user import User, StudentProfile
from app.models.academic import CourseOffering, Subject, Section, StudentSubjectMap, SubstituteGrant, TimetableEntry, DayOfWeek
from app.models.attendance import (
    AttendanceSession,
    AttendanceRecord,
    SessionMode,
    SessionStatus,
    CorrectionRequest, 
    CorrectionStatus, 
)
from app.schemas.attendance import (
    OfflineSyncPayload,
    StudentRosterResponse,
    CourseScheduleResponse,
    SessionHistoryResponse,
    PendingClassResponse,
    PostponeClassRequest,
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
    current_day = None
    if target_day_str != "ALL":
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
    )
    if current_day is not None:
        statement = statement.where(TimetableEntry.day_of_week == current_day)
    statement = statement.order_by(TimetableEntry.start_time)
    results = (await db.exec(statement)).all()

    schedule = []
    seen = set()
    # Results is a tuple of (CourseOffering, Subject, Section, TimetableEntry)
    for offering, subject, section, timetable_entry in results:
        # Deduplicate identical timeslots if course offerings exist across multiple terms
        key = (timetable_entry.start_time, subject.course_code, section.name)
        if key in seen:
            continue
        seen.add(key)
        schedule.append(CourseScheduleResponse(
            course_offering_id=offering.id,
            subject_name=subject.name,
            subject_code=subject.course_code,
            section_name=section.name,
            start_time=timetable_entry.start_time.strftime("%H:%M:%S"),
            end_time=timetable_entry.end_time.strftime("%H:%M:%S"),
            room=timetable_entry.room,
            credits=subject.credits or 3,
            course_type=offering.course_type or subject.course_type or "LECTURE",
            weekly_hours=offering.weekly_hours,
        ))
        
    return schedule


@router.get("/pending", response_model=List[PendingClassResponse])
async def get_pending_classes(
    teacher_id: int = Depends(require_teacher),
    db: AsyncSession = Depends(get_session)
):
    """
    Fetch all classes conducted within the last 15 days where attendance has not yet been marked.
    Classes older than 15 days expire and are excluded from marking.
    """
    teacher = await db.get(User, teacher_id)
    matched_teacher_ids = [teacher_id]
    if teacher:
        same_name_ids = (await db.exec(select(User.id).where(User.full_name == teacher.full_name))).all()
        matched_teacher_ids = list(set(matched_teacher_ids + list(same_name_ids)))

    # Fetch all timetable entries for this teacher
    stmt = (
        select(CourseOffering, Subject, Section, TimetableEntry)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .join(Section, CourseOffering.section_id == Section.id)
        .join(TimetableEntry, CourseOffering.id == TimetableEntry.course_offering_id)
        .where(CourseOffering.teacher_id.in_(matched_teacher_ids))
        .order_by(TimetableEntry.start_time)
    )
    all_tts = (await db.exec(stmt)).all()

    DAY_OF_WEEK_MAP = {
        0: DayOfWeek.MON,
        1: DayOfWeek.TUE,
        2: DayOfWeek.WED,
        3: DayOfWeek.THU,
        4: DayOfWeek.FRI,
        5: DayOfWeek.SAT,
    }

    now = datetime.now()
    today = now.date()

    # Pre-fetch existing attendance sessions for these offerings over the last 16 days
    min_date = today - timedelta(days=16)
    offering_ids = [co.id for co, _, _, _ in all_tts]
    existing_sessions = set()
    if offering_ids:
        sess_stmt = select(AttendanceSession.course_offering_id, AttendanceSession.date).where(
            AttendanceSession.course_offering_id.in_(offering_ids),
            AttendanceSession.date >= min_date,
            AttendanceSession.status != SessionStatus.DRAFT
        )
        for row in (await db.exec(sess_stmt)).all():
            existing_sessions.add((row[0], row[1]))

    pending = []
    # Check each day in the 15-day grace window (day_offset 0 to 14)
    for day_offset in range(15):
        target_date = today - timedelta(days=day_offset)
        weekday = target_date.weekday()
        if weekday not in DAY_OF_WEEK_MAP:
            continue
        target_dow = DAY_OF_WEEK_MAP[weekday]

        for co, sub, sec, tte in all_tts:
            if tte.day_of_week != target_dow:
                continue

            # If today, only include if class start time has already passed
            if day_offset == 0 and tte.start_time > now.time():
                continue

            # Check if attendance session was already submitted
            if (co.id, target_date) in existing_sessions:
                continue

            days_remaining = 15 - day_offset
            pending.append(PendingClassResponse(
                course_offering_id=co.id,
                subject_name=sub.name,
                subject_code=sub.course_code,
                section_name=sec.name,
                date=target_date.isoformat(),
                formatted_date=target_date.strftime("%a, %d %b"),
                day_name=target_date.strftime("%A"),
                start_time=tte.start_time.strftime("%H:%M:%S"),
                end_time=tte.end_time.strftime("%H:%M:%S"),
                room=tte.room or "TBA",
                days_remaining=days_remaining,
                is_expiring_soon=(days_remaining <= 3),
            ))

    # Sort: newest date to oldest date, then by class start_time
    pending.sort(key=lambda x: (x.date, x.start_time), reverse=True)
    return pending


@router.get("/roster/{course_offering_id}", response_model=List[StudentRosterResponse])
async def get_class_roster(
    course_offering_id: int,
    session_date: Optional[date] = None,
    date: Optional[date] = None,
    teacher_id: int = Depends(require_teacher),
    db: AsyncSession = Depends(get_session)
):
    """Fetch the exact list of students enrolled in this specific course offering, optionally pre-filled with attendance for that date."""
    
    teacher = await db.get(User, teacher_id)
    matched_teacher_ids = [teacher_id]
    if teacher:
        same_name_ids = (await db.exec(select(User.id).where(User.full_name == teacher.full_name))).all()
        matched_teacher_ids = list(set(matched_teacher_ids + list(same_name_ids)))

    offering = await db.get(CourseOffering, course_offering_id)
    if not offering or offering.teacher_id not in matched_teacher_ids:
        raise HTTPException(status_code=403, detail="You do not have access to this roster.")

    target_date = session_date or date
    status_map = {}
    if target_date:
        session_stmt = (
            select(AttendanceSession)
            .where(
                AttendanceSession.course_offering_id == course_offering_id,
                AttendanceSession.date == target_date,
                AttendanceSession.is_held == True
            )
            .order_by(AttendanceSession.id.desc())
        )
        existing_session = (await db.exec(session_stmt)).first()
        if existing_session:
            rec_stmt = select(AttendanceRecord).where(AttendanceRecord.session_id == existing_session.id)
            existing_records = (await db.exec(rec_stmt)).all()
            for r in existing_records:
                status_map[r.student_id] = r.status

    statement = (
        select(User, StudentProfile)
        .join(StudentProfile, User.id == StudentProfile.user_id)
        .join(StudentSubjectMap, User.id == StudentSubjectMap.student_id)
        .where(StudentSubjectMap.course_offering_id == course_offering_id)
        .order_by(StudentProfile.roll_number.asc(), User.full_name.asc())
    )
    
    results = await db.exec(statement)
    
    roster = []
    for user, profile in results:
        roster.append(StudentRosterResponse(
            student_id=user.id,
            roll_number=profile.roll_number,
            full_name=user.full_name,
            status=status_map.get(user.id)
        ))
        
    return roster


@router.post("/sync", status_code=status.HTTP_200_OK)
async def sync_offline_attendance(
    payload: OfflineSyncPayload,
    background_tasks: BackgroundTasks,
    teacher_id: int = Depends(require_teacher),
    db: AsyncSession = Depends(get_session)
):
    """Idempotent endpoint for synchronizing offline attendance captures."""
    
    # 0. 15-Day Attendance Marking Window Enforcement
    days_since_class = (datetime.now().date() - payload.date).days
    if days_since_class > 15:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Attendance window expired: cannot submit attendance for a class conducted {days_since_class} days ago (limit is 15 days)."
        )
    
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

    # 3. Check for existing session on the same date and class (to allow direct edits/updates)
    duplicate_check_stmt = (
        select(AttendanceSession)
        .where(
            AttendanceSession.course_offering_id == payload.course_offering_id,
            AttendanceSession.date == payload.date,
            AttendanceSession.is_held == True
        )
        .order_by(AttendanceSession.id.desc())
    )
    existing_session = (await db.exec(duplicate_check_stmt)).first()

    # 4. Fetch current real-time enrollment to detect Stale Lists
    enrolled_stmt = select(StudentSubjectMap.student_id).where(
        StudentSubjectMap.course_offering_id == payload.course_offering_id
    )
    enrolled_students = set((await db.exec(enrolled_stmt)).all())

    # 5. Create or Update AttendanceSession
    if existing_session:
        target_session = existing_session
        target_session.duration_periods = payload.duration_periods
        target_session.mode = payload.mode
        target_session.is_held = payload.is_held
        target_session.reason_not_held = payload.reason_not_held
        target_session.status = SessionStatus.SUBMITTED
        target_session.submitted_at = datetime.utcnow()
        db.add(target_session)
        await db.commit()
        await db.refresh(target_session)

        # Remove previous records for this session to store the updated marks
        old_records_stmt = select(AttendanceRecord).where(AttendanceRecord.session_id == target_session.id)
        old_records = (await db.exec(old_records_stmt)).all()
        for old_rec in old_records:
            await db.delete(old_rec)
        await db.commit()

        new_session = target_session
    else:
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
        is_flagged = False
        flag_reason = None

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

    # Dispatch student push notifications in background
    try:
        subject = await db.get(Subject, offering.subject_id)
        subject_name = subject.name if subject else "Class"
        subject_code = subject.course_code if subject else ""
        date_str = payload.date.strftime("%d %b %Y")

        student_ids = [item.student_id for item in payload.records]
        if student_ids:
            student_query = (
                select(StudentProfile.user_id, StudentProfile.roll_number, User.full_name)
                .join(User, StudentProfile.user_id == User.id)
                .where(StudentProfile.user_id.in_(student_ids))
            )
            student_rows = (await db.exec(student_query)).all()
            student_info_map = {row[0]: {"roll": row[1], "name": row[2]} for row in student_rows}

            alerts = []
            for item in payload.records:
                info = student_info_map.get(item.student_id)
                if info and info.get("roll"):
                    alerts.append({
                        "roll_number": info["roll"],
                        "student_name": info["name"],
                        "subject_name": subject_name,
                        "subject_code": subject_code,
                        "status": item.status,
                        "date_str": date_str,
                    })

            if alerts:
                background_tasks.add_task(
                    notification_service.batch_send_attendance_alerts,
                    alerts
                )
    except Exception as exc:
        pass

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


@router.post("/postpone", status_code=status.HTTP_200_OK)
async def postpone_class(
    payload: PostponeClassRequest,
    background_tasks: BackgroundTasks,
    teacher_id: int = Depends(require_teacher),
    db: AsyncSession = Depends(get_session)
):
    """Postpone or mark a class not held for a specific date, notifying enrolled students."""
    # 1. 15-day limit check for past dates
    days_diff = (datetime.now().date() - payload.date).days
    if days_diff > 15:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Attendance window expired: cannot postpone a class from {days_diff} days ago (limit is 15 days)."
        )

    # 2. Authorization check
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
            raise HTTPException(status_code=403, detail="Not authorized to postpone this class.")

    # 3. Check if attendance was already submitted
    existing_stmt = select(AttendanceSession).where(
        AttendanceSession.course_offering_id == payload.course_offering_id,
        AttendanceSession.date == payload.date
    )
    existing_session = (await db.exec(existing_stmt)).first()
    if existing_session and existing_session.is_held and existing_session.status == SessionStatus.SUBMITTED:
        raise HTTPException(status_code=400, detail="Attendance has already been marked for this session. Use correction requests to modify.")

    # 4. Upsert AttendanceSession as cancelled / not held
    reason = payload.reason.strip() if payload.reason else "Faculty on Leave"
    if existing_session:
        existing_session.is_held = False
        existing_session.reason_not_held = reason
        existing_session.status = SessionStatus.CANCELLED
        existing_session.submitted_at = datetime.utcnow()
        db.add(existing_session)
    else:
        new_session = AttendanceSession(
            session_uuid=str(uuid.uuid4()),
            course_offering_id=payload.course_offering_id,
            teacher_id=teacher_id,
            date=payload.date,
            duration_periods=1,
            mode=SessionMode.MANUAL,
            is_held=False,
            reason_not_held=reason,
            status=SessionStatus.CANCELLED,
            submitted_at=datetime.utcnow()
        )
        db.add(new_session)

    await db.commit()

    # 5. Notify enrolled students in background
    try:
        subject = await db.get(Subject, offering.subject_id)
        subject_name = subject.name if subject else "Class"
        subject_code = subject.course_code if subject else ""
        date_str = payload.date.strftime("%d %b %Y")

        student_query = (
            select(StudentProfile.roll_number, User.full_name)
            .join(User, StudentProfile.user_id == User.id)
            .join(StudentSubjectMap, User.id == StudentSubjectMap.student_id)
            .where(StudentSubjectMap.course_offering_id == payload.course_offering_id)
        )
        student_rows = (await db.exec(student_query)).all()

        alerts = [
            {
                "roll_number": row[0],
                "student_name": row[1],
                "subject_name": subject_name,
                "subject_code": subject_code,
                "date_str": date_str,
                "reason": reason
            }
            for row in student_rows if row[0]
        ]

        if alerts:
            background_tasks.add_task(
                notification_service.batch_send_class_postponed_alerts,
                alerts
            )
    except Exception as exc:
        pass

    return {
        "status": "success",
        "message": f"Class on {payload.date.strftime('%a, %d %b')} has been postponed."
    }
