from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from typing import List
from pydantic import BaseModel

from app.core.database import get_session
from app.api.deps import require_hod
from app.models.academic import (
    AcademicTerm, 
    CourseOffering, 
    TermCreate, 
    CourseOfferingCreate, 
    Subject, 
    Section, 
    StudentSubjectMap
)
from app.models.user import User, RoleEnum


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

router = APIRouter()

# --- CREATION ENDPOINTS ---

@router.post("/terms", status_code=status.HTTP_201_CREATED)
async def create_term(
    payload: TermCreate,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    """Create a new academic semester/term."""
    
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

@router.post("/course-offerings", status_code=status.HTTP_201_CREATED)
async def assign_teacher_to_class(
    payload: CourseOfferingCreate,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    """Map a Teacher to a Subject and Section for a specific Term."""
    
    teacher = await db.get(User, payload.teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="User not found.")
    if teacher.role not in [RoleEnum.TEACHER, RoleEnum.COORDINATOR, RoleEnum.HOD]:
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

@router.post("/enrollments/bulk", status_code=status.HTTP_201_CREATED)
async def bulk_enroll_students(
    payload: BulkEnrollmentCreate,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    """Enroll multiple students into a single course offering."""
    
    offering = await db.get(CourseOffering, payload.course_offering_id)
    if not offering:
        raise HTTPException(status_code=404, detail="Course offering not found.")

    if not payload.student_ids:
        raise HTTPException(status_code=400, detail="No student IDs provided.")

    valid_students_query = select(User.id).where(
        User.id.in_(payload.student_ids),
        User.role == RoleEnum.STUDENT,
        User.is_active == True
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

# --- DATA FETCHERS FOR FRONTEND DROPDOWNS ---

@router.get("/terms", response_model=List[AcademicTerm])
async def get_all_terms(
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    """Fetch all academic terms for the dropdown."""
    result = await db.exec(select(AcademicTerm).order_by(AcademicTerm.start_date.desc()))
    return result.all()
@router.get("/course-offerings", response_model=List[CourseOfferingListResponse])
async def get_all_course_offerings(
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    """Fetch all course offerings with human-readable names for the enrollment dropdown."""
    
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
    
    # Map the raw SQL results into our Pydantic schema
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

@router.get("/subjects", response_model=List[Subject])
async def get_all_subjects(
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    """Fetch all subjects for the dropdown."""
    result = await db.exec(select(Subject).order_by(Subject.name))
    return result.all()

@router.get("/sections", response_model=List[Section])
async def get_all_sections(
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    """Fetch all sections for the dropdown."""
    result = await db.exec(select(Section).order_by(Section.name))
    return result.all()

@router.get("/faculty", response_model=List[FacultyResponse])
async def get_faculty_list(
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    """Fetch all active faculty members (Teachers, Coordinators, HODs, Admins)."""
    allowed_roles = [RoleEnum.TEACHER, RoleEnum.COORDINATOR, RoleEnum.HOD, RoleEnum.ADMIN]
    
    statement = (
        select(User)
        .where(User.role.in_(allowed_roles))
        .where(User.is_active == True)
        .order_by(User.full_name)
    )
    
    result = await db.exec(statement)
    return result.all()