# app/models/user.py
from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, List
from enum import Enum
from datetime import datetime

class RoleEnum(str, Enum):
    ADMIN = "ADMIN"
    DEAN = "DEAN"
    HOD = "HOD"
    COORDINATOR = "COORDINATOR"
    TEACHER = "TEACHER"
    STUDENT = "STUDENT"

class ScopeTypeEnum(str, Enum):
    UNIVERSITY = "UNIVERSITY"
    SCHOOL = "SCHOOL"
    DEPARTMENT = "DEPARTMENT"
    PROGRAM = "PROGRAM"
    ASSIGNMENT = "ASSIGNMENT"

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    full_name: str
    
    is_active: bool = Field(default=True)
    must_change_password: bool = Field(default=True)
    fcm_token: Optional[str] = None 
    created_at: datetime = Field(default_factory=datetime.utcnow)

    student_profile: Optional["StudentProfile"] = Relationship(back_populates="user")
    role_assignments: List["RoleAssignment"] = Relationship(back_populates="user")

class RoleAssignment(SQLModel, table=True):
    """Links a user to a role and a specific scope (e.g., HOD of CSE Dept)"""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    role: RoleEnum
    scope_type: ScopeTypeEnum
    scope_id: Optional[int] = Field(default=None)

    user: User = Relationship(back_populates="role_assignments")
    
class StudentProfile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", unique=True)
    roll_number: str = Field(unique=True, index=True)
    batch_id: int = Field(foreign_key="batch.id") 
    
    user: User = Relationship(back_populates="student_profile")

class RefreshToken(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    token: str = Field(unique=True, index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    expires_at: datetime
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_revoked: bool = Field(default=False)
    
    
class PasswordResetOTP(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True)
    otp_code: str
    expires_at: datetime