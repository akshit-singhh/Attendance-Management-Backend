import random
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, delete
from sqlalchemy.orm import selectinload

from app.core.database import get_session
from app.core.config import settings
from app.core.security import verify_password, create_access_token, create_refresh_token, get_password_hash
from app.models.user import User, RefreshToken, PasswordResetOTP
from app.api.deps import get_current_user
from app.services.email_service import send_otp_email

# Import all schemas from the models file
from app.models.auth import (
    LoginRequest,
    TokenResponse,
    RefreshTokenRequest,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    SwitchRoleRequest
)


router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_session)
):
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

    cleanup_stmt = delete(RefreshToken).where(
        (RefreshToken.user_id == user.id) &
        (
            (RefreshToken.is_revoked == True) |
            (RefreshToken.expires_at < datetime.now(timezone.utc))
        )
    )
    await db.exec(cleanup_stmt)

    # Extract ALL roles to send to the Android app
    available_roles = [
        {
            "role": ra.role.value,
            "scope_type": ra.scope_type.value,
            "scope_id": ra.scope_id
        }
        for ra in user.role_assignments
    ]

    # Extract the primary role for the initial JWT payload
    primary_assignment = user.role_assignments[0] if user.role_assignments else None
    primary_role = primary_assignment.role.value if primary_assignment else "USER"

    access_token = create_access_token(subject=user.id, role=primary_role)
    refresh_token_str = create_refresh_token(subject=user.id)

    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )

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
        "available_roles": available_roles,
        "active_role": primary_role
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

    if db_token.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Refresh token expired")

    statement_user = select(User).options(
        selectinload(User.role_assignments)
    ).where(User.id == db_token.user_id)

    result_user = await db.exec(statement_user)
    user = result_user.first()

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User is no longer active")

    db_token.is_revoked = True
    db.add(db_token)

    available_roles = [
        {
            "role": ra.role.value,
            "scope_type": ra.scope_type.value,
            "scope_id": ra.scope_id
        }
        for ra in user.role_assignments
    ]

    primary_assignment = user.role_assignments[0] if user.role_assignments else None
    primary_role = primary_assignment.role.value if primary_assignment else "USER"

    new_access_token = create_access_token(
        subject=user.id,
        role=primary_role
    )

    new_refresh_token_str = create_refresh_token(
        subject=user.id
    )

    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )

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
        "token_type": "bearer",
        "must_change_password": user.must_change_password,
        "available_roles": available_roles,
        "active_role": primary_role
    }


@router.post("/logout")
async def logout(
    request: RefreshTokenRequest,
    db: AsyncSession = Depends(get_session)
):
    statement = select(RefreshToken).where(
        RefreshToken.token == request.refresh_token
    )

    result = await db.exec(statement)
    db_token = result.first()

    if db_token:
        db_token.is_revoked = True
        db.add(db_token)
        await db.commit()

    return {
        "status": "success",
        "message": "Successfully logged out"
    }


@router.post("/change-password", status_code=status.HTTP_200_OK)
async def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session)
):
    if not verify_password(
        payload.old_password,
        current_user.hashed_password
    ):
        raise HTTPException(
            status_code=400,
            detail="Incorrect current password."
        )

    current_user.hashed_password = get_password_hash(
        payload.new_password
    )

    current_user.must_change_password = False

    db.add(current_user)

    cleanup_stmt = delete(RefreshToken).where(
        RefreshToken.user_id == current_user.id
    )

    await db.exec(cleanup_stmt)
    await db.commit()

    return {
        "status": "success",
        "message": "Password updated successfully."
    }


@router.post("/forgot-password", status_code=status.HTTP_200_OK)
async def forgot_password(
    payload: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session)
):
    user_stmt = select(User).where(User.email == payload.email)
    user = (await db.exec(user_stmt)).first()

    if not user or not user.is_active:
        return {
            "status": "success",
            "message": "If that account exists, an OTP has been sent."
        }

    otp_code = str(random.randint(100000, 999999))

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    await db.exec(
        delete(PasswordResetOTP).where(
            PasswordResetOTP.email == payload.email
        )
    )

    db_otp = PasswordResetOTP(
        email=payload.email,
        otp_code=otp_code,
        expires_at=expires_at
    )

    db.add(db_otp)
    await db.commit()

    background_tasks.add_task(
        send_otp_email,
        to_email=payload.email,
        otp=otp_code
    )

    return {
        "status": "success",
        "message": "If that account exists, an OTP has been sent."
    }


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_session)
):
    otp_stmt = select(PasswordResetOTP).where(
        (PasswordResetOTP.email == payload.email) &
        (PasswordResetOTP.otp_code == payload.otp)
    )

    db_otp = (await db.exec(otp_stmt)).first()

    if not db_otp:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired OTP."
        )

    if db_otp.expires_at < datetime.now(timezone.utc):
        await db.exec(
            delete(PasswordResetOTP).where(
                PasswordResetOTP.id == db_otp.id
            )
        )

        await db.commit()

        raise HTTPException(
            status_code=400,
            detail="OTP has expired. Please request a new one."
        )

    user = (
        await db.exec(
            select(User).where(User.email == payload.email)
        )
    ).first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found."
        )

    user.hashed_password = get_password_hash(
        payload.new_password
    )

    user.must_change_password = False

    db.add(user)

    await db.exec(
        delete(PasswordResetOTP).where(
            PasswordResetOTP.id == db_otp.id
        )
    )

    await db.exec(
        delete(RefreshToken).where(
            RefreshToken.user_id == user.id
        )
    )

    await db.commit()

    return {
        "status": "success",
        "message": "Password has been successfully reset. Please log in."
    }


@router.post("/switch-role", response_model=TokenResponse)
async def switch_role(
    payload: SwitchRoleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session)
):
    """Reissue a JWT with a different active role/scope from the user's authorized list."""

    # Verify the user actually has the requested role/scope combination
    target_assignment = next(
        (
            ra
            for ra in current_user.role_assignments
            if ra.role.value == payload.role
            and ra.scope_id == payload.scope_id
        ),
        None
    )

    if not target_assignment:
        raise HTTPException(
            status_code=403,
            detail="You are not authorized to assume this role or scope."
        )

    # Generate new tokens using the selected role
    new_access_token = create_access_token(
        subject=current_user.id,
        role=target_assignment.role.value
    )

    new_refresh_token_str = create_refresh_token(
        subject=current_user.id
    )

    # Rotate refresh tokens
    await db.exec(
        delete(RefreshToken).where(
            RefreshToken.user_id == current_user.id
        )
    )

    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )

    db.add(
        RefreshToken(
            user_id=current_user.id,
            token=new_refresh_token_str,
            expires_at=expires_at
        )
    )

    await db.commit()

    available_roles = [
        {
            "role": ra.role.value,
            "scope_type": ra.scope_type.value,
            "scope_id": ra.scope_id
        }
        for ra in current_user.role_assignments
    ]

    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token_str,
        "token_type": "bearer",
        "must_change_password": current_user.must_change_password,
        "available_roles": available_roles,
        "active_role": target_assignment.role.value
    }