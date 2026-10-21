# app/schemas/attendance.py

from typing import List, Optional
from datetime import date, datetime
from pydantic import BaseModel
from app.models.attendance import AttendanceStatus, SessionMode


# --- Mobile Sync & Teacher Roll Call ---

class SyncRecordItem(BaseModel):
    record_uuid: str           # Client-generated UUID for the individual student's mark
    student_id: int
    status: AttendanceStatus
    captured_at: datetime      # The exact time the teacher tapped the button offline


class OfflineSyncPayload(BaseModel):
    session_uuid: str          # Client-generated UUID for the whole class session
    course_offering_id: int
    date: date
    duration_periods: int = 1
    mode: SessionMode = SessionMode.MANUAL
    is_held: bool = True
    reason_not_held: Optional[str] = None
    records: List[SyncRecordItem]


class StudentRosterResponse(BaseModel):
    student_id: int
    roll_number: str
    full_name: str


class CourseScheduleResponse(BaseModel):
    course_offering_id: int
    subject_name: str
    subject_code: str
    section_name: str
    start_time: str
    end_time: str
    room: Optional[str] = None
    credits: Optional[int] = 3
    course_type: Optional[str] = "LECTURE"
    weekly_hours: Optional[float] = None


class SessionHistoryResponse(BaseModel):
    course_offering_id: int
    subject_name: str
    section_name: str
    date: date


class PendingClassResponse(BaseModel):
    course_offering_id: int
    subject_name: str
    subject_code: str
    section_name: str
    date: str
    formatted_date: str
    day_name: str
    start_time: str
    end_time: str
    room: Optional[str] = "TBA"
    days_remaining: int
    is_expiring_soon: bool


# --- Admin / Coordinator Live Attendance Session Views ---

class AttendanceSessionItemResponse(BaseModel):
    id: int
    session_uuid: str
    date: str
    teacher_id: int
    teacher_name: str
    teacher_email: str
    course_offering_id: int
    subject_code: str
    subject_name: str
    section_name: str
    term_name: Optional[str] = None
    duration_periods: int
    mode: str
    status: str
    total_students: int
    present_count: int
    absent_count: int
    leave_count: int
    attendance_percentage: float
    submitted_at: Optional[datetime] = None
    created_at: datetime


class PaginatedAttendanceSessionsResponse(BaseModel):
    items: List[AttendanceSessionItemResponse]
    total: int
    page: int
    size: int
    summary_today: Optional[dict] = None


class SessionStudentRecord(BaseModel):
    record_id: int
    student_id: int
    roll_number: str
    student_name: str
    student_email: str
    status: str
    captured_at: datetime
    is_flagged: bool
    flag_reason: Optional[str] = None


class SessionDetailResponse(BaseModel):
    session: AttendanceSessionItemResponse
    records: List[SessionStudentRecord]
