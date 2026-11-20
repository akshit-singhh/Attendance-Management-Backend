import logging
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Query
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, func, or_
from typing import List, Optional
from datetime import date, datetime, timezone, timedelta
import math
import uuid
from sqlalchemy.exc import IntegrityError

from app.core.database import get_session
from app.api.deps import require_teacher
from app.models.user import User, StudentProfile
from app.models.academic import CourseOffering, Subject, Section, StudentSubjectMap, SubstituteGrant, TimetableEntry, DayOfWeek
from app.models.attendance import (
    AttendanceSession,
    AttendanceRecord,
    AttendanceStatus,
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
    StudentAttendanceSummaryResponse,
)
from app.schemas.corrections import CorrectionRequestCreate
from app.api.v1.notifications import notify_users

logger = logging.getLogger("attendance.notifications")

router = APIRouter()


def merge_consecutive_schedule_slots(items: List[CourseScheduleResponse]) -> List[CourseScheduleResponse]:
    """
    Merges contiguous slots that belong to the same course offering and section.
    For example: a 2-hour lab from 11:30-12:30 and 12:30-13:30 merges into
    11:30-13:30 with duration_periods = 2.
    """
    if not items:
        return []

    # Sort primarily by course_offering_id and start_time to easily detect consecutive blocks
    sorted_items = sorted(items, key=lambda x: (x.course_offering_id, x.start_time))
    merged: List[CourseScheduleResponse] = []

    for item in sorted_items:
        if (
            merged
            and merged[-1].course_offering_id == item.course_offering_id
            and (merged[-1].batch_label or "") == (item.batch_label or "")
            and merged[-1].end_time == item.start_time
        ):
            # Contiguous timeslot of the same course offering: extend end_time and increment duration_periods
            merged[-1].end_time = item.end_time
            merged[-1].duration_periods = (merged[-1].duration_periods or 1) + (item.duration_periods or 1)
            if not merged[-1].room and item.room:
                merged[-1].room = item.room
        else:
            merged.append(item)

    # Sort final merged schedule chronologically by start_time
    merged.sort(key=lambda x: x.start_time)
    return merged


def merge_consecutive_pending_classes(items: List[PendingClassResponse]) -> List[PendingClassResponse]:
    """
    Merges contiguous slots on the same date for the same course offering into a single pending session.
    """
    if not items:
        return []

    # Group and sort by date, course_offering_id, start_time
    sorted_items = sorted(items, key=lambda x: (x.date, x.course_offering_id, x.start_time))
    merged: List[PendingClassResponse] = []

    for item in sorted_items:
        if (
            merged
            and merged[-1].date == item.date
            and merged[-1].course_offering_id == item.course_offering_id
            and (merged[-1].batch_label or "") == (item.batch_label or "")
            and merged[-1].end_time == item.start_time
        ):
            merged[-1].end_time = item.end_time
            merged[-1].duration_periods = (merged[-1].duration_periods or 1) + (item.duration_periods or 1)
            if not merged[-1].room and item.room:
                merged[-1].room = item.room
        else:
            merged.append(item)

    # Sort newest date first, then by start_time
    merged.sort(key=lambda x: (x.date, x.start_time), reverse=True)
    return merged


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
            duration_periods=1,
            batch_label=timetable_entry.batch_label,
            day_of_week=timetable_entry.day_of_week.value if hasattr(timetable_entry.day_of_week, "value") else str(timetable_entry.day_of_week),
        ))
        
    return merge_consecutive_schedule_slots(schedule)


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

    # Pre-fetch existing attendance sessions for these offerings over past 60 days
    min_date = today - timedelta(days=60)
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
    # Check past 60 days for unmarked past sessions
    for day_offset in range(60):
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
                days_remaining=0,
                is_expiring_soon=False,
                duration_periods=1,
                batch_label=tte.batch_label,
            ))

    return merge_consecutive_pending_classes(pending)


@router.get("/roster/{course_offering_id}", response_model=List[StudentRosterResponse])
async def get_class_roster(
    course_offering_id: int,
    session_date: Optional[date] = None,
    date: Optional[date] = None,
    batch_label: Optional[str] = None,
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
        sess_conds = [
            AttendanceSession.course_offering_id == course_offering_id,
            AttendanceSession.date == target_date,
            AttendanceSession.is_held == True
        ]
        if batch_label:
            norm = batch_label.replace("-", "").strip().upper()
            sess_conds.append(
                or_(
                    AttendanceSession.batch_label == batch_label,
                    func.replace(func.coalesce(AttendanceSession.batch_label, ""), "-", "") == norm
                )
            )
        session_stmt = (
            select(AttendanceSession)
            .where(*sess_conds)
            .order_by(AttendanceSession.id.desc())
        )
        existing_sessions = (await db.exec(session_stmt)).all()
        for sess in existing_sessions:
            rec_stmt = select(AttendanceRecord).where(AttendanceRecord.session_id == sess.id)
            existing_records = (await db.exec(rec_stmt)).all()
            for r in existing_records:
                status_map[r.student_id] = (r.status, r.id)

    statement = (
        select(User, StudentProfile, StudentSubjectMap)
        .join(StudentProfile, User.id == StudentProfile.user_id)
        .join(StudentSubjectMap, User.id == StudentSubjectMap.student_id)
        .where(StudentSubjectMap.course_offering_id == course_offering_id)
        .order_by(StudentProfile.roll_number.asc(), User.full_name.asc())
    )
    
    results = (await db.exec(statement)).all()
    total_students = len(results)
    half = (total_students + 1) // 2

    roster = []
    has_unassigned = False
    for idx, (user, profile, ssm) in enumerate(results):
        rec_data = status_map.get(user.id)
        stat_val = rec_data[0] if isinstance(rec_data, tuple) else rec_data
        rec_id = rec_data[1] if isinstance(rec_data, tuple) else None

        group = ssm.lab_group
        if not group:
            group = "G1" if idx < half else "G2"
            ssm.lab_group = group
            db.add(ssm)
            has_unassigned = True

        roster.append(StudentRosterResponse(
            student_id=user.id,
            roll_number=profile.roll_number,
            full_name=user.full_name,
            status=stat_val,
            record_id=rec_id,
            lab_group=group
        ))
        
    if has_unassigned:
        await db.commit()

    return roster


@router.post("/sync", status_code=status.HTTP_200_OK)
async def sync_offline_attendance(
    payload: OfflineSyncPayload,
    background_tasks: BackgroundTasks,
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

    # 3. Check for existing session on the same date, class, and batch (to allow direct edits/updates)
    dup_conditions = [
        AttendanceSession.course_offering_id == payload.course_offering_id,
        AttendanceSession.date == payload.date,
        AttendanceSession.is_held == True
    ]
    if payload.batch_label:
        norm = payload.batch_label.replace("-", "").strip().upper()
        dup_conditions.append(
            or_(
                AttendanceSession.batch_label == payload.batch_label,
                func.replace(func.coalesce(AttendanceSession.batch_label, ""), "-", "") == norm
            )
        )

    duplicate_check_stmt = (
        select(AttendanceSession)
        .where(*dup_conditions)
        .order_by(AttendanceSession.id.desc())
    )
    existing_session = (await db.exec(duplicate_check_stmt)).first()

    # 4. Fetch current real-time enrollment to detect Stale Lists
    enrolled_stmt = select(StudentSubjectMap.student_id).where(
        StudentSubjectMap.course_offering_id == payload.course_offering_id
    )
    enrolled_students = set((await db.exec(enrolled_stmt)).all())

    # 5. Create or Update AttendanceSession
    old_status_map = {}
    is_edit_session = existing_session is not None
    if existing_session:
        target_session = existing_session
        target_session.duration_periods = payload.duration_periods
        target_session.mode = payload.mode
        target_session.is_held = payload.is_held
        target_session.reason_not_held = payload.reason_not_held
        target_session.batch_label = payload.batch_label
        target_session.status = SessionStatus.SUBMITTED
        target_session.submitted_at = datetime.utcnow()
        db.add(target_session)
        await db.commit()
        await db.refresh(target_session)

        # Remove previous records for this session to store the updated marks
        old_records_stmt = select(AttendanceRecord).where(AttendanceRecord.session_id == target_session.id)
        old_records = (await db.exec(old_records_stmt)).all()
        for old_rec in old_records:
            st = old_rec.status.value.upper() if hasattr(old_rec.status, "value") else str(old_rec.status).upper()
            old_status_map[old_rec.student_id] = st
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
            batch_label=payload.batch_label,
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
    created_session_id = new_session.id

    # 7. Smart Weekly Auto-Fulfillment for Combined Labs (Approach B)
    if payload.batch_label in ("ALL", "COMBINED") or len(payload.records) >= len(enrolled_students):
        try:
            week_start = payload.date - timedelta(days=payload.date.weekday())
            other_tt_stmt = select(TimetableEntry).where(
                TimetableEntry.course_offering_id == payload.course_offering_id
            )
            other_entries = (await db.exec(other_tt_stmt)).all()

            DOW_OFFSET = {
                DayOfWeek.MON: 0,
                DayOfWeek.TUE: 1,
                DayOfWeek.WED: 2,
                DayOfWeek.THU: 3,
                DayOfWeek.FRI: 4,
                DayOfWeek.SAT: 5,
            }

            for o_tte in other_entries:
                slot_offset = DOW_OFFSET.get(o_tte.day_of_week)
                if slot_offset is None:
                    continue
                slot_date = week_start + timedelta(days=slot_offset)
                if slot_date != payload.date:
                    exist_other_stmt = select(AttendanceSession).where(
                        AttendanceSession.course_offering_id == payload.course_offering_id,
                        AttendanceSession.date == slot_date
                    )
                    exist_other = (await db.exec(exist_other_stmt)).first()
                    if not exist_other:
                        combined_placeholder = AttendanceSession(
                            session_uuid=str(uuid.uuid4()),
                            course_offering_id=payload.course_offering_id,
                            teacher_id=teacher_id,
                            date=slot_date,
                            duration_periods=payload.duration_periods,
                            mode=SessionMode.MANUAL,
                            is_held=False,
                            reason_not_held=f"Combined Lab conducted on {payload.date.strftime('%a, %d %b')}",
                            batch_label="COMBINED",
                            status=SessionStatus.SUBMITTED,
                            submitted_at=datetime.utcnow()
                        )
                        db.add(combined_placeholder)
                        await db.commit()
                        logger.info(f"Auto-fulfilled slot on {slot_date} for combined lab on {payload.date}")
        except Exception as e:
            logger.warning(f"Error in weekly lab auto-fulfillment: {e}")

    # Dispatch student push notifications in background
    try:
        subject = await db.get(Subject, offering.subject_id)
        subject_name = subject.name if subject else "Class"
        subject_code = subject.course_code if subject else ""
        date_str = payload.date.strftime("%d %b %Y")

        for item in payload.records:
            st = item.status.value.upper() if hasattr(item.status, "value") else str(item.status).upper()

            if is_edit_session:
                old_st = old_status_map.get(item.student_id)
                # Only notify if status actually changed during this edit/correction
                if old_st and old_st != st:
                    if "PRESENT" in st or st in ("PRESENT", "P"):
                        title = f"Attendance Corrected: Present ({subject_code})" if subject_code else "Attendance Corrected: Present"
                        message = f"Your attendance for {subject_name} on {date_str} was corrected to Present."
                        prio = "HIGH"
                    elif "ABSENT" in st or st in ("ABSENT", "A"):
                        title = f"Attendance Updated: Absent ({subject_code})" if subject_code else "Attendance Updated: Absent"
                        message = f"Your attendance for {subject_name} on {date_str} was updated to Absent."
                        prio = "HIGH"
                    else:
                        title = f"Attendance Updated ({subject_code})" if subject_code else "Attendance Updated"
                        message = f"Your attendance for {subject_name} on {date_str} was updated to on Leave."
                        prio = "NORMAL"

                    await notify_users(
                        db=db,
                        background_tasks=background_tasks,
                        sender_id=teacher_id,
                        user_ids=[item.student_id],
                        title=title,
                        message=message,
                        notification_type="ATTENDANCE_ALERT",
                        priority=prio,
                        section_id=offering.section_id
                    )
            else:
                # Regular session or pending class marked for the first time
                if "ABSENT" in st or st in ("ABSENT", "A"):
                    title = f"Attendance: Absent ({subject_code})" if subject_code else "Attendance: Absent"
                    message = f"You were marked Absent for {subject_name} on {date_str}."
                    await notify_users(
                        db=db,
                        background_tasks=background_tasks,
                        sender_id=teacher_id,
                        user_ids=[item.student_id],
                        title=title,
                        message=message,
                        notification_type="ATTENDANCE_ALERT",
                        priority="HIGH",
                        section_id=offering.section_id
                    )
                elif "LEAVE" in st or st in ("LEAVE", "L"):
                    title = f"Attendance: on Leave ({subject_code})" if subject_code else "Attendance: on Leave"
                    message = f"You were marked on Leave for {subject_name} on {date_str}."
                    await notify_users(
                        db=db,
                        background_tasks=background_tasks,
                        sender_id=teacher_id,
                        user_ids=[item.student_id],
                        title=title,
                        message=message,
                        notification_type="ATTENDANCE_ALERT",
                        priority="NORMAL",
                        section_id=offering.section_id
                    )
                elif "PRESENT" in st or st in ("PRESENT", "P"):
                    title = f"Attendance: Present ({subject_code})" if subject_code else "Attendance: Present"
                    message = f"You were marked Present for {subject_name} on {date_str}."
                    await notify_users(
                        db=db,
                        background_tasks=background_tasks,
                        sender_id=teacher_id,
                        user_ids=[item.student_id],
                        title=title,
                        message=message,
                        notification_type="ATTENDANCE_ALERT",
                        priority="LOW",
                        section_id=offering.section_id
                    )
    except Exception as exc:
        logger.exception("Failed to dispatch attendance notification")

    return {
        "status": "success",
        "message": f"Successfully synchronized {len(db_records)} records.",
        "session_id": created_session_id
    }
    
@router.get("/history", response_model=List[SessionHistoryResponse])
async def get_teacher_history(
    teacher_id: int = Depends(require_teacher),
    db: AsyncSession = Depends(get_session)
):
    """Fetch past classes this teacher has conducted with aggregated turnout stats."""
    from sqlalchemy import case

    teacher = await db.get(User, teacher_id)
    matched_teacher_ids = [teacher_id]
    if teacher:
        same_name_ids = (await db.exec(select(User.id).where(User.full_name == teacher.full_name))).all()
        matched_teacher_ids = list(set(matched_teacher_ids + list(same_name_ids)))

    statement = (
        select(
            AttendanceSession.id.label("session_id"),
            AttendanceSession.course_offering_id,
            AttendanceSession.date,
            AttendanceSession.batch_label,
            Subject.name.label("subject_name"),
            Section.name.label("section_name"),
            func.count(AttendanceRecord.id).label("total_students"),
            func.count(case((AttendanceRecord.status == AttendanceStatus.PRESENT, 1))).label("present_count"),
            func.count(case((AttendanceRecord.status == AttendanceStatus.ABSENT, 1))).label("absent_count"),
        )
        .join(CourseOffering, AttendanceSession.course_offering_id == CourseOffering.id)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .join(Section, CourseOffering.section_id == Section.id)
        .outerjoin(AttendanceRecord, AttendanceSession.id == AttendanceRecord.session_id)
        .where(
            AttendanceSession.teacher_id.in_(matched_teacher_ids),
            AttendanceSession.is_held == True
        )
        .group_by(
            AttendanceSession.id,
            AttendanceSession.course_offering_id,
            AttendanceSession.date,
            AttendanceSession.batch_label,
            Subject.name,
            Section.name
        )
        .having(func.count(AttendanceRecord.id) > 0)
        .order_by(AttendanceSession.date.desc(), AttendanceSession.id.desc())
    )
    
    result = await db.exec(statement)
    
    history = []
    for row in result:
        tot = row.total_students or 0
        pres = row.present_count or 0
        absen = row.absent_count or 0
        pct = round(pres / tot * 100.0, 1) if tot > 0 else 0.0
        history.append(SessionHistoryResponse(
            session_id=row.session_id,
            course_offering_id=row.course_offering_id,
            date=row.date,
            subject_name=row.subject_name,
            section_name=row.section_name,
            batch_label=row.batch_label,
            total_students=tot,
            present_count=pres,
            absent_count=absen,
            attendance_percentage=pct
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
    # 1. Authorization check
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
            duration_periods=getattr(payload, "duration_periods", 1) or 1,
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
            select(StudentSubjectMap.student_id)
            .where(StudentSubjectMap.course_offering_id == payload.course_offering_id)
        )
        enrolled_student_ids = (await db.exec(student_query)).all()

        if enrolled_student_ids:
            title = f"Class Postponed: {subject_code}" if subject_code else "Class Postponed"
            message = f"Your {subject_name} class on {date_str} has been postponed. Reason: {reason}."
            await notify_users(
                db=db,
                background_tasks=background_tasks,
                sender_id=teacher_id,
                user_ids=list(enrolled_student_ids),
                title=title,
                message=message,
                notification_type="ACADEMIC_ORDER",
                priority="NORMAL",
                section_id=offering.section_id
            )
    except Exception as exc:
        logger.exception("Failed to dispatch postponement notification")

    return {
        "status": "success",
        "message": f"Class on {payload.date.strftime('%a, %d %b')} has been postponed."
    }


@router.get("/course/{course_offering_id}/students-summary", response_model=List[StudentAttendanceSummaryResponse])
async def get_course_students_summary(
    course_offering_id: int,
    threshold: Optional[float] = None,
    teacher_id: int = Depends(require_teacher),
    db: AsyncSession = Depends(get_session)
):
    """
    Fetch aggregated student attendance percentages for a specific course offering.
    Optionally filters students whose attendance percentage is strictly below `threshold` (e.g. 75.0, 50.0).
    """
    teacher = await db.get(User, teacher_id)
    matched_teacher_ids = [teacher_id]
    if teacher:
        same_name_ids = (await db.exec(select(User.id).where(User.full_name == teacher.full_name))).all()
        matched_teacher_ids = list(set(matched_teacher_ids + list(same_name_ids)))

    offering = await db.get(CourseOffering, course_offering_id)
    if not offering or offering.teacher_id not in matched_teacher_ids:
        raise HTTPException(status_code=403, detail="You do not have access to this course offering.")

    # 1. Total held sessions for this course offering (weighted by duration_periods)
    held_sessions_query = (
        select(AttendanceSession.id, AttendanceSession.duration_periods)
        .where(
            AttendanceSession.course_offering_id == course_offering_id,
            AttendanceSession.is_held == True,
            AttendanceSession.status == SessionStatus.SUBMITTED
        )
    )
    held_sessions = (await db.exec(held_sessions_query)).all()
    session_durations = {row[0]: (row[1] or 1) for row in held_sessions}
    total_held = sum(session_durations.values())

    # 2. Query all enrolled students in this course offering
    students_query = (
        select(User, StudentProfile)
        .join(StudentProfile, User.id == StudentProfile.user_id)
        .join(StudentSubjectMap, User.id == StudentSubjectMap.student_id)
        .where(StudentSubjectMap.course_offering_id == course_offering_id)
        .order_by(StudentProfile.roll_number.asc(), User.full_name.asc())
    )
    enrolled_students = (await db.exec(students_query)).all()

    if not enrolled_students:
        return []

    # 3. If sessions were held, count attended per student
    attendance_map = {}
    if total_held > 0 and session_durations:
        records_query = (
            select(AttendanceRecord.student_id, AttendanceRecord.session_id)
            .where(
                AttendanceRecord.session_id.in_(list(session_durations.keys())),
                AttendanceRecord.status == AttendanceStatus.PRESENT
            )
        )
        records_res = (await db.exec(records_query)).all()
        for s_id, sess_id in records_res:
            dur = session_durations.get(sess_id, 1)
            attendance_map[s_id] = attendance_map.get(s_id, 0) + dur

    summaries = []
    for user, profile in enrolled_students:
        attended = attendance_map.get(user.id, 0)
        pct = round((attended / total_held * 100.0), 1) if total_held > 0 else 100.0

        needed_75 = 0
        if total_held > 0 and pct < 75.0:
            raw_needed = (0.75 * total_held - attended) / 0.25
            needed_75 = max(0, int(math.ceil(raw_needed)))

        if threshold is not None and pct >= threshold:
            continue

        summaries.append(StudentAttendanceSummaryResponse(
            student_id=user.id,
            roll_number=profile.roll_number,
            full_name=user.full_name,
            total_held=total_held,
            total_attended=attended,
            attendance_percentage=pct,
            classes_needed_for_75=needed_75
        ))

    # Sort: lowest attendance percentage first (most at-risk first), then roll number
    summaries.sort(key=lambda s: (s.attendance_percentage, s.roll_number))
    return summaries


class SplitLabGroupsRequest(BaseModel):
    g1_count: Optional[int] = None
    custom_g1_student_ids: Optional[List[int]] = None


@router.put("/course/{course_offering_id}/student/{student_id}/lab-group")
async def update_student_lab_group(
    course_offering_id: int,
    student_id: int,
    lab_group: str = Query(...),
    teacher_id: int = Depends(require_teacher),
    db: AsyncSession = Depends(get_session)
):
    """Assign or switch a student's lab group (e.g. G1 or G2)."""
    clean_group = lab_group.strip().upper()
    stmt = select(StudentSubjectMap).where(
        StudentSubjectMap.course_offering_id == course_offering_id,
        StudentSubjectMap.student_id == student_id
    )
    ssm = (await db.exec(stmt)).first()
    if not ssm:
        raise HTTPException(status_code=404, detail="Student is not enrolled in this course offering.")
    ssm.lab_group = clean_group
    db.add(ssm)
    await db.commit()
    return {"status": "success", "student_id": student_id, "lab_group": clean_group}


@router.put("/course/{course_offering_id}/split-groups")
async def split_course_lab_groups(
    course_offering_id: int,
    payload: SplitLabGroupsRequest,
    teacher_id: int = Depends(require_teacher),
    db: AsyncSession = Depends(get_session)
):
    """Quickly partition students into G1 and G2 for this lab course offering."""
    stmt = (
        select(StudentSubjectMap)
        .join(StudentProfile, StudentSubjectMap.student_id == StudentProfile.user_id)
        .where(StudentSubjectMap.course_offering_id == course_offering_id)
        .order_by(StudentProfile.roll_number.asc())
    )
    ssms = (await db.exec(stmt)).all()
    if not ssms:
        raise HTTPException(status_code=404, detail="No enrolled students found.")

    g1_ids = set(payload.custom_g1_student_ids or [])
    g1_cutoff = payload.g1_count if payload.g1_count is not None else (len(ssms) + 1) // 2

    for idx, ssm in enumerate(ssms):
        if g1_ids:
            ssm.lab_group = "G1" if ssm.student_id in g1_ids else "G2"
        else:
            ssm.lab_group = "G1" if idx < g1_cutoff else "G2"
        db.add(ssm)

    await db.commit()
    return {"status": "success", "total_students": len(ssms), "g1_count": g1_cutoff}
