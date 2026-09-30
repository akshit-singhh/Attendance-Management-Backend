from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from sqlalchemy import func
from pydantic import BaseModel

from app.core.database import get_session
from app.models.user import User, RoleAssignment, RoleEnum
from app.api.deps import require_admin
from app.models.user import User

router = APIRouter()

# --- Schemas ---
class DashboardStatsResponse(BaseModel):
    total_users: int = 0
    active_users: int = 0
    total_students: int = 0
    total_teachers: int = 0
    total_coordinators: int = 0
    total_admins: int = 0

# --- Endpoints ---
@router.get("/stats", response_model=DashboardStatsResponse)
async def get_dashboard_statistics(
    db: AsyncSession = Depends(get_session),
    current_admin: User = Depends(require_admin)  
):
    """Fetch high-level user aggregates for the admin web dashboard."""
    
    # 1. Get total users and active users directly from the User table
    total_users = (await db.exec(select(func.count(User.id)))).one()
    active_users = (await db.exec(select(func.count(User.id)).where(User.is_active == True))).one()

    # 2. Get breakdown of all users by role in ONE query via RoleAssignment
    # We use func.distinct() just in case a user has the same role in multiple scopes
    role_counts_stmt = (
        select(RoleAssignment.role, func.count(func.distinct(RoleAssignment.user_id)))
        .group_by(RoleAssignment.role)
    )
    role_counts_result = (await db.exec(role_counts_stmt)).all()
    
    # Convert list of tuples [(RoleEnum.STUDENT, 76)] into a dictionary using the enum's string value
    role_stats = {role.value: count for role, count in role_counts_result}

    # 3. Map the dictionary to the response schema
    return DashboardStatsResponse(
        total_users=total_users,
        active_users=active_users,
        total_students=role_stats.get(RoleEnum.STUDENT.value, 0),
        total_teachers=role_stats.get(RoleEnum.TEACHER.value, 0),
        # Aggregate DEAN, HOD, and COORDINATOR into the total_coordinators bucket
        total_coordinators=(
            role_stats.get(RoleEnum.COORDINATOR.value, 0) + 
            role_stats.get(RoleEnum.HOD.value, 0) + 
            role_stats.get(RoleEnum.DEAN.value, 0)
        ),
        total_admins=role_stats.get(RoleEnum.ADMIN.value, 0)
    )