from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, List
from datetime import date, time, datetime
from pydantic import BaseModel
from enum import Enum

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
    is planning/reference data (SRS Section 9: a timetable entry is never an
    attendance gate), so it carries no status/lock fields."""
    id: Optional[int] = Field(default=None, primary_key=True)
    course_offering_id: int = Field(foreign_key="courseoffering.id", index=True)
    day_of_week: DayOfWeek
    start_time: time
    end_time: time
    room: Optional[str] = None
    batch_label: Optional[str] = None  # e.g. "G-1" / "G-2" for split lab groups

    # Which scrape run produced this row — lets a re-scrape replace exactly its
    # own prior output without touching rows a different source (manual edit,
    # a different scraper run) created. See scrape_and_seed_timetable.py.
    source: str = Field(default="scrape")
    scraped_at: Optional[datetime] = None


class TimetableSlotResponse(BaseModel):
    course_offering_id: int
    subject_code: str
    subject_name: str
    section_name: str
    day_of_week: DayOfWeek
    start_time: time
    end_time: time
    room: Optional[str] = None
    batch_label: Optional[str] = None