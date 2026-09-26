import asyncio
from sqlalchemy import text
from app.core.database import engine

async def reset_schema():
    async with engine.begin() as conn:
        print("Wiping all tables, ENUMs, and Alembic history...")
        await conn.execute(text("DROP SCHEMA public CASCADE;"))
        await conn.execute(text("CREATE SCHEMA public;"))
        await conn.execute(text("GRANT ALL ON SCHEMA public TO postgres;"))
        await conn.execute(text("GRANT ALL ON SCHEMA public TO public;"))
        print("✅ Database is completely clean!")

if __name__ == "__main__":
    asyncio.run(reset_schema())