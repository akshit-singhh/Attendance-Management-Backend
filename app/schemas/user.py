# app/schemas/user.py

from typing import List, Optional
from pydantic import BaseModel
from app.models.user import RoleEnum, ScopeTypeEnum


class RoleAssignmentItem(BaseModel):
    role: RoleEnum
    scope_type: Optional[ScopeTypeEnum] = None
    scope_id: Optional[int] = None


class RoleAssignmentResponse(BaseModel):
    id: Optional[int] = None
    role: RoleEnum
    scope_type: ScopeTypeEnum
    scope_id: Optional[int] = None


class UserCreate(BaseModel):
    email: str
    full_name: str
    password: str
    role: RoleEnum
    roles: Optional[List[RoleEnum]] = None
    role_assignments: Optional[List[RoleAssignmentItem]] = None
    scope_type: ScopeTypeEnum = ScopeTypeEnum.UNIVERSITY
    scope_id: Optional[int] = None
    roll_number: Optional[str] = None
    employee_code: Optional[str] = None
    batch_id: Optional[int] = None


class UpdateUserRolesRequest(BaseModel):
    roles: List[RoleAssignmentItem]


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str
    is_active: bool
    role: RoleEnum
    scope_type: ScopeTypeEnum
    scope_id: Optional[int] = None
    roles: List[RoleEnum] = []
    role_assignments: List[RoleAssignmentResponse] = []
    roll_number: Optional[str] = None
    employee_code: Optional[str] = None
    batch_id: Optional[int] = None


class PaginatedUserResponse(BaseModel):
    items: List[UserResponse]
    total: int
    page: int
    size: int


class AdminUpdateUserPasswordRequest(BaseModel):
    new_password: str
    must_change_password: Optional[bool] = False


class AdminUpdateUserProfileRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    roll_number: Optional[str] = None
    employee_code: Optional[str] = None
    new_password: Optional[str] = None
    must_change_password: Optional[bool] = None
