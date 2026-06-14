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

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    full_name: str
    role: RoleEnum
    
    is_active: bool = Field(default=True)
    fcm_token: Optional[str] = None # For Android Push Notifications
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    student_profile: Optional["StudentProfile"] = Relationship(back_populates="user")
    
class StudentProfile(SQLModel, table=True):
    """Created only if User role is STUDENT"""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", unique=True)
    roll_number: str = Field(unique=True, index=True)
    batch_id: int = Field(foreign_key="batch.id") # Links to models/academic.py
    
    user: User = Relationship(back_populates="student_profile")
    
class RefreshToken(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    token: str = Field(unique=True, index=True)
    
    expires_at: datetime
    is_revoked: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)