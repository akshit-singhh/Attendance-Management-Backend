# app/schemas/timetable.py

from typing import Dict, List, Optional
from pydantic import BaseModel


class TimetableSlotItem(BaseModel):
    day: int
    day_name: str
    period: int
    period_time: str
    subject_code: str
    subject_name: str
    teacher_name: str
    teacher_abbr: str
    section_name: str
    room: str
    building: str
    activity: str


class TeachingWorkloadItem(BaseModel):
    section_name: str
    subject_code: str
    subject_name: str
    activity: str
    hours: float
    teacher_name: Optional[str] = None


class TeacherTimetableResponse(BaseModel):
    title: str
    subtitle: str
    semester_info: str = "GBU 2026-27 (ODD SEMESTER) • Faculty Weekly Load"
    total_hours: float
    total_slots: int
    slots: List[TimetableSlotItem]
    workload: List[TeachingWorkloadItem]
    period_definitions: Dict[int, str]
    day_definitions: Dict[int, str]


class SyncApplyRequest(BaseModel):
    section_name: str
    samay_section_id: Optional[str] = None
    school: Optional[str] = None
    dept: Optional[str] = None
    auto_enroll_students: bool = True
