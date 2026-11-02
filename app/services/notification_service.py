import os
import asyncio
import logging
import re
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
import httpx

from app.core.config import settings

logger = logging.getLogger("attendance.notifications")

def normalize_student_topic(roll_number: str) -> str:
    """Normalize student roll number into a valid, safe ntfy pubsub topic."""
    clean = re.sub(r"[^a-zA-Z0-9_]", "_", roll_number.strip().lower())
    return f"student_{clean}"




class FCMNotificationProvider:
    """
    Firebase Cloud Messaging (FCM) Multicast Provider.
    Dispatches push notifications to Android & iOS mobile devices.
    Safely captures invalid/unregistered tokens for database pruning.
    """
    def __init__(self):
        self._app = None
        self._initialized = False
        self._init_firebase()

    def _init_firebase(self):
        try:
            import firebase_admin
            from firebase_admin import credentials
            cred_path = getattr(settings, "FIREBASE_CREDENTIALS_PATH", None) or os.getenv("FIREBASE_CREDENTIALS_PATH")
            if cred_path and os.path.exists(cred_path):
                if not firebase_admin._apps:
                    cred = credentials.Certificate(cred_path)
                    self._app = firebase_admin.initialize_app(cred)
                else:
                    self._app = firebase_admin.get_app()
                self._initialized = True
                logger.info(f"Firebase Admin initialized successfully from: {cred_path}")
            else:
                logger.info("Firebase credentials path not configured or file absent; FCM provider ready in standby mode.")
        except Exception as exc:
            logger.warning(f"Firebase Admin initialization deferred/failed: {exc}")

    async def send_multicast(
        self,
        tokens: List[str],
        title: str,
        message: str,
        priority: str = "NORMAL",
        extra_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Sends multicast push notifications to device tokens.
        Returns:
            {"success_count": int, "failure_count": int, "invalid_tokens": List[str]}
        """
        if not tokens:
            return {"success_count": 0, "failure_count": 0, "invalid_tokens": []}

        if not self._initialized:
            logger.info(f"[FCM Standby] Prepared push for {len(tokens)} device tokens: '{title}'")
            return {"success_count": len(tokens), "failure_count": 0, "invalid_tokens": []}

        invalid_tokens = []
        success_count = 0
        failure_count = 0

        try:
            from firebase_admin import messaging
            fcm_data = {k: str(v) for k, v in (extra_data or {}).items()}

            BATCH_SIZE = 500
            for i in range(0, len(tokens), BATCH_SIZE):
                batch_tokens = tokens[i:i + BATCH_SIZE]
                fcm_priority = "high" if str(priority).upper() in ("HIGH", "URGENT") else "normal"

                multicast_msg = messaging.MulticastMessage(
                    tokens=batch_tokens,
                    notification=messaging.Notification(
                        title=title,
                        body=message,
                    ),
                    data=fcm_data,
                    android=messaging.AndroidConfig(
                        priority=fcm_priority,
                        notification=messaging.AndroidNotification(
                            channel_id="university_broadcasts",
                            sound="default"
                        )
                    )
                )

                response = await asyncio.to_thread(messaging.send_each_for_multicast, multicast_msg)
                success_count += response.success_count
                failure_count += response.failure_count

                for idx, res in enumerate(response.responses):
                    if not res.success:
                        err = res.exception
                        if err and hasattr(err, "code") and err.code in ("NOT_REGISTERED", "INVALID_ARGUMENT", "UNREGISTERED"):
                            invalid_tokens.append(batch_tokens[idx])

        except Exception as exc:
            logger.warning(f"Error during FCM multicast dispatch: {exc}")
            failure_count += len(tokens)

        return {
            "success_count": success_count,
            "failure_count": failure_count,
            "invalid_tokens": invalid_tokens
        }

class NotificationService:
    def __init__(self):
        self.fcm_provider = FCMNotificationProvider()

    async def send_attendance_alert(
        self,
        roll_number: str,
        student_name: str,
        subject_name: str,
        subject_code: str,
        status: str,
        date_str: str,
        tokens: Optional[List[str]] = None
    ) -> bool:
        if not roll_number:
            return False

        status_clean = status.strip().capitalize()

        if status_clean in ("Absent", "A"):
            title = f"Attendance: Absent ({subject_code})"
            message = f"You were marked Absent for {subject_name} on {date_str}."
            priority = "high"
        else:
            title = f"Attendance: {status_clean} ({subject_code})"
            message = f"You were marked {status_clean} for {subject_name} on {date_str}."
            priority = "default"

        extra_data = {
            "type": "attendance_update",
            "roll_number": roll_number,
            "subject_code": subject_code,
            "status": status_clean,
            "date": date_str
        }

        if tokens:
            await self.fcm_provider.send_multicast(
                tokens=tokens,
                title=title,
                message=message,
                priority=priority,
                extra_data=extra_data
            )
        return True

    async def batch_send_attendance_alerts(self, alerts: List[Dict[str, Any]]):
        """Send notifications to students in paced batches to prevent burst rate-limiting."""
        semaphore = asyncio.Semaphore(3)

        async def paced_dispatch(alert_dict: Dict[str, Any]) -> bool:
            async with semaphore:
                await asyncio.sleep(0.04)  # 40ms stagger between dispatches
                return await self.send_attendance_alert(
                    roll_number=alert_dict.get("roll_number", ""),
                    student_name=alert_dict.get("student_name", ""),
                    subject_name=alert_dict.get("subject_name", ""),
                    subject_code=alert_dict.get("subject_code", ""),
                    status=alert_dict.get("status", "Present"),
                    date_str=alert_dict.get("date_str", "")
                )

        tasks = [paced_dispatch(a) for a in alerts if a.get("roll_number")]
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            succeeded = sum(1 for r in results if r is True)
            logger.info(f"Dispatched {len(tasks)} attendance notifications. Succeeded: {succeeded}/{len(tasks)}")

    async def send_class_postponed_alert(
        self,
        roll_number: str,
        student_name: str,
        subject_name: str,
        subject_code: str,
        date_str: str,
        reason: str
    ) -> bool:
        if not roll_number:
            return False

        topic = normalize_student_topic(roll_number)
        title = f"Class Postponed: {subject_code}"
        message = f"Your {subject_name} class on {date_str} has been postponed. Reason: {reason}."
        priority = "default"
        tags = ["calendar", "information_source"]

        extra_data = {
            "type": "class_postponed",
            "roll_number": roll_number,
            "subject_code": subject_code,
            "date": date_str,
            "reason": reason
        }

        return True

    async def batch_send_class_postponed_alerts(self, alerts: List[Dict[str, Any]]):
        """Send class postponed notifications in paced batches."""
        semaphore = asyncio.Semaphore(3)

        async def paced_dispatch(alert_dict: Dict[str, Any]) -> bool:
            async with semaphore:
                await asyncio.sleep(0.04)
                return await self.send_class_postponed_alert(
                    roll_number=alert_dict.get("roll_number", ""),
                    student_name=alert_dict.get("student_name", ""),
                    subject_name=alert_dict.get("subject_name", ""),
                    subject_code=alert_dict.get("subject_code", ""),
                    date_str=alert_dict.get("date_str", ""),
                    reason=alert_dict.get("reason", "Faculty on Leave")
                )

        tasks = [paced_dispatch(a) for a in alerts if a.get("roll_number")]
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            succeeded = sum(1 for r in results if r is True)
            logger.info(f"Dispatched {len(tasks)} postponement notifications. Succeeded: {succeeded}/{len(tasks)}")


    async def send_broadcast_push(
        self,
        tokens: List[str],
        title: str,
        message: str,
        priority: str = "NORMAL",
        extra_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Dispatches mobile push notifications to a list of device tokens."""
        return await self.fcm_provider.send_multicast(
            tokens=tokens,
            title=title,
            message=message,
            priority=priority,
            extra_data=extra_data
        )

notification_service = NotificationService()
