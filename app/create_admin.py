import asyncio
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from passlib.context import CryptContext

from app.core.database import engine
from app.core.config import settings
from app.models.user import User, RoleEnum, RoleAssignment, ScopeTypeEnum

# Standard bcrypt password hasher
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

async def init_admin():
    async with AsyncSession(engine, expire_on_commit=False) as session:
        print("Checking for existing admin user...")
        
        # 1. Check if the admin already exists
        statement = select(User).where(User.email == settings.ADMIN_EMAIL)
        result = await session.exec(statement)
        existing_admin = result.first()
        
        if existing_admin:
            print(f"Admin user '{settings.ADMIN_EMAIL}' already exists. Exiting.")
            return

        # 2. Hash the password and create the user without a direct role
        print(f"Creating admin user for '{settings.ADMIN_EMAIL}'...")
        hashed_password = pwd_context.hash(settings.ADMIN_PASSWORD)
        
        admin_user = User(
            email=settings.ADMIN_EMAIL,
            full_name=settings.ADMIN_NAME,
            hashed_password=hashed_password,
            is_active=True
        )
        
        session.add(admin_user)
        await session.commit()
        await session.refresh(admin_user) # Retrieve the generated ID
        
        # 3. Create the Role Assignment (v0.2 Architecture)
        admin_role = RoleAssignment(
            user_id=admin_user.id,
            role=RoleEnum.ADMIN,
            scope_type=ScopeTypeEnum.UNIVERSITY
        )
        
        session.add(admin_role)
        await session.commit()
        
        print("Success: Admin user created!")

if __name__ == "__main__":
    asyncio.run(init_admin())