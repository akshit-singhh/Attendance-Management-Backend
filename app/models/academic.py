from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, List
from datetime import date

# --- PILLAR 1: Programs and Batches ---
class Program(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True) # e.g., "B.Tech CSE"
    total_semesters: int
    
    batches: List["Batch"] = Relationship(back_populates="program")

class Batch(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    program_id: int = Field(foreign_key="program.id")
    start_year: int
    expected_end_year: int
    
    program: Program = Relationship(back_populates="batches")
    sections: List["Section"] = Relationship(back_populates="batch")

# --- PILLAR 2: Academic Timeline ---
class AcademicTerm(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str # e.g., "Autumn 2026"
    start_date: date
    end_date: date
    is_active: bool = Field(default=False) 

# --- PILLAR 3: Sections & Subjects ---
class Section(SQLModel, table=True):
    """Dynamic grouping of students per term."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str # e.g., "CSE-A"
    batch_id: int = Field(foreign_key="batch.id")
    term_id: int = Field(foreign_key="academicterm.id")
    
    batch: Batch = Relationship(back_populates="sections")

class Subject(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    course_code: str = Field(unique=True, index=True)
    name: str
    is_elective: bool = Field(default=False)

# --- PILLAR 4: Course Offerings & Enrollment ---
class CourseOffering(SQLModel, table=True):
    """The master map: Who teaches what to whom, when."""
    id: Optional[int] = Field(default=None, primary_key=True)
    term_id: int = Field(foreign_key="academicterm.id")
    section_id: int = Field(foreign_key="section.id")
    subject_id: int = Field(foreign_key="subject.id")
    teacher_id: int = Field(foreign_key="user.id") # Will link to User table

class StudentSubjectMap(SQLModel, table=True):
    """Junction table: The exact classes a student sits in."""
    id: Optional[int] = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="user.id") # Links to Student profile
    course_offering_id: int = Field(foreign_key="courseoffering.id")