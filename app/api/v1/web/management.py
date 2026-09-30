from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, or_
from sqlalchemy.orm import selectinload

from typing import List, Optional
from pydantic import BaseModel

from app.core.database import get_session
from app.core.security import get_password_hash
from app.api.deps import require_hod, require_admin
from app.models.academic import (
    Department,
    Programme,
    Specialization,
    Batch,
    Section,
    AcademicTerm,
    Subject,
    CourseOffering,
    StudentSubjectMap,
    TermCreate,
    CourseOfferingCreate,
)
from app.models.user import (
    User,
    RoleEnum,
    RoleAssignment,
    ScopeTypeEnum,
    StudentProfile,
)


# ============================================================================
# SCHEMAS
# ============================================================================

# --- Academic Hierarchy Schemas ---

class DepartmentCreate(BaseModel):
    name: str
    code: str

class DepartmentResponse(BaseModel):
    id: int
    name: str
    code: str

class ProgrammeCreate(BaseModel):
    department_code: str  # Replaced department_id
    name: str
    code: str
    total_semesters: int

class ProgrammeResponse(BaseModel):
    id: int
    name: str
    code: str
    department_code: str

class SpecializationCreate(BaseModel):
    programme_code: str  # Replaced programme_id
    name: str
    code: str

class SpecializationResponse(BaseModel):
    id: int
    name: str
    code: str
    programme_code: str

class BatchCreate(BaseModel):
    name: str                   # <-- Accepts manual user input
    programme_code: str
    specialization_code: str
    start_year: int
    expected_end_year: int
class SectionCreate(BaseModel):
    name: str
    batch_id: int
    term_id: int

class SubjectCreate(BaseModel):
    course_code: str
    name: str
    is_elective: bool = False

# --- Class & Enrollment Schemas ---

class FacultyResponse(BaseModel):
    id: int
    full_name: str
    email: str
    role: RoleEnum

class BulkEnrollmentCreate(BaseModel):
    course_offering_id: int
    student_ids: List[int]

class CourseOfferingListResponse(BaseModel):
    id: int
    term_name: str
    section_name: str
    subject_name: str
    teacher_name: str

# --- User Provisioning Schemas ---

class UserCreate(BaseModel):
    email: str
    full_name: str
    password: str
    role: RoleEnum
    scope_type: ScopeTypeEnum = ScopeTypeEnum.UNIVERSITY
    scope_id: Optional[int] = None
    
    # Required if role == RoleEnum.STUDENT
    roll_number: Optional[str] = None
    batch_id: Optional[int] = None

class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str
    is_active: bool
    role: RoleEnum
    scope_type: ScopeTypeEnum
    scope_id: Optional[int]
    roll_number: Optional[str] = None
    batch_id: Optional[int] = None


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
    """Create a user, assign their role scope, and link a student to a batch if applicable."""
    existing_user = (await db.exec(select(User).where(User.email == payload.email))).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="User with this email already exists.")

    # Student-specific validations
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

    new_user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=get_password_hash(payload.password),
        is_active=True
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    role_assignment = RoleAssignment(
        user_id=new_user.id,
        role=payload.role,
        scope_type=payload.scope_type,
        scope_id=payload.scope_id
    )
    db.add(role_assignment)

    roll_number_out = None
    batch_id_out = None

    if payload.role == RoleEnum.STUDENT:
        student_profile = StudentProfile(
            user_id=new_user.id,
            roll_number=payload.roll_number,
            batch_id=payload.batch_id
        )
        db.add(student_profile)
        roll_number_out = payload.roll_number
        batch_id_out = payload.batch_id

    await db.commit()

    return UserResponse(
        id=new_user.id,
        email=new_user.email,
        full_name=new_user.full_name,
        is_active=new_user.is_active,
        role=role_assignment.role,
        scope_type=role_assignment.scope_type,
        scope_id=role_assignment.scope_id,
        roll_number=roll_number_out,
        batch_id=batch_id_out
    )

@router.get("/users", response_model=List[UserResponse], tags=["Admin - User Provisioning"])
async def list_users(
    role: Optional[RoleEnum] = Query(default=None),
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_session)
):
    """List users with optional role filtering and eagerly loaded student profiles."""
    statement = (
        select(User)
        .options(
            selectinload(User.role_assignments),
            selectinload(User.student_profile)
        )
        .order_by(User.full_name)
    )
    result = await db.exec(statement)
    
    users = []
    for user in result.all():
        primary_assignment = user.role_assignments[0] if user.role_assignments else None
        user_role = primary_assignment.role if primary_assignment else RoleEnum.STUDENT

        if role and user_role != role:
            continue

        users.append(UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            role=user_role,
            scope_type=primary_assignment.scope_type if primary_assignment else ScopeTypeEnum.UNIVERSITY,
            scope_id=primary_assignment.scope_id if primary_assignment else None,
            roll_number=user.student_profile.roll_number if user.student_profile else None,
            batch_id=user.student_profile.batch_id if user.student_profile else None
        ))
        
    return users

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


# ============================================================================
# 2. HOD / ADMIN: ACADEMIC HIERARCHY SETUP (FLOW / PROGRAMMES)
# ============================================================================

# --- Departments ---

@router.post("/departments", status_code=status.HTTP_201_CREATED, response_model=DepartmentResponse, tags=["Academic Flow - Hierarchy"])
async def create_department(
    payload: DepartmentCreate,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    existing = (await db.exec(
        select(Department).where(
            or_(Department.name == payload.name, Department.code == payload.code.upper())
        )
    )).first()
    if existing:
        raise HTTPException(status_code=400, detail="Department name or code already exists.")
    
    dept = Department(name=payload.name, code=payload.code.upper())
    db.add(dept)
    await db.commit()
    await db.refresh(dept)
    
    return DepartmentResponse(id=dept.id, name=dept.name, code=dept.code)

@router.get("/departments", response_model=List[DepartmentResponse], tags=["Academic Flow - Hierarchy"])
async def get_departments(
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    result = await db.exec(select(Department).order_by(Department.name))
    return [
        DepartmentResponse(id=d.id, name=d.name, code=d.code) 
        for d in result.all()
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
    # 1. Resolve and validate the Programme from code
    programme = (await db.execute(
        select(Programme).where(Programme.code == payload.programme_code.upper().strip())
    )).scalar_one_or_none()
    
    if not programme:
        raise HTTPException(status_code=404, detail=f"Programme '{payload.programme_code}' not found")

    # 2. Resolve and validate the Specialization strictly under this programme
    spec = (await db.execute(
        select(Specialization).where(
            Specialization.code == payload.specialization_code.upper().strip(),
            Specialization.programme_id == programme.id,
        )
    )).scalar_one_or_none()
    
    if not spec:
        raise HTTPException(status_code=404, detail=f"Specialization '{payload.specialization_code}' not found under {programme.code}")

    # 3. Use the exact manual name provided by the user from the frontend form
    batch = Batch(
        name=payload.name.strip(),          # <-- Directly uses user input (e.g. "Lateral Entry")
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
    # 1. Verify Batch and Term exist
    batch = await db.get(Batch, payload.batch_id)
    term = await db.get(AcademicTerm, payload.term_id)
    if not batch or not term:
        raise HTTPException(status_code=404, detail="Invalid Batch ID or Term ID provided.")

    # 2. Check for duplicate section name in the same batch & term combo
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

    # 3. Create the section
    section = Section(
        name=payload.name.strip().upper(),
        batch_id=payload.batch_id,
        term_id=payload.term_id
    )
    
    db.add(section)
    await db.commit()
    await db.refresh(section)
    
    return section

@router.get("/sections", response_model=List[Section], tags=["Academic Flow - Hierarchy"])
async def get_all_sections(
    batch_id: Optional[int] = Query(default=None),
    term_id: Optional[int] = Query(default=None),
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    stmt = select(Section)
    if batch_id:
        stmt = stmt.where(Section.batch_id == batch_id)
    if term_id:
        stmt = stmt.where(Section.term_id == term_id)
    result = await db.exec(stmt.order_by(Section.name))
    return result.all()


# ============================================================================
# 3. TERMS & SUBJECTS
# ============================================================================

@router.post("/terms", status_code=status.HTTP_201_CREATED, response_model=AcademicTerm, tags=["HOD - Academic Setup"])
async def create_term(
    payload: TermCreate,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    # If this new term is set to active, deactivate all other terms first
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
    admin_id: int = Depends(require_hod),
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
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    teacher = await db.get(User, payload.teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="User not found.")
        
    role_stmt = select(RoleAssignment).where(RoleAssignment.user_id == teacher.id)
    user_roles = [r.role for r in (await db.exec(role_stmt)).all()]
    if not any(r in [RoleEnum.TEACHER, RoleEnum.COORDINATOR, RoleEnum.HOD] for r in user_roles):
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
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    statement = (
        select(
            CourseOffering.id,
            AcademicTerm.name.label("term_name"),
            Section.name.label("section_name"),
            Subject.name.label("subject_name"),
            User.full_name.label("teacher_name")
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
            teacher_name=row.teacher_name
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

@router.delete("/departments/{department_id}", status_code=status.HTTP_200_OK, tags=["Academic Flow - Hierarchy"])
async def delete_department(
    department_id: int,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    department = await db.get(Department, department_id)
    if not department:
        raise HTTPException(status_code=404, detail="Department not found.")

    # Check for linked programmes
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

    # Check for linked specializations
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

    # Check for linked batches
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

    # Check for linked student profiles
    linked_students = (await db.exec(select(StudentProfile).where(StudentProfile.batch_id == batch_id))).first()
    if linked_students:
        raise HTTPException(status_code=400, detail="Cannot delete batch. Students are currently assigned to it.")

    # Check for linked sections
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

    # Check for linked course offerings
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

    # Check for linked sections
    linked_sections = (await db.exec(select(Section).where(Section.term_id == term_id))).first()
    if linked_sections:
        raise HTTPException(status_code=400, detail="Cannot delete term. Sections are linked to it.")

    # Check for linked course offerings
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

    # Check for linked course offerings
    linked_courses = (await db.exec(select(CourseOffering).where(CourseOffering.subject_id == subject_id))).first()
    if linked_courses:
        raise HTTPException(status_code=400, detail="Cannot delete subject. It is actively offered in one or more classes.")

    await db.delete(subject)
    await db.commit()
    return {"status": "success", "message": f"Subject '{subject.course_code}' deleted successfully."}


@router.delete("/course-offerings/{course_offering_id}", status_code=status.HTTP_200_OK, tags=["HOD - Academic Setup"])
async def delete_course_offering(
    course_offering_id: int,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    offering = await db.get(CourseOffering, course_offering_id)
    if not offering:
        raise HTTPException(status_code=404, detail="Course offering not found.")

    # Check for enrolled students
    linked_students = (await db.exec(select(StudentSubjectMap).where(StudentSubjectMap.course_offering_id == course_offering_id))).first()
    if linked_students:
        raise HTTPException(status_code=400, detail="Cannot delete course offering. Students are currently enrolled in it.")

    await db.delete(offering)
    await db.commit()
    return {"status": "success", "message": "Course offering deleted successfully."}