import asyncio
from datetime import date
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import engine
from app.core.security import get_password_hash
from app.models.user import User, RoleEnum, ScopeTypeEnum, RoleAssignment, StudentProfile
from app.models.academic import Program, Batch, AcademicTerm, Section, Subject, CourseOffering, StudentSubjectMap

async def seed_data():
    async with AsyncSession(engine, expire_on_commit=False) as db:
        print("Seeding database with scraper-aligned data...")

        password_hash = get_password_hash("password123")

        # 1. Create Base Admin/Student Users
        student_user = User(email="student@college.edu", hashed_password=password_hash, full_name="Alice Smith")
        admin_user = User(email="admin@college.edu", hashed_password=password_hash, full_name="System Admin")
        coord_user = User(email="coordinator@college.edu", hashed_password=password_hash, full_name="Program Coordinator")

        db.add_all([student_user, admin_user, coord_user])
        await db.commit() 

        # 2. Assign Base Roles
        roles = [
            RoleAssignment(user_id=admin_user.id, role=RoleEnum.ADMIN, scope_type=ScopeTypeEnum.UNIVERSITY),
            RoleAssignment(user_id=student_user.id, role=RoleEnum.STUDENT, scope_type=ScopeTypeEnum.PROGRAM),
            RoleAssignment(user_id=coord_user.id, role=RoleEnum.COORDINATOR, scope_type=ScopeTypeEnum.PROGRAM)
        ]
        db.add_all(roles)
        await db.commit()

        # 3. Create the Academic Structure
        program = Program(name="B.Tech Information Technology", total_semesters=8)
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

        # 4. Create the Exact Term and Section the Scraper is looking for
        term = AcademicTerm(name="2026-27 Odd", start_date=date(2026, 8, 1), end_date=date(2026, 12, 15), is_active=True)
        db.add(term)
        await db.commit()

        section = Section(name="CS-IV-A", batch_id=batch.id, term_id=term.id)
        db.add(section)
        await db.commit()

        # 5. Create the Exact Subjects found by the Scraper
        subjects = [
            Subject(course_code="CS401", name="Internet of Things"),
            Subject(course_code="CS403", name="Soft Computing Techniques"),
            Subject(course_code="CS405", name="Machine Learning"),
            Subject(course_code="CS407", name="Pattern Recognition"),
            Subject(course_code="CS481", name="Internet of Things Lab"),
            Subject(course_code="MA402", name="Simulation & modelling"),
        ]
        db.add_all(subjects)
        await db.commit()

        # 6. Create the Teachers 
        # WARNING: The full_name MUST EXACTLY match the name in the Remarks table
        teachers_data = [
            ("teacher_vs@college.edu", "Vidushi Sharma"),
            ("teacher_asb@college.edu", "Anurag Singh Baghel"),
            ("teacher_sg@college.edu", "Shipra Gupta"),
            ("teacher_vg@college.edu", "Varshika Gautam"),
            ("teacher_hb@college.edu", "Harsh Baliyan")
        ]
        
        teacher_users = []
        for email, name in teachers_data:
            user = User(email=email, hashed_password=password_hash, full_name=name)
            db.add(user)
            teacher_users.append(user)
        
        await db.commit()

        # Assign TEACHER roles to all of them
        teacher_roles = [
            RoleAssignment(user_id=u.id, role=RoleEnum.TEACHER, scope_type=ScopeTypeEnum.ASSIGNMENT) 
            for u in teacher_users
        ]
        db.add_all(teacher_roles)
        await db.commit()

        print("✅ Seeding complete!")
        print("Database is now prepared for scrape_and_seed.py")

if __name__ == "__main__":
    asyncio.run(seed_data())