import asyncio
from sqlalchemy import text
from app.core.database import engine

async def clean_db():
    async with engine.begin() as conn:
        print("Dropping residual ENUMs...")
        # Drop the enums if they exist, cascading the deletion to anything that might be stuck
        await conn.execute(text("DROP TYPE IF EXISTS roleenum CASCADE;"))
        await conn.execute(text("DROP TYPE IF EXISTS attendancestatus CASCADE;"))
        await conn.execute(text("DROP TYPE IF EXISTS leavestatus CASCADE;"))
        print("✅ Database is completely clean!")

if __name__ == "__main__":
    asyncio.run(clean_db())