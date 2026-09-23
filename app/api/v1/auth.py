from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, delete
from sqlalchemy.orm import selectinload
from pydantic import BaseModel

from app.core.database import get_session
from app.core.config import settings
from app.core.security import verify_password, create_access_token, create_refresh_token
from app.models.user import User, RefreshToken
from app.models.auth import TokenResponse, RefreshTokenRequest

router = APIRouter()

class LoginRequest(BaseModel):
    email: str
    password: str

@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_session)
):
    # Fetch User and eagerly load role_assignments to prevent async MissingGreenlet errors
    statement = select(User).options(selectinload(User.role_assignments)).where(User.email == payload.email)
    result = await db.exec(statement)
    user = result.first()

    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    # --- GARBAGE COLLECTION ---
    # Delete all expired or revoked tokens for this specific user to prevent database bloat
    cleanup_stmt = delete(RefreshToken).where(
        (RefreshToken.user_id == user.id) & 
        ((RefreshToken.is_revoked == True) | (RefreshToken.expires_at < datetime.utcnow()))
    )
    await db.exec(cleanup_stmt)
    # --------------------------

    # Extract the primary role for the JWT payload
    primary_role = user.role_assignments[0].role.value if user.role_assignments else "USER"
    access_token = create_access_token(subject=user.id, role=primary_role)
    refresh_token_str = create_refresh_token(subject=user.id)

    expires_at = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    db_refresh_token = RefreshToken(
        user_id=user.id,
        token=refresh_token_str,
        expires_at=expires_at
    )
    db.add(db_refresh_token)
    await db.commit()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token_str,
        "token_type": "bearer"
    }

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: RefreshTokenRequest,
    db: AsyncSession = Depends(get_session)
):
    statement = select(RefreshToken).where(RefreshToken.token == request.refresh_token)
    result = await db.exec(statement)
    db_token = result.first()

    if not db_token:
        raise HTTPException(status_code=404, detail="Refresh token not found")
    if db_token.is_revoked:
        raise HTTPException(status_code=401, detail="Refresh token has been revoked")
    if db_token.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Refresh token expired")

    statement_user = select(User).options(selectinload(User.role_assignments)).where(User.id == db_token.user_id)
    result_user = await db.exec(statement_user)
    user = result_user.first()

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User is no longer active")

    db_token.is_revoked = True
    db.add(db_token)

    primary_role = user.role_assignments[0].role.value if user.role_assignments else "USER"
    new_access_token = create_access_token(subject=user.id, role=primary_role)
    new_refresh_token_str = create_refresh_token(subject=user.id)

    expires_at = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    new_db_token = RefreshToken(
        user_id=user.id,
        token=new_refresh_token_str,
        expires_at=expires_at
    )
    db.add(new_db_token)
    await db.commit()

    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token_str,
        "token_type": "bearer"
    }

@router.post("/logout")
async def logout(
    request: RefreshTokenRequest,
    db: AsyncSession = Depends(get_session)
):
    statement = select(RefreshToken).where(RefreshToken.token == request.refresh_token)
    result = await db.exec(statement)
    db_token = result.first()

    if db_token:
        db_token.is_revoked = True
        db.add(db_token)
        await db.commit()
        
    return {"status": "success", "message": "Successfully logged out"}