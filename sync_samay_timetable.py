import json
import asyncio
import os
from datetime import time
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

# Make sure 'engine' matches the name of the engine exported in your database.py file
from app.core.database import engine 
from app.models.user import User, RoleEnum, RoleAssignment, ScopeTypeEnum
from app.models.academic import Subject, Section, CourseOffering, TimetableEntry, DayOfWeek, AcademicTerm
from app.core.security import get_password_hash

# Map GBU periods to actual times
PERIOD_MAP = {
    "1": (time(8, 30), time(9, 30)),
    "2": (time(9, 30), time(10, 30)),
    "3": (time(10, 30), time(11, 30)),
    "4": (time(11, 30), time(12, 30)),
    "5": (time(12, 30), time(13, 30)),
    "6": (time(13, 30), time(14, 30)),
    "7": (time(14, 30), time(15, 30)),
    "8": (time(15, 30), time(16, 30)),
    "9": (time(16, 30), time(17, 30)),
    "10": (time(17, 30), time(18, 30)),
    "11": (time(18, 30), time(19, 30)),
}

DAY_MAP = {
    "1": DayOfWeek.MON,
    "2": DayOfWeek.TUE,
    "3": DayOfWeek.WED,
    "4": DayOfWeek.THU,
    "5": DayOfWeek.FRI,
    "6": DayOfWeek.SAT,
}

async def sync_timetable(json_file_path: str):
    with open(json_file_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    # expire_on_commit=False prevents the MissingGreenlet lazy-load crashes
    async with AsyncSession(engine, expire_on_commit=False) as db:
        
        # 1. Ensure an active Academic Term exists
        active_term = (await db.exec(select(AcademicTerm).where(AcademicTerm.is_active == True))).first()
        if not active_term:
            print("No active term found. Creating 'Autumn Term 2026'...")
            active_term = AcademicTerm(name="Autumn Term 2026", start_date="2026-08-01", end_date="2026-12-31", is_active=True)
            db.add(active_term)
            await db.commit()
            await db.refresh(active_term)
            
        active_term_id = active_term.id  # Cache ID as primitive integer

        print(f"Processing {len(raw_data)} timetable entries...")

        for entry in raw_data:
            teacher_name = entry.get("TeacherName", "").strip()
            subject_code = entry.get("Subject_Code", "").strip()
            subject_name = entry.get("subject_name", "").strip()
            section_name = entry.get("SectionName", "").strip()
            room_name = entry.get("RoomName", "").strip() if entry.get("RoomName") else "TBA"
            tt_day = str(entry.get("TT_Day"))
            tt_period = str(entry.get("TT_Period"))
            is_lab = entry.get("ActivityTag", "") == "Lab"

            if not teacher_name or not subject_code or tt_day not in DAY_MAP or tt_period not in PERIOD_MAP:
                continue

            # --- 2. UPSERT TEACHER ---
            teacher_email = f"{teacher_name.lower().replace(' ', '.')}@gbu.edu"
            teacher = (await db.exec(select(User).where(User.email == teacher_email))).first()
            if not teacher:
                teacher = User(
                    email=teacher_email,
                    full_name=teacher_name,
                    hashed_password=get_password_hash("faculty123"),
                    is_active=True
                )
                db.add(teacher)
                await db.commit()
                await db.refresh(teacher)
                
                role_assignment = RoleAssignment(
                    user_id=teacher.id, role=RoleEnum.TEACHER, scope_type=ScopeTypeEnum.ASSIGNMENT
                )
                db.add(role_assignment)
                await db.commit()
            
            teacher_id = teacher.id

            # --- 3. UPSERT SUBJECT ---
            subject = (await db.exec(select(Subject).where(Subject.course_code == subject_code))).first()
            if not subject:
                subject = Subject(course_code=subject_code, name=subject_name)
                db.add(subject)
                await db.commit()
                await db.refresh(subject)
                
            subject_id = subject.id

            # --- 4. UPSERT SECTION ---
            batch_label = f"G-{entry['Batch_Id']}" if is_lab and entry.get("Batch_Id", "0") != "0" else None

            section = (await db.exec(
                select(Section).where(Section.name == section_name, Section.term_id == active_term_id)
            )).first()
            
            if not section:
                section = Section(name=section_name, batch_id=1, term_id=active_term_id)
                db.add(section)
                await db.commit()
                await db.refresh(section)
                
            section_id = section.id

            # --- 5. UPSERT COURSE OFFERING ---
            offering = (await db.exec(
                select(CourseOffering).where(
                    CourseOffering.term_id == active_term_id,
                    CourseOffering.section_id == section_id,
                    CourseOffering.subject_id == subject_id,
                    CourseOffering.teacher_id == teacher_id
                )
            )).first()
            
            if not offering:
                offering = CourseOffering(
                    term_id=active_term_id, section_id=section_id, subject_id=subject_id, teacher_id=teacher_id
                )
                db.add(offering)
                await db.commit()
                await db.refresh(offering)
                
            offering_id = offering.id

            # --- 6. UPSERT TIMETABLE ENTRY ---
            start_time, end_time = PERIOD_MAP[tt_period]
            day_enum = DAY_MAP[tt_day]
            
            existing_tt = (await db.exec(
                select(TimetableEntry).where(
                    TimetableEntry.course_offering_id == offering_id,
                    TimetableEntry.day_of_week == day_enum,
                    TimetableEntry.start_time == start_time,
                    TimetableEntry.batch_label == batch_label
                )
            )).first()
            
            if not existing_tt:
                new_tt = TimetableEntry(
                    course_offering_id=offering_id,
                    day_of_week=day_enum,
                    start_time=start_time,
                    end_time=end_time,
                    room=room_name,
                    batch_label=batch_label
                )
                db.add(new_tt)
                await db.commit()

        print("Timetable successfully synchronized!")

if __name__ == "__main__":
    candidate_paths = [
        os.path.join("data", "timetableApi.txt"),
        "timetableApi.txt",
    ]
    target_path = next((p for p in candidate_paths if os.path.exists(p)), candidate_paths[0])
    asyncio.run(sync_timetable(target_path))