# app/schemas/student.py

from typing import Dict, List, Optional
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


class CalendarClassItem(BaseModel):
    course_offering_id: int
    subject_code: str
    subject_name: str
    teacher_name: str
    room: str
    start_time: str
    end_time: str
    status: str  # "PRESENT", "ABSENT", "SCHEDULED", "NOT_MARKED"
    batch_label: Optional[str] = None


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
