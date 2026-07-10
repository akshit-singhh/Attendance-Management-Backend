import asyncio
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from passlib.context import CryptContext

from app.core.database import engine
from app.core.config import settings
from app.models.user import User, RoleEnum

# Standard bcrypt password hasher
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

async def init_admin():
    async with AsyncSession(engine) as session:
        print("Checking for existing admin user...")
        
        # 1. Check if the admin already exists
        statement = select(User).where(User.email == settings.ADMIN_EMAIL)
        result = await session.exec(statement)
        existing_admin = result.first()
        
        if existing_admin:
            print(f"Admin user '{settings.ADMIN_EMAIL}' already exists. Exiting.")
            return

        # 2. Hash the password and create the user
        print(f"Creating admin user for '{settings.ADMIN_EMAIL}'...")
        hashed_password = pwd_context.hash(settings.ADMIN_PASSWORD)
        
        admin_user = User(
            email=settings.ADMIN_EMAIL,
            full_name=settings.ADMIN_NAME,
            hashed_password=hashed_password,
            role=RoleEnum.ADMIN,
            is_active=True
        )
        
        # 3. Save to database
        session.add(admin_user)
        await session.commit()
        print("Success: Admin user created!")

if __name__ == "__main__":
    asyncio.run(init_admin())