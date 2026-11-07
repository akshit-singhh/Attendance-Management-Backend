import pytest
from datetime import date, datetime
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import BackgroundTasks
from app.main import app
from app.api.deps import get_current_user
from app.core.database import get_session
from app.models.user import User, RoleEnum, ScopeTypeEnum, RoleAssignment
from app.models.academic import CourseOffering, Subject
from app.models.notification import Notification, NotificationRecipient
from app.schemas.attendance import OfflineSyncPayload, SyncRecordItem
from app.api.v1.mobile.attendance import sync_offline_attendance


@pytest.mark.anyio
async def test_scope_authorization_and_validation():
    # Setup HOD user for Department 10
    hod_user = User(id=1, email="hod@test.com", full_name="Test HOD", is_active=True)
    hod_assignment = RoleAssignment(
        id=1,
        user_id=1,
        role=RoleEnum.HOD,
        scope_type=ScopeTypeEnum.DEPARTMENT,
        scope_id=10
    )

    mock_db = AsyncMock()
    async def mock_exec(stmt):
        m = MagicMock()
        m.first.return_value = None
        m.all.return_value = [hod_assignment]
        return m

    mock_db.exec.side_effect = mock_exec

    app.dependency_overrides[get_current_user] = lambda: hod_user
    app.dependency_overrides[get_session] = lambda: mock_db

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. HOD sending to another department (ID 99) -> 403 Forbidden
            res = await client.post(
                "/api/v1/notifications/broadcast",
                json={
                    "title": "Dept Notice",
                    "message": "Hello Dept 99",
                    "scope_type": "DEPARTMENT",
                    "scope_id": 99,
                    "target_role": "ALL"
                }
            )
            assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"

            # 2. Scope PROGRAM (unsupported) -> 400 Bad Request
            res_program = await client.post(
                "/api/v1/notifications/broadcast",
                json={
                    "title": "Program Notice",
                    "message": "Hello Program",
                    "scope_type": "PROGRAM",
                    "scope_id": 1,
                    "target_role": "ALL"
                }
            )
            assert res_program.status_code == 400, f"Expected 400, got {res_program.status_code}: {res_program.text}"

            # 3. Scope SCHOOL (unsupported) -> 400 Bad Request
            res_school = await client.post(
                "/api/v1/notifications/broadcast",
                json={
                    "title": "School Notice",
                    "message": "Hello School",
                    "scope_type": "SCHOOL",
                    "scope_id": 1,
                    "target_role": "ALL"
                }
            )
            assert res_school.status_code == 400, f"Expected 400, got {res_school.status_code}: {res_school.text}"

            # 4. Invalid target_role -> 400 Bad Request
            res_role = await client.post(
                "/api/v1/notifications/broadcast",
                json={
                    "title": "Invalid Role",
                    "message": "Test",
                    "scope_type": "DEPARTMENT",
                    "scope_id": 10,
                    "target_role": "INVALID_ROLE"
                }
            )
            assert res_role.status_code == 400, f"Expected 400, got {res_role.status_code}: {res_role.text}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_absent_student_notification_and_fcm_resilience():
    # Verify that marking a student absent creates exactly one notification recipient
    # and the API returns 200 even when FCM raises an exception.
    added_objects = []

    mock_db = AsyncMock()
    offering = CourseOffering(id=1, teacher_id=10, term_id=1, section_id=5, subject_id=100)
    subject = Subject(id=100, name="Database Systems", course_code="CS401")

    async def mock_get(model, ident):
        if model == CourseOffering:
            return offering
        if model == Subject:
            return subject
        return None

    mock_db.get.side_effect = mock_get

    async def mock_exec(stmt):
        m = MagicMock()
        m.first.return_value = None
        # Enrolled student query returns student 42
        m.all.return_value = [42]
        return m

    mock_db.exec.side_effect = mock_exec

    def mock_add(obj):
        added_objects.append(obj)
        if hasattr(obj, "id") and obj.id is None:
            obj.id = 123

    def mock_add_all(objs):
        added_objects.extend(objs)

    async def mock_refresh(obj):
        if hasattr(obj, "id") and obj.id is None:
            obj.id = 123

    mock_db.add = MagicMock(side_effect=mock_add)
    mock_db.add_all = MagicMock(side_effect=mock_add_all)
    mock_db.refresh.side_effect = mock_refresh

    bg_tasks = BackgroundTasks()
    payload = OfflineSyncPayload(
        session_uuid="test-session-uuid",
        course_offering_id=1,
        date=date(2026, 11, 4),
        records=[
            SyncRecordItem(
                record_uuid="rec-1",
                student_id=42,
                status="ABSENT",
                captured_at=datetime.utcnow()
            )
        ]
    )

    with patch("app.services.notification_service.notification_service.send_broadcast_push", side_effect=RuntimeError("FCM Hard Crash")):
        response = await sync_offline_attendance(
            payload=payload,
            background_tasks=bg_tasks,
            teacher_id=10,
            db=mock_db
        )

    # API returns success 200
    assert response["status"] == "success"

    # Verify a Notification was created for the absent student
    created_notifications = [o for o in added_objects if isinstance(o, Notification)]
    assert len(created_notifications) == 1
    assert created_notifications[0].scope_type in ("INDIVIDUAL", "NotificationScope.INDIVIDUAL")
    assert created_notifications[0].scope_id == 42

    # Now execute the scheduled background fanout worker with the mock session maker
    # to assert that exactly one NotificationRecipient row is created
    # and FCM exception is caught safely
    from app.api.v1.notifications import dispatch_notification_fanout
    recipient_rows = []

    class MockAsyncSessionMaker:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            session = AsyncMock()
            notif_row = Notification(
                id=1,
                title="Attendance: Absent",
                message="You were marked Absent",
                priority="HIGH",
                notification_type="ATTENDANCE_ALERT",
                status="PENDING",
                processed_count=0
            )
            async def sess_get(model, ident):
                if model == Notification:
                    return notif_row
                return None
            session.get.side_effect = sess_get

            async def sess_exec(stmt):
                m = MagicMock()
                # Existing recipient IDs -> empty
                m.all.return_value = []
                return m
            session.exec.side_effect = sess_exec

            def sess_add(obj):
                recipient_rows.append(obj)
            def sess_add_all(objs):
                recipient_rows.extend(objs)
            session.add = MagicMock(side_effect=sess_add)
            session.add_all = MagicMock(side_effect=sess_add_all)
            return session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return False

    with patch("app.api.v1.notifications.sessionmaker", return_value=MockAsyncSessionMaker):
        with patch("app.services.notification_service.notification_service.send_broadcast_push", side_effect=RuntimeError("FCM Hard Crash")):
            # Running the background fanout
            for task in bg_tasks.tasks:
                await task()

    recipients = [r for r in recipient_rows if isinstance(r, NotificationRecipient)]
    assert len(recipients) == 1, f"Expected 1 NotificationRecipient, got {len(recipients)}"
    assert recipients[0].user_id == 42


@pytest.mark.anyio
async def test_health_and_device_unregister():
    # 1. Health check includes fcm_configured
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert "fcm_configured" in data
        assert isinstance(data["fcm_configured"], bool)

        # 2. Test device unregister
        student = User(id=42, email="student@test.com", full_name="Student Test", is_active=True, fcm_token="tok123")
        mock_db = AsyncMock()
        mock_token = MagicMock()
        mock_token.is_active = True
        async def mock_exec(stmt):
            m = MagicMock()
            m.all.return_value = [mock_token]
            return m
        mock_db.exec.side_effect = mock_exec
        mock_db.add = MagicMock()

        app.dependency_overrides[get_current_user] = lambda: student
        app.dependency_overrides[get_session] = lambda: mock_db
        try:
            unreg_res = await client.post("/api/v1/notifications/devices/unregister", json={"fcm_token": "tok123"})
            assert unreg_res.status_code == 200
            assert mock_token.is_active is False
            assert student.fcm_token is None
        finally:
            app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_my_notifications_string_attributes():
    user = User(id=42, email="student@test.com", full_name="Student Test", is_active=True)
    sender = User(id=1, email="admin@test.com", full_name="Admin Test", is_active=True)
    notif = Notification(
        id=10,
        sender_id=1,
        title="Test Notification",
        message="Hello World",
        notification_type="ATTENDANCE_ALERT",
        priority="HIGH",
        scope_type="INDIVIDUAL",
        scope_id=42
    )
    recip = NotificationRecipient(
        id=100,
        notification_id=10,
        user_id=42,
        is_read=False
    )

    mock_db = AsyncMock()
    async def mock_exec(stmt):
        m = MagicMock()
        m.one.return_value = 1
        m.all.return_value = [(recip, notif, sender)]
        return m

    mock_db.exec.side_effect = mock_exec

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session] = lambda: mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/api/v1/notifications/my-notifications?page=1&size=50&unread_only=false")
            assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
            data = res.json()
            assert data["total"] == 1
            assert len(data["items"]) == 1
            item = data["items"][0]
            assert item["notification_type"] == "ATTENDANCE_ALERT"
            assert item["priority"] == "HIGH"
            assert item["title"] == "Test Notification"
    finally:
        app.dependency_overrides.clear()

