#app/api/v1/web/management.py

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.api.deps import require_hod
from app.models.academic import AcademicTerm, CourseOffering, TermCreate, CourseOfferingCreate, Subject, Section
from app.models.user import User, RoleEnum

router = APIRouter()

@router.post("/terms", status_code=status.HTTP_201_CREATED)
async def create_term(
    payload: TermCreate,
    admin_id: int = Depends(require_hod),
    db: AsyncSession = Depends(get_session)
):
    """Create a new academic semester/term."""
    
    # Optional: If creating an active term, you might want to deactivate old ones
    if payload.is_active:
        await db.exec(
            select(AcademicTerm).where(AcademicTerm.is_active == True)
            # In a full app, you would run an UPDATE here to set others to False
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
    
    # 1. Validation: Does the user exist AND are they actually a teacher?
    teacher = await db.get(User, payload.teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="User not found.")
    if teacher.role not in [RoleEnum.TEACHER, RoleEnum.COORDINATOR, RoleEnum.HOD]:
        raise HTTPException(status_code=400, detail="The assigned user is not a faculty member.")

    # 2. Validation: Do the academic entities exist?
    term = await db.get(AcademicTerm, payload.term_id)
    section = await db.get(Section, payload.section_id)
    subject = await db.get(Subject, payload.subject_id)
    
    if not all([term, section, subject]):
        raise HTTPException(status_code=404, detail="Invalid Term, Section, or Subject ID provided.")

    # 3. Idempotency: Prevent assigning the same teacher to the exact same class twice
    existing_assignment = await db.exec(
        select(CourseOffering).where(
            CourseOffering.term_id == payload.term_id,
            CourseOffering.section_id == payload.section_id,
            CourseOffering.subject_id == payload.subject_id
        )
    )
    if existing_assignment.first():
        raise HTTPException(status_code=400, detail="A teacher is already assigned to this specific class.")

    # 4. Save the mapping
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