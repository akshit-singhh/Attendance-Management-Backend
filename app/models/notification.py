# app/models/notification.py
from sqlmodel import SQLModel, Field
from typing import Optional
from enum import Enum
from datetime import datetime


class NotificationType(str, Enum):
    ANNOUNCEMENT = "ANNOUNCEMENT"
    CIRCULAR = "CIRCULAR"
    ATTENDANCE_ALERT = "ATTENDANCE_ALERT"
    LEAVE_UPDATE = "LEAVE_UPDATE"
    ACADEMIC_ORDER = "ACADEMIC_ORDER"
    GENERAL = "GENERAL"


class NotificationPriority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class NotificationScope(str, Enum):
    UNIVERSITY = "UNIVERSITY"
    SCHOOL = "SCHOOL"
    DEPARTMENT = "DEPARTMENT"
    PROGRAM = "PROGRAM"
    BATCH = "BATCH"
    SECTION = "SECTION"
    INDIVIDUAL = "INDIVIDUAL"


class Notification(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    sender_id: int = Field(foreign_key="user.id", index=True)
    title: str = Field(index=True)
    message: str
    notification_type: NotificationType = Field(default=NotificationType.ANNOUNCEMENT)
    priority: NotificationPriority = Field(default=NotificationPriority.NORMAL)
    scope_type: NotificationScope = Field(default=NotificationScope.UNIVERSITY)
    scope_id: Optional[int] = Field(default=None)
    target_role: Optional[str] = Field(default="ALL")  # "ALL", "STUDENT", "TEACHER", "COORDINATOR", etc.
    action_url: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    expires_at: Optional[datetime] = Field(default=None)


class NotificationRecipient(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    notification_id: int = Field(foreign_key="notification.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    is_read: bool = Field(default=False, index=True)
    read_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UserDeviceToken(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    fcm_token: str = Field(index=True)
    device_type: str = Field(default="android")
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class NotificationSetting(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(unique=True, index=True)
    value: str
    description: Optional[str] = Field(default=None)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
