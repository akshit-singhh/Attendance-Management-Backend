# app/api/v1/notifications.py
from fastapi import APIRouter, Depends, HTTPException, Query, status, BackgroundTasks
from sqlalchemy.orm import sessionmaker
from app.services.notification_service import notification_service
from app.core.database import get_session, engine
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, func, or_
from typing import List, Optional
from datetime import datetime

from app.api.deps import get_current_user, require_admin, require_coordinator
from app.models.user import User, RoleEnum, ScopeTypeEnum, StudentProfile, RoleAssignment
from app.models.academic import School, Department, Programme, Batch, Section, CourseOffering, StudentSubjectMap
from app.models.notification import (
    Notification,
    NotificationRecipient,
    UserDeviceToken,
    NotificationSetting,
    NotificationType,
    NotificationPriority,
    NotificationScope,
)
from app.schemas.notification import (
    NotificationCreate,
    NotificationBroadcastResponse,
    NotificationRecipientItem,
    PaginatedNotificationsResponse,
    UnreadCountResponse,
    DeviceTokenRegisterRequest,
    NotificationSettingsResponse,
    NotificationSettingsUpdate,
)

router = APIRouter()


async def get_or_create_setting(db: AsyncSession, key: str, default_val: str, description: str = "") -> str:
    stmt = select(NotificationSetting).where(NotificationSetting.key == key)
    res = (await db.exec(stmt)).first()
    if not res:
        setting = NotificationSetting(key=key, value=default_val, description=description)
        db.add(setting)
        await db.commit()
        await db.refresh(setting)
        return default_val
    return res.value


# ---------------------------------------------------------------------------
# 1. BROADCAST SENDER (Role & Scope Governed)
# ---------------------------------------------------------------------------


async def dispatch_notification_fanout(
    notification_id: int,
    recipient_user_ids: List[int],
    title: str,
    message: str,
    priority: str,
    notification_type: str,
    action_url: Optional[str] = None
):
    """
    Decoupled Asynchronous Fan-Out & Multi-Channel Push Worker:
    1. Chunks recipient database inserts (500 records/batch) to keep connection and memory bounded.
    2. Queries registered active device tokens (FCM/Mobile) for recipients.
    3. Triggers mobile push delivery with title, body, priority and deep-linking data.
    4. Automatically deactivates unregistered/expired device tokens reported by FCM.
    """
    if not recipient_user_ids:
        return

    async_session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.utcnow()
    CHUNK_SIZE = 500

    # 1. Chunked In-App Fan-Out Insertion
    try:
        async with async_session_maker() as session:
            for i in range(0, len(recipient_user_ids), CHUNK_SIZE):
                chunk = recipient_user_ids[i:i + CHUNK_SIZE]
                recipients = [
                    NotificationRecipient(
                        notification_id=notification_id,
                        user_id=uid,
                        is_read=False,
                        created_at=now
                    )
                    for uid in chunk
                ]
                session.add_all(recipients)
                await session.commit()
    except Exception as exc:
        print(f"Failed during recipient fan-out chunk insertion for notification {notification_id}: {exc}")

    # 2. Query Active Device Tokens for Mobile Push
    try:
        async with async_session_maker() as session:
            stmt = (
                select(UserDeviceToken)
                .where(
                    UserDeviceToken.user_id.in_(recipient_user_ids),
                    UserDeviceToken.is_active == True
                )
            )
            device_tokens_records = (await session.exec(stmt)).all()

            if not device_tokens_records:
                return

            tokens = list({dt.fcm_token for dt in device_tokens_records if dt.fcm_token})

            # 3. Dispatch Push Notifications via Notification Service
            extra_data = {
                "notification_id": str(notification_id),
                "notification_type": notification_type,
                "priority": priority,
                "action_url": action_url or ""
            }

            dispatch_result = await notification_service.send_broadcast_push(
                tokens=tokens,
                title=title,
                message=message,
                priority=priority,
                extra_data=extra_data
            )

            # 4. Invalidate Unregistered / Expired Tokens
            invalid_tokens = dispatch_result.get("invalid_tokens", [])
            if invalid_tokens:
                inv_stmt = (
                    select(UserDeviceToken)
                    .where(UserDeviceToken.fcm_token.in_(invalid_tokens))
                )
                bad_records = (await session.exec(inv_stmt)).all()
                for rec in bad_records:
                    rec.is_active = False
                    rec.updated_at = datetime.utcnow()
                    session.add(rec)
                await session.commit()

    except Exception as exc:
        print(f"Failed during mobile push dispatch for notification {notification_id}: {exc}")


@router.post("/broadcast", response_model=NotificationBroadcastResponse, status_code=status.HTTP_202_ACCEPTED, tags=["Notifications"])
async def send_broadcast(
    payload: NotificationCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session)
):
    """
    Broadcasts a notification to a scoped target audience.
    Enforces university hierarchy:
    - ADMIN: University-wide or any sub-scope.
    - DEAN: School scope.
    - HOD: Department scope.
    - COORDINATOR: Programme / Batch scope (subject to admin toggle).
    """
    # 1. Check user roles
    stmt_roles = select(RoleAssignment).where(RoleAssignment.user_id == current_user.id)
    assignments = (await db.exec(stmt_roles)).all()
    user_roles = [a.role for a in assignments]

    is_admin = RoleEnum.ADMIN in user_roles
    is_dean = RoleEnum.DEAN in user_roles
    is_hod = RoleEnum.HOD in user_roles
    is_coord = RoleEnum.COORDINATOR in user_roles
    is_teacher = RoleEnum.TEACHER in user_roles

    if not (is_admin or is_dean or is_hod or is_coord or is_teacher):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to send broadcasts."
        )

    # 2. Check settings toggles
    if is_coord and not (is_admin or is_dean or is_hod):
        coord_allowed = await get_or_create_setting(
            db, "allow_coordinator_broadcast", "true", "Allow coordinators to send batch broadcasts"
        )
        if coord_allowed.lower() != "true":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Coordinator broadcasts are currently disabled by the administration."
            )

    if is_teacher and not (is_admin or is_dean or is_hod or is_coord):
        teacher_allowed = await get_or_create_setting(
            db, "allow_teacher_broadcast", "false", "Allow teachers to send broadcasts"
        )
        if teacher_allowed.lower() != "true":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Teachers are restricted from sending general broadcasts."
            )

    # 3. Scope validation based on sender role
    if payload.scope_type == NotificationScope.UNIVERSITY and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only University Administrators can send university-wide broadcasts."
        )

    # 4. Resolve Target Audience User IDs
    target_role_filter = payload.target_role.upper() if payload.target_role else "ALL"
    recipient_user_ids = set()

    # Query matching users based on scope
    if payload.scope_type == NotificationScope.UNIVERSITY:
        user_stmt = select(User.id).where(User.is_active == True)
        if target_role_filter != "ALL":
            sub_r = select(RoleAssignment.user_id).where(RoleAssignment.role == target_role_filter)
            user_stmt = user_stmt.where(User.id.in_(sub_r))
        recipient_user_ids.update((await db.exec(user_stmt)).all())

    elif payload.scope_type == NotificationScope.DEPARTMENT:
        dept_id = payload.scope_id
        if not dept_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Department ID is required.")
        
        # Students in department
        st_sub = (
            select(StudentProfile.user_id)
            .join(Batch, StudentProfile.batch_id == Batch.id)
            .join(Programme, Batch.programme_id == Programme.id)
            .where(Programme.department_id == dept_id)
        )
        # Teachers / Staff in department
        fac_sub = (
            select(RoleAssignment.user_id)
            .where(RoleAssignment.scope_type == ScopeTypeEnum.DEPARTMENT, RoleAssignment.scope_id == dept_id)
        )

        if target_role_filter == "STUDENT":
            stmt_u = select(User.id).where(User.id.in_(st_sub), User.is_active == True)
        elif target_role_filter in ("TEACHER", "FACULTY"):
            stmt_u = select(User.id).where(User.id.in_(fac_sub), User.is_active == True)
        else:
            stmt_u = select(User.id).where(or_(User.id.in_(st_sub), User.id.in_(fac_sub)), User.is_active == True)

        recipient_user_ids.update((await db.exec(stmt_u)).all())

    elif payload.scope_type == NotificationScope.BATCH:
        batch_id = payload.scope_id
        if not batch_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Batch ID is required.")
        
        stmt_u = (
            select(StudentProfile.user_id)
            .join(User, StudentProfile.user_id == User.id)
            .where(StudentProfile.batch_id == batch_id, User.is_active == True)
        )
        recipient_user_ids.update((await db.exec(stmt_u)).all())

    elif payload.scope_type == NotificationScope.SECTION:
        sec_id = payload.scope_id
        if not sec_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Section ID is required.")
        
        stmt_u = (
            select(StudentSubjectMap.student_id)
            .join(CourseOffering, StudentSubjectMap.course_offering_id == CourseOffering.id)
            .where(CourseOffering.section_id == sec_id)
        )
        recipient_user_ids.update((await db.exec(stmt_u)).all())

    elif payload.scope_type == NotificationScope.INDIVIDUAL:
        if not payload.scope_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Recipient User ID is required.")
        recipient_user_ids.add(payload.scope_id)

    else:
        # Fallback to active users
        user_stmt = select(User.id).where(User.is_active == True)
        recipient_user_ids.update((await db.exec(user_stmt)).all())

    # Exclude sender from recipients if present
    recipient_user_ids.discard(current_user.id)

    # 5. Persist Notification record
    notification = Notification(
        sender_id=current_user.id,
        title=payload.title.strip(),
        message=payload.message.strip(),
        notification_type=payload.notification_type,
        priority=payload.priority,
        scope_type=payload.scope_type,
        scope_id=payload.scope_id,
        target_role=target_role_filter,
        action_url=payload.action_url,
        expires_at=payload.expires_at,
        created_at=datetime.utcnow(),
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)

    # 6. Schedule Decoupled Background Worker for Non-blocking Fan-Out & Push Delivery
    recipient_list = list(recipient_user_ids)
    if recipient_list:
        background_tasks.add_task(
            dispatch_notification_fanout,
            notification_id=notification.id,
            recipient_user_ids=recipient_list,
            title=notification.title,
            message=notification.message,
            priority=notification.priority.value,
            notification_type=notification.notification_type.value,
            action_url=notification.action_url
        )

    return NotificationBroadcastResponse(
        id=notification.id,
        sender_id=current_user.id,
        sender_name=current_user.full_name,
        sender_email=current_user.email,
        title=notification.title,
        message=notification.message,
        notification_type=notification.notification_type.value,
        priority=notification.priority.value,
        scope_type=notification.scope_type.value,
        scope_id=notification.scope_id,
        target_role=notification.target_role,
        action_url=notification.action_url,
        created_at=notification.created_at,
        recipient_count=len(recipient_list)
    )


# ---------------------------------------------------------------------------
# 2. INBOX & NOTIFICATIONS FOR AUTHENTICATED USER (Bell / History)
# ---------------------------------------------------------------------------

@router.get("/my-notifications", response_model=PaginatedNotificationsResponse, tags=["Notifications"])
async def get_my_notifications(
    unread_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=15, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session)
):
    """Returns paginated notifications addressed to the current logged-in user."""
    base_stmt = (
        select(NotificationRecipient, Notification, User)
        .join(Notification, NotificationRecipient.notification_id == Notification.id)
        .join(User, Notification.sender_id == User.id)
        .where(NotificationRecipient.user_id == current_user.id)
    )

    if unread_only:
        base_stmt = base_stmt.where(NotificationRecipient.is_read == False)

    # Total count
    count_stmt = select(func.count(NotificationRecipient.id)).where(NotificationRecipient.user_id == current_user.id)
    if unread_only:
        count_stmt = count_stmt.where(NotificationRecipient.is_read == False)
    total = (await db.exec(count_stmt)).one()

    # Unread count
    unread_stmt = select(func.count(NotificationRecipient.id)).where(
        NotificationRecipient.user_id == current_user.id,
        NotificationRecipient.is_read == False
    )
    unread_count = (await db.exec(unread_stmt)).one()

    # Fetch page
    skip = (page - 1) * size
    stmt = base_stmt.order_by(Notification.created_at.desc()).offset(skip).limit(size)
    rows = (await db.exec(stmt)).all()

    items = []
    for recipient, notif, sender in rows:
        items.append(NotificationRecipientItem(
            id=recipient.id,
            notification_id=notif.id,
            title=notif.title,
            message=notif.message,
            notification_type=notif.notification_type.value,
            priority=notif.priority.value,
            sender_name=sender.full_name,
            action_url=notif.action_url,
            is_read=recipient.is_read,
            read_at=recipient.read_at,
            created_at=notif.created_at
        ))

    return PaginatedNotificationsResponse(
        items=items,
        total=total,
        unread_count=unread_count,
        page=page,
        size=size
    )


@router.get("/unread-count", response_model=UnreadCountResponse, tags=["Notifications"])
async def get_unread_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session)
):
    """Ultra-lightweight endpoint for top-bar notification bell badge counter."""
    stmt = select(func.count(NotificationRecipient.id)).where(
        NotificationRecipient.user_id == current_user.id,
        NotificationRecipient.is_read == False
    )
    count = (await db.exec(stmt)).one()
    return UnreadCountResponse(unread_count=count)


@router.patch("/{recipient_id}/read", status_code=status.HTTP_200_OK, tags=["Notifications"])
async def mark_as_read(
    recipient_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session)
):
    """Marks a single notification as read."""
    stmt = select(NotificationRecipient).where(
        NotificationRecipient.id == recipient_id,
        NotificationRecipient.user_id == current_user.id
    )
    rec = (await db.exec(stmt)).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Notification item not found.")

    if not rec.is_read:
        rec.is_read = True
        rec.read_at = datetime.utcnow()
        db.add(rec)
        await db.commit()

    return {"status": "success", "message": "Notification marked as read."}


@router.post("/mark-all-read", status_code=status.HTTP_200_OK, tags=["Notifications"])
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session)
):
    """Marks all unread notifications as read for current user."""
    stmt = select(NotificationRecipient).where(
        NotificationRecipient.user_id == current_user.id,
        NotificationRecipient.is_read == False
    )
    unread_items = (await db.exec(stmt)).all()
    now = datetime.utcnow()
    for item in unread_items:
        item.is_read = True
        item.read_at = now
        db.add(item)
    await db.commit()

    return {"status": "success", "marked_count": len(unread_items)}


# ---------------------------------------------------------------------------
# 3. SENT BROADCAST HISTORY (For Admin & Authorities)
# ---------------------------------------------------------------------------

@router.get("/broadcasts", tags=["Notifications"])
async def get_broadcast_history(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    search: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session)
):
    """Lists dispatched broadcasts with recipient reach stats."""
    stmt_roles = select(RoleAssignment.role).where(RoleAssignment.user_id == current_user.id)
    roles = (await db.exec(stmt_roles)).all()
    allowed_roles = {RoleEnum.ADMIN, RoleEnum.DEAN, RoleEnum.HOD, RoleEnum.COORDINATOR}
    if not any(r in allowed_roles for r in roles):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

    base_stmt = select(Notification, User).join(User, Notification.sender_id == User.id)

    # Check if admin; if not, only show broadcasts sent by current user
    if RoleEnum.ADMIN not in roles:
        base_stmt = base_stmt.where(Notification.sender_id == current_user.id)

    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        base_stmt = base_stmt.where(
            func.lower(Notification.title).like(term) |
            func.lower(Notification.message).like(term)
        )

    count_subq = base_stmt.subquery()
    total = (await db.exec(select(func.count()).select_from(count_subq))).one()

    stmt = base_stmt.order_by(Notification.created_at.desc()).offset((page - 1) * size).limit(size)
    rows = (await db.exec(stmt)).all()

    items = []
    for notif, sender in rows:
        # Count recipients
        ct_stmt = select(func.count(NotificationRecipient.id)).where(NotificationRecipient.notification_id == notif.id)
        rec_ct = (await db.exec(ct_stmt)).one()

        items.append({
            "id": notif.id,
            "sender_id": notif.sender_id,
            "sender_name": sender.full_name,
            "sender_email": sender.email,
            "title": notif.title,
            "message": notif.message,
            "notification_type": notif.notification_type.value,
            "priority": notif.priority.value,
            "scope_type": notif.scope_type.value,
            "scope_id": notif.scope_id,
            "target_role": notif.target_role,
            "action_url": notif.action_url,
            "created_at": notif.created_at.isoformat(),
            "recipient_count": rec_ct
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size
    }


# ---------------------------------------------------------------------------
# 4. MOBILE FCM DEVICE TOKEN REGISTRATION
# ---------------------------------------------------------------------------

@router.post("/devices/register", status_code=status.HTTP_200_OK, tags=["Notifications"])
async def register_device_token(
    payload: DeviceTokenRegisterRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session)
):
    """Registers or updates Firebase FCM device registration token for push notifications."""
    clean_token = payload.fcm_token.strip()
    if not clean_token:
        raise HTTPException(status_code=400, detail="FCM token cannot be empty.")

    stmt = select(UserDeviceToken).where(
        UserDeviceToken.user_id == current_user.id,
        UserDeviceToken.fcm_token == clean_token
    )
    token_rec = (await db.exec(stmt)).first()

    now = datetime.utcnow()
    if token_rec:
        token_rec.is_active = True
        token_rec.updated_at = now
        token_rec.device_type = payload.device_type
        db.add(token_rec)
    else:
        new_token = UserDeviceToken(
            user_id=current_user.id,
            fcm_token=clean_token,
            device_type=payload.device_type,
            is_active=True,
            created_at=now,
            updated_at=now
        )
        db.add(new_token)

    # Also keep User.fcm_token synced for backward compatibility
    current_user.fcm_token = clean_token
    db.add(current_user)

    await db.commit()
    return {"status": "success", "message": "FCM device token registered successfully."}


# ---------------------------------------------------------------------------
# 5. ADMIN SETTINGS (Toggle Coordinator / Teacher Broadcast Permissions)
# ---------------------------------------------------------------------------

@router.get("/settings", response_model=NotificationSettingsResponse, tags=["Notifications"])
async def get_notification_settings(
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_session)
):
    """Retrieves broadcast permission settings."""
    coord_val = await get_or_create_setting(
        db, "allow_coordinator_broadcast", "true", "Allow coordinators to send batch broadcasts"
    )
    teacher_val = await get_or_create_setting(
        db, "allow_teacher_broadcast", "false", "Allow teachers to send broadcasts"
    )
    return NotificationSettingsResponse(
        allow_coordinator_broadcast=(coord_val.lower() == "true"),
        allow_teacher_broadcast=(teacher_val.lower() == "true")
    )


@router.patch("/settings", response_model=NotificationSettingsResponse, tags=["Notifications"])
async def update_notification_settings(
    payload: NotificationSettingsUpdate,
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_session)
):
    """Updates broadcast permission settings (Admin Only)."""
    if payload.allow_coordinator_broadcast is not None:
        stmt = select(NotificationSetting).where(NotificationSetting.key == "allow_coordinator_broadcast")
        s = (await db.exec(stmt)).first()
        val = "true" if payload.allow_coordinator_broadcast else "false"
        if s:
            s.value = val
            s.updated_at = datetime.utcnow()
            db.add(s)
        else:
            db.add(NotificationSetting(key="allow_coordinator_broadcast", value=val))

    if payload.allow_teacher_broadcast is not None:
        stmt = select(NotificationSetting).where(NotificationSetting.key == "allow_teacher_broadcast")
        s = (await db.exec(stmt)).first()
        val = "true" if payload.allow_teacher_broadcast else "false"
        if s:
            s.value = val
            s.updated_at = datetime.utcnow()
            db.add(s)
        else:
            db.add(NotificationSetting(key="allow_teacher_broadcast", value=val))

    await db.commit()

    coord_val = await get_or_create_setting(db, "allow_coordinator_broadcast", "true")
    teacher_val = await get_or_create_setting(db, "allow_teacher_broadcast", "false")

    return NotificationSettingsResponse(
        allow_coordinator_broadcast=(coord_val.lower() == "true"),
        allow_teacher_broadcast=(teacher_val.lower() == "true")
    )
