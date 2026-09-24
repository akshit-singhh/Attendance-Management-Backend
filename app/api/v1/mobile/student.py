from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, func
from sqlalchemy import case
from typing import List
from pydantic import BaseModel

from app.core.database import get_session
from app.api.deps import require_student
from app.models.academic import CourseOffering, Subject, StudentSubjectMap, AcademicTerm
from app.models.attendance import AttendanceRecord, AttendanceSession, SessionStatus, AttendanceStatus
from app.models.user import User

router = APIRouter()

# --- REQUEST / RESPONSE SCHEMAS ---

class SubjectAttendanceHistory(BaseModel):
    subject_name: str
    course_code: str
    teacher_name: str
    periods_held: int
    periods_attended: int
    percentage: float
    standing: str

class StudentDashboardResponse(BaseModel):
    current_semester: str
    overall_percentage: float
    highest_score_subject: str
    risk_level: str
    subjects: List[SubjectAttendanceHistory]

# --- ENDPOINTS ---

@router.get("/dashboard", response_model=StudentDashboardResponse, tags=["Mobile Student API"])
async def get_student_dashboard(
    term_id: int,
    student_id: int = Depends(require_student),
    db: AsyncSession = Depends(get_session)
):
    """Fetches the aggregated attendance breakdown applying the v0.2 calculation rules."""
    
    term = await db.get(AcademicTerm, term_id)
    if not term:
        raise HTTPException(status_code=404, detail="Term not found")

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
        .where(CourseOffering.term_id == term_id)
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
        
        percentage = (periods_attended / periods_held * 100) if periods_held > 0 else 100.0
        
        if percentage >= 85:
            standing = "Excellent"
        elif percentage >= WARNING_THRESHOLD:
            standing = "Good Standing"
        else:
            standing = "Low Attendance"
            critical_subjects += 1
            
        if percentage >= highest_score and periods_held > 0:
            highest_score = percentage
            highest_subject = f"{row.subject_name} ({percentage:.1f}%)"

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

    overall_percentage = (total_attended / total_held * 100) if total_held > 0 else 100.0
    risk_level = f"{critical_subjects} Critical" if critical_subjects > 0 else "Safe"

    return StudentDashboardResponse(
        current_semester=term.name,
        overall_percentage=round(overall_percentage, 1),
        highest_score_subject=highest_subject,
        risk_level=risk_level,
        subjects=subjects_history
    )