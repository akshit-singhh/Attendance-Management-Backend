# app/models/auth.py
# Backward-compatibility re-exports from app.schemas.auth

from app.schemas.auth import (
    LoginRequest,
    RefreshTokenRequest,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    RoleAssignmentRead,
    TokenResponse,
    SwitchRoleRequest,
)

__all__ = [
    "LoginRequest",
    "RefreshTokenRequest",
    "ChangePasswordRequest",
    "ForgotPasswordRequest",
    "ResetPasswordRequest",
    "RoleAssignmentRead",
    "TokenResponse",
    "SwitchRoleRequest",
]