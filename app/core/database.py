from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine 
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession 
from app.core.config import settings

# Create async engine with connection pooling safety checks
engine = create_async_engine(
    settings.DATABASE_URL, 
    echo=False,              # Set to False in production
    future=True,
    pool_pre_ping=True,      # <-- Tests connection liveness before checking it out
    pool_recycle=3600        # <-- Recycles connections older than 1 hour to prevent drops
)

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session