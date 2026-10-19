# app/schemas/dashboard.py

from typing import Optional
from pydantic import BaseModel


class DashboardStatsResponse(BaseModel):
    total_users: int = 0
    active_users: int = 0
    total_students: int = 0
    total_teachers: int = 0
    total_coordinators: int = 0
    total_admins: int = 0


class ActionItemsResponse(BaseModel):
    pending_corrections: int
    pending_leave_requests: int
    unassigned_courses: int
    unmapped_students: int


class AttendancePulseResponse(BaseModel):
    classes_held_today: int
    overall_attendance_today: float


class SetupHealthResponse(BaseModel):
    active_term_name: Optional[str] = None
    batches_without_sections: int
    empty_sections: int
