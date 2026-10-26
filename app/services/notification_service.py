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

class BaseNotificationProvider(ABC):
    @abstractmethod
    async def send(self, topic_or_token: str, title: str, message: str, priority: str = "default", tags: Optional[List[str]] = None, extra_data: Optional[Dict[str, Any]] = None) -> bool:
        pass

class NtfyNotificationProvider(BaseNotificationProvider):
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def send(self, topic_or_token: str, title: str, message: str, priority: str = "default", tags: Optional[List[str]] = None, extra_data: Optional[Dict[str, Any]] = None) -> bool:
        url = f"{self.base_url}/{topic_or_token}"
        headers = {
            "Title": title,
            "Priority": priority,
        }
        if tags:
            headers["Tags"] = ",".join(tags)

        # Attach custom JSON header for client-side deep linking
        if extra_data:
            import json
            headers["X-Data"] = json.dumps(extra_data)

        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                for attempt in range(3):
                    resp = await client.post(url, content=message.encode("utf-8"), headers=headers)
                    if resp.status_code in (200, 201, 204):
                        return True
                    elif resp.status_code == 429:
                        # Rate limit reached: back off briefly and retry
                        backoff = 0.4 * (attempt + 1)
                        logger.info(f"ntfy 429 rate limit hit for {topic_or_token}, retrying in {backoff}s...")
                        await asyncio.sleep(backoff)
                        continue
                    else:
                        logger.warning(f"ntfy server returned {resp.status_code} for topic {topic_or_token}: {resp.text}")
                        return False
                return False
        except Exception as exc:
            logger.warning(f"Failed to deliver notification to {topic_or_token} via {url}: {exc}")
            return False

class NotificationService:
    def __init__(self):
        provider_name = getattr(settings, "NOTIFICATION_PROVIDER", "ntfy").lower()
        if provider_name == "ntfy":
            server_url = getattr(settings, "NTFY_SERVER_URL", "http://192.168.1.7:8095")
            self.provider: BaseNotificationProvider = NtfyNotificationProvider(base_url=server_url)
        else:
            server_url = getattr(settings, "NTFY_SERVER_URL", "http://192.168.1.7:8095")
            self.provider = NtfyNotificationProvider(base_url=server_url)

    async def send_attendance_alert(
        self,
        roll_number: str,
        student_name: str,
        subject_name: str,
        subject_code: str,
        status: str,
        date_str: str
    ) -> bool:
        if not roll_number:
            return False

        topic = normalize_student_topic(roll_number)
        status_clean = status.strip().capitalize()

        if status_clean in ("Absent", "A"):
            title = f"Attendance: Absent ({subject_code})"
            message = f"You were marked Absent for {subject_name} on {date_str}."
            priority = "high"
            tags = ["x", "warning"]
        else:
            title = f"Attendance: {status_clean} ({subject_code})"
            message = f"You were marked {status_clean} for {subject_name} on {date_str}."
            priority = "default"
            tags = ["white_check_mark", "school"]

        extra_data = {
            "type": "attendance_update",
            "roll_number": roll_number,
            "subject_code": subject_code,
            "status": status_clean,
            "date": date_str
        }

        return await self.provider.send(
            topic_or_token=topic,
            title=title,
            message=message,
            priority=priority,
            tags=tags,
            extra_data=extra_data
        )

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

notification_service = NotificationService()
