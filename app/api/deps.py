from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from pydantic import ValidationError
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_session
from app.models.user import User, RoleEnum
from app.core.rbac import AllowRoles

# This creates the simple "Paste Token" box in Swagger
token_auth_scheme = HTTPBearer()

def get_token_payload(credentials: HTTPAuthorizationCredentials = Depends(token_auth_scheme)) -> dict:
    # Extract the actual raw token string from the credentials object
    token = credentials.credentials
    
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        return payload
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not validate credentials or token expired",
        )

# Move get_current_user ABOVE the role guards so they can use it
async def get_current_user(
    payload: dict = Depends(get_token_payload), 
    db: AsyncSession = Depends(get_session)
) -> User:
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    # CRITICAL FIX: Eager load role_assignments to prevent async MissingGreenlet crashes
    statement = select(User).where(User.id == int(user_id)).options(selectinload(User.role_assignments))
    user = (await db.exec(statement)).first()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    
    return user


# --- THE ROLE GUARDS ---

# 1. Define the internal checkers that execute the DB role validation
check_student = AllowRoles(get_current_user, RoleEnum.STUDENT)
check_teacher = AllowRoles(get_current_user, RoleEnum.TEACHER, RoleEnum.COORDINATOR, RoleEnum.HOD, RoleEnum.ADMIN)
check_coordinator = AllowRoles(get_current_user, RoleEnum.COORDINATOR, RoleEnum.HOD, RoleEnum.ADMIN)
check_hod = AllowRoles(get_current_user, RoleEnum.HOD, RoleEnum.ADMIN)
check_admin = AllowRoles(get_current_user, RoleEnum.ADMIN)

# 2. Expose the dependencies to return the User ID (keeping your existing endpoints happy)
async def require_student(user: User = Depends(check_student)) -> int:
    return user.id

async def require_teacher(user: User = Depends(check_teacher)) -> int:
    return user.id

async def require_coordinator(user: User = Depends(check_coordinator)) -> int:
    return user.id

async def require_hod(user: User = Depends(check_hod)) -> int:
    return user.id

async def require_admin(user: User = Depends(check_admin)) -> int:
    return user.id