# app/models/notification.py
from sqlmodel import SQLModel, Field
from sqlalchemy import UniqueConstraint, Index
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



class NotificationDeliveryStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

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
    notification_type: str = Field(default="ANNOUNCEMENT")
    priority: str = Field(default="NORMAL")
    scope_type: str = Field(default="UNIVERSITY")
    scope_id: Optional[int] = Field(default=None)
    target_role: Optional[str] = Field(default="ALL")  # "ALL", "STUDENT", "TEACHER", "COORDINATOR", etc.
    action_url: Optional[str] = Field(default=None)
    status: NotificationDeliveryStatus = Field(default=NotificationDeliveryStatus.PENDING, index=True)
    failure_reason: Optional[str] = Field(default=None)
    processed_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    expires_at: Optional[datetime] = Field(default=None)


class NotificationRecipient(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("notification_id", "user_id", name="uq_notification_recipient"),
        Index("ix_notificationrecipient_user_id_is_read", "user_id", "is_read"),
    )
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
