import hashlib
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import case
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import delete
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import AuthRateLimit


async def enforce_auth_limit(
    db: AsyncSession,
    scope: str,
    identity: str,
    limit: int,
    window_seconds: int = 900,
) -> None:
    """Atomically count an auth attempt in PostgreSQL across all app instances."""
    now = datetime.utcnow()
    key = hashlib.sha256(f"{scope}:{identity}".encode("utf-8")).hexdigest()
    cutoff = now - timedelta(seconds=window_seconds)
    statement = insert(AuthRateLimit).values(
        key=key,
        window_started_at=now,
        attempt_count=1,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[AuthRateLimit.key],
        set_={
            "window_started_at": case(
                (AuthRateLimit.window_started_at <= cutoff, now),
                else_=AuthRateLimit.window_started_at,
            ),
            "attempt_count": case(
                (AuthRateLimit.window_started_at <= cutoff, 1),
                else_=AuthRateLimit.attempt_count + 1,
            ),
        },
    ).returning(AuthRateLimit.attempt_count)
    attempts = (await db.execute(statement)).scalar_one()
    # Opportunistically expire stale identities so arbitrary login names cannot
    # grow the persistent counter table without bound.
    if int(key[:2], 16) == (int(now.timestamp()) // 900) % 256:
        await db.exec(
            delete(AuthRateLimit).where(
                AuthRateLimit.window_started_at < now - timedelta(days=1)
            )
        )
    await db.commit()
    if attempts > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication attempts. Please try again later.",
            headers={"Retry-After": str(window_seconds)},
        )
