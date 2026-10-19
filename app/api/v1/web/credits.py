# app/api/v1/web/credits.py

import re
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, func
from sqlalchemy.orm import selectinload

from app.core.database import get_session
from app.api.deps import require_coordinator
from app.models.user import User, RoleEnum, RoleAssignment, StudentProfile
from app.models.academic import (
    Subject,
    CourseOffering,
    Section,
    AcademicTerm,
    StudentSubjectMap,
)
from app.schemas.academic import (
    SubjectResponse,
    SubjectUpdate,
    FacultyCourseLoadItem,
    TeacherWorkloadItem,
    FacultyWorkloadReportResponse,
    StudentEnrolledCourseItem,
    StudentCreditAuditItem,
    StudentCreditAuditResponse,
    SyncCreditsResult,
)
from app.api.v1.web.timetable import _remarks_cache, _load_remarks_backup

router = APIRouter()


# ---------------------------------------------------------------------------
# 1. SYNC CREDITS & WORKLOAD FROM TIMETABLE REMARKS
# ---------------------------------------------------------------------------

@router.post("/sync-from-timetable", response_model=SyncCreditsResult, tags=["Admin - Credit & Workload"])
async def sync_credits_and_workload(
    admin_id: int = Depends(require_coordinator),
    db: AsyncSession = Depends(get_session)
):
    """
    Parses official university course allocations (from Timetable Remarks)
    and updates credits, course types, and weekly faculty contact hours across
    the Subject and CourseOffering tables.
    """
    # Ensure backup / cache is populated
    _load_remarks_backup()

    if not _remarks_cache:
        return SyncCreditsResult(
            subjects_updated=0,
            offerings_updated=0,
            total_remarks_processed=0,
            message="No university timetable remarks found in cache or backup."
        )

    # Flatten all remarks from all sections
    all_remarks = []
    seen_keys = set()
    for section_key, remarks_list in _remarks_cache.items():
        if isinstance(remarks_list, list):
            for r in remarks_list:
                if isinstance(r, dict) and r.get("subject_code"):
                    item_key = (r.get("subject_code", "").strip().upper(), r.get("teacher_name", "").strip())
                    if item_key not in seen_keys:
                        seen_keys.add(item_key)
                        all_remarks.append(r)

    # Fetch all subjects and course offerings from DB
    subjects_res = await db.exec(select(Subject))
    subjects = subjects_res.all()
    subject_by_code = {s.course_code.strip().upper(): s for s in subjects}

    offerings_res = await db.exec(
        select(CourseOffering, Subject, User)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .outerjoin(User, CourseOffering.teacher_id == User.id)
    )
    all_offerings = offerings_res.all()

    subjects_updated = 0
    offerings_updated = 0

    for r in all_remarks:
        code = r.get("subject_code", "").strip().upper()
        if not code:
            continue

        # Parse credit integer
        raw_credit = str(r.get("credits", "")).strip()
        credit_match = re.search(r'\d+', raw_credit)
        parsed_credit = int(credit_match.group()) if credit_match else 3

        # Parse activity / course type
        act = (r.get("activity") or "Lecture").strip().upper()
        if "LAB" in act:
            c_type = "LAB"
        elif "PROJECT" in act:
            c_type = "PROJECT"
        elif "TRAIN" in act or "INTERN" in act:
            c_type = "TRAINING"
        elif "SEMINAR" in act:
            c_type = "SEMINAR"
        else:
            c_type = "LECTURE"

        # Parse hours
        raw_hours = str(r.get("hours", "")).strip()
        try:
            parsed_hours = float(raw_hours) if raw_hours and raw_hours != ".0" else float(parsed_credit)
        except ValueError:
            parsed_hours = float(parsed_credit)

        # 1. Update Subject credits and course_type
        if code in subject_by_code:
            subj = subject_by_code[code]
            if subj.credits != parsed_credit or subj.course_type != c_type:
                subj.credits = parsed_credit
                subj.course_type = c_type
                db.add(subj)
                subjects_updated += 1

        # 2. Update matching CourseOfferings
        teacher_name_raw = r.get("teacher_name", "").strip().lower()
        for offering, subj, teacher in all_offerings:
            if subj.course_code.strip().upper() == code:
                # Check teacher match if available or assign weekly hours
                teacher_matches = False
                if teacher and teacher_name_raw:
                    t_full = teacher.full_name.strip().lower()
                    if t_full in teacher_name_raw or teacher_name_raw in t_full:
                        teacher_matches = True

                if teacher_matches or not offering.weekly_hours:
                    offering.weekly_hours = parsed_hours
                    offering.course_type = c_type
                    db.add(offering)
                    offerings_updated += 1

    await db.commit()

    return SyncCreditsResult(
        subjects_updated=subjects_updated,
        offerings_updated=offerings_updated,
        total_remarks_processed=len(all_remarks),
        message=f"Successfully synced {subjects_updated} subjects and {offerings_updated} course offerings from official University Timetable."
    )


# ---------------------------------------------------------------------------
# 2. FACULTY WORKLOAD REPORT
# ---------------------------------------------------------------------------

@router.get("/faculty-workload", response_model=FacultyWorkloadReportResponse, tags=["Admin - Credit & Workload"])
async def get_faculty_workload_report(
    search: Optional[str] = Query(default=None),
    admin_id: int = Depends(require_coordinator),
    db: AsyncSession = Depends(get_session)
):
    """
    Returns weekly teaching workload hours per faculty member,
    broken down by theory, lab, and project contact hours.
    """
    # Query all course offerings joined with Subject, Section, AcademicTerm, and Teacher
    stmt = (
        select(CourseOffering, Subject, Section, AcademicTerm, User)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .join(Section, CourseOffering.section_id == Section.id)
        .join(AcademicTerm, CourseOffering.term_id == AcademicTerm.id)
        .join(User, CourseOffering.teacher_id == User.id)
        .where(User.is_active == True)
    )

    if search and search.strip():
        q = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            func.lower(User.full_name).like(q) |
            func.lower(User.email).like(q) |
            func.lower(Subject.name).like(q) |
            func.lower(Subject.course_code).like(q)
        )

    results = (await db.exec(stmt)).all()

    # Group by teacher
    teachers_map = {}
    for offering, subject, section, term, teacher in results:
        t_id = teacher.id
        if t_id not in teachers_map:
            teachers_map[t_id] = {
                "teacher_id": t_id,
                "teacher_name": teacher.full_name,
                "teacher_email": teacher.email,
                "total_weekly_hours": 0.0,
                "lecture_hours": 0.0,
                "lab_hours": 0.0,
                "project_training_hours": 0.0,
                "courses": [],
            }

        # Calculate effective weekly hours
        eff_hours = offering.weekly_hours if offering.weekly_hours is not None else float(subject.credits or 3.0)
        c_type = (offering.course_type or subject.course_type or "LECTURE").upper()

        teachers_map[t_id]["total_weekly_hours"] += eff_hours
        if "LAB" in c_type:
            teachers_map[t_id]["lab_hours"] += eff_hours
        elif "PROJECT" in c_type or "TRAIN" in c_type or "SEMINAR" in c_type:
            teachers_map[t_id]["project_training_hours"] += eff_hours
        else:
            teachers_map[t_id]["lecture_hours"] += eff_hours

        teachers_map[t_id]["courses"].append(
            FacultyCourseLoadItem(
                course_offering_id=offering.id,
                subject_code=subject.course_code,
                subject_name=subject.name,
                section_name=section.name,
                term_name=term.name,
                course_type=c_type,
                credits=subject.credits or 3,
                weekly_hours=round(eff_hours, 1),
            )
        )

    teacher_items = []
    total_hours_sum = 0.0

    for t_data in teachers_map.values():
        total_h = round(t_data["total_weekly_hours"], 1)
        total_hours_sum += total_h
        teacher_items.append(
            TeacherWorkloadItem(
                teacher_id=t_data["teacher_id"],
                teacher_name=t_data["teacher_name"],
                teacher_email=t_data["teacher_email"],
                total_weekly_hours=total_h,
                lecture_hours=round(t_data["lecture_hours"], 1),
                lab_hours=round(t_data["lab_hours"], 1),
                project_training_hours=round(t_data["project_training_hours"], 1),
                course_count=len(t_data["courses"]),
                courses=t_data["courses"],
            )
        )

    # Sort descending by total teaching load
    teacher_items.sort(key=lambda x: x.total_weekly_hours, reverse=True)

    t_count = len(teacher_items)
    avg_hours = round(total_hours_sum / t_count, 1) if t_count > 0 else 0.0

    return FacultyWorkloadReportResponse(
        total_teachers=t_count,
        total_teaching_hours=round(total_hours_sum, 1),
        avg_teaching_hours=avg_hours,
        teachers=teacher_items,
    )


# ---------------------------------------------------------------------------
# 3. STUDENT CREDIT AUDIT REPORT
# ---------------------------------------------------------------------------

@router.get("/student-audit", response_model=StudentCreditAuditResponse, tags=["Admin - Credit & Workload"])
async def get_student_credit_audit(
    section_id: Optional[int] = Query(default=None),
    search: Optional[str] = Query(default=None),
    admin_id: int = Depends(require_coordinator),
    db: AsyncSession = Depends(get_session)
):
    """
    Returns student-wise enrolled academic credit breakdown
    (Total Credits, Theory Credits, Lab Credits, Project Credits).
    """
    # Fetch student mappings
    stmt = (
        select(StudentSubjectMap, CourseOffering, Subject, Section, User, StudentProfile)
        .join(CourseOffering, StudentSubjectMap.course_offering_id == CourseOffering.id)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .join(Section, CourseOffering.section_id == Section.id)
        .join(User, StudentSubjectMap.student_id == User.id)
        .outerjoin(StudentProfile, User.id == StudentProfile.user_id)
        .where(User.is_active == True)
    )

    if section_id:
        stmt = stmt.where(CourseOffering.section_id == section_id)

    if search and search.strip():
        q = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            func.lower(User.full_name).like(q) |
            func.lower(User.email).like(q) |
            func.lower(StudentProfile.roll_number).like(q)
        )

    records = (await db.exec(stmt)).all()

    students_map = {}
    for mapping, offering, subject, section, student, profile in records:
        s_id = student.id
        if s_id not in students_map:
            roll = profile.roll_number if profile and profile.roll_number else "N/A"
            students_map[s_id] = {
                "student_id": s_id,
                "student_name": student.full_name,
                "roll_number": roll,
                "student_email": student.email,
                "section_name": section.name,
                "total_credits": 0,
                "lecture_credits": 0,
                "lab_credits": 0,
                "project_training_credits": 0,
                "courses": [],
            }

        cr = subject.credits or 3
        c_type = (subject.course_type or "LECTURE").upper()

        students_map[s_id]["total_credits"] += cr
        if "LAB" in c_type:
            students_map[s_id]["lab_credits"] += cr
        elif "PROJECT" in c_type or "TRAIN" in c_type or "SEMINAR" in c_type:
            students_map[s_id]["project_training_credits"] += cr
        else:
            students_map[s_id]["lecture_credits"] += cr

        students_map[s_id]["courses"].append(
            StudentEnrolledCourseItem(
                course_offering_id=offering.id,
                subject_code=subject.course_code,
                subject_name=subject.name,
                course_type=c_type,
                credits=cr,
            )
        )

    student_items = []
    total_cr_sum = 0

    for s_data in students_map.values():
        total_cr_sum += s_data["total_credits"]
        student_items.append(
            StudentCreditAuditItem(
                student_id=s_data["student_id"],
                student_name=s_data["student_name"],
                roll_number=s_data["roll_number"],
                student_email=s_data["student_email"],
                section_name=s_data["section_name"],
                total_credits=s_data["total_credits"],
                lecture_credits=s_data["lecture_credits"],
                lab_credits=s_data["lab_credits"],
                project_training_credits=s_data["project_training_credits"],
                course_count=len(s_data["courses"]),
                courses=s_data["courses"],
            )
        )

    # Sort by Roll number / Name
    student_items.sort(key=lambda x: (x.section_name, x.roll_number))

    s_count = len(student_items)
    avg_credits = round(total_cr_sum / s_count, 1) if s_count > 0 else 0.0

    return StudentCreditAuditResponse(
        total_students=s_count,
        avg_credits_enrolled=avg_credits,
        students=student_items,
    )


# ---------------------------------------------------------------------------
# 4. SUBJECT DIRECTORY WITH CREDITS
# ---------------------------------------------------------------------------

@router.get("/subjects", response_model=List[SubjectResponse], tags=["Admin - Credit & Workload"])
async def list_subjects_with_credits(
    search: Optional[str] = Query(default=None),
    admin_id: int = Depends(require_coordinator),
    db: AsyncSession = Depends(get_session)
):
    """Returns all academic subjects with their credit allocations and course types."""
    stmt = select(Subject).order_by(Subject.course_code)
    if search and search.strip():
        q = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            func.lower(Subject.course_code).like(q) |
            func.lower(Subject.name).like(q)
        )

    results = (await db.exec(stmt)).all()
    return [
        SubjectResponse(
            id=s.id,
            course_code=s.course_code,
            name=s.name,
            is_elective=s.is_elective,
            credits=s.credits or 3,
            course_type=s.course_type or "LECTURE",
        )
        for s in results
    ]


@router.patch("/subjects/{subject_id}", response_model=SubjectResponse, tags=["Admin - Credit & Workload"])
async def update_subject_credits(
    subject_id: int,
    payload: SubjectUpdate,
    admin_id: int = Depends(require_coordinator),
    db: AsyncSession = Depends(get_session)
):
    """Allows an administrator to manually calibrate credits or course type for a subject."""
    subject = await db.get(Subject, subject_id)
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")

    if payload.name is not None:
        subject.name = payload.name
    if payload.credits is not None:
        subject.credits = payload.credits
    if payload.course_type is not None:
        subject.course_type = payload.course_type.upper()
    if payload.is_elective is not None:
        subject.is_elective = payload.is_elective

    db.add(subject)
    await db.commit()
    await db.refresh(subject)

    return SubjectResponse(
        id=subject.id,
        course_code=subject.course_code,
        name=subject.name,
        is_elective=subject.is_elective,
        credits=subject.credits or 3,
        course_type=subject.course_type or "LECTURE",
    )
