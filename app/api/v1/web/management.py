from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from sqlalchemy.orm import selectinload

from typing import List, Optional
from pydantic import BaseModel

from app.core.database import get_session
from app.core.security import get_password_hash
from app.api.deps import require_hod, require_admin
from app.models.academic import (
    AcademicTerm, 
    CourseOffering, 
    TermCreate, 
    CourseOfferingCreate, 
    Subject, 
    Section, 
    StudentSubjectMap
)
from app.models.user import User, RoleEnum, RoleAssignment, ScopeTypeEnum


# --- SCHEMAS ---

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

class UserCreate(BaseModel):
    email: str
    full_name: str
    password: str
    role: RoleEnum
    scope_type: ScopeTypeEnum
    scope_id: Optional[int] = None

class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str
    is_active: bool
    role: RoleEnum
    scope_type: ScopeTypeEnum
    scope_id: Optional[int]


router = APIRouter()

# --- ADMIN: USER MANAGEMENT ---

@router.post("/users", status_code=status.HTTP_201_CREATED, response_model=UserResponse, tags=["Admin - User Provisioning"])
async def create_user(
    payload: UserCreate,
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_session)
):
    existing_user_stmt = select(User).where(User.email == payload.email)
    existing_user = (await db.exec(existing_user_stmt)).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="User with this email already exists.")

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
    await db.commit()

    return UserResponse(
        id=new_user.id,
        email=new_user.email,
        full_name=new_user.full_name,
        is_active=new_user.is_active,
        role=role_assignment.role,
        scope_type=role_assignment.scope_type,
        scope_id=role_assignment.scope_id
    )

@router.get("/users", response_model=List[UserResponse], tags=["Admin - User Provisioning"])
async def list_users(
    admin_id: int = Depends(require_admin),
    db: AsyncSession = Depends(get_session)
):
    statement = select(User).options(selectinload(User.role_assignments)).order_by(User.full_name)
    result = await db.exec(statement)
    
    users = []
    for user in result.all():
        primary_assignment = user.role_assignments[0] if user.role_assignments else None
        
        users.append(UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            role=primary_assignment.role if primary_assignment else RoleEnum.STUDENT,
            scope_type=primary_assignment.scope_type if primary_assignment else ScopeTypeEnum.UNIVERSITY,
            scope_id=primary_assignment.scope_id if primary_assignment else None
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
    """Restore access for a previously deactivated user."""
    
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if user.is_active:
        return {"status": "success", "message": f"User {user.email} is already active."}

    user.is_active = True
    db.add(user)
    await db.commit()
    
    return {"status": "success", "message": f"User {user.email} has been reactivated."}


# --- HOD: ACADEMIC SETUP ---

@router.post("/terms", status_code=status.HTTP_201_CREATED, tags=["HOD - Academic Setup"])
async def create_term(
    payload: TermCreate,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    if payload.is_active:
        await db.exec(
            select(AcademicTerm).where(AcademicTerm.is_active == True)
        )

    new_term = AcademicTerm(
        name=payload.name,
        start_date=payload.start_date,
        end_date=payload.end_date,
        is_active=payload.is_active
    )
    
    db.add(new_term)
    await db.commit()
    await db.refresh(new_term)
    
    return new_term

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
    role_result = await db.exec(role_stmt)
    user_roles = [r.role for r in role_result.all()]
    
    if not any(r in [RoleEnum.TEACHER, RoleEnum.COORDINATOR, RoleEnum.HOD] for r in user_roles):
        raise HTTPException(status_code=400, detail="The assigned user is not a faculty member.")

    term = await db.get(AcademicTerm, payload.term_id)
    section = await db.get(Section, payload.section_id)
    subject = await db.get(Subject, payload.subject_id)
    
    if not all([term, section, subject]):
        raise HTTPException(status_code=404, detail="Invalid Term, Section, or Subject ID provided.")

    existing_assignment = await db.exec(
        select(CourseOffering).where(
            CourseOffering.term_id == payload.term_id,
            CourseOffering.section_id == payload.section_id,
            CourseOffering.subject_id == payload.subject_id
        )
    )
    if existing_assignment.first():
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
    valid_students_result = await db.exec(valid_students_query)
    valid_student_ids = set(valid_students_result.all())

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
    existing_result = await db.exec(existing_enrollments_query)
    already_enrolled = set(existing_result.all())

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

# --- HOD: DATA FETCHERS ---

@router.get("/terms", response_model=List[AcademicTerm], tags=["HOD - Data Fetchers"])
async def get_all_terms(
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    result = await db.exec(select(AcademicTerm).order_by(AcademicTerm.start_date.desc()))
    return result.all()

@router.get("/course-offerings", response_model=List[CourseOfferingListResponse], tags=["HOD - Data Fetchers"])
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
    
    offerings = []
    for row in result:
        offerings.append(
            CourseOfferingListResponse(
                id=row.id,
                term_name=row.term_name,
                section_name=row.section_name,
                subject_name=row.subject_name,
                teacher_name=row.teacher_name
            )
        )
        
    return offerings

@router.get("/subjects", response_model=List[Subject], tags=["HOD - Data Fetchers"])
async def get_all_subjects(
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    result = await db.exec(select(Subject).order_by(Subject.name))
    return result.all()

@router.get("/sections", response_model=List[Section], tags=["HOD - Data Fetchers"])
async def get_all_sections(
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    result = await db.exec(select(Section).order_by(Section.name))
    return result.all()

@router.get("/faculty", response_model=List[FacultyResponse], tags=["HOD - Data Fetchers"])
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