from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, or_, delete
from sqlalchemy.orm import selectinload

from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy import func

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
    SpecializationCreate,
    SpecializationResponse,
    BatchCreate,
    SectionCreate,
    SectionResponse,
    SubjectCreate,
    TermCreate,
    CourseOfferingCreate,
    FacultyResponse,
    BulkEnrollmentCreate,
    CourseOfferingListResponse,
    CourseOfferingReassign,
)

from app.schemas.user import (
    UserCreate,
    UserResponse,
    PaginatedUserResponse,
    RoleAssignmentItem,
    RoleAssignmentResponse,
    UpdateUserRolesRequest,
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
        if payload.scope_type != ScopeTypeEnum.PROGRAM or not payload.scope_id:
            raise HTTPException(status_code=400, detail="Coordinators must have a scope_type of PROGRAM and a valid scope_id.")
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
            st = ScopeTypeEnum.PROGRAM
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
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=100),
    user_id: int = Depends(require_hod),  # <-- Changed dependency here
    db: AsyncSession = Depends(get_session)
):
    """List users with pagination, optional role filtering, and eagerly loaded student profiles."""
    
    # 1. Base queries
    count_stmt = select(func.count(User.id))
    stmt = select(User).options(
        selectinload(User.role_assignments),
        selectinload(User.student_profile)
    )

    # 2. Apply role filter in SQL (crucial for accurate pagination)
    if role:
        count_stmt = count_stmt.join(RoleAssignment).where(RoleAssignment.role == role)
        stmt = stmt.join(RoleAssignment).where(RoleAssignment.role == role)

    # 3. Execute total count
    total_users = (await db.exec(count_stmt)).one()

    # 4. Apply pagination offsets and fetch the specific page
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
            st = ScopeTypeEnum.PROGRAM
            if not sid:
                raise HTTPException(status_code=400, detail="Programme scope is required for Coordinator role.")
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
        department_code=dept.code
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
            department_code=dept.code
        )
        for prog, dept in result.all()
    ]

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

    spec = (await db.execute(
        select(Specialization).where(
            Specialization.code == payload.specialization_code.upper().strip(),
            Specialization.programme_id == programme.id,
        )
    )).scalar_one_or_none()
    
    if not spec:
        raise HTTPException(status_code=404, detail=f"Specialization '{payload.specialization_code}' not found under {programme.code}")

    batch = Batch(
        name=payload.name.strip(),          
        programme_id=programme.id,
        specialization_id=spec.id,
        start_year=payload.start_year,
        expected_end_year=payload.expected_end_year,
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
        )
        for row in result
    ]


# ============================================================================
# 3. TERMS & SUBJECTS
# ============================================================================

@router.post("/terms", status_code=status.HTTP_201_CREATED, response_model=AcademicTerm, tags=["HOD - Academic Setup"])
async def create_term(
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
        is_active=payload.is_active
    )
    db.add(new_term)
    await db.commit()
    await db.refresh(new_term)
    return new_term

@router.get("/terms", response_model=List[AcademicTerm], tags=["HOD - Academic Setup"])
async def get_all_terms(
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    result = await db.exec(select(AcademicTerm).order_by(AcademicTerm.start_date.desc()))
    return result.all()

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
            CourseOffering.subject_id.label("subject_id")
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
            subject_id=row.subject_id
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


@router.delete("/terms/{term_id}", status_code=status.HTTP_200_OK, tags=["HOD - Academic Setup"])
async def delete_term(
    term_id: int,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    term = await db.get(AcademicTerm, term_id)
    if not term:
        raise HTTPException(status_code=404, detail="Academic Term not found.")

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