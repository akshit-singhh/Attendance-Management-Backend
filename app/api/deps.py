from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from pydantic import ValidationError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.models.user import User, RoleEnum

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

# --- THE ROLE GUARDS ---

def require_student(payload: dict = Depends(get_token_payload)) -> int:
    if payload.get("role") != RoleEnum.STUDENT:
        raise HTTPException(status_code=403, detail="Student access required")
    return int(payload.get("sub"))

def require_teacher(payload: dict = Depends(get_token_payload)) -> int:
    # Coordinators and above can also act as teachers
    allowed_roles = [RoleEnum.TEACHER, RoleEnum.COORDINATOR, RoleEnum.HOD, RoleEnum.ADMIN]
    if payload.get("role") not in allowed_roles:
        raise HTTPException(status_code=403, detail="Teacher access required")
    return int(payload.get("sub"))

def require_coordinator(payload: dict = Depends(get_token_payload)) -> int:
    allowed_roles = [RoleEnum.COORDINATOR, RoleEnum.HOD, RoleEnum.ADMIN]
    if payload.get("role") not in allowed_roles:
        raise HTTPException(status_code=403, detail="Coordinator access required")
    return int(payload.get("sub"))

def require_hod(payload: dict = Depends(get_token_payload)) -> int:
    allowed_roles = [RoleEnum.HOD, RoleEnum.ADMIN]
    if payload.get("role") not in allowed_roles:
        raise HTTPException(status_code=403, detail="HOD access required")
    return int(payload.get("sub"))

def require_admin(payload: dict = Depends(get_token_payload)) -> int:
    if payload.get("role") != RoleEnum.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return int(payload.get("sub"))

# Optional: If you actually need the full User object from the DB
async def get_current_user(
    payload: dict = Depends(get_token_payload), 
    db: AsyncSession = Depends(get_session)
) -> User:
    user_id = payload.get("sub")
    user = await db.get(User, int(user_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return user