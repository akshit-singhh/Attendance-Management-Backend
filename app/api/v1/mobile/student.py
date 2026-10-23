from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, func
from sqlalchemy import case
from typing import List, Optional, Dict
from pydantic import BaseModel

from app.core.database import get_session
from app.api.deps import require_student
from app.models.academic import CourseOffering, Subject, StudentSubjectMap, AcademicTerm, Batch
from app.models.attendance import AttendanceRecord, AttendanceSession, SessionStatus, AttendanceStatus
from app.models.user import User, StudentProfile

router = APIRouter()

# --- REQUEST / RESPONSE SCHEMAS (imported from app.schemas.student) ---
from app.schemas.student import (
    SubjectAttendanceHistory,
    StudentDashboardResponse,
)

# --- ENDPOINTS ---

@router.get("/dashboard", response_model=StudentDashboardResponse, tags=["Mobile Student API"])
async def get_student_dashboard(
    term_id: Optional[int] = Query(default=None),
    semester_id: Optional[int] = Query(default=None),
    student_id: int = Depends(require_student),
    db: AsyncSession = Depends(get_session)
):
    """Fetches the aggregated attendance breakdown applying the v0.2 calculation rules."""
    target_id = semester_id or term_id
    if not target_id:
        active = (await db.exec(select(AcademicTerm).where(AcademicTerm.is_active == True))).first()
        target_id = active.id if active else 1

    term = await db.get(AcademicTerm, target_id)
    if not term:
        raise HTTPException(status_code=404, detail="Semester not found")

    # Advanced SQL Aggregation: Enforce duration weighting and exclude "class not held" sessions
    statement = (
        select(
            Subject.name.label("subject_name"),
            Subject.course_code,
            User.full_name.label("teacher_name"),
            # Total periods held (Weighting the duration)
            func.sum(
                case(
                    (
                        (AttendanceSession.is_held == True) & 
                        (AttendanceSession.status == SessionStatus.SUBMITTED), 
                        AttendanceSession.duration_periods
                    ),
                    else_=0
                )
            ).label("periods_held"),
            # Total periods attended (Weighting the duration if PRESENT)
            func.sum(
                case(
                    (
                        (AttendanceSession.is_held == True) & 
                        (AttendanceSession.status == SessionStatus.SUBMITTED) & 
                        (AttendanceRecord.status == AttendanceStatus.PRESENT), 
                        AttendanceSession.duration_periods
                    ),
                    else_=0
                )
            ).label("periods_attended")
        )
        .select_from(CourseOffering)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .join(User, CourseOffering.teacher_id == User.id) # The Teacher
        .join(StudentSubjectMap, StudentSubjectMap.course_offering_id == CourseOffering.id)
        # Outer joins ensure we still return subjects even if 0 classes have been held so far
        .outerjoin(
            AttendanceSession, 
            AttendanceSession.course_offering_id == CourseOffering.id
        )
        .outerjoin(
            AttendanceRecord, 
            (AttendanceRecord.session_id == AttendanceSession.id) & 
            (AttendanceRecord.student_id == student_id)
        )
        .where(StudentSubjectMap.student_id == student_id)
        .where(CourseOffering.term_id == target_id)
        .group_by(Subject.name, Subject.course_code, User.full_name)
    )
    
    results = await db.exec(statement)
    
    subjects_history = []
    total_held = 0
    total_attended = 0
    highest_score = 0.0
    highest_subject = "N/A"
    critical_subjects = 0

    # V1 Proposed Minimum Threshold
    WARNING_THRESHOLD = 75.0

    for row in results:
        # Handle SQLAlchemy returning None for sums when no sessions exist yet
        periods_held = int(row.periods_held) if row.periods_held else 0
        periods_attended = int(row.periods_attended) if row.periods_attended else 0
        
        total_held += periods_held
        total_attended += periods_attended
        
        if periods_held > 0:
            percentage = (periods_attended / periods_held * 100)
            if percentage >= 85:
                standing = "Excellent"
            elif percentage >= WARNING_THRESHOLD:
                standing = "Good Standing"
            else:
                standing = "Low Attendance"
                critical_subjects += 1
            if percentage >= highest_score:
                highest_score = percentage
                highest_subject = f"{row.subject_name} ({percentage:.1f}%)"
        else:
            percentage = 0.0
            standing = "Not Started"
        subjects_history.append(
            SubjectAttendanceHistory(
                subject_name=row.subject_name,
                course_code=row.course_code,
                teacher_name=row.teacher_name,
                periods_held=periods_held,
                periods_attended=periods_attended,
                percentage=round(percentage, 1),
                standing=standing
            )
        )

    overall_percentage = (total_attended / total_held * 100) if total_held > 0 else 0.0
    risk_level = f"{critical_subjects} Critical" if critical_subjects > 0 else "Safe"

    # Compute batch progression semester (e.g. "Semester 5 (2026-27 Odd)")
    current_sem_label = term.name
    student_profile = (await db.exec(select(StudentProfile).where(StudentProfile.user_id == student_id))).first()
    if student_profile and student_profile.batch_id:
        batch = await db.get(Batch, student_profile.batch_id)
        if batch and batch.start_year and term.start_date:
            term_year = term.start_date.year
            is_odd = "odd" in term.name.lower() or term.start_date.month >= 6
            diff = term_year - batch.start_year
            if diff >= 0:
                sem_num = (diff * 2 + 1) if is_odd else max(2, (diff - 1) * 2 + 2)
                current_sem_label = f"Semester {sem_num} ({term.name})"

    return StudentDashboardResponse(
        current_semester=current_sem_label,
        overall_percentage=round(overall_percentage, 1),
        highest_score_subject=highest_subject,
        risk_level=risk_level,
        subjects=subjects_history
    )

import calendar as py_calendar
from datetime import date as dt_date, datetime as dt_datetime
from app.models.academic import TimetableEntry, DayOfWeek

class CalendarClassItem(BaseModel):
    course_offering_id: int
    subject_code: str
    subject_name: str
    teacher_name: str
    room: str
    start_time: str
    end_time: str
    status: str  # "PRESENT", "ABSENT", "SCHEDULED", "NOT_MARKED"

class CalendarDayData(BaseModel):
    date: str
    day_name: str
    day_number: int
    has_classes: bool
    dot_status: Optional[str] = None  # "PRESENT", "ABSENT", "MIXED", or None
    classes: List[CalendarClassItem] = []

class StudentCalendarResponse(BaseModel):
    month: str
    month_label: str
    total_attended: int = 0
    total_missed: int = 0
    days: Dict[str, CalendarDayData] = {}


@router.get("/calendar", response_model=StudentCalendarResponse, tags=["Mobile Student API"])
async def get_student_calendar(
    month: Optional[str] = Query(default=None),
    student_id: int = Depends(require_student),
    db: AsyncSession = Depends(get_session)
):
    """
    Returns daily class schedule and attendance breakdown for the student for a given month.
    Accurately maps classes to the official timetable and marks attended/missed/scheduled.
    """
    today = dt_date.today()
    target_year = today.year
    target_month = today.month

    if month:
        try:
            parts = month.strip().split("-")
            target_year = int(parts[0])
            target_month = int(parts[1])
        except Exception:
            pass

    max_days = py_calendar.monthrange(target_year, target_month)[1]
    start_of_month = dt_date(target_year, target_month, 1)
    end_of_month = dt_date(target_year, target_month, max_days)
    month_label = start_of_month.strftime("%B %Y")
    month_key = f"{target_year:04d}-{target_month:02d}"

    day_map = {
        0: DayOfWeek.MON,
        1: DayOfWeek.TUE,
        2: DayOfWeek.WED,
        3: DayOfWeek.THU,
        4: DayOfWeek.FRI,
        5: DayOfWeek.SAT,
        6: DayOfWeek.SUN,
    }
    dow_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}

    # Fetch student timetable slots
    tt_stmt = (
        select(TimetableEntry, CourseOffering, Subject, User.full_name.label("teacher_name"))
        .join(CourseOffering, TimetableEntry.course_offering_id == CourseOffering.id)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .outerjoin(User, CourseOffering.teacher_id == User.id)
        .join(StudentSubjectMap, StudentSubjectMap.course_offering_id == CourseOffering.id)
        .where(StudentSubjectMap.student_id == student_id)
        .order_by(TimetableEntry.start_time)
    )
    tt_results = (await db.exec(tt_stmt)).all()
    day_slots_map: Dict[DayOfWeek, list] = {}
    for row in tt_results:
        dow = row[0].day_of_week
        day_slots_map.setdefault(dow, []).append(row)

    # Fetch attendance records for this month
    rec_stmt = (
        select(AttendanceRecord)
        .where(
            AttendanceRecord.student_id == student_id,
            AttendanceRecord.date >= start_of_month,
            AttendanceRecord.date <= end_of_month
        )
    )
    records = (await db.exec(rec_stmt)).all()
    rec_map = {(r.date, r.course_offering_id): r.status for r in records}

    days_dict: Dict[str, CalendarDayData] = {}
    month_attended = 0
    month_missed = 0

    for day_num in range(1, max_days + 1):
        cur_date = dt_date(target_year, target_month, day_num)
        dow = day_map.get(cur_date.weekday())
        day_tt = day_slots_map.get(dow, [])
        day_classes: List[CalendarClassItem] = []
        has_present = False
        has_absent = False

        for tt, off, subj, teacher_name in day_tt:
            rec_status = rec_map.get((cur_date, off.id))
            if rec_status is not None:
                st_val = rec_status.value if hasattr(rec_status, "value") else str(rec_status)
                if st_val == "PRESENT":
                    has_present = True
                    month_attended += 1
                elif st_val == "ABSENT":
                    has_absent = True
                    month_missed += 1
                status_str = st_val
            else:
                if cur_date > today:
                    status_str = "SCHEDULED"
                else:
                    status_str = "NOT_MARKED"

            day_classes.append(
                CalendarClassItem(
                    course_offering_id=off.id,
                    subject_code=subj.course_code,
                    subject_name=subj.name,
                    teacher_name=teacher_name or "Faculty",
                    room=tt.room or "TBA",
                    start_time=str(tt.start_time),
                    end_time=str(tt.end_time),
                    status=status_str,
                )
            )

        dot_status: Optional[str] = None
        if day_classes:
            if has_absent and has_present:
                dot_status = "MIXED"
            elif has_absent:
                dot_status = "ABSENT"
            elif has_present:
                dot_status = "PRESENT"

        date_key = cur_date.isoformat()
        days_dict[date_key] = CalendarDayData(
            date=date_key,
            day_name=dow_names.get(cur_date.weekday(), "Mon"),
            day_number=day_num,
            has_classes=len(day_classes) > 0,
            dot_status=dot_status,
            classes=day_classes,
        )

    return StudentCalendarResponse(
        month=month_key,
        month_label=month_label,
        total_attended=month_attended,
        total_missed=month_missed,
        days=days_dict,
    )
