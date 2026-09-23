# app/models/academic.py
from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, List
from datetime import date
from pydantic import BaseModel

class Program(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True) 
    total_semesters: int
    
    batches: List["Batch"] = Relationship(back_populates="program")

class Batch(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    program_id: int = Field(foreign_key="program.id")
    start_year: int
    expected_end_year: int
    
    program: Program = Relationship(back_populates="batches")
    sections: List["Section"] = Relationship(back_populates="batch")

class AcademicTerm(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str 
    start_date: date
    end_date: date
    is_active: bool = Field(default=False) 

class Section(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str 
    batch_id: int = Field(foreign_key="batch.id")
    term_id: int = Field(foreign_key="academicterm.id")
    
    batch: Batch = Relationship(back_populates="sections")

class Subject(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    course_code: str = Field(unique=True, index=True)
    name: str
    is_elective: bool = Field(default=False)

class CourseOffering(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    term_id: int = Field(foreign_key="academicterm.id")
    section_id: int = Field(foreign_key="section.id")
    subject_id: int = Field(foreign_key="subject.id")
    teacher_id: int = Field(foreign_key="user.id") 

class StudentSubjectMap(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="user.id") 
    course_offering_id: int = Field(foreign_key="courseoffering.id")

class SubstituteGrant(SQLModel, table=True):
    """Time-limited permission letting a substitute mark attendance"""
    id: Optional[int] = Field(default=None, primary_key=True)
    substitute_teacher_id: int = Field(foreign_key="user.id")
    course_offering_id: int = Field(foreign_key="courseoffering.id")
    start_date: date
    end_date: date
    granted_by_id: int = Field(foreign_key="user.id")

# --- API SCHEMAS ---

class TermCreate(BaseModel):
    name: str
    start_date: date
    end_date: date
    is_active: bool = False

class CourseOfferingCreate(BaseModel):
    term_id: int
    section_id: int
    subject_id: int
    teacher_id: int