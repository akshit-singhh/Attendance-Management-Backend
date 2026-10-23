import re
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, or_, delete
from sqlalchemy.orm import selectinload

from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy import func, distinct

from app.core.database import get_session
from app.core.security import get_password_hash
from app.api.deps import require_hod, require_admin, require_coordinator
from app.models.academic import (
    School,
    Department,
    Programme,
    Specialization,
    Batch,
    Section,
    AcademicTerm,
    Subject,
    CourseOffering,
    StudentSubjectMap,
    TimetableEntry,
    SubstituteGrant,
)
from app.models.attendance import AttendanceSession
from app.models.user import (
    User,
    RoleEnum,
    RoleAssignment,
    ScopeTypeEnum,
    StudentProfile,
)


# ============================================================================
# SCHEMAS (imported from app.schemas)
# ============================================================================

from app.schemas.academic import (
    SchoolCreate,
    SchoolResponse,
    DepartmentCreate,
    DepartmentResponse,
    ProgrammeCreate,
    ProgrammeResponse,
    ProgrammeUpdate,
    SpecializationCreate,
    SpecializationResponse,
    BatchCreate,
    SectionCreate,
    SectionResponse,
    SubjectCreate,
    TermCreate,
    TermUpdate,
    CourseOfferingCreate,
    FacultyResponse,
    BulkEnrollmentCreate,
    CourseOfferingListResponse,
    CourseOfferingReassign,
    StudentAcademicCourse,
    StudentAcademicItem,
    PaginatedStudentAcademicResponse,
    StudentImportItem,
    StudentBulkImportRequest,
    StudentBulkImportResponse,
)

from app.schemas.user import (
    UserCreate,
    UserResponse,
    PaginatedUserResponse,
    RoleAssignmentItem,
    RoleAssignmentResponse,
    UpdateUserRolesRequest,
    AdminUpdateUserPasswordRequest,
    AdminUpdateUserProfileRequest,
)

router = APIRouter()


# ============================================================================
# 1. ADMIN: USER MANAGEMENT & STUDENT LINKING
# ============================================================================

@router.post("/users", status_code=status.HTTP_201_CREATED, response_model=UserResponse, tags=["Admin - User Provisioning"])
async def create_user(
    payload: UserCreate,
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_session)
):
    """Create a user, assign their role scope strictly, and link a student to a batch if applicable."""
    existing_user = (await db.exec(select(User).where(User.email == payload.email))).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="User with this email already exists.")

    # --- STRICT SCOPE ENFORCEMENT ---
    if payload.role == RoleEnum.HOD:
        if payload.scope_type != ScopeTypeEnum.DEPARTMENT or not payload.scope_id:
            raise HTTPException(status_code=400, detail="HODs must have a scope_type of DEPARTMENT and a valid scope_id.")
        dept = await db.get(Department, payload.scope_id)
        if not dept:
            raise HTTPException(status_code=404, detail="Invalid Department ID provided for HOD scope.")

    elif payload.role == RoleEnum.COORDINATOR:
        if payload.scope_type in (ScopeTypeEnum.SECTION, None):
            payload.scope_type = ScopeTypeEnum.SECTION
            if not payload.scope_id:
                raise HTTPException(status_code=400, detail="Coordinators must have a scope_type of SECTION and a valid section scope_id.")
            sec = await db.get(Section, payload.scope_id)
            if not sec:
                raise HTTPException(status_code=404, detail="Invalid Section ID provided for Coordinator scope.")
        elif payload.scope_type == ScopeTypeEnum.PROGRAM:
            if not payload.scope_id:
                raise HTTPException(status_code=400, detail="Coordinators must have a valid scope_id.")
            prog = await db.get(Programme, payload.scope_id)
            if not prog:
                raise HTTPException(status_code=404, detail="Invalid Programme ID provided for Coordinator scope.")
            
    elif payload.role == RoleEnum.ADMIN:
        payload.scope_type = ScopeTypeEnum.UNIVERSITY
        payload.scope_id = None
        
    elif payload.role == RoleEnum.TEACHER:
        payload.scope_type = ScopeTypeEnum.ASSIGNMENT
        payload.scope_id = None

    # --- STUDENT VALIDATION ---
    if payload.role == RoleEnum.STUDENT:
        if not payload.roll_number or not payload.batch_id:
            raise HTTPException(
                status_code=400, 
                detail="Roll number and Batch ID are strictly required when provisioning a student."
            )
        
        batch = await db.get(Batch, payload.batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Assigned Batch ID does not exist.")

        existing_roll = (await db.exec(
            select(StudentProfile).where(StudentProfile.roll_number == payload.roll_number)
        )).first()
        if existing_roll:
            raise HTTPException(status_code=400, detail="A student with this roll number already exists.")

    # --- RECORD CREATION ---
    new_user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=get_password_hash(payload.password),
        is_active=True
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    # --- ROLE ASSIGNMENTS (Single or Multiple) ---
    assignments_to_create = []
    if payload.role_assignments:
        assignments_to_create = payload.role_assignments
    elif payload.roles:
        # Create assignments for each role in roles list
        for r in payload.roles:
            st = payload.scope_type if r == payload.role else None
            sid = payload.scope_id if r == payload.role else None
            assignments_to_create.append(RoleAssignmentItem(role=r, scope_type=st, scope_id=sid))
    else:
        assignments_to_create = [
            RoleAssignmentItem(
                role=payload.role,
                scope_type=payload.scope_type,
                scope_id=payload.scope_id
            )
        ]

    created_assignments: List[RoleAssignment] = []
    for item in assignments_to_create:
        st = item.scope_type
        sid = item.scope_id
        if item.role == RoleEnum.TEACHER:
            st = ScopeTypeEnum.ASSIGNMENT
            sid = None
        elif item.role == RoleEnum.ADMIN:
            st = ScopeTypeEnum.UNIVERSITY
            sid = None
        elif item.role == RoleEnum.HOD:
            st = ScopeTypeEnum.DEPARTMENT
            sid = sid or payload.scope_id
        elif item.role == RoleEnum.COORDINATOR:
            st = item.scope_type or ScopeTypeEnum.SECTION
            sid = sid or payload.scope_id
        elif item.role == RoleEnum.DEAN:
            st = ScopeTypeEnum.SCHOOL
            sid = sid or payload.scope_id
        elif item.role == RoleEnum.STUDENT:
            st = ScopeTypeEnum.UNIVERSITY

        ra = RoleAssignment(
            user_id=new_user.id,
            role=item.role,
            scope_type=st or ScopeTypeEnum.UNIVERSITY,
            scope_id=sid
        )
        db.add(ra)
        created_assignments.append(ra)

    roll_number_out = None
    batch_id_out = None

    if any(item.role == RoleEnum.STUDENT for item in assignments_to_create):
        student_profile = StudentProfile(
            user_id=new_user.id,
            roll_number=payload.roll_number,
            batch_id=payload.batch_id
        )
        db.add(student_profile)
        roll_number_out = payload.roll_number
        batch_id_out = payload.batch_id

    await db.commit()

    primary_ra = created_assignments[0] if created_assignments else None

    return UserResponse(
        id=new_user.id,
        email=new_user.email,
        full_name=new_user.full_name,
        is_active=new_user.is_active,
        role=primary_ra.role if primary_ra else RoleEnum.STUDENT,
        scope_type=primary_ra.scope_type if primary_ra else ScopeTypeEnum.UNIVERSITY,
        scope_id=primary_ra.scope_id if primary_ra else None,
        roles=[a.role for a in created_assignments],
        role_assignments=[
            RoleAssignmentResponse(
                id=a.id,
                role=a.role,
                scope_type=a.scope_type,
                scope_id=a.scope_id
            )
            for a in created_assignments
        ],
        roll_number=roll_number_out,
        batch_id=batch_id_out
    )

@router.get("/users", response_model=PaginatedUserResponse, tags=["Admin - User Provisioning"])
async def list_users(
    role: Optional[RoleEnum] = Query(default=None),
    search: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=100),
    user_id: int = Depends(require_hod),  # <-- Changed dependency here
    db: AsyncSession = Depends(get_session)
):
    """List users with pagination, optional role and search filtering, and eagerly loaded student profiles."""
    
    # 1. Base queries
    count_stmt = select(func.count(User.id))
    stmt = select(User).options(
        selectinload(User.role_assignments),
        selectinload(User.student_profile)
    )

    # 2. Apply role filter in SQL
    if role:
        sub_role = select(RoleAssignment.user_id).where(RoleAssignment.role == role)
        count_stmt = count_stmt.where(User.id.in_(sub_role))
        stmt = stmt.where(User.id.in_(sub_role))

    # 3. Apply search filter in SQL across name, email, roll number
    if search and search.strip():
        term = f"%{search.strip()}%"
        search_filter = or_(
            User.full_name.ilike(term),
            User.email.ilike(term),
            User.id.in_(select(StudentProfile.user_id).where(StudentProfile.roll_number.ilike(term)))
        )
        count_stmt = count_stmt.where(search_filter)
        stmt = stmt.where(search_filter)

    # 4. Execute total count
    total_users = (await db.exec(count_stmt)).one()

    # 5. Apply pagination offsets and fetch the specific page
    skip = (page - 1) * size
    stmt = stmt.order_by(User.full_name).offset(skip).limit(size)
    result = await db.exec(stmt)
    
    # 5. Format results
    users = []
    for user in result.all():
        assignments = user.role_assignments or []
        primary_assignment = assignments[0] if assignments else None
        user_role = primary_assignment.role if primary_assignment else RoleEnum.STUDENT

        users.append(UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            role=user_role,
            scope_type=primary_assignment.scope_type if primary_assignment else ScopeTypeEnum.UNIVERSITY,
            scope_id=primary_assignment.scope_id if primary_assignment else None,
            roles=[a.role for a in assignments],
            role_assignments=[
                RoleAssignmentResponse(
                    id=a.id,
                    role=a.role,
                    scope_type=a.scope_type,
                    scope_id=a.scope_id
                )
                for a in assignments
            ],
            roll_number=user.student_profile.roll_number if user.student_profile else None,
            batch_id=user.student_profile.batch_id if user.student_profile else None
        ))
        
    return PaginatedUserResponse(
        items=users,
        total=total_users,
        page=page,
        size=size
    )

@router.patch("/users/{user_id}/deactivate", status_code=status.HTTP_200_OK, tags=["Admin - User Provisioning"])
async def deactivate_user(
    user_id: int,
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_session)
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
        
    if user.id == admin_id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own admin account.")

    user.is_active = False
    db.add(user)
    await db.commit()
    return {"status": "success", "message": f"User {user.email} has been deactivated."}

@router.patch("/users/{user_id}/activate", status_code=status.HTTP_200_OK, tags=["Admin - User Provisioning"])
async def activate_user(
    user_id: int,
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_session)
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if user.is_active:
        return {"status": "success", "message": f"User {user.email} is already active."}

    user.is_active = True
    db.add(user)
    await db.commit()
    return {"status": "success", "message": f"User {user.email} has been reactivated."}

@router.patch("/users/{user_id}/password", status_code=status.HTTP_200_OK, tags=["Admin - User Provisioning"])
async def admin_update_user_password(
    user_id: int,
    payload: AdminUpdateUserPasswordRequest,
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_session)
):
    """Allows Admin to update or reset any teacher, student, or coordinator password."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    pwd_clean = payload.new_password.strip()
    if len(pwd_clean) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters long.")

    user.hashed_password = get_password_hash(pwd_clean)
    if payload.must_change_password is not None:
        user.must_change_password = payload.must_change_password
    db.add(user)
    await db.commit()
    return {
        "status": "success",
        "message": f"Password for {user.full_name} ({user.email}) updated successfully."
    }

@router.patch("/users/{user_id}/profile", response_model=UserResponse, tags=["Admin - User Provisioning"])
async def admin_update_user_profile(
    user_id: int,
    payload: AdminUpdateUserProfileRequest,
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_session)
):
    """Update profile details for any user (name, email, roll number, and optional password)."""
    user_stmt = (
        select(User)
        .options(selectinload(User.role_assignments), selectinload(User.student_profile))
        .where(User.id == user_id)
    )
    user = (await db.exec(user_stmt)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # 1. Update Full Name
    if payload.full_name is not None and payload.full_name.strip():
        user.full_name = payload.full_name.strip()

    # 2. Update Email (Validate uniqueness)
    if payload.email is not None and payload.email.strip():
        new_email = payload.email.strip().lower()
        if new_email != user.email.lower():
            existing = (await db.exec(
                select(User).where(func.lower(User.email) == new_email, User.id != user_id)
            )).first()
            if existing:
                raise HTTPException(status_code=400, detail=f"Email '{new_email}' is already registered to another user.")
            user.email = new_email

    # 3. Update Student Roll Number (if student profile exists or is provided)
    if payload.roll_number is not None:
        new_roll = payload.roll_number.strip().upper() if payload.roll_number.strip() else None
        if new_roll:
            existing_roll = (await db.exec(
                select(StudentProfile).where(
                    func.lower(StudentProfile.roll_number) == new_roll.lower(),
                    StudentProfile.user_id != user_id
                )
            )).first()
            if existing_roll:
                raise HTTPException(status_code=400, detail=f"Roll number '{new_roll}' is already assigned to another student.")
            
            if user.student_profile:
                user.student_profile.roll_number = new_roll
                db.add(user.student_profile)
            else:
                sp = StudentProfile(user_id=user.id, roll_number=new_roll)
                db.add(sp)

    # 4. Update Password (if provided and non-empty)
    if payload.new_password is not None and payload.new_password.strip():
        pwd_clean = payload.new_password.strip()
        if len(pwd_clean) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters long.")
        user.hashed_password = get_password_hash(pwd_clean)

    # 5. Update Must Change Password flag
    if payload.must_change_password is not None:
        user.must_change_password = payload.must_change_password

    db.add(user)
    await db.commit()

    # Re-fetch user with loaded relationships
    reloaded_user = (await db.exec(
        select(User)
        .options(selectinload(User.role_assignments), selectinload(User.student_profile))
        .where(User.id == user_id)
    )).first()

    assignments = reloaded_user.role_assignments or []
    primary = assignments[0] if assignments else None

    return UserResponse(
        id=reloaded_user.id,
        email=reloaded_user.email,
        full_name=reloaded_user.full_name,
        is_active=reloaded_user.is_active,
        role=primary.role if primary else RoleEnum.STUDENT,
        scope_type=primary.scope_type if primary else ScopeTypeEnum.UNIVERSITY,
        scope_id=primary.scope_id if primary else None,
        roles=[a.role for a in assignments],
        role_assignments=[
            RoleAssignmentResponse(
                id=a.id,
                role=a.role,
                scope_type=a.scope_type,
                scope_id=a.scope_id
            )
            for a in assignments
        ],
        roll_number=reloaded_user.student_profile.roll_number if reloaded_user.student_profile else None,
        batch_id=reloaded_user.student_profile.batch_id if reloaded_user.student_profile else None,
    )

@router.put("/users/{user_id}/roles", response_model=UserResponse, tags=["Admin - User Provisioning"])
async def update_user_roles(
    user_id: int,
    payload: UpdateUserRolesRequest,
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_session)
):
    """Update role assignments for a user. Supports multi-roles (e.g. Teacher + Coordinator or HOD + Teacher)."""
    user_stmt = (
        select(User)
        .options(selectinload(User.role_assignments), selectinload(User.student_profile))
        .where(User.id == user_id)
    )
    user = (await db.exec(user_stmt)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if not payload.roles:
        raise HTTPException(status_code=400, detail="User must have at least one role assigned.")

    # Guard: prevent removing ADMIN role from currently logged in admin
    if user.id == admin_id and not any(r.role == RoleEnum.ADMIN for r in payload.roles):
        raise HTTPException(status_code=400, detail="You cannot remove the ADMIN role from your own account.")

    # Delete existing role assignments
    stmt_del = delete(RoleAssignment).where(RoleAssignment.user_id == user_id)
    await db.exec(stmt_del)

    # Insert new role assignments
    for item in payload.roles:
        st = item.scope_type
        sid = item.scope_id
        if item.role == RoleEnum.TEACHER:
            st = ScopeTypeEnum.ASSIGNMENT
            sid = None
        elif item.role == RoleEnum.ADMIN:
            st = ScopeTypeEnum.UNIVERSITY
            sid = None
        elif item.role == RoleEnum.HOD:
            st = ScopeTypeEnum.DEPARTMENT
            if not sid:
                raise HTTPException(status_code=400, detail="Department scope is required for HOD role.")
        elif item.role == RoleEnum.COORDINATOR:
            st = item.scope_type or ScopeTypeEnum.SECTION
            if not sid:
                raise HTTPException(status_code=400, detail="Section scope is required for Coordinator role.")
            if st == ScopeTypeEnum.SECTION:
                sec = await db.get(Section, sid)
                if not sec:
                    raise HTTPException(status_code=404, detail="Invalid Section ID provided for Coordinator scope.")
        elif item.role == RoleEnum.DEAN:
            st = ScopeTypeEnum.SCHOOL
        elif item.role == RoleEnum.STUDENT:
            st = ScopeTypeEnum.UNIVERSITY

        ra = RoleAssignment(
            user_id=user.id,
            role=item.role,
            scope_type=st or ScopeTypeEnum.UNIVERSITY,
            scope_id=sid
        )
        db.add(ra)

    await db.commit()

    # Re-fetch user with newly created assignments
    reloaded_user = (await db.exec(
        select(User)
        .options(selectinload(User.role_assignments), selectinload(User.student_profile))
        .where(User.id == user_id)
    )).first()

    assignments = reloaded_user.role_assignments or []
    primary = assignments[0] if assignments else None

    return UserResponse(
        id=reloaded_user.id,
        email=reloaded_user.email,
        full_name=reloaded_user.full_name,
        is_active=reloaded_user.is_active,
        role=primary.role if primary else RoleEnum.STUDENT,
        scope_type=primary.scope_type if primary else ScopeTypeEnum.UNIVERSITY,
        scope_id=primary.scope_id if primary else None,
        roles=[a.role for a in assignments],
        role_assignments=[
            RoleAssignmentResponse(
                id=a.id,
                role=a.role,
                scope_type=a.scope_type,
                scope_id=a.scope_id
            )
            for a in assignments
        ],
        roll_number=reloaded_user.student_profile.roll_number if reloaded_user.student_profile else None,
        batch_id=reloaded_user.student_profile.batch_id if reloaded_user.student_profile else None
    )


# ============================================================================
# 2. HOD / ADMIN: ACADEMIC HIERARCHY SETUP (FLOW / PROGRAMMES)
# ============================================================================

# --- Schools ---

@router.post("/schools", status_code=status.HTTP_201_CREATED, response_model=SchoolResponse, tags=["Academic Flow - Hierarchy"])
async def create_school(
    payload: SchoolCreate,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    existing = (await db.exec(
        select(School).where(
            or_(School.name == payload.name.strip(), School.code == payload.code.upper().strip())
        )
    )).first()
    if existing:
        raise HTTPException(status_code=400, detail="School name or code already exists.")
    
    school = School(name=payload.name.strip(), code=payload.code.upper().strip())
    db.add(school)
    await db.commit()
    await db.refresh(school)
    
    return SchoolResponse(id=school.id, name=school.name, code=school.code)

@router.get("/schools", response_model=List[SchoolResponse], tags=["Academic Flow - Hierarchy"])
async def get_schools(
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    result = await db.exec(select(School).order_by(School.name))
    return [
        SchoolResponse(id=s.id, name=s.name, code=s.code)
        for s in result.all()
    ]

# --- Departments ---

@router.post("/departments", status_code=status.HTTP_201_CREATED, response_model=DepartmentResponse, tags=["Academic Flow - Hierarchy"])
async def create_department(
    payload: DepartmentCreate,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    existing = (await db.exec(
        select(Department).where(
            or_(Department.name == payload.name.strip(), Department.code == payload.code.upper().strip())
        )
    )).first()
    if existing:
        raise HTTPException(status_code=400, detail="Department name or code already exists.")
    
    school_id = None
    school_code = None
    school_name = None
    if payload.school_code:
        school = (await db.exec(select(School).where(School.code == payload.school_code.upper().strip()))).first()
        if not school:
            raise HTTPException(status_code=404, detail=f"School code '{payload.school_code}' not found.")
        school_id = school.id
        school_code = school.code
        school_name = school.name
    
    dept = Department(name=payload.name.strip(), code=payload.code.upper().strip(), school_id=school_id)
    db.add(dept)
    await db.commit()
    await db.refresh(dept)
    
    return DepartmentResponse(
        id=dept.id, 
        name=dept.name, 
        code=dept.code,
        school_code=school_code,
        school_name=school_name
    )

@router.get("/departments", response_model=List[DepartmentResponse], tags=["Academic Flow - Hierarchy"])
async def get_departments(
    school_code: Optional[str] = Query(default=None),
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    stmt = select(Department).options(selectinload(Department.school))
    if school_code:
        stmt = stmt.join(Department.school).where(School.code == school_code.upper().strip())
    
    result = await db.exec(stmt.order_by(Department.name))
    departments = result.all()
    return [
        DepartmentResponse(
            id=d.id, 
            name=d.name, 
            code=d.code,
            school_code=d.school.code if d.school else None,
            school_name=d.school.name if d.school else None
        ) 
        for d in departments
    ]

# --- Programmes ---

@router.post("/programmes", status_code=status.HTTP_201_CREATED, response_model=ProgrammeResponse, tags=["Academic Flow - Hierarchy"])
async def create_programme(
    payload: ProgrammeCreate,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    dept = (await db.exec(select(Department).where(Department.code == payload.department_code.upper()))).first()
    if not dept:
        raise HTTPException(status_code=404, detail=f"Department code '{payload.department_code}' not found.")

    existing = (await db.exec(select(Programme).where(Programme.code == payload.code.upper()))).first()
    if existing:
        raise HTTPException(status_code=400, detail="Programme code already exists.")

    prog = Programme(
        department_id=dept.id, 
        name=payload.name,
        code=payload.code.upper(),
        total_semesters=payload.total_semesters
    )
    db.add(prog)
    await db.commit()
    await db.refresh(prog)
    
    return ProgrammeResponse(
        id=prog.id, 
        name=prog.name, 
        code=prog.code, 
        department_code=dept.code,
        total_semesters=prog.total_semesters
    )

@router.get("/programmes", response_model=List[ProgrammeResponse], tags=["Academic Flow - Hierarchy"])
async def get_programmes(
    department_code: Optional[str] = Query(default=None),
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    stmt = select(Programme, Department).join(Department)
    if department_code:
        stmt = stmt.where(Department.code == department_code.upper())
        
    result = await db.exec(stmt.order_by(Programme.name))
    
    return [
        ProgrammeResponse(
            id=prog.id,
            name=prog.name,
            code=prog.code,
            department_code=dept.code,
            total_semesters=prog.total_semesters
        )
        for prog, dept in result.all()
    ]

@router.put("/programmes/{programme_id}", response_model=ProgrammeResponse, tags=["Academic Flow - Hierarchy"])
async def update_programme(
    programme_id: int,
    payload: ProgrammeUpdate,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    prog = await db.get(Programme, programme_id)
    if not prog:
        raise HTTPException(status_code=404, detail="Programme not found.")

    if payload.department_code:
        dept = (await db.exec(select(Department).where(Department.code == payload.department_code.upper().strip()))).first()
        if not dept:
            raise HTTPException(status_code=404, detail=f"Department code '{payload.department_code}' not found.")
        prog.department_id = dept.id

    if payload.code:
        new_code = payload.code.upper().strip()
        if new_code != prog.code:
            existing = (await db.exec(select(Programme).where(Programme.code == new_code))).first()
            if existing:
                raise HTTPException(status_code=400, detail=f"Programme code '{new_code}' already exists.")
            prog.code = new_code

    if payload.name:
        prog.name = payload.name.strip()

    if payload.total_semesters is not None:
        prog.total_semesters = payload.total_semesters

    db.add(prog)
    await db.commit()
    await db.refresh(prog)

    dept = await db.get(Department, prog.department_id)

    return ProgrammeResponse(
        id=prog.id,
        name=prog.name,
        code=prog.code,
        department_code=dept.code if dept else "",
        total_semesters=prog.total_semesters
    )

# --- Specializations ---

@router.post("/specializations", status_code=status.HTTP_201_CREATED, response_model=SpecializationResponse, tags=["Academic Flow - Hierarchy"])
async def create_specialization(
    payload: SpecializationCreate,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    prog = (await db.exec(select(Programme).where(Programme.code == payload.programme_code.upper()))).first()
    if not prog:
        raise HTTPException(status_code=404, detail=f"Programme code '{payload.programme_code}' not found.")

    existing = (await db.exec(select(Specialization).where(Specialization.code == payload.code.upper()))).first()
    if existing:
        raise HTTPException(status_code=400, detail="Specialization code already exists.")

    spec = Specialization(
        programme_id=prog.id, 
        name=payload.name,
        code=payload.code.upper()
    )
    db.add(spec)
    await db.commit()
    await db.refresh(spec)
    
    return SpecializationResponse(
        id=spec.id,
        name=spec.name,
        code=spec.code,
        programme_code=prog.code
    )

@router.get("/specializations", response_model=List[SpecializationResponse], tags=["Academic Flow - Hierarchy"])
async def get_specializations(
    programme_code: Optional[str] = Query(default=None),
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    stmt = select(Specialization, Programme).join(Programme)
    if programme_code:
        stmt = stmt.where(Programme.code == programme_code.upper())
        
    result = await db.exec(stmt.order_by(Specialization.name))
    
    return [
        SpecializationResponse(
            id=spec.id,
            name=spec.name,
            code=spec.code,
            programme_code=prog.code
        )
        for spec, prog in result.all()
    ]

# --- Batches ---

@router.post("/batches", status_code=status.HTTP_201_CREATED, response_model=Batch, tags=["Academic Flow - Hierarchy"])
async def create_batch(
    payload: BatchCreate,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    programme = (await db.execute(
        select(Programme).where(Programme.code == payload.programme_code.upper().strip())
    )).scalar_one_or_none()
    
    if not programme:
        raise HTTPException(status_code=404, detail=f"Programme '{payload.programme_code}' not found")

    spec = None
    if payload.specialization_code and payload.specialization_code.strip():
        spec = (await db.execute(
            select(Specialization).where(
                Specialization.code == payload.specialization_code.upper().strip(),
                Specialization.programme_id == programme.id,
            )
        )).scalar_one_or_none()
        
        if not spec:
            raise HTTPException(status_code=404, detail=f"Specialization '{payload.specialization_code}' not found under {programme.code}")

    start_year = int(payload.start_year)
    expected_end_year = int(payload.expected_end_year)
    if expected_end_year < start_year:
        raise HTTPException(status_code=400, detail="Expected end year cannot be earlier than start year.")

    batch = Batch(
        name=payload.name.strip(),          
        programme_id=programme.id,
        specialization_id=spec.id if spec else None,
        start_year=start_year,
        expected_end_year=expected_end_year,
    )
    
    db.add(batch)
    await db.commit()
    await db.refresh(batch)
    
    return batch

@router.get("/batches", response_model=List[Batch], tags=["Academic Flow - Hierarchy"])
async def get_batches(
    specialization_id: Optional[int] = Query(default=None),
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    stmt = select(Batch)
    if specialization_id:
        stmt = stmt.where(Batch.specialization_id == specialization_id)
    result = await db.exec(stmt.order_by(Batch.start_year.desc()))
    return result.all()

# --- Sections ---

@router.post("/sections", status_code=status.HTTP_201_CREATED, response_model=Section, tags=["Academic Flow - Hierarchy"])
async def create_section(
    payload: SectionCreate,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    batch = await db.get(Batch, payload.batch_id)
    term = await db.get(AcademicTerm, payload.term_id)
    if not batch or not term:
        raise HTTPException(status_code=404, detail="Invalid Batch ID or Term ID provided.")

    existing = (await db.exec(
        select(Section).where(
            Section.name == payload.name.strip().upper(),
            Section.batch_id == payload.batch_id,
            Section.term_id == payload.term_id
        )
    )).first()
    
    if existing:
        raise HTTPException(
            status_code=400, 
            detail=f"Section '{payload.name}' already exists for this batch in the selected term."
        )

    section = Section(
        name=payload.name.strip().upper(),
        batch_id=payload.batch_id,
        term_id=payload.term_id
    )
    
    db.add(section)
    await db.commit()
    await db.refresh(section)
    
    return section

@router.get("/sections", response_model=List[SectionResponse], tags=["Academic Flow - Hierarchy"])
async def get_all_sections(
    batch_id: Optional[int] = Query(default=None),
    term_id: Optional[int] = Query(default=None),
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    stmt = (
        select(
            Section.id.label("id"),
            Section.name.label("name"),
            Section.batch_id.label("batch_id"),
            Section.term_id.label("term_id"),
            Batch.name.label("batch_name"),
            Batch.start_year.label("start_year"),
            Batch.expected_end_year.label("expected_end_year"),
            Programme.name.label("programme_name"),
            Programme.code.label("programme_code"),
            AcademicTerm.name.label("term_name"),
            AcademicTerm.is_active.label("is_term_active"),
        )
        .outerjoin(Batch, Section.batch_id == Batch.id)
        .outerjoin(Programme, Batch.programme_id == Programme.id)
        .outerjoin(AcademicTerm, Section.term_id == AcademicTerm.id)
    )
    if batch_id:
        stmt = stmt.where(Section.batch_id == batch_id)
    if term_id:
        stmt = stmt.where(Section.term_id == term_id)
    result = await db.exec(stmt.order_by(Section.name))

    # Fast count of distinct enrolled students per section
    cnt_stmt = (
        select(CourseOffering.section_id, func.count(distinct(StudentSubjectMap.student_id)))
        .join(StudentSubjectMap, CourseOffering.id == StudentSubjectMap.course_offering_id)
        .group_by(CourseOffering.section_id)
    )
    section_counts = dict((await db.exec(cnt_stmt)).all())

    return [
        SectionResponse(
            id=row.id,
            name=row.name,
            batch_id=row.batch_id,
            term_id=row.term_id,
            batch_name=row.batch_name,
            start_year=row.start_year,
            expected_end_year=row.expected_end_year,
            programme_name=row.programme_name,
            programme_code=row.programme_code,
            term_name=row.term_name,
            is_term_active=row.is_term_active,
            enrolled_students_count=section_counts.get(row.id, 0),
        )
        for row in result
    ]


# ============================================================================
# 3. TERMS & SUBJECTS
# ============================================================================

@router.post("/semesters", status_code=status.HTTP_201_CREATED, response_model=AcademicTerm, tags=["HOD - Academic Setup"])
@router.post("/terms", status_code=status.HTTP_201_CREATED, response_model=AcademicTerm, tags=["HOD - Academic Setup"])
async def create_semester(
    payload: TermCreate,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    if payload.is_active:
        statement = select(AcademicTerm).where(AcademicTerm.is_active == True)
        active_terms = (await db.exec(statement)).all()
        for t in active_terms:
            t.is_active = False
            db.add(t)

    new_term = AcademicTerm(
        name=payload.name.strip(),
        start_date=payload.start_date,
        end_date=payload.end_date,
        is_active=payload.is_active,
        batch_id=payload.batch_id,
    )
    db.add(new_term)
    await db.commit()
    await db.refresh(new_term)
    return new_term

# Backward-compatible function alias
create_term = create_semester

@router.get("/semesters", response_model=List[AcademicTerm], tags=["HOD - Academic Setup"])
@router.get("/terms", response_model=List[AcademicTerm], tags=["HOD - Academic Setup"])
async def get_all_semesters(
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    result = await db.exec(select(AcademicTerm).order_by(AcademicTerm.start_date.desc()))
    return result.all()

# Backward-compatible function alias
get_all_terms = get_all_semesters

@router.put("/semesters/{term_id}", response_model=AcademicTerm, tags=["HOD - Academic Setup"])
@router.put("/terms/{term_id}", response_model=AcademicTerm, tags=["HOD - Academic Setup"])
async def update_semester(
    term_id: int,
    payload: TermUpdate,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    term = await db.get(AcademicTerm, term_id)
    if not term:
        raise HTTPException(status_code=404, detail="Academic Term not found.")

    if payload.is_active is True:
        statement = select(AcademicTerm).where(AcademicTerm.is_active == True, AcademicTerm.id != term_id)
        active_terms = (await db.exec(statement)).all()
        for t in active_terms:
            t.is_active = False
            db.add(t)

    if payload.name is not None:
        term.name = payload.name.strip()
    if payload.start_date is not None:
        term.start_date = payload.start_date
    if payload.end_date is not None:
        term.end_date = payload.end_date
    if payload.is_active is not None:
        term.is_active = payload.is_active
    if payload.batch_id is not None:
        term.batch_id = payload.batch_id

    db.add(term)
    await db.commit()
    await db.refresh(term)
    return term

# Backward-compatible function alias
update_term = update_semester

@router.put("/semesters/{term_id}/activate", response_model=AcademicTerm, tags=["HOD - Academic Setup"])
@router.put("/terms/{term_id}/activate", response_model=AcademicTerm, tags=["HOD - Academic Setup"])
async def activate_semester(
    term_id: int,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    term = await db.get(AcademicTerm, term_id)
    if not term:
        raise HTTPException(status_code=404, detail="Academic Term not found.")

    statement = select(AcademicTerm).where(AcademicTerm.is_active == True)
    active_terms = (await db.exec(statement)).all()
    for t in active_terms:
        t.is_active = False
        db.add(t)

    term.is_active = True
    db.add(term)
    await db.commit()
    await db.refresh(term)
    return term

# Backward-compatible function alias
activate_term = activate_semester

@router.post("/subjects", status_code=status.HTTP_201_CREATED, response_model=Subject, tags=["HOD - Academic Setup"])
async def create_subject(
    payload: SubjectCreate,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    existing = (await db.exec(select(Subject).where(Subject.course_code == payload.course_code))).first()
    if existing:
        raise HTTPException(status_code=400, detail="Subject code already exists.")

    subject = Subject(
        course_code=payload.course_code,
        name=payload.name,
        is_elective=payload.is_elective
    )
    db.add(subject)
    await db.commit()
    await db.refresh(subject)
    return subject

@router.get("/subjects", response_model=List[Subject], tags=["HOD - Academic Setup"])
async def get_all_subjects(
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    result = await db.exec(select(Subject).order_by(Subject.name))
    return result.all()


# ============================================================================
# 4. COURSE OFFERINGS & BULK ENROLLMENT
# ============================================================================

@router.get("/faculty", response_model=List[FacultyResponse], tags=["HOD - Academic Setup"])
async def get_faculty_list(
    admin_id: int = Depends(require_coordinator),
    db: AsyncSession = Depends(get_session)
):
    allowed_roles = [RoleEnum.TEACHER, RoleEnum.COORDINATOR, RoleEnum.HOD, RoleEnum.ADMIN]
    statement = (
        select(User, RoleAssignment.role)
        .join(RoleAssignment, User.id == RoleAssignment.user_id)
        .where(RoleAssignment.role.in_(allowed_roles))
        .where(User.is_active == True)
        .order_by(User.full_name)
    )
    result = await db.exec(statement)
    
    faculty_list = []
    seen_ids = set()
    for user, role in result:
        if user.id not in seen_ids:
            faculty_list.append(FacultyResponse(
                id=user.id,
                full_name=user.full_name,
                email=user.email,
                role=role
            ))
            seen_ids.add(user.id)
            
    return faculty_list

@router.post("/course-offerings", status_code=status.HTTP_201_CREATED, tags=["HOD - Academic Setup"])
async def assign_teacher_to_class(
    payload: CourseOfferingCreate,
    admin_id: int = Depends(require_coordinator),
    db: AsyncSession = Depends(get_session)
):
    teacher = await db.get(User, payload.teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="User not found.")
        
    role_stmt = select(RoleAssignment).where(RoleAssignment.user_id == teacher.id)
    user_roles = [r.role for r in (await db.exec(role_stmt)).all()]
    if not any(r in [RoleEnum.TEACHER, RoleEnum.COORDINATOR, RoleEnum.HOD, RoleEnum.ADMIN] for r in user_roles):
        raise HTTPException(status_code=400, detail="The assigned user is not a faculty member.")

    term = await db.get(AcademicTerm, payload.term_id)
    section = await db.get(Section, payload.section_id)
    subject = await db.get(Subject, payload.subject_id)
    if not all([term, section, subject]):
        raise HTTPException(status_code=404, detail="Invalid Term, Section, or Subject ID provided.")

    existing_assignment = (await db.exec(
        select(CourseOffering).where(
            CourseOffering.term_id == payload.term_id,
            CourseOffering.section_id == payload.section_id,
            CourseOffering.subject_id == payload.subject_id
        )
    )).first()
    if existing_assignment:
        raise HTTPException(status_code=400, detail="A teacher is already assigned to this specific class.")

    offering = CourseOffering(
        term_id=payload.term_id,
        section_id=payload.section_id,
        subject_id=payload.subject_id,
        teacher_id=payload.teacher_id
    )
    db.add(offering)
    await db.commit()
    await db.refresh(offering)
    return {"status": "success", "course_offering_id": offering.id}

@router.get("/course-offerings", response_model=List[CourseOfferingListResponse], tags=["HOD - Academic Setup"])
async def get_all_course_offerings(
    admin_id: int = Depends(require_coordinator),
    db: AsyncSession = Depends(get_session)
):
    statement = (
        select(
            CourseOffering.id,
            AcademicTerm.name.label("term_name"),
            Section.name.label("section_name"),
            Subject.name.label("subject_name"),
            User.full_name.label("teacher_name"),
            User.id.label("teacher_id"),
            Subject.course_code.label("subject_code"),
            CourseOffering.term_id.label("term_id"),
            CourseOffering.section_id.label("section_id"),
            CourseOffering.subject_id.label("subject_id"),
            CourseOffering.course_type.label("offering_course_type"),
            Subject.course_type.label("subject_course_type"),
            Subject.credits.label("subject_credits")
        )
        .join(AcademicTerm, CourseOffering.term_id == AcademicTerm.id)
        .join(Section, CourseOffering.section_id == Section.id)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .join(User, CourseOffering.teacher_id == User.id)
        .order_by(AcademicTerm.name, Section.name, Subject.name)
    )
    result = await db.exec(statement)
    
    return [
        CourseOfferingListResponse(
            id=row.id,
            term_name=row.term_name,
            section_name=row.section_name,
            subject_name=row.subject_name,
            teacher_name=row.teacher_name,
            teacher_id=row.teacher_id,
            subject_code=row.subject_code,
            term_id=row.term_id,
            section_id=row.section_id,
            subject_id=row.subject_id,
            course_type=row.offering_course_type or row.subject_course_type or "LECTURE",
            credits=row.subject_credits or 3
        )
        for row in result
    ]

@router.post("/enrollments/bulk", status_code=status.HTTP_201_CREATED, tags=["HOD - Academic Setup"])
async def bulk_enroll_students(
    payload: BulkEnrollmentCreate,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    offering = await db.get(CourseOffering, payload.course_offering_id)
    if not offering:
        raise HTTPException(status_code=404, detail="Course offering not found.")

    if not payload.student_ids:
        raise HTTPException(status_code=400, detail="No student IDs provided.")

    valid_students_query = (
        select(User.id)
        .join(RoleAssignment)
        .where(User.id.in_(payload.student_ids))
        .where(RoleAssignment.role == RoleEnum.STUDENT)
        .where(User.is_active == True)
    )
    valid_student_ids = set((await db.exec(valid_students_query)).all())

    invalid_ids = set(payload.student_ids) - valid_student_ids
    if invalid_ids:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid or inactive student IDs found: {invalid_ids}"
        )

    existing_enrollments_query = select(StudentSubjectMap.student_id).where(
        StudentSubjectMap.course_offering_id == payload.course_offering_id,
        StudentSubjectMap.student_id.in_(valid_student_ids)
    )
    already_enrolled = set((await db.exec(existing_enrollments_query)).all())
    students_to_enroll = valid_student_ids - already_enrolled
    
    if not students_to_enroll:
        return {"status": "success", "message": "All provided students are already enrolled.", "enrolled_count": 0}

    new_mappings = [
        StudentSubjectMap(student_id=s_id, course_offering_id=payload.course_offering_id)
        for s_id in students_to_enroll
    ]
    db.add_all(new_mappings)
    await db.commit()

    return {
        "status": "success", 
        "message": f"Successfully enrolled {len(new_mappings)} students.",
        "enrolled_count": len(new_mappings),
        "skipped_duplicates": len(already_enrolled)
    }





@router.get("/students", response_model=PaginatedStudentAcademicResponse, tags=["Academic Flow - Students Directory"])
async def list_academic_students(
    school_id: Optional[int] = Query(default=None),
    department_id: Optional[int] = Query(default=None),
    programme_id: Optional[int] = Query(default=None),
    batch_id: Optional[int] = Query(default=None),
    section_id: Optional[int] = Query(default=None),
    subject_id: Optional[int] = Query(default=None),
    course_offering_id: Optional[int] = Query(default=None),
    search: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=25, ge=1, le=500),
    user_id: int = Depends(require_coordinator),
    db: AsyncSession = Depends(get_session)
):
    """List students with multi-level academic hierarchy filters (school, department, programme, batch, section, course)."""
    school_id = school_id if isinstance(school_id, int) else None
    department_id = department_id if isinstance(department_id, int) else None
    programme_id = programme_id if isinstance(programme_id, int) else None
    batch_id = batch_id if isinstance(batch_id, int) else None
    section_id = section_id if isinstance(section_id, int) else None
    course_offering_id = course_offering_id if isinstance(course_offering_id, int) else None
    subject_id = subject_id if isinstance(subject_id, int) else None
    search = search if isinstance(search, str) else None
    page = page if isinstance(page, int) else 1
    size = size if isinstance(size, int) else 25

    base_stmt = (
        select(User, StudentProfile, Batch, Programme, Department, School)
        .join(StudentProfile, User.id == StudentProfile.user_id)
        .outerjoin(Batch, StudentProfile.batch_id == Batch.id)
        .outerjoin(Programme, Batch.programme_id == Programme.id)
        .outerjoin(Department, Programme.department_id == Department.id)
        .outerjoin(School, Department.school_id == School.id)
    )

    if school_id:
        base_stmt = base_stmt.where(School.id == school_id)
    if department_id:
        base_stmt = base_stmt.where(Department.id == department_id)
    if programme_id:
        base_stmt = base_stmt.where(Programme.id == programme_id)
    if batch_id:
        base_stmt = base_stmt.where(Batch.id == batch_id)

    if section_id:
        enrolled_in_sec = (
            select(StudentSubjectMap.student_id)
            .join(CourseOffering, StudentSubjectMap.course_offering_id == CourseOffering.id)
            .where(CourseOffering.section_id == section_id)
        )
        base_stmt = base_stmt.where(User.id.in_(enrolled_in_sec))

    if course_offering_id:
        enrolled_in_off = (
            select(StudentSubjectMap.student_id)
            .where(StudentSubjectMap.course_offering_id == course_offering_id)
        )
        base_stmt = base_stmt.where(User.id.in_(enrolled_in_off))

    if subject_id:
        enrolled_in_sub = (
            select(StudentSubjectMap.student_id)
            .join(CourseOffering, StudentSubjectMap.course_offering_id == CourseOffering.id)
            .where(CourseOffering.subject_id == subject_id)
        )
        base_stmt = base_stmt.where(User.id.in_(enrolled_in_sub))

    if search and search.strip():
        q_term = f"%{search.strip().lower()}%"
        base_stmt = base_stmt.where(
            func.lower(User.full_name).like(q_term) |
            func.lower(User.email).like(q_term) |
            func.lower(StudentProfile.roll_number).like(q_term)
        )

    count_subq = base_stmt.subquery()
    total = (await db.exec(select(func.count()).select_from(count_subq))).one()

    active_subq = base_stmt.where(User.is_active == True).subquery()
    total_active = (await db.exec(select(func.count()).select_from(active_subq))).one()

    ordered_stmt = (
        base_stmt.order_by(StudentProfile.roll_number, User.full_name)
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = (await db.exec(ordered_stmt)).all()

    u_ids = [u.id for u, sp, b, p, d, s in rows]
    enrollment_map = {uid: [] for uid in u_ids}

    if u_ids:
        enroll_stmt = (
            select(
                StudentSubjectMap.student_id,
                CourseOffering.id,
                Subject.course_code,
                Subject.name,
                Section.name,
                AcademicTerm.name,
                User.full_name,
                Subject.course_type,
            )
            .join(CourseOffering, StudentSubjectMap.course_offering_id == CourseOffering.id)
            .join(Subject, CourseOffering.subject_id == Subject.id)
            .join(Section, CourseOffering.section_id == Section.id)
            .outerjoin(AcademicTerm, CourseOffering.term_id == AcademicTerm.id)
            .outerjoin(User, CourseOffering.teacher_id == User.id)
            .where(StudentSubjectMap.student_id.in_(u_ids))
        )
        enroll_rows = (await db.exec(enroll_stmt)).all()
        for s_id, off_id, sub_code, sub_name, sec_name, term_name, teacher_name, c_type in enroll_rows:
            if s_id in enrollment_map:
                enrollment_map[s_id].append(
                    StudentAcademicCourse(
                        offering_id=off_id,
                        course_code=sub_code,
                        subject_name=sub_name,
                        section_name=sec_name,
                        term_name=term_name,
                        teacher_name=teacher_name,
                        course_type=c_type,
                    )
                )

    items = []
    for u, sp, b, p, d, s in rows:
        courses = enrollment_map.get(u.id, [])
        items.append(
            StudentAcademicItem(
                id=u.id,
                full_name=u.full_name,
                email=u.email,
                roll_number=sp.roll_number if sp else None,
                is_active=u.is_active,
                batch_id=b.id if b else None,
                batch_name=b.name if b else None,
                programme_id=p.id if p else None,
                programme_name=p.name if p else None,
                programme_code=p.code if p else None,
                department_id=d.id if d else None,
                department_name=d.name if d else None,
                department_code=d.code if d else None,
                school_id=s.id if s else None,
                school_name=s.name if s else None,
                school_code=s.code if s else None,
                enrolled_courses_count=len(courses),
                enrolled_courses=courses,
            )
        )

    return PaginatedStudentAcademicResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        total_active=total_active,
    )





@router.post("/students/bulk-import", response_model=StudentBulkImportResponse, status_code=status.HTTP_200_OK, tags=["Academic Flow - Students Directory"])
async def bulk_import_students(
    payload: StudentBulkImportRequest,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    """Bulk import students from CSV/roster, create accounts, link batch, and optionally enroll in courses/sections."""
    # 1. Validate Batch
    batch = await db.get(Batch, payload.batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Selected Batch ID does not exist.")

    # 2. Validate Course Offerings if provided (only enroll if explicitly selected)
    offering_ids = list(payload.course_offering_ids or [])

    # 3. Process students
    created_count = 0
    updated_count = 0
    errors = []
    processed_student_user_ids = []

    # Cache existing users by email and student profiles by roll_number for speed
    all_emails = {s.email.strip().lower() for s in payload.students if s.email and s.email.strip()}
    all_rolls = {s.roll_number.strip().upper() for s in payload.students if s.roll_number and s.roll_number.strip()}

    existing_users_by_email = {}
    if all_emails:
        u_stmt = select(User).where(User.email.in_(all_emails))
        for u in (await db.exec(u_stmt)).all():
            existing_users_by_email[u.email.lower()] = u

    existing_profiles_by_roll = {}
    if all_rolls:
        sp_stmt = select(StudentProfile).where(StudentProfile.roll_number.in_(all_rolls))
        for sp in (await db.exec(sp_stmt)).all():
            existing_profiles_by_roll[sp.roll_number.upper()] = sp

    seen_rolls = set()

    for student_item in payload.students:
        roll = student_item.roll_number.strip()
        name = student_item.full_name.strip()
        if not roll or not name:
            errors.append(f"Skipped invalid record: roll='{roll}', name='{name}'")
            continue

        if roll.upper() in seen_rolls:
            continue
        seen_rolls.add(roll.upper())

        raw_email = student_item.email.strip().lower() if student_item.email and student_item.email.strip() and '@' in student_item.email else None
        if not raw_email:
            sanitized_roll = re.sub(r'[^a-zA-Z0-9]', '', roll).lower()
            raw_email = f"{sanitized_roll}@gbu.ac.in"

        # Check existing profile or existing user
        existing_profile = existing_profiles_by_roll.get(roll.upper())
        existing_user = existing_users_by_email.get(raw_email)

        if existing_profile:
            # Student profile exists -> update batch and user full name
            user = await db.get(User, existing_profile.user_id)
            if user:
                user.full_name = name
                db.add(user)
            existing_profile.batch_id = payload.batch_id
            db.add(existing_profile)
            updated_count += 1
            processed_student_user_ids.append(existing_profile.user_id)
        elif existing_user:
            # User exists with this email -> link student profile
            user_profile = (await db.exec(
                select(StudentProfile).where(StudentProfile.user_id == existing_user.id)
            )).first()
            if user_profile:
                user_profile.roll_number = roll
                user_profile.batch_id = payload.batch_id
                db.add(user_profile)
            else:
                new_profile = StudentProfile(
                    user_id=existing_user.id,
                    roll_number=roll,
                    batch_id=payload.batch_id
                )
                db.add(new_profile)
                existing_profiles_by_roll[roll.upper()] = new_profile

            existing_user.full_name = name
            db.add(existing_user)
            updated_count += 1
            processed_student_user_ids.append(existing_user.id)
        else:
            # Create new user, role assignment, and student profile
            default_pwd = roll if len(roll) >= 6 else "GBU@12345"
            new_user = User(
                email=raw_email,
                full_name=name,
                hashed_password=get_password_hash(default_pwd),
                is_active=True,
                must_change_password=True
            )
            db.add(new_user)
            await db.flush()  # assign new_user.id

            role_assignment = RoleAssignment(
                user_id=new_user.id,
                role=RoleEnum.STUDENT,
                scope_type=ScopeTypeEnum.UNIVERSITY,
                scope_id=None
            )
            db.add(role_assignment)

            new_profile = StudentProfile(
                user_id=new_user.id,
                roll_number=roll,
                batch_id=payload.batch_id
            )
            db.add(new_profile)
            existing_profiles_by_roll[roll.upper()] = new_profile
            existing_users_by_email[raw_email] = new_user

            created_count += 1
            processed_student_user_ids.append(new_user.id)

    # 4. Enroll into course offerings if any
    total_enrolled = 0
    if offering_ids and processed_student_user_ids:
        for off_id in offering_ids:
            # check existing enrollments
            existing_enrollments = set((await db.exec(
                select(StudentSubjectMap.student_id).where(
                    StudentSubjectMap.course_offering_id == off_id,
                    StudentSubjectMap.student_id.in_(processed_student_user_ids)
                )
            )).all())

            to_enroll = set(processed_student_user_ids) - existing_enrollments
            for s_id in to_enroll:
                db.add(StudentSubjectMap(student_id=s_id, course_offering_id=off_id))
                total_enrolled += 1

    await db.commit()

    return StudentBulkImportResponse(
        status="success",
        message=f"Processed {created_count + updated_count} students: {created_count} created, {updated_count} updated, {total_enrolled} course enrollments created.",
        total_processed=created_count + updated_count,
        created_count=created_count,
        updated_count=updated_count,
        enrolled_count=total_enrolled,
        skipped_count=len(payload.students) - (created_count + updated_count),
        errors=errors
    )





@router.delete("/students/{student_id}/courses/{offering_id}", status_code=status.HTTP_200_OK, tags=["Academic Flow - Students Directory"])
async def unenroll_student_from_course(
    student_id: int,
    offering_id: int,
    admin_id: int = Depends(require_coordinator),
    db: AsyncSession = Depends(get_session)
):
    """Unenroll a student from a specific course offering."""
    mapping = (await db.exec(
        select(StudentSubjectMap).where(
            StudentSubjectMap.student_id == student_id,
            StudentSubjectMap.course_offering_id == offering_id
        )
    )).first()
    if not mapping:
        raise HTTPException(status_code=404, detail="Student is not enrolled in this course offering.")

    await db.delete(mapping)
    await db.commit()
    return {"status": "success", "message": "Student successfully unenrolled from course."}


# ============================================================================
# 5. SAFE DELETION ENDPOINTS
# ============================================================================

@router.delete("/schools/{school_id}", status_code=status.HTTP_200_OK, tags=["Academic Flow - Hierarchy"])
async def delete_school(
    school_id: int,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    school = await db.get(School, school_id)
    if not school:
        raise HTTPException(status_code=404, detail="School not found.")

    linked_departments = (await db.exec(select(Department).where(Department.school_id == school_id))).first()
    if linked_departments:
        raise HTTPException(status_code=400, detail="Cannot delete school. It is linked to one or more departments.")

    await db.delete(school)
    await db.commit()
    return {"status": "success", "message": f"School '{school.name}' deleted successfully."}


@router.delete("/departments/{department_id}", status_code=status.HTTP_200_OK, tags=["Academic Flow - Hierarchy"])
async def delete_department(
    department_id: int,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    department = await db.get(Department, department_id)
    if not department:
        raise HTTPException(status_code=404, detail="Department not found.")

    linked_programmes = (await db.exec(select(Programme).where(Programme.department_id == department_id))).first()
    if linked_programmes:
        raise HTTPException(status_code=400, detail="Cannot delete department. It is linked to one or more programmes.")

    await db.delete(department)
    await db.commit()
    return {"status": "success", "message": f"Department '{department.name}' deleted successfully."}


@router.delete("/programmes/{programme_id}", status_code=status.HTTP_200_OK, tags=["Academic Flow - Hierarchy"])
async def delete_programme(
    programme_id: int,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    programme = await db.get(Programme, programme_id)
    if not programme:
        raise HTTPException(status_code=404, detail="Programme not found.")

    linked_specs = (await db.exec(select(Specialization).where(Specialization.programme_id == programme_id))).first()
    if linked_specs:
        raise HTTPException(status_code=400, detail="Cannot delete programme. It is linked to one or more specializations.")

    await db.delete(programme)
    await db.commit()
    return {"status": "success", "message": f"Programme '{programme.name}' deleted successfully."}


@router.delete("/specializations/{specialization_id}", status_code=status.HTTP_200_OK, tags=["Academic Flow - Hierarchy"])
async def delete_specialization(
    specialization_id: int,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    specialization = await db.get(Specialization, specialization_id)
    if not specialization:
        raise HTTPException(status_code=404, detail="Specialization not found.")

    linked_batches = (await db.exec(select(Batch).where(Batch.specialization_id == specialization_id))).first()
    if linked_batches:
        raise HTTPException(status_code=400, detail="Cannot delete specialization. It is linked to one or more batches.")

    await db.delete(specialization)
    await db.commit()
    return {"status": "success", "message": f"Specialization '{specialization.name}' deleted successfully."}


@router.delete("/batches/{batch_id}", status_code=status.HTTP_200_OK, tags=["Academic Flow - Hierarchy"])
async def delete_batch(
    batch_id: int,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    batch = await db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found.")

    linked_students = (await db.exec(select(StudentProfile).where(StudentProfile.batch_id == batch_id))).first()
    if linked_students:
        raise HTTPException(status_code=400, detail="Cannot delete batch. Students are currently assigned to it.")

    linked_sections = (await db.exec(select(Section).where(Section.batch_id == batch_id))).first()
    if linked_sections:
        raise HTTPException(status_code=400, detail="Cannot delete batch. Sections are currently assigned to it.")

    await db.delete(batch)
    await db.commit()
    return {"status": "success", "message": f"Batch '{batch.start_year}-{batch.expected_end_year}' deleted successfully."}


@router.delete("/sections/{section_id}", status_code=status.HTTP_200_OK, tags=["Academic Flow - Hierarchy"])
async def delete_section(
    section_id: int,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    section = await db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found.")

    linked_courses = (await db.exec(select(CourseOffering).where(CourseOffering.section_id == section_id))).first()
    if linked_courses:
        raise HTTPException(status_code=400, detail="Cannot delete section. Course offerings are linked to it.")

    await db.delete(section)
    await db.commit()
    return {"status": "success", "message": f"Section '{section.name}' deleted successfully."}


@router.delete("/semesters/{term_id}", status_code=status.HTTP_200_OK, tags=["HOD - Academic Setup"])
@router.delete("/terms/{term_id}", status_code=status.HTTP_200_OK, tags=["HOD - Academic Setup"])
async def delete_semester(
    term_id: int,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    term = await db.get(AcademicTerm, term_id)
    if not term:
        raise HTTPException(status_code=404, detail="Academic Term not found.")

    if term.is_active:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the active semester. Please activate another semester before deleting this one."
        )

    linked_sections = (await db.exec(select(Section).where(Section.term_id == term_id))).first()
    if linked_sections:
        raise HTTPException(status_code=400, detail="Cannot delete term. Sections are linked to it.")

    linked_courses = (await db.exec(select(CourseOffering).where(CourseOffering.term_id == term_id))).first()
    if linked_courses:
        raise HTTPException(status_code=400, detail="Cannot delete term. Course offerings are linked to it.")

    await db.delete(term)
    await db.commit()
    return {"status": "success", "message": f"Term '{term.name}' deleted successfully."}


@router.delete("/subjects/{subject_id}", status_code=status.HTTP_200_OK, tags=["HOD - Academic Setup"])
async def delete_subject(
    subject_id: int,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    subject = await db.get(Subject, subject_id)
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found.")

    linked_courses = (await db.exec(select(CourseOffering).where(CourseOffering.subject_id == subject_id))).first()
    if linked_courses:
        raise HTTPException(status_code=400, detail="Cannot delete subject. It is actively offered in one or more classes.")

    await db.delete(subject)
    await db.commit()
    return {"status": "success", "message": f"Subject '{subject.course_code}' deleted successfully."}


@router.delete("/course-offerings/{course_offering_id}", status_code=status.HTTP_200_OK, tags=["HOD - Academic Setup"])
async def delete_course_offering(
    course_offering_id: int,
    admin_id: int = Depends(require_coordinator),
    db: AsyncSession = Depends(get_session)
):
    offering = await db.get(CourseOffering, course_offering_id)
    if not offering:
        raise HTTPException(status_code=404, detail="Course offering not found.")

    # 1. Guard against deleting offerings that already have recorded attendance
    linked_sessions = (await db.exec(
        select(AttendanceSession).where(AttendanceSession.course_offering_id == course_offering_id)
    )).first()
    if linked_sessions:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete course offering. Attendance sessions have already been recorded for this class. Reassign the teacher instead."
        )

    # 2. Clean up foreign key references
    linked_timetable = (await db.exec(
        select(TimetableEntry).where(TimetableEntry.course_offering_id == course_offering_id)
    )).all()
    for entry in linked_timetable:
        await db.delete(entry)

    linked_substitutes = (await db.exec(
        select(SubstituteGrant).where(SubstituteGrant.course_offering_id == course_offering_id)
    )).all()
    for sub in linked_substitutes:
        await db.delete(sub)

    linked_students = (await db.exec(
        select(StudentSubjectMap).where(StudentSubjectMap.course_offering_id == course_offering_id)
    )).all()
    for mapping in linked_students:
        await db.delete(mapping)

    # 3. Safely delete the course offering
    await db.delete(offering)
    await db.commit()
    return {"status": "success", "message": "Course offering deleted successfully."}


@router.patch("/course-offerings/{course_offering_id}/reassign", status_code=status.HTTP_200_OK, tags=["HOD - Academic Setup"])
async def reassign_teacher_to_offering(
    course_offering_id: int,
    payload: CourseOfferingReassign,
    admin_id: int = Depends(require_coordinator),
    db: AsyncSession = Depends(get_session)
):
    offering = await db.get(CourseOffering, course_offering_id)
    if not offering:
        raise HTTPException(status_code=404, detail="Course offering not found.")

    new_teacher = await db.get(User, payload.teacher_id)
    if not new_teacher:
        raise HTTPException(status_code=404, detail="Selected teacher not found.")

    role_stmt = select(RoleAssignment).where(RoleAssignment.user_id == new_teacher.id)
    user_roles = [r.role for r in (await db.exec(role_stmt)).all()]
    if not any(r in [RoleEnum.TEACHER, RoleEnum.COORDINATOR, RoleEnum.HOD, RoleEnum.ADMIN] for r in user_roles):
        raise HTTPException(status_code=400, detail="The assigned user is not a faculty member.")

    offering.teacher_id = payload.teacher_id
    db.add(offering)
    await db.commit()
    await db.refresh(offering)
    return {"status": "success", "message": f"Successfully reassigned class to {new_teacher.full_name}."}