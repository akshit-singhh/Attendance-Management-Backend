# app/schemas/notification.py
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.models.notification import NotificationType, NotificationPriority, NotificationScope


class NotificationCreate(BaseModel):
    title: str
    message: str
    notification_type: NotificationType = NotificationType.ANNOUNCEMENT
    priority: NotificationPriority = NotificationPriority.NORMAL
    scope_type: NotificationScope = NotificationScope.UNIVERSITY
    scope_id: Optional[int] = None
    target_role: Optional[str] = "ALL"  # "ALL", "STUDENT", "TEACHER", "COORDINATOR", etc.
    action_url: Optional[str] = None
    expires_at: Optional[datetime] = None


class NotificationBroadcastResponse(BaseModel):
    id: int
    sender_id: int
    sender_name: str
    sender_email: str
    title: str
    message: str
    notification_type: str
    priority: str
    scope_type: str
    scope_id: Optional[int] = None
    target_role: Optional[str] = None
    action_url: Optional[str] = None
    created_at: datetime
    recipient_count: int
    status: str = "PENDING"
    failure_reason: Optional[str] = None


class NotificationRecipientItem(BaseModel):
    id: int  # NotificationRecipient id
    notification_id: int
    title: str
    message: str
    notification_type: str
    priority: str
    sender_name: str
    action_url: Optional[str] = None
    is_read: bool
    read_at: Optional[datetime] = None
    created_at: datetime


class PaginatedNotificationsResponse(BaseModel):
    items: List[NotificationRecipientItem]
    total: int
    unread_count: int
    page: int
    size: int


class UnreadCountResponse(BaseModel):
    unread_count: int


class DeviceTokenRegisterRequest(BaseModel):
    fcm_token: str
    device_type: str = "android"


class NotificationSettingsResponse(BaseModel):
    allow_coordinator_broadcast: bool
    allow_teacher_broadcast: bool


class NotificationSettingsUpdate(BaseModel):
    allow_coordinator_broadcast: Optional[bool] = None
    allow_teacher_broadcast: Optional[bool] = None
