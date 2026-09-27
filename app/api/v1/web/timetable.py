#app/api/v1/web/timetable.py

from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from typing import List

from app.core.database import get_session
from app.api.deps import require_coordinator
from app.models.academic import CourseOffering, Subject, Section, TimetableEntry, TimetableSlotResponse

router = APIRouter()


@router.get("/", response_model=List[TimetableSlotResponse])
async def list_timetable(
    term_id: int,
    section_id: int,
    coordinator_id: int = Depends(require_coordinator),
    db: AsyncSession = Depends(get_session),
):
    """Read-only: lets a Coordinator confirm a scrape landed correctly for a
    section. Editing individual entries isn't built — SRS Section 11 makes
    manual upload/edit the required fallback, but that's a separate ask from
    "get the scraped data in and visible"; add a PATCH here when someone
    actually needs to hand-correct one slot rather than re-scrape.
    """
    statement = (
        select(TimetableEntry, CourseOffering, Subject, Section)
        .join(CourseOffering, TimetableEntry.course_offering_id == CourseOffering.id)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .join(Section, CourseOffering.section_id == Section.id)
        .where(CourseOffering.term_id == term_id, CourseOffering.section_id == section_id)
    )
    results = await db.exec(statement)
    return [
        TimetableSlotResponse(
            course_offering_id=offering.id,
            subject_code=subject.course_code,
            subject_name=subject.name,
            section_name=section.name,
            day_of_week=entry.day_of_week,
            start_time=entry.start_time,
            end_time=entry.end_time,
            room=entry.room,
            batch_label=entry.batch_label,
        )
        for entry, offering, subject, section in results
    ]