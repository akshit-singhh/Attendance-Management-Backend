from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, List
from datetime import date, datetime
from enum import Enum
from sqlmodel import SQLModel, Field
from datetime import date, datetime
from pydantic import BaseModel


class AttendanceStatus(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LEAVE = "LEAVE"

class LeaveStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class AttendanceRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="user.id")
    course_offering_id: int = Field(foreign_key="courseoffering.id")
    date: date
    status: AttendanceStatus
    
    marked_by_id: int = Field(foreign_key="user.id") 
    is_overridden: bool = Field(default=False)
    
    audit_logs: List["AttendanceAuditLog"] = Relationship(back_populates="record")

class AttendanceAuditLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    attendance_record_id: int = Field(foreign_key="attendancerecord.id")
    changed_by_id: int = Field(foreign_key="user.id")
    
    previous_status: AttendanceStatus
    new_status: AttendanceStatus
    reason: str
    
    changed_at: datetime = Field(default_factory=datetime.utcnow)
    record: AttendanceRecord = Relationship(back_populates="audit_logs")
    
class LeaveRequest(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="user.id", index=True)
    start_date: date
    end_date: date
    reason: str
    document_url: str | None = None # Where the file is stored
    status: LeaveStatus = Field(default=LeaveStatus.PENDING)
    applied_on: datetime = Field(default_factory=datetime.utcnow)

class StudentAttendanceItem(BaseModel):
    student_id: int
    status: AttendanceStatus

class BulkAttendanceSubmit(BaseModel):
    course_offering_id: int
    date: date
    records: List[StudentAttendanceItem]

class StudentRosterResponse(BaseModel):
    student_id: int
    roll_number: str
    full_name: str

class CourseScheduleResponse(BaseModel):
    course_offering_id: int
    subject_name: str
    subject_code: str
    section_name: str