# app/schemas/corrections.py

from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel
from app.models.attendance import AttendanceStatus


class CorrectionRequestCreate(BaseModel):
    attendance_record_id: int
    suggested_status: AttendanceStatus
    reason: str


class CorrectionDecisionPayload(BaseModel):
    decision: str  # "APPROVE" or "REJECT"
    rejection_reason: Optional[str] = None  # Required if decision is REJECT


class BulkCorrectionDecisionPayload(BaseModel):
    request_ids: List[int]
    decision: str  # "APPROVE" or "REJECT"
    rejection_reason: Optional[str] = None


class CorrectionQueueResponse(BaseModel):
    id: int
    attendance_record_id: int
    requested_by_name: str
    student_name: Optional[str] = None
    student_roll: Optional[str] = None
    subject_code: Optional[str] = None
    subject_name: Optional[str] = None
    class_date: Optional[str] = None
    current_status: Optional[str] = None
    suggested_status: str
    status: str = "PENDING"
    reason: str
    requested_at: datetime
    is_escalated: bool = False
