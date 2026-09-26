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

# --- MULTI-ROLE SCHEMAS ---

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

class SwitchRoleRequest(BaseModel):
    role: str
    scope_id: Optional[int] = None