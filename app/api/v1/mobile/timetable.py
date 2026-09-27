#app/api/v1/mobile/timetable.py

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from typing import List

from app.core.database import get_session
from app.api.deps import get_token_payload
from app.models.user import RoleEnum
from app.models.academic import (
    CourseOffering,
    Subject,
    Section,
    StudentSubjectMap,
    TimetableEntry,
    TimetableSlotResponse,
)

router = APIRouter()


@router.get("/schedule", response_model=List[TimetableSlotResponse])
async def get_my_timetable(
    term_id: int,
    payload: dict = Depends(get_token_payload),
    db: AsyncSession = Depends(get_session),
):
    """One endpoint for both apps' Timetable screens: Teacher (and
    Coordinator/HOD/Admin acting as teacher, matching require_teacher's own
    allowance elsewhere) sees their own teaching assignments; Student sees
    their enrolled course offerings via StudentSubjectMap. Not a require_*
    guard from deps.py because this is the one place both roles legitimately
    share a single implementation — splitting it in two would just duplicate
    the same four-table join with a different WHERE clause.
    """
    user_id = int(payload.get("sub"))
    role = payload.get("role")

    statement = (
        select(TimetableEntry, CourseOffering, Subject, Section)
        .join(CourseOffering, TimetableEntry.course_offering_id == CourseOffering.id)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .join(Section, CourseOffering.section_id == Section.id)
        .where(CourseOffering.term_id == term_id)
    )

    if role == RoleEnum.STUDENT:
        statement = statement.join(
            StudentSubjectMap, StudentSubjectMap.course_offering_id == CourseOffering.id
        ).where(StudentSubjectMap.student_id == user_id)
    elif role in (RoleEnum.TEACHER, RoleEnum.COORDINATOR, RoleEnum.HOD, RoleEnum.ADMIN):
        statement = statement.where(CourseOffering.teacher_id == user_id)
    else:
        raise HTTPException(status_code=403, detail="Role not permitted to view a timetable")

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