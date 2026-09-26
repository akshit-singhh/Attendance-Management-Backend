from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, List
from datetime import date, datetime
from enum import Enum
from pydantic import BaseModel

class AttendanceStatus(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"

class SessionStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    CANCELLED = "CANCELLED"
    CORRECTION_REQUESTED = "CORRECTION_REQUESTED"

class SessionMode(str, Enum):
    MANUAL = "MANUAL"

class AttendanceSession(SQLModel, table=True):
    """An actual attendance event carrying duration and mode"""
    id: Optional[int] = Field(default=None, primary_key=True)
    session_uuid: str = Field(unique=True, index=True) # Enables idempotent offline sync
    course_offering_id: int = Field(foreign_key="courseoffering.id")
    teacher_id: int = Field(foreign_key="user.id")
    
    date: date
    duration_periods: int = Field(default=1)
    mode: SessionMode = Field(default=SessionMode.MANUAL)
    
    is_held: bool = Field(default=True)
    reason_not_held: Optional[str] = None
    
    status: SessionStatus = Field(default=SessionStatus.DRAFT)
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    submitted_at: Optional[datetime] = None
    
    records: List["AttendanceRecord"] = Relationship(back_populates="session")

class AttendanceRecord(SQLModel, table=True):
    """Individual student Present/Absent record linked to a Session"""
    id: Optional[int] = Field(default=None, primary_key=True)
    record_uuid: str = Field(unique=True, index=True)
    session_id: int = Field(foreign_key="attendancesession.id")
    student_id: int = Field(foreign_key="user.id")
    
    status: AttendanceStatus
    
    captured_at: datetime # Device timestamp of original capture
    synced_at: datetime = Field(default_factory=datetime.utcnow) # Server timestamp
    
    is_overridden: bool = Field(default=False)
    is_flagged: bool = Field(default=False) # For suspect offline records like stale lists
    flag_reason: Optional[str] = None
    
    session: AttendanceSession = Relationship(back_populates="records")
    audit_logs: List["AttendanceAuditLog"] = Relationship(back_populates="record")

class CorrectionStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class CorrectionRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    attendance_record_id: int = Field(foreign_key="attendancerecord.id")
    
    suggested_status: AttendanceStatus
    reason: str
    status: CorrectionStatus = Field(default=CorrectionStatus.PENDING)
    
    submitted_by_id: int = Field(foreign_key="user.id")
    applied_on: datetime = Field(default_factory=datetime.utcnow)

class CorrectionRequestCreate(BaseModel):
    attendance_record_id: int
    suggested_status: AttendanceStatus
    reason: str

class SessionHistoryResponse(BaseModel):
    course_offering_id: int
    subject_name: str
    section_name: str
    date: date

class AttendanceAuditLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    attendance_record_id: int = Field(foreign_key="attendancerecord.id")
    changed_by_id: int = Field(foreign_key="user.id")
    
    previous_status: AttendanceStatus
    new_status: AttendanceStatus
    reason: str
    
    changed_at: datetime = Field(default_factory=datetime.utcnow)
    record: AttendanceRecord = Relationship(back_populates="audit_logs")


# --- API SCHEMAS ---

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
    
class LeaveStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class LeaveRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="user.id")
    
    start_date: date
    end_date: date
    reason: str
    document_url: str
    status: LeaveStatus = Field(default=LeaveStatus.PENDING)
    
    reviewed_by_id: Optional[int] = Field(default=None, foreign_key="user.id")
    applied_on: datetime = Field(default_factory=datetime.utcnow)