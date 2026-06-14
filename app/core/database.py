from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine # Removed AsyncSession from here
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession # Added SQLModel's AsyncSession here
from app.core.config import settings

# Create async engine
engine = create_async_engine(
    settings.DATABASE_URL, 
    echo=False, # Set to False in production
    future=True
)

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session