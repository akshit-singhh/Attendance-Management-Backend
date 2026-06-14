from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, List
from datetime import date, datetime
from enum import Enum

class AttendanceStatus(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LEAVE = "LEAVE"

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

from pydantic import BaseModel

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