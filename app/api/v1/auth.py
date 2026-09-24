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
from app.api.deps import get_current_user
from app.core.security import get_password_hash


import random
from fastapi import BackgroundTasks
from app.models.user import PasswordResetOTP
from app.services.email_service import send_otp_email

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
        "token_type": "bearer",
        "must_change_password": user.must_change_password,
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

# --- PASSWORD MANAGEMENT ---

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

@router.post("/change-password", status_code=status.HTTP_200_OK)
async def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session)
):
    if not verify_password(payload.old_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect current password.")
        
    current_user.hashed_password = get_password_hash(payload.new_password)
    current_user.must_change_password = False # <-- ADD THIS LINE
    
    db.add(current_user)
    
    cleanup_stmt = delete(RefreshToken).where(RefreshToken.user_id == current_user.id)
    await db.exec(cleanup_stmt)
    await db.commit()
    
    return {"status": "success", "message": "Password updated successfully."}

# --- OTP RESET SCHEMAS ---

class ForgotPasswordRequest(BaseModel):
    email: str

class ResetPasswordRequest(BaseModel):
    email: str
    otp: str
    new_password: str

# --- OTP RESET ENDPOINTS ---

@router.post("/forgot-password", status_code=status.HTTP_200_OK)
async def forgot_password(
    payload: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session)
):
    # 1. Verify user exists
    user_stmt = select(User).where(User.email == payload.email)
    user = (await db.exec(user_stmt)).first()
    
    # We return success even if the user doesn't exist to prevent email enumeration attacks
    if not user or not user.is_active:
        return {"status": "success", "message": "If that account exists, an OTP has been sent."}

    # 2. Generate 6-digit OTP and set 5-minute expiry
    otp_code = str(random.randint(100000, 999999))
    expires_at = datetime.utcnow() + timedelta(minutes=5)

    # 3. Clear any existing OTPs for this email to prevent code overlap
    await db.exec(delete(PasswordResetOTP).where(PasswordResetOTP.email == payload.email))
    
    # 4. Save new OTP
    db_otp = PasswordResetOTP(email=payload.email, otp_code=otp_code, expires_at=expires_at)
    db.add(db_otp)
    await db.commit()

    # 5. Dispatch email in the background to prevent blocking the HTTP response[cite: 5]
    background_tasks.add_task(send_otp_email, to_email=payload.email, otp=otp_code)

    return {"status": "success", "message": "If that account exists, an OTP has been sent."}

@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_session)
):
    # 1. Find the OTP record
    otp_stmt = select(PasswordResetOTP).where(
        (PasswordResetOTP.email == payload.email) & 
        (PasswordResetOTP.otp_code == payload.otp)
    )
    db_otp = (await db.exec(otp_stmt)).first()

    if not db_otp:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP.")

    if db_otp.expires_at < datetime.utcnow():
        await db.exec(delete(PasswordResetOTP).where(PasswordResetOTP.id == db_otp.id))
        await db.commit()
        raise HTTPException(status_code=400, detail="OTP has expired. Please request a new one.")

    # 2. Update the User's Password
    user = (await db.exec(select(User).where(User.email == payload.email))).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    user.hashed_password = get_password_hash(payload.new_password)
    user.must_change_password = False
    db.add(user)

    # 3. Wipe OTP and all existing active sessions
    await db.exec(delete(PasswordResetOTP).where(PasswordResetOTP.id == db_otp.id))
    await db.exec(delete(RefreshToken).where(RefreshToken.user_id == user.id))
    
    await db.commit()

    return {"status": "success", "message": "Password has been successfully reset. Please log in."}