from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from typing import List
from datetime import date
from sqlalchemy.exc import IntegrityError

from app.core.database import get_session
from app.api.deps import require_teacher
from app.models.user import User, StudentProfile
from app.models.academic import CourseOffering, Subject, Section, StudentSubjectMap
from app.models.attendance import AttendanceRecord, BulkAttendanceSubmit, StudentRosterResponse, CourseScheduleResponse

router = APIRouter()

@router.get("/schedule", response_model=List[CourseScheduleResponse])
async def get_daily_schedule(
    # We pass the currently active term ID. In a real app, you might fetch this from a config table.
    term_id: int, 
    teacher_id: int = Depends(require_teacher),
    db: AsyncSession = Depends(get_session)
):
    """Fetch all classes assigned to this specific teacher for the current term."""
    
    # Notice the explicit joins. We grab the Course, Subject, and Section in one query.
    statement = (
        select(CourseOffering, Subject, Section)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .join(Section, CourseOffering.section_id == Section.id)
        .where(CourseOffering.teacher_id == teacher_id)
        .where(CourseOffering.term_id == term_id)
    )
    
    results = await db.exec(statement)
    
    schedule = []
    for offering, subject, section in results:
        schedule.append(CourseScheduleResponse(
            course_offering_id=offering.id,
            subject_name=subject.name,
            subject_code=subject.course_code,
            section_name=section.name
        ))
        
    return schedule

@router.get("/roster/{course_offering_id}", response_model=List[StudentRosterResponse])
async def get_class_roster(
    course_offering_id: int,
    teacher_id: int = Depends(require_teacher),
    db: AsyncSession = Depends(get_session)
):
    """Fetch the exact list of students enrolled in this specific course offering."""
    
    # 1. Security Check: Ensure the teacher actually teaches this class
    offering = await db.get(CourseOffering, course_offering_id)
    if not offering or offering.teacher_id != teacher_id:
        raise HTTPException(status_code=403, detail="You do not have access to this roster.")

    # 2. Optimized Join: Map -> Profile -> User (for the name)
    statement = (
        select(User, StudentProfile)
        .join(StudentProfile, User.id == StudentProfile.user_id)
        .join(StudentSubjectMap, StudentProfile.id == StudentSubjectMap.student_id)
        .where(StudentSubjectMap.course_offering_id == course_offering_id)
    )
    
    results = await db.exec(statement)
    
    roster = []
    for user, profile in results:
        roster.append(StudentRosterResponse(
            student_id=user.id,
            roll_number=profile.roll_number,
            full_name=user.full_name
        ))
        
    return roster

@router.post("/submit")
async def submit_attendance(
    payload: BulkAttendanceSubmit,
    teacher_id: int = Depends(require_teacher),
    db: AsyncSession = Depends(get_session)
):
    """Bulk insert attendance records. Must be highly resilient to double-taps."""
    
    # 1. Security Check: Is this their class?
    offering = await db.get(CourseOffering, payload.course_offering_id)
    if not offering or offering.teacher_id != teacher_id:
        raise HTTPException(status_code=403, detail="Not authorized to mark attendance for this class.")

    # 2. Idempotency Check: Did they already submit attendance for this date?
    # We prevent duplicates by checking if records already exist for this class/date combo.
    check_stmt = select(AttendanceRecord).where(
        AttendanceRecord.course_offering_id == payload.course_offering_id,
        AttendanceRecord.date == payload.date
    )
    existing = await db.exec(check_stmt)
    if existing.first():
        raise HTTPException(
            status_code=400, 
            detail="Attendance for this date has already been submitted. Use the update endpoint to modify it."
        )

    # 3. Prepare the Bulk Insert
    new_records = []
    for item in payload.records:
        record = AttendanceRecord(
            student_id=item.student_id,
            course_offering_id=payload.course_offering_id,
            date=payload.date,
            status=item.status,
            marked_by_id=teacher_id
        )
        new_records.append(record)
        
    db.add_all(new_records)
    
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Database integrity error. Check student IDs.")

    return {"status": "success", "message": f"Successfully marked attendance for {len(new_records)} students."}