# app/schemas/auth.py

from typing import List, Optional
from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    email: str
    otp: str
    new_password: str


class RoleAssignmentRead(BaseModel):
    role: str
    scope_type: str
    scope_id: Optional[int] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    must_change_password: bool = False

    available_roles: List[RoleAssignmentRead] = []
    active_role: str = ""
    user_name: Optional[str] = None
    user_email: Optional[str] = None


class SwitchRoleRequest(BaseModel):
    role: str
    scope_id: Optional[int] = None


class UserProfileResponse(BaseModel):
    user_id: int
    full_name: str
    email: str
    roles: List[str]
    primary_role: str
    department: str = "Computer Science & Engineering"
    school: str = "School of Information & Communication Technology"
    total_courses: Optional[int] = None
    weekly_hours: Optional[float] = None
    roll_number: Optional[str] = None
    employee_code: Optional[str] = None
    program_name: Optional[str] = None
    batch_name: Optional[str] = None
    overall_attendance_pct: Optional[float] = None
