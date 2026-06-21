from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, func
from sqlalchemy import case
from typing import List
from pydantic import BaseModel

from app.core.database import get_session
from app.api.deps import require_student
from app.models.academic import CourseOffering, Subject, StudentSubjectMap, AcademicTerm
from app.models.attendance import AttendanceRecord
from app.models.user import User

router = APIRouter()

# --- REQUEST / RESPONSE SCHEMAS ---

class SubjectAttendanceHistory(BaseModel):
    subject_name: str
    course_code: str
    teacher_name: str
    classes_held: int
    classes_attended: int
    percentage: float
    standing: str

class StudentDashboardResponse(BaseModel):
    current_semester: str
    overall_percentage: float
    highest_score_subject: str
    risk_level: str
    subjects: List[SubjectAttendanceHistory]

# --- ENDPOINTS ---

@router.get("/dashboard", response_model=StudentDashboardResponse)
async def get_student_dashboard(
    term_id: int,
    student_id: int = Depends(require_student),
    db: AsyncSession = Depends(get_session)
):
    """Fetches the complete attendance breakdown for a student in a specific term."""
    
    term = await db.get(AcademicTerm, term_id)
    if not term:
        raise HTTPException(status_code=404, detail="Term not found")

    # Advanced SQL Aggregation: Group by Subject and calculate percentages directly in Postgres
    statement = (
        select(
            Subject.name.label("subject_name"),
            Subject.course_code,
            User.full_name.label("teacher_name"),
            func.count(AttendanceRecord.id).label("classes_held"),
            func.sum(
                case((AttendanceRecord.status == "PRESENT", 1), else_=0)
            ).label("classes_attended")
        )
        .select_from(CourseOffering)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .join(User, CourseOffering.teacher_id == User.id) # The Teacher
        .join(StudentSubjectMap, StudentSubjectMap.course_offering_id == CourseOffering.id)
        .outerjoin(
            AttendanceRecord, 
            (AttendanceRecord.course_offering_id == CourseOffering.id) & 
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

    # Process the SQL results into our Python schemas
    for row in results:
        classes_held = row.classes_held or 0
        classes_attended = row.classes_attended or 0
        
        total_held += classes_held
        total_attended += classes_attended
        
        # Calculate percentage for this specific subject
        percentage = (classes_attended / classes_held * 100) if classes_held > 0 else 100.0
        
        # Determine standing based on your design rules
        if percentage >= 85:
            standing = "Excellent"
        elif percentage >= 75:
            standing = "Good Standing"
        else:
            standing = "Low Attendance"
            critical_subjects += 1
            
        # Track highest score
        if percentage >= highest_score and classes_held > 0:
            highest_score = percentage
            highest_subject = f"{row.subject_name} ({percentage:.1f}%)"

        subjects_history.append(
            SubjectAttendanceHistory(
                subject_name=row.subject_name,
                course_code=row.course_code,
                teacher_name=row.teacher_name,
                classes_held=classes_held,
                classes_attended=classes_attended,
                percentage=round(percentage, 1),
                standing=standing
            )
        )

    # Calculate overall metrics
    overall_percentage = (total_attended / total_held * 100) if total_held > 0 else 100.0
    risk_level = f"{critical_subjects} Critical" if critical_subjects > 0 else "Safe"

    return StudentDashboardResponse(
        current_semester=term.name,
        overall_percentage=round(overall_percentage, 1),
        highest_score_subject=highest_subject,
        risk_level=risk_level,
        subjects=subjects_history
    )