from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.core.config import settings
from app.core.security import verify_password, create_access_token, create_refresh_token
from app.models.user import User, RefreshToken
from app.models.auth import TokenResponse, RefreshTokenRequest

router = APIRouter()

@router.post("/login", response_model=TokenResponse)
async def login(
    db: AsyncSession = Depends(get_session),
    # Using OAuth2PasswordRequestForm makes the interactive Swagger UI work perfectly
    form_data: OAuth2PasswordRequestForm = Depends() 
):
    # 1. Fetch User (FastAPI uses 'username' in the form, but we treat it as 'email')
    statement = select(User).where(User.email == form_data.username)
    result = await db.exec(statement)
    user = result.first()

    # 2. Verify Credentials
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    # 3. Generate Tokens
    access_token = create_access_token(subject=user.id, role=user.role.value)
    refresh_token_str = create_refresh_token(subject=user.id)

    # 4. Save Refresh Token to Database
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
    # 1. Find the token in the DB
    statement = select(RefreshToken).where(RefreshToken.token == request.refresh_token)
    result = await db.exec(statement)
    db_token = result.first()

    # 2. Strict Validation
    if not db_token:
        raise HTTPException(status_code=404, detail="Refresh token not found")
    if db_token.is_revoked:
        raise HTTPException(status_code=401, detail="Refresh token has been revoked")
    if db_token.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Refresh token expired")

    # 3. Fetch the User to get their current role
    user = await db.get(User, db_token.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User is no longer active")

    # 4. Token Rotation (Security Best Practice)
    # Revoke the old token so it can never be used again
    db_token.is_revoked = True
    db.add(db_token)

    # Generate a brand new pair
    new_access_token = create_access_token(subject=user.id, role=user.role.value)
    new_refresh_token_str = create_refresh_token(subject=user.id)

    # Save the new refresh token
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
    # Find token and immediately revoke it
    statement = select(RefreshToken).where(RefreshToken.token == request.refresh_token)
    result = await db.exec(statement)
    db_token = result.first()

    if db_token:
        db_token.is_revoked = True
        db.add(db_token)
        await db.commit()
        
    # We return success even if the token wasn't found to prevent enumeration attacks
    return {"status": "success", "message": "Successfully logged out"}