import asyncio
import json
import os
import sys
from datetime import time, date
from sqlmodel import select, text
from sqlmodel.ext.asyncio.session import AsyncSession
from dotenv import load_dotenv

load_dotenv(r'D:\Attendance_management\Attendance_backend\.env')
sys.path.insert(0, r'D:\Attendance_management\Attendance_backend')

from app.core.database import engine
from app.core.security import get_password_hash
from app.models.user import User, RoleAssignment, RoleEnum, ScopeTypeEnum, StudentProfile
from app.models.academic import (
    AcademicTerm, Subject, Section, CourseOffering, TimetableEntry, DayOfWeek, Batch, StudentSubjectMap
)

PERIOD_MAP = {
    '1': (time(8, 30), time(9, 30)),
    '2': (time(9, 30), time(10, 30)),
    '3': (time(10, 30), time(11, 30)),
    '4': (time(11, 30), time(12, 30)),
    '5': (time(12, 30), time(13, 30)),
    '6': (time(13, 30), time(14, 30)),
    '7': (time(14, 30), time(15, 30)),
    '8': (time(15, 30), time(16, 30)),
    '9': (time(16, 30), time(17, 30)),
    '10': (time(17, 30), time(18, 30)),
    '11': (time(18, 30), time(19, 30)),
}

DAY_MAP = {
    '1': DayOfWeek.MON,
    '2': DayOfWeek.TUE,
    '3': DayOfWeek.WED,
    '4': DayOfWeek.THU,
    '5': DayOfWeek.FRI,
    '6': DayOfWeek.SAT,
}

def determine_course_type(code: str, name: str) -> str:
    code_l = code.lower()
    name_l = name.lower()
    if 'lab' in name_l or 'lab' in code_l or code_l.endswith('l') or any(code.endswith(f"8{i}") for i in range(10)):
        return "LAB"
    if 'project' in name_l or 'proj' in code_l:
        return "PROJECT"
    if 'training' in name_l or 'internship' in name_l or 'train' in code_l:
        return "TRAINING"
    if 'seminar' in name_l:
        return "SEMINAR"
    return "LECTURE"

async def rebuild_all():
    print("==================================================")
    print("STEP 1: Load Timetable and Backup Data")
    print("==================================================")
    tt_path = r'D:\Attendance_management\Attendance_backend\data\timetableApi.txt'
    with open(tt_path, 'r', encoding='utf-8') as f:
        raw_tt = json.load(f)
    print(f"Loaded {len(raw_tt)} timetable entries.")

    students_path = r'D:\Attendance_management\Attendance_backend\data\students_backup.json'
    with open(students_path, 'r', encoding='utf-8') as f:
        students_data = json.load(f)
    print(f"Loaded {len(students_data)} backed-up students.")

    async with AsyncSession(engine, expire_on_commit=False) as db:
        print("\n==================================================")
        print("STEP 2: Clean Old Corrupted Data")
        print("==================================================")
        
        tables_to_clear = [
            "attendanceauditlog",
            "correctionrequest",
            "leaverequest",
            "refreshtoken",
            "substitutegrant",
            "timetablesynclog",
            "attendancerecord",
            "attendancesession",
            "studentsubjectmap",
            "timetableentry",
            "courseoffering",
            "section",
            "subject",
            "studentprofile",
        ]
        for tbl in tables_to_clear:
            try:
                await db.execute(text(f'DELETE FROM "{tbl}";'))
            except Exception as e:
                print(f"Note on {tbl}: {e}")

        # Clear non-admin users and role assignments
        admin_user = (await db.exec(select(User).where(User.email == "admin@gbu.ac.in"))).first()
        if admin_user:
            await db.execute(text(f"DELETE FROM roleassignment WHERE user_id != {admin_user.id};"))
            await db.execute(text(f"DELETE FROM \"user\" WHERE id != {admin_user.id};"))
        else:
            admin_user = User(
                email="admin@gbu.ac.in",
                full_name="System Administrator",
                hashed_password=get_password_hash("GBU123"),
                is_active=True
            )
            db.add(admin_user)
            await db.flush()
            role_admin = RoleAssignment(
                user_id=admin_user.id,
                role=RoleEnum.ADMIN,
                scope_type=ScopeTypeEnum.UNIVERSITY
            )
            db.add(role_admin)

        # Set Single Global Active Semester
        await db.execute(text("UPDATE semester SET is_active = False;"))
        sem = (await db.exec(select(AcademicTerm).where(AcademicTerm.id == 1))).first()
        if not sem:
            sem = AcademicTerm(
                name="2026-27 Odd",
                start_date=date(2026, 8, 1),
                end_date=date(2026, 12, 31),
                is_active=True
            )
            db.add(sem)
            await db.flush()
        else:
            sem.name = "2026-27 Odd"
            sem.is_active = True
            sem.start_date = date(2026, 8, 1)
            sem.end_date = date(2026, 12, 31)
            db.add(sem)
        
        # Delete any extra test semesters
        await db.execute(text(f"DELETE FROM semester WHERE id != {sem.id};"))
        await db.commit()
        print(f"Active Semester set to: ID {sem.id} ({sem.name})")

        # Get default batch for sections
        default_batch = (await db.exec(select(Batch))).first()
        default_batch_id = default_batch.id if default_batch else 1

        print("\n==================================================")
        print("STEP 3: Ingest Teachers, Subjects, Sections, Timetable")
        print("==================================================")
        user_by_name = {}
        user_by_email = {}
        subject_by_code = {}
        section_by_name = {}
        offering_map = {}
        entry_set = set()

        added_teachers = 0
        added_subjects = 0
        added_sections = 0
        added_offerings = 0
        added_tt = 0

        faculty_pwd = get_password_hash("faculty123")

        for entry in raw_tt:
            tt_day = str(entry.get('TT_Day'))
            tt_period = str(entry.get('TT_Period'))
            if tt_day not in DAY_MAP or tt_period not in PERIOD_MAP:
                continue

            teacher_name = entry.get('TeacherName', '').strip()
            subject_code = entry.get('Subject_Code', '').strip()
            subject_name = entry.get('subject_name', '').strip()
            section_name = entry.get('SectionName', '').strip()
            room_name = entry.get('RoomName', '').strip() if entry.get('RoomName') else 'TBA'
            is_lab = entry.get('ActivityTag', '') == 'Lab'
            batch_id_val = str(entry.get('Batch_Id', '0'))

            if not teacher_name or not subject_code or not section_name:
                continue

            if is_lab and batch_id_val != '0':
                section_name = f"{section_name} (Batch {batch_id_val})"

            # Teacher
            u = user_by_name.get(teacher_name.lower())
            if not u:
                email = f"{teacher_name.lower().replace(' ', '.')}@gbu.edu"
                u = user_by_email.get(email)
                if not u:
                    u = User(
                        email=email,
                        full_name=teacher_name,
                        hashed_password=faculty_pwd,
                        is_active=True
                    )
                    db.add(u)
                    await db.flush()
                    user_by_name[teacher_name.lower()] = u
                    user_by_email[email] = u
                    role_assign = RoleAssignment(
                        user_id=u.id,
                        role=RoleEnum.TEACHER,
                        scope_type=ScopeTypeEnum.ASSIGNMENT
                    )
                    db.add(role_assign)
                    added_teachers += 1

            # Subject
            s = subject_by_code.get(subject_code)
            c_type = determine_course_type(subject_code, subject_name)
            if not s:
                s = Subject(course_code=subject_code, name=subject_name, course_type=c_type)
                db.add(s)
                await db.flush()
                subject_by_code[subject_code] = s
                added_subjects += 1

            # Section
            sec = section_by_name.get(section_name)
            if not sec:
                sec = Section(name=section_name, batch_id=default_batch_id, term_id=sem.id)
                db.add(sec)
                await db.flush()
                section_by_name[section_name] = sec
                added_sections += 1

            # Course Offering
            offering = offering_map.get((sec.id, s.id, u.id))
            if not offering:
                offering = CourseOffering(
                    term_id=sem.id,
                    section_id=sec.id,
                    subject_id=s.id,
                    teacher_id=u.id,
                    course_type=c_type
                )
                db.add(offering)
                await db.flush()
                offering_map[(sec.id, s.id, u.id)] = offering
                added_offerings += 1

            # Timetable Entry
            start_time, end_time = PERIOD_MAP[tt_period]
            day_enum = DAY_MAP[tt_day]

            if (offering.id, day_enum, start_time) not in entry_set:
                new_tt = TimetableEntry(
                    course_offering_id=offering.id,
                    day_of_week=day_enum,
                    start_time=start_time,
                    end_time=end_time,
                    room=room_name,
                    source='scrape'
                )
                db.add(new_tt)
                entry_set.add((offering.id, day_enum, start_time))
                added_tt += 1

                if added_tt % 300 == 0:
                    await db.commit()

        await db.commit()
        print(f"Teachers created: {added_teachers}")
        print(f"Subjects created: {added_subjects}")
        print(f"Sections created: {added_sections}")
        print(f"Course Offerings created: {added_offerings}")
        print(f"Timetable Entries created: {added_tt}")

        print("\n==================================================")
        print("STEP 4: Re-insert and Enroll Students")
        print("==================================================")
        student_pwd = get_password_hash("gbu@123")
        students_added = 0
        enrollments_added = 0

        # Map batch by start year
        all_batches = (await db.exec(select(Batch))).all()
        batch_by_year = {b.start_year: b for b in all_batches}

        # Find target sections: CS-IV-A for 235ICS and CS-II-A for 225/UCS
        sec_cs_iv_a = section_by_name.get("CS-IV-A")
        sec_cs_ii_a = section_by_name.get("CS-II-A") or section_by_name.get("BCS-II A") or list(section_by_name.values())[0]

        offerings_iv_a = []
        if sec_cs_iv_a:
            offerings_iv_a = (await db.exec(select(CourseOffering).where(CourseOffering.section_id == sec_cs_iv_a.id))).all()

        offerings_ii_a = []
        if sec_cs_ii_a:
            offerings_ii_a = (await db.exec(select(CourseOffering).where(CourseOffering.section_id == sec_cs_ii_a.id))).all()

        for s_info in students_data:
            roll = s_info["roll_number"].strip()
            name = s_info["name"].strip()
            email = s_info["email"].strip()
            start_yr = s_info.get("start_year", 2024)

            # Match batch
            batch = batch_by_year.get(start_yr, default_batch)
            batch_id = batch.id if batch else default_batch_id

            u = User(
                email=email,
                full_name=name,
                hashed_password=student_pwd,
                is_active=True
            )
            db.add(u)
            await db.flush()

            role_assign = RoleAssignment(
                user_id=u.id,
                role=RoleEnum.STUDENT,
                scope_type=ScopeTypeEnum.ASSIGNMENT
            )
            db.add(role_assign)

            profile = StudentProfile(
                user_id=u.id,
                roll_number=roll,
                batch_id=batch_id
            )
            db.add(profile)
            await db.flush()
            students_added += 1

            # Enroll student into courses
            target_offerings = offerings_iv_a if "235ICS" in roll else offerings_ii_a
            for off in target_offerings:
                map_entry = StudentSubjectMap(
                    student_id=u.id,
                    course_offering_id=off.id
                )
                db.add(map_entry)
                enrollments_added += 1

        await db.commit()
        print(f"Students re-inserted: {students_added}")
        print(f"Student Course Enrollments: {enrollments_added}")

        print("\n==================================================")
        print("DATABASE REBUILD & SYNC FINISHED SUCCESSFULLY!")
        print("==================================================")

if __name__ == "__main__":
    asyncio.run(rebuild_all())
