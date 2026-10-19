#app/models/academic.py

from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, List
from datetime import date, time, datetime
from pydantic import BaseModel
from enum import Enum

# --- 1. 6-Tier Hierarchical Structure (School -> Department -> Programme -> Specialization -> Batch -> Section) ---

class School(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    code: str = Field(unique=True, index=True)  # e.g., SOICT, SOE, SOM

    departments: List["Department"] = Relationship(back_populates="school")

class Department(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True) 
    code: str = Field(unique=True, index=True)
    school_id: Optional[int] = Field(default=None, foreign_key="school.id")
    
    school: Optional[School] = Relationship(back_populates="departments")
    programmes: List["Programme"] = Relationship(back_populates="department")

class Programme(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    department_id: int = Field(foreign_key="department.id")
    name: str 
    code: str = Field(unique=True, index=True)
    total_semesters: int
    
    department: Department = Relationship(back_populates="programmes")
    specializations: List["Specialization"] = Relationship(back_populates="programme")
    batches: List["Batch"] = Relationship(back_populates="programme")
class Specialization(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    programme_id: int = Field(foreign_key="programme.id")
    name: str 
    code: str = Field(unique=True, index=True)  # <-- Added Code Field
    
    programme: Programme = Relationship(back_populates="specializations")
    batches: List["Batch"] = Relationship(back_populates="specialization")
class Batch(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)  # <-- Added Batch Name (e.g., "2024-2028 Batch" or "BTECH-2024")
    programme_id: int = Field(foreign_key="programme.id")
    specialization_id: Optional[int] = Field(default=None, foreign_key="specialization.id")
    start_year: int
    expected_end_year: int
    
    programme: Programme = Relationship(back_populates="batches")
    specialization: Optional[Specialization] = Relationship(back_populates="batches")
    sections: List["Section"] = Relationship(back_populates="batch")

# --- 2. Timeline & Mapping ---

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
    credits: int = Field(default=3)
    course_type: Optional[str] = Field(default="LECTURE")

class CourseOffering(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    term_id: int = Field(foreign_key="academicterm.id")
    section_id: int = Field(foreign_key="section.id")
    subject_id: int = Field(foreign_key="subject.id")
    teacher_id: Optional[int] = Field(default=None, foreign_key="user.id")
    weekly_hours: Optional[float] = Field(default=None)
    course_type: Optional[str] = Field(default=None) 

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

# --- 3. Timetable Additions ---

class DayOfWeek(str, Enum):
    MON = "MON"
    TUE = "TUE"
    WED = "WED"
    THU = "THU"
    FRI = "FRI"
    SAT = "SAT"
    SUN = "SUN"

class TimetableEntry(SQLModel, table=True):
    """One row per (course_offering, day, start_time). Deliberately thin — this
    is planning/reference data, so it carries no status/lock fields."""
    id: Optional[int] = Field(default=None, primary_key=True)
    course_offering_id: int = Field(foreign_key="courseoffering.id", index=True)
    day_of_week: DayOfWeek
    start_time: time
    end_time: time
    room: Optional[str] = None
    batch_label: Optional[str] = None  # e.g. "G-1" / "G-2" for split lab groups

    # Which scrape run produced this row
    source: str = Field(default="scrape")
    scraped_at: Optional[datetime] = None