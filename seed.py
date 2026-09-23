import asyncio
from datetime import date
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import engine
from app.core.security import get_password_hash
from app.models.user import User, RoleEnum, ScopeTypeEnum, RoleAssignment, StudentProfile
from app.models.academic import Program, Batch, AcademicTerm, Section, Subject, CourseOffering, StudentSubjectMap

async def seed_data():
    async with AsyncSession(engine, expire_on_commit=False) as db:
        print("Seeding database for v0.2...")

        password_hash = get_password_hash("password123")

        # 1. Create Users
        teacher = User(email="teacher@college.edu", hashed_password=password_hash, full_name="Prof. Alan Turing")
        student_user = User(email="student@college.edu", hashed_password=password_hash, full_name="Alice Smith")
        admin_user = User(email="admin@college.edu", hashed_password=password_hash, full_name="System Admin")
        coord_user = User(email="coordinator@college.edu", hashed_password=password_hash, full_name="Program Coordinator")

        db.add_all([teacher, student_user, admin_user, coord_user])
        await db.commit() # Commit to generate IDs

        # 2. Assign Roles (The v0.2 Way)
        roles = [
            RoleAssignment(user_id=admin_user.id, role=RoleEnum.ADMIN, scope_type=ScopeTypeEnum.UNIVERSITY),
            RoleAssignment(user_id=teacher.id, role=RoleEnum.TEACHER, scope_type=ScopeTypeEnum.ASSIGNMENT),
            RoleAssignment(user_id=student_user.id, role=RoleEnum.STUDENT, scope_type=ScopeTypeEnum.PROGRAM),
            RoleAssignment(user_id=coord_user.id, role=RoleEnum.COORDINATOR, scope_type=ScopeTypeEnum.PROGRAM)
        ]
        db.add_all(roles)
        await db.commit()

        # 3. Create the Academic Structure
        program = Program(name="B.Tech Computer Science", total_semesters=8)
        db.add(program)
        await db.commit()

        batch = Batch(program_id=program.id, start_year=2024, expected_end_year=2028)
        db.add(batch)
        await db.commit()

        # Link student profile to batch
        student_profile = StudentProfile(
            user_id=student_user.id,
            roll_number="CS2024-001",
            batch_id=batch.id
        )
        db.add(student_profile)

        # 4. Create the Active Term and Subject
        term = AcademicTerm(name="Fall 2026", start_date=date(2026, 8, 1), end_date=date(2026, 12, 15), is_active=True)
        db.add(term)
        await db.commit()

        section = Section(name="CSE-A", batch_id=batch.id, term_id=term.id)
        subject = Subject(course_code="CS401", name="Artificial Intelligence")
        db.add(section)
        db.add(subject)
        await db.commit()

        # 5. Map the Teacher to the Class (CourseOffering)
        offering = CourseOffering(
            term_id=term.id,
            section_id=section.id,
            subject_id=subject.id,
            teacher_id=teacher.id
        )
        db.add(offering)
        await db.commit()

        # 6. Enroll the Student in the Class
        enrollment = StudentSubjectMap(
            student_id=student_user.id,
            course_offering_id=offering.id
        )
        db.add(enrollment)
        await db.commit()

        print("✅ Seeding complete!")
        print(f"Admin Login: admin@college.edu / password123")
        print(f"Coordinator Login: coordinator@college.edu / password123")
        print(f"Teacher Login: teacher@college.edu / password123")
        print(f"Student Login: student@college.edu / password123")

if __name__ == "__main__":
    asyncio.run(seed_data())