import os
import asyncio
import logging
import re
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
import httpx

from app.core.config import settings

logger = logging.getLogger("attendance.notifications")


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

    @property
    def is_configured(self) -> bool:
        return self._initialized

    def _init_firebase(self):
        try:
            import json
            import firebase_admin
            from firebase_admin import credentials

            cred_json = getattr(settings, "FIREBASE_CREDENTIALS_JSON", None) or os.getenv("FIREBASE_CREDENTIALS_JSON")
            cred_path = getattr(settings, "FIREBASE_CREDENTIALS_PATH", None) or os.getenv("FIREBASE_CREDENTIALS_PATH")

            cred = None
            if cred_json and cred_json.strip():
                cert_dict = json.loads(cred_json)
                cred = credentials.Certificate(cert_dict)
            elif cred_path and os.path.exists(cred_path):
                cred = credentials.Certificate(cred_path)

            if cred:
                if not firebase_admin._apps:
                    self._app = firebase_admin.initialize_app(cred)
                else:
                    self._app = firebase_admin.get_app()
                self._initialized = True
                logger.info("Firebase Admin initialized successfully.")
            else:
                logger.error("FCM is NOT configured! Neither FIREBASE_CREDENTIALS_JSON nor valid FIREBASE_CREDENTIALS_PATH found.")
        except Exception as exc:
            logger.error(f"Firebase Admin initialization failed: {exc}")

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
            logger.error(f"[FCM Not Configured] Cannot deliver push for {len(tokens)} device tokens: '{title}'")
            return {"success_count": 0, "failure_count": len(tokens), "invalid_tokens": []}

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
                        if isinstance(res.exception, messaging.UnregisteredError):
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

    @property
    def is_configured(self) -> bool:
        return self.fcm_provider.is_configured

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
