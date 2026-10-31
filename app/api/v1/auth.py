import random
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, delete, func
from sqlalchemy import case
from sqlalchemy.orm import selectinload

from app.core.database import get_session
from app.core.config import settings
from app.core.security import verify_password, create_access_token, create_refresh_token, get_password_hash
from app.models.user import User, RefreshToken, PasswordResetOTP, RoleEnum, StudentProfile
from app.models.academic import CourseOffering, TimetableEntry, Batch, Programme
from app.models.attendance import AttendanceRecord, AttendanceSession, SessionStatus, AttendanceStatus
from app.api.deps import get_current_user
from app.services.email_service import send_otp_email

# Import schemas from app.schemas.auth
from app.schemas.auth import (
    LoginRequest,
    TokenResponse,
    RefreshTokenRequest,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    SwitchRoleRequest,
    UserProfileResponse
)

router = APIRouter()

@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_session)
):
    identifier = payload.email.strip()

    # 1. Search User by email (case-insensitive)
    statement = (
        select(User)
        .options(selectinload(User.role_assignments))
        .where(func.lower(User.email) == identifier.lower())
    )
    result = await db.exec(statement)
    user = result.first()

    # 2. If no direct email match, check StudentProfile by roll number (case-insensitive)
    if not user:
        clean_roll = identifier.replace("/", "").replace("-", "").replace(" ", "").lower()
        student_statement = (
            select(User)
            .join(StudentProfile, StudentProfile.user_id == User.id)
            .options(selectinload(User.role_assignments))
            .where(
                (func.lower(StudentProfile.roll_number) == identifier.lower()) |
                (func.replace(func.replace(func.replace(func.lower(StudentProfile.roll_number), "/", ""), "-", ""), " ", "") == clean_roll)
            )
        )
        student_result = await db.exec(student_statement)
        user = student_result.first()

    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email, roll number, or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    cleanup_stmt = delete(RefreshToken).where(
        (RefreshToken.user_id == user.id) &
        (
            (RefreshToken.is_revoked == True) |
            (RefreshToken.expires_at < datetime.utcnow())
        )
    )
    await db.exec(cleanup_stmt)

    available_roles = [
        {
            "role": ra.role.value,
            "scope_type": ra.scope_type.value,
            "scope_id": ra.scope_id
        }
        for ra in user.role_assignments
    ]

    # Prioritize TEACHER > STUDENT > other roles for primary active role
    teacher_ra = next((ra for ra in user.role_assignments if ra.role == RoleEnum.TEACHER), None)
    student_ra = next((ra for ra in user.role_assignments if ra.role == RoleEnum.STUDENT), None)
    primary_assignment = teacher_ra or student_ra or (user.role_assignments[0] if user.role_assignments else None)
    primary_role = primary_assignment.role.value if primary_assignment else "USER"

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
        "available_roles": available_roles,
        "active_role": primary_role,
        "user_name": user.full_name,
        "user_email": user.email,
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
        "token_type": "bearer",
        "must_change_password": user.must_change_password,
        "available_roles": available_roles,
        "active_role": primary_role,
        "user_name": user.full_name,
        "user_email": user.email,
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

@router.post("/change-password", status_code=status.HTTP_200_OK)
async def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session)
):
    if not verify_password(payload.old_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect current password.")

    current_user.hashed_password = get_password_hash(payload.new_password)
    current_user.must_change_password = False
    db.add(current_user)

    cleanup_stmt = delete(RefreshToken).where(RefreshToken.user_id == current_user.id)
    await db.exec(cleanup_stmt)
    await db.commit()

    return {"status": "success", "message": "Password updated successfully."}

@router.post("/forgot-password", status_code=status.HTTP_200_OK)
async def forgot_password(
    payload: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session)
):
    identifier = payload.email.strip()
    user_stmt = select(User).where(func.lower(User.email) == identifier.lower())
    user = (await db.exec(user_stmt)).first()

    if not user:
        clean_roll = identifier.replace("/", "").replace("-", "").replace(" ", "").lower()
        student_stmt = (
            select(User)
            .join(StudentProfile, StudentProfile.user_id == User.id)
            .where(
                (func.lower(StudentProfile.roll_number) == identifier.lower()) |
                (func.replace(func.replace(func.replace(func.lower(StudentProfile.roll_number), "/", ""), "-", ""), " ", "") == clean_roll)
            )
        )
        user = (await db.exec(student_stmt)).first()

    if not user or not user.is_active:
        return {"status": "success", "message": "If that account exists, an OTP has been sent."}

    target_email = user.email
    otp_code = str(random.randint(100000, 999999))
    expires_at = datetime.utcnow() + timedelta(minutes=5)

    await db.exec(delete(PasswordResetOTP).where(PasswordResetOTP.email == target_email))

    db_otp = PasswordResetOTP(email=target_email, otp_code=otp_code, expires_at=expires_at)
    db.add(db_otp)
    await db.commit()

    background_tasks.add_task(send_otp_email, to_email=target_email, otp=otp_code)

    return {"status": "success", "message": "If that account exists, an OTP has been sent."}

@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_session)
):
    identifier = payload.email.strip()
    target_email = identifier

    if "@" not in identifier:
        clean_roll = identifier.replace("/", "").replace("-", "").replace(" ", "").lower()
        student_stmt = (
            select(User)
            .join(StudentProfile, StudentProfile.user_id == User.id)
            .where(
                (func.lower(StudentProfile.roll_number) == identifier.lower()) |
                (func.replace(func.replace(func.replace(func.lower(StudentProfile.roll_number), "/", ""), "-", ""), " ", "") == clean_roll)
            )
        )
        found_user = (await db.exec(student_stmt)).first()
        if found_user:
            target_email = found_user.email

    otp_stmt = select(PasswordResetOTP).where(
        (func.lower(PasswordResetOTP.email) == target_email.lower()) &
        (PasswordResetOTP.otp_code == payload.otp)
    )

    db_otp = (await db.exec(otp_stmt)).first()

    if not db_otp:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP.")

    if db_otp.expires_at < datetime.utcnow():
        await db.exec(delete(PasswordResetOTP).where(PasswordResetOTP.id == db_otp.id))
        await db.commit()
        raise HTTPException(status_code=400, detail="OTP has expired. Please request a new one.")

    user = (await db.exec(select(User).where(func.lower(User.email) == target_email.lower()))).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    user.hashed_password = get_password_hash(payload.new_password)
    user.must_change_password = False
    db.add(user)

    await db.exec(delete(PasswordResetOTP).where(PasswordResetOTP.id == db_otp.id))
    await db.exec(delete(RefreshToken).where(RefreshToken.user_id == user.id))
    await db.commit()

    return {"status": "success", "message": "Password has been successfully reset. Please log in."}

@router.post("/switch-role", response_model=TokenResponse)
async def switch_role(
    payload: SwitchRoleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session)
):
    target_assignment = next(
        (
            ra for ra in current_user.role_assignments
            if ra.role.value == payload.role and ra.scope_id == payload.scope_id
        ),
        None
    )

    if not target_assignment:
        raise HTTPException(status_code=403, detail="You are not authorized to assume this role or scope.")

    new_access_token = create_access_token(subject=current_user.id, role=target_assignment.role.value)
    new_refresh_token_str = create_refresh_token(subject=current_user.id)

    await db.exec(delete(RefreshToken).where(RefreshToken.user_id == current_user.id))

    expires_at = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    db.add(RefreshToken(user_id=current_user.id, token=new_refresh_token_str, expires_at=expires_at))
    await db.commit()

    available_roles = [
        {"role": ra.role.value, "scope_type": ra.scope_type.value, "scope_id": ra.scope_id}
        for ra in current_user.role_assignments
    ]

    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token_str,
        "token_type": "bearer",
        "must_change_password": current_user.must_change_password,
        "available_roles": available_roles,
        "active_role": target_assignment.role.value,
        "user_name": current_user.full_name,
        "user_email": current_user.email,
    }


@router.get("/me", response_model=UserProfileResponse)
async def get_my_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session)
):
    """
    Returns full profile details for the authenticated user,
    including all assigned roles (e.g. Teacher + Coordinator),
    institutional affiliation, and academic/teaching statistics.
    """
    stmt = select(User).options(selectinload(User.role_assignments)).where(User.id == current_user.id)
    user = (await db.exec(stmt)).first() or current_user

    roles = [ra.role.value for ra in user.role_assignments]
    
    if RoleEnum.TEACHER.value in roles:
        primary = RoleEnum.TEACHER.value
    elif RoleEnum.STUDENT.value in roles:
        primary = RoleEnum.STUDENT.value
    else:
        primary = roles[0] if roles else "USER"

    total_courses = None
    weekly_hours = None
    roll_number = None
    program_name = None
    batch_name = None
    overall_attendance_pct = None

    # Check if teacher/faculty
    matched_teacher_ids = [user.id]
    if user.full_name:
        same_name_ids = (await db.exec(select(User.id).where(User.full_name == user.full_name))).all()
        matched_teacher_ids = list(set(matched_teacher_ids + list(same_name_ids)))

    co_stmt = select(CourseOffering).where(CourseOffering.teacher_id.in_(matched_teacher_ids))
    teacher_offerings = (await db.exec(co_stmt)).all()

    if teacher_offerings:
        offering_ids = [co.id for co in teacher_offerings]
        total_courses = len(teacher_offerings)
        tt_stmt = select(TimetableEntry).where(TimetableEntry.course_offering_id.in_(offering_ids))
        tt_entries = (await db.exec(tt_stmt)).all()
        weekly_hours = float(len(tt_entries))

    # Check if student
    sp_stmt = select(StudentProfile).where(StudentProfile.user_id == user.id)
    student_profile = (await db.exec(sp_stmt)).first()

    if student_profile:
        roll_number = student_profile.roll_number
        if student_profile.batch_id:
            batch = await db.get(Batch, student_profile.batch_id)
            if batch:
                batch_name = batch.name
                if batch.programme_id:
                    prog = await db.get(Programme, batch.programme_id)
                    if prog:
                        program_name = prog.name

        # Calculate student overall attendance percentage
        att_stmt = (
            select(
                func.sum(
                    case(
                        (
                            (AttendanceSession.is_held == True) & 
                            (AttendanceSession.status == SessionStatus.SUBMITTED), 
                            AttendanceSession.duration_periods
                        ),
                        else_=0
                    )
                ).label("held"),
                func.sum(
                    case(
                        (
                            (AttendanceSession.is_held == True) & 
                            (AttendanceSession.status == SessionStatus.SUBMITTED) & 
                            (AttendanceRecord.status == AttendanceStatus.PRESENT), 
                            AttendanceSession.duration_periods
                        ),
                        else_=0
                    )
                ).label("attended")
            )
            .select_from(AttendanceRecord)
            .join(AttendanceSession, AttendanceRecord.session_id == AttendanceSession.id)
            .where(AttendanceRecord.student_id == user.id)
        )
        res = (await db.exec(att_stmt)).first()
        if res and res.held and res.held > 0:
            overall_attendance_pct = round((float(res.attended or 0) / float(res.held)) * 100.0, 1)
        elif res and res.held == 0:
            overall_attendance_pct = 100.0

    return UserProfileResponse(
        user_id=user.id,
        full_name=user.full_name,
        email=user.email,
        roles=roles,
        primary_role=primary,
        department="Computer Science and Engineering",
        school="School of Information and Communication Technology",
        total_courses=total_courses,
        weekly_hours=weekly_hours,
        roll_number=roll_number,
        employee_code=user.employee_code,
        program_name=program_name,
        batch_name=batch_name,
        overall_attendance_pct=overall_attendance_pct,
    )
