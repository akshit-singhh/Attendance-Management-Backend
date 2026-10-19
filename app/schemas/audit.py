# app/schemas/audit.py

from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel


class AuditLogItemResponse(BaseModel):
    id: int
    attendance_record_id: int
    changed_by_name: str
    changed_by_email: str
    old_status: str
    new_status: str
    reason: Optional[str]
    timestamp: datetime


class PaginatedAuditLogsResponse(BaseModel):
    items: List[AuditLogItemResponse]
    total: int
    page: int
    size: int
