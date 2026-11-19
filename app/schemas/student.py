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
    lab_group: Optional[str] = None


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


class StudentClassSessionItem(BaseModel):
    session_id: int
    date: str
    day_name: str
    formatted_date: str
    status: str  # "PRESENT", "ABSENT", "POSTPONED"
    duration_periods: int = 1
    batch_label: Optional[str] = None
    teacher_name: str
    room: Optional[str] = None
    time_display: Optional[str] = None
    is_held: bool = True
    reason_not_held: Optional[str] = None


class StudentSubjectHistoryResponse(BaseModel):
    course_code: str
    subject_name: str
    teacher_name: str
    lab_group: Optional[str] = None
    periods_held: int
    periods_attended: int
    percentage: float
    standing: str
    sessions: List[StudentClassSessionItem] = []

