import asyncio
import json
from datetime import time
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.core.database import engine
from app.models.user import User, RoleAssignment, RoleEnum, ScopeTypeEnum
from app.models.academic import Subject, Section, CourseOffering, TimetableEntry, DayOfWeek
from app.core.security import get_password_hash

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

async def run_sync():
    with open('data/timetableApi.txt', 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    print(f"Loaded {len(raw_data)} entries from timetableApi.txt")

    async with AsyncSession(engine, expire_on_commit=False) as db:
        users = (await db.exec(select(User))).all()
        user_by_name = {u.full_name.strip().lower(): u for u in users if u.full_name}
        user_by_email = {u.email.strip().lower(): u for u in users if u.email}

        subjects = (await db.exec(select(Subject))).all()
        subject_by_code = {s.course_code.strip(): s for s in subjects if s.course_code}

        sections = (await db.exec(select(Section))).all()
        section_by_name = {s.name.strip(): s for s in sections if s.name}

        offerings = (await db.exec(select(CourseOffering))).all()
        offering_map = {}
        for o in offerings:
            offering_map[(o.section_id, o.subject_id, o.teacher_id)] = o

        entries = (await db.exec(select(TimetableEntry))).all()
        entry_set = set()
        for e in entries:
            entry_set.add((e.course_offering_id, e.day_of_week, e.start_time))

        added_tt = 0
        added_offering = 0
        batch_counter = 0

        for entry in raw_data:
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
            batch_id = str(entry.get('Batch_Id', '0'))

            if not teacher_name or not subject_code:
                continue

            if is_lab and batch_id != '0':
                section_name = f"{section_name} (Batch {batch_id})"

            # Teacher
            u = user_by_name.get(teacher_name.lower())
            if not u:
                email = f"{teacher_name.lower().replace(' ', '.')}@gbu.edu"
                u = user_by_email.get(email)
                if not u:
                    u = User(
                        email=email,
                        full_name=teacher_name,
                        hashed_password=get_password_hash("faculty123"),
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

            # Subject
            s = subject_by_code.get(subject_code)
            if not s:
                s = Subject(course_code=subject_code, name=subject_name)
                db.add(s)
                await db.flush()
                subject_by_code[subject_code] = s

            # Section
            sec = section_by_name.get(section_name)
            if not sec:
                sec = Section(name=section_name, batch_id=1, term_id=2)
                db.add(sec)
                await db.flush()
                section_by_name[section_name] = sec

            # Offering
            offering = offering_map.get((sec.id, s.id, u.id))
            if not offering:
                offering = CourseOffering(
                    term_id=2,
                    section_id=sec.id,
                    subject_id=s.id,
                    teacher_id=u.id
                )
                db.add(offering)
                await db.flush()
                offering_map[(sec.id, s.id, u.id)] = offering
                added_offering += 1

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
                batch_counter += 1

                if batch_counter >= 200:
                    await db.commit()
                    batch_counter = 0

        if batch_counter > 0:
            await db.commit()

        print(f"Sync complete! Added {added_offering} course offerings and {added_tt} timetable entries.")

if __name__ == '__main__':
    asyncio.run(run_sync())
