# app/schemas/corrections.py

from typing import Optional
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


class CorrectionQueueResponse(BaseModel):
    id: int
    attendance_record_id: int
    requested_by_name: str
    suggested_status: str
    reason: str
    requested_at: datetime
