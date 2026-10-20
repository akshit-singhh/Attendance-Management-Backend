# app/schemas/academic.py

from typing import List, Optional, Union
from datetime import date, time
from pydantic import BaseModel, field_validator
from app.models.user import RoleEnum
from app.models.academic import DayOfWeek


# --- Schools ---
class SchoolCreate(BaseModel):
    name: str
    code: str


class SchoolResponse(BaseModel):
    id: int
    name: str
    code: str


# --- Departments ---
class DepartmentCreate(BaseModel):
    name: str
    code: str
    school_code: Optional[str] = None


class DepartmentResponse(BaseModel):
    id: int
    name: str
    code: str
    school_code: Optional[str] = None
    school_name: Optional[str] = None


# --- Programmes ---
class ProgrammeCreate(BaseModel):
    department_code: str
    name: str
    code: str
    total_semesters: int


class ProgrammeResponse(BaseModel):
    id: int
    name: str
    code: str
    department_code: str


# --- Specializations ---
class SpecializationCreate(BaseModel):
    programme_code: str
    name: str
    code: str


class SpecializationResponse(BaseModel):
    id: int
    name: str
    code: str
    programme_code: str


# --- Batches & Sections ---
class BatchCreate(BaseModel):
    name: str
    programme_code: str
    specialization_code: Optional[str] = None
    start_year: Union[int, str]
    expected_end_year: Union[int, str]

    @field_validator("start_year", "expected_end_year", mode="before")
    @classmethod
    def parse_year(cls, v):
        if v is None:
            return v
        if isinstance(v, int):
            return v
        s = str(v).strip()
        if "-" in s:
            try:
                return int(s.split("-")[0])
            except ValueError:
                pass
        try:
            return int(s)
        except ValueError:
            raise ValueError(f"Invalid year format: {v}")


class SectionCreate(BaseModel):
    name: str
    batch_id: int
    term_id: int


class SectionResponse(BaseModel):
    id: int
    name: str
    batch_id: int
    term_id: int
    batch_name: Optional[str] = None
    start_year: Optional[int] = None
    expected_end_year: Optional[int] = None
    programme_name: Optional[str] = None
    programme_code: Optional[str] = None
    term_name: Optional[str] = None
    is_term_active: Optional[bool] = None
    enrolled_students_count: Optional[int] = 0



# --- Subjects & Terms ---
class SubjectCreate(BaseModel):
    course_code: str
    name: str
    is_elective: bool = False
    credits: int = 3
    course_type: Optional[str] = "LECTURE"


class SubjectUpdate(BaseModel):
    name: Optional[str] = None
    credits: Optional[int] = None
    course_type: Optional[str] = None
    is_elective: Optional[bool] = None


class SubjectResponse(BaseModel):
    id: int
    course_code: str
    name: str
    is_elective: bool
    credits: int
    course_type: Optional[str] = "LECTURE"


class TermCreate(BaseModel):
    name: str
    start_date: date
    end_date: date
    is_active: bool = False


# --- Course Offerings & Enrollments ---
class CourseOfferingCreate(BaseModel):
    term_id: int
    section_id: int
    subject_id: int
    teacher_id: int


class CourseOfferingListResponse(BaseModel):
    id: int
    term_name: str
    section_name: str
    subject_name: str
    teacher_name: str
    teacher_id: Optional[int] = None
    subject_code: Optional[str] = None
    term_id: Optional[int] = None
    section_id: Optional[int] = None
    subject_id: Optional[int] = None
    credits: Optional[int] = 3
    course_type: Optional[str] = None
    weekly_hours: Optional[float] = None


class CourseOfferingReassign(BaseModel):
    teacher_id: int


class BulkEnrollmentCreate(BaseModel):
    course_offering_id: int
    student_ids: List[int]


class FacultyResponse(BaseModel):
    id: int
    full_name: str
    email: str
    role: RoleEnum


# --- Credit & Workload Management Schemas ---
class FacultyCourseLoadItem(BaseModel):
    course_offering_id: int
    subject_code: str
    subject_name: str
    section_name: str
    term_name: str
    course_type: str
    credits: int
    weekly_hours: float


class TeacherWorkloadItem(BaseModel):
    teacher_id: int
    teacher_name: str
    teacher_email: str
    total_weekly_hours: float
    lecture_hours: float
    lab_hours: float
    project_training_hours: float
    course_count: int
    courses: List[FacultyCourseLoadItem]


class FacultyWorkloadReportResponse(BaseModel):
    total_teachers: int
    total_teaching_hours: float
    avg_teaching_hours: float
    teachers: List[TeacherWorkloadItem]


class StudentEnrolledCourseItem(BaseModel):
    course_offering_id: int
    subject_code: str
    subject_name: str
    course_type: str
    credits: int


class StudentCreditAuditItem(BaseModel):
    student_id: int
    student_name: str
    roll_number: str
    student_email: str
    section_name: str
    total_credits: int
    lecture_credits: int
    lab_credits: int
    project_training_credits: int
    course_count: int
    courses: List[StudentEnrolledCourseItem]


class StudentCreditAuditResponse(BaseModel):
    total_students: int
    avg_credits_enrolled: float
    students: List[StudentCreditAuditItem]


class SyncCreditsResult(BaseModel):
    subjects_updated: int
    offerings_updated: int
    total_remarks_processed: int
    message: str


# --- Timetable Slots ---
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

# --- Academic Students Directory ---
class StudentAcademicCourse(BaseModel):
    offering_id: int
    course_code: str
    subject_name: str
    section_name: str
    term_name: Optional[str] = None
    teacher_name: Optional[str] = None
    course_type: Optional[str] = None


class StudentAcademicItem(BaseModel):
    id: int
    full_name: str
    email: str
    roll_number: Optional[str] = None
    is_active: bool
    batch_id: Optional[int] = None
    batch_name: Optional[str] = None
    programme_id: Optional[int] = None
    programme_name: Optional[str] = None
    programme_code: Optional[str] = None
    department_id: Optional[int] = None
    department_name: Optional[str] = None
    department_code: Optional[str] = None
    school_id: Optional[int] = None
    school_name: Optional[str] = None
    school_code: Optional[str] = None
    enrolled_courses_count: int = 0
    enrolled_courses: List[StudentAcademicCourse] = []


class PaginatedStudentAcademicResponse(BaseModel):
    items: List[StudentAcademicItem]
    total: int
    page: int
    size: int
    total_active: int

class StudentImportItem(BaseModel):
    roll_number: str
    full_name: str
    email: Optional[str] = None


class StudentBulkImportRequest(BaseModel):
    batch_id: int
    section_id: Optional[int] = None
    course_offering_ids: Optional[List[int]] = None
    students: List[StudentImportItem]


class StudentBulkImportResponse(BaseModel):
    status: str
    message: str
    total_processed: int
    created_count: int
    updated_count: int
    enrolled_count: int
    skipped_count: int = 0
    errors: List[str] = []

