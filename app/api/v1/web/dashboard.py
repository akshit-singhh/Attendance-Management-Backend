from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from sqlalchemy import func
from pydantic import BaseModel
from datetime import date

from app.core.database import get_session
from app.api.deps import require_admin
from app.models.user import User, RoleAssignment, RoleEnum, StudentProfile
from app.models.academic import (
    CourseOffering, 
    StudentSubjectMap, 
    AcademicTerm, 
    Batch, 
    Section
)
# Assuming these model names based on your database sidebar
from app.models.attendance import AttendanceSession, AttendanceRecord, CorrectionRequest, LeaveRequest

router = APIRouter()

# ============================================================================
# SCHEMAS
# ============================================================================

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
    active_term_name: str | None
    batches_without_sections: int
    empty_sections: int


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("/stats", response_model=DashboardStatsResponse, tags=["Admin - Dashboard"])
async def get_dashboard_statistics(
    db: AsyncSession = Depends(get_session),
    current_admin: User = Depends(require_admin)  
):
    """Fetch high-level user aggregates."""
    total_users = (await db.exec(select(func.count(User.id)))).one()
    active_users = (await db.exec(select(func.count(User.id)).where(User.is_active == True))).one()

    role_counts_stmt = (
        select(RoleAssignment.role, func.count(func.distinct(RoleAssignment.user_id)))
        .group_by(RoleAssignment.role)
    )
    role_counts_result = (await db.exec(role_counts_stmt)).all()
    
    # Safely handle the DB driver returning either an Enum object or a raw string
    role_stats = {}
    for role, count in role_counts_result:
        role_key = role.value if hasattr(role, 'value') else str(role)
        role_stats[role_key] = count

    return DashboardStatsResponse(
        total_users=total_users,
        active_users=active_users,
        total_students=role_stats.get(RoleEnum.STUDENT.value, 0),
        total_teachers=role_stats.get(RoleEnum.TEACHER.value, 0),
        total_coordinators=(
            role_stats.get(RoleEnum.COORDINATOR.value, 0) + 
            role_stats.get(RoleEnum.HOD.value, 0) + 
            role_stats.get(RoleEnum.DEAN.value, 0)
        ),
        total_admins=role_stats.get(RoleEnum.ADMIN.value, 0)
    )

@router.get("/action-items", response_model=ActionItemsResponse, tags=["Admin - Dashboard"])
async def get_action_items(
    db: AsyncSession = Depends(get_session),
    current_admin: User = Depends(require_admin)
):
    """Fetch operational bottlenecks requiring administrative intervention."""
    # 1. Pending Approvals (Requires your specific status enum/string)
    pending_corrections = (await db.exec(
        select(func.count(CorrectionRequest.id)).where(CorrectionRequest.status == "PENDING")
    )).one()
    
    pending_leaves = (await db.exec(
        select(func.count(LeaveRequest.id)).where(LeaveRequest.status == "PENDING")
    )).one()

    # 2. Setup gaps
    unassigned_courses = (await db.exec(
        select(func.count(CourseOffering.id)).where(CourseOffering.teacher_id == None)
    )).one()

    # 3. Students not enrolled in any subjects for the active term
    active_term = (await db.exec(select(AcademicTerm.id).where(AcademicTerm.is_active == True))).first()
    unmapped_students = 0
    if active_term:
        active_student_ids = select(User.id).join(RoleAssignment).where(RoleAssignment.role == RoleEnum.STUDENT, User.is_active == True)
        mapped_student_ids = select(StudentSubjectMap.student_id).join(CourseOffering).where(CourseOffering.term_id == active_term)
        
        unmapped_stmt = select(func.count(User.id)).where(
            User.id.in_(active_student_ids),
            User.id.not_in(mapped_student_ids)
        )
        unmapped_students = (await db.exec(unmapped_stmt)).one()

    return ActionItemsResponse(
        pending_corrections=pending_corrections,
        pending_leave_requests=pending_leaves,
        unassigned_courses=unassigned_courses,
        unmapped_students=unmapped_students
    )

@router.get("/attendance-pulse", response_model=AttendancePulseResponse, tags=["Admin - Dashboard"])
async def get_attendance_pulse(
    db: AsyncSession = Depends(get_session),
    current_admin: User = Depends(require_admin)
):
    """Fetch live, daily health metrics of the attendance system."""
    today = date.today()

    # 1. Classes held today (Assuming AttendanceSession has a 'date' or 'created_at' column)
    classes_held = (await db.exec(
        select(func.count(AttendanceSession.id)).where(func.date(AttendanceSession.date) == today)
    )).one()

    # 2. Overall present percentage today
    total_records = (await db.exec(
        select(func.count(AttendanceRecord.id))
        .join(AttendanceSession)
        .where(func.date(AttendanceSession.date) == today)
    )).one()

    present_records = (await db.exec(
        select(func.count(AttendanceRecord.id))
        .join(AttendanceSession)
        .where(func.date(AttendanceSession.date) == today, AttendanceRecord.status == "PRESENT")
    )).one()

    overall_pct = (present_records / total_records * 100) if total_records > 0 else 0.0

    return AttendancePulseResponse(
        classes_held_today=classes_held,
        overall_attendance_today=round(overall_pct, 2)
    )

@router.get("/setup-health", response_model=SetupHealthResponse, tags=["Admin - Dashboard"])
async def get_setup_health(
    db: AsyncSession = Depends(get_session),
    current_admin: User = Depends(require_admin)
):
    """Validate the structural integrity of the academic term hierarchy."""
    active_term = (await db.exec(select(AcademicTerm).where(AcademicTerm.is_active == True))).first()
    
    batches_without_sections = 0
    empty_sections = 0

    if active_term:
        # Batches that have no sections linked in the active term
        sections_in_term = select(Section.batch_id).where(Section.term_id == active_term.id)
        batches_without_sections = (await db.exec(
            select(func.count(Batch.id)).where(Batch.id.not_in(sections_in_term))
        )).one()

        # Sections in the active term that have no course offerings
        offerings_in_term = select(CourseOffering.section_id).where(CourseOffering.term_id == active_term.id)
        empty_sections = (await db.exec(
            select(func.count(Section.id)).where(
                Section.term_id == active_term.id,
                Section.id.not_in(offerings_in_term)
            )
        )).one()

    return SetupHealthResponse(
        active_term_name=active_term.name if active_term else None,
        batches_without_sections=batches_without_sections,
        empty_sections=empty_sections
    )