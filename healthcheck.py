"""Docker Healthcheck Script for Attendance Management Backend.

Probes the FastAPI /health endpoint using Python's standard library only (urllib.request).
Designed for production Docker / Coolify deployments on slim images without curl or wget.

Exit codes:
- 0: Container is healthy (HTTP 200 received within timeout)
- 1: Container is unhealthy (request failed, timed out, or returned non-200)
"""

import sys
import urllib.request
import urllib.error

HEALTH_URL = "http://127.0.0.1:8000/health"
TIMEOUT_SECONDS = 4


def check_health() -> int:
    try:
        req = urllib.request.Request(
            url=HEALTH_URL,
            headers={"User-Agent": "Docker-Healthcheck/1.0"}
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", None) or response.getcode()
            if status == 200:
                return 0
            return 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(check_health())
