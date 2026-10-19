# app/schemas/student.py

from typing import List
from pydantic import BaseModel


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
