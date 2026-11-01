# app/schemas/__init__.py

from app.schemas.auth import (
    LoginRequest,
    RefreshTokenRequest,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    RoleAssignmentRead,
    TokenResponse,
    SwitchRoleRequest,
)

from app.schemas.user import (
    UserCreate,
    UserResponse,
    PaginatedUserResponse,
)

from app.schemas.academic import (
    SchoolCreate,
    SchoolResponse,
    DepartmentCreate,
    DepartmentResponse,
    ProgrammeCreate,
    ProgrammeResponse,
    SpecializationCreate,
    SpecializationResponse,
    BatchCreate,
    SectionCreate,
    SubjectCreate,
    SubjectUpdate,
    SubjectResponse,
    TermCreate,
    CourseOfferingCreate,
    CourseOfferingListResponse,
    CourseOfferingReassign,
    BulkEnrollmentCreate,
    FacultyResponse,
    TimetableSlotResponse,
    FacultyCourseLoadItem,
    TeacherWorkloadItem,
    FacultyWorkloadReportResponse,
    StudentEnrolledCourseItem,
    StudentCreditAuditItem,
    StudentCreditAuditResponse,
    SyncCreditsResult,
    StudentAcademicCourse,
    StudentAcademicItem,
    PaginatedStudentAcademicResponse,
    StudentImportItem,
    StudentBulkImportRequest,
    StudentBulkImportResponse,
)

from app.schemas.attendance import (
    SyncRecordItem,
    OfflineSyncPayload,
    StudentRosterResponse,
    CourseScheduleResponse,
    SessionHistoryResponse,
    AttendanceSessionItemResponse,
    PaginatedAttendanceSessionsResponse,
    SessionStudentRecord,
    SessionDetailResponse,
)

from app.schemas.audit import (
    AuditLogItemResponse,
    PaginatedAuditLogsResponse,
)

from app.schemas.corrections import (
    CorrectionRequestCreate,
    CorrectionDecisionPayload,
    BulkCorrectionDecisionPayload,
    CorrectionQueueResponse,
)

from app.schemas.dashboard import (
    DashboardStatsResponse,
    ActionItemsResponse,
    AttendancePulseResponse,
    SetupHealthResponse,
)

from app.schemas.student import (
    SubjectAttendanceHistory,
    StudentDashboardResponse,
    CalendarClassItem,
    CalendarDayData,
    StudentCalendarResponse,
)

from app.schemas.timetable import (
    TimetableSlotItem,
    TeachingWorkloadItem,
    TeacherTimetableResponse,
    SyncApplyRequest,
)

from app.schemas.health import (
    DatabaseCheck,
    StorageCheck,
    AppMetadata,
    HealthCheckResponse,
)

__all__ = [
    # Auth
    "LoginRequest",
    "RefreshTokenRequest",
    "ChangePasswordRequest",
    "ForgotPasswordRequest",
    "ResetPasswordRequest",
    "RoleAssignmentRead",
    "TokenResponse",
    "SwitchRoleRequest",
    # User
    "UserCreate",
    "UserResponse",
    "PaginatedUserResponse",
    # Academic
    "SchoolCreate",
    "SchoolResponse",
    "DepartmentCreate",
    "DepartmentResponse",
    "ProgrammeCreate",
    "ProgrammeResponse",
    "SpecializationCreate",
    "SpecializationResponse",
    "BatchCreate",
    "SectionCreate",
    "SubjectCreate",
    "SubjectUpdate",
    "SubjectResponse",
    "TermCreate",
    "CourseOfferingCreate",
    "CourseOfferingListResponse",
    "CourseOfferingReassign",
    "BulkEnrollmentCreate",
    "FacultyResponse",
    "TimetableSlotResponse",
    "FacultyCourseLoadItem",
    "TeacherWorkloadItem",
    "FacultyWorkloadReportResponse",
    "StudentEnrolledCourseItem",
    "StudentCreditAuditItem",
    "StudentCreditAuditResponse",
    "SyncCreditsResult",
    "StudentAcademicCourse",
    "StudentAcademicItem",
    "PaginatedStudentAcademicResponse",
    "StudentImportItem",
    "StudentBulkImportRequest",
    "StudentBulkImportResponse",
    # Attendance
    "SyncRecordItem",
    "OfflineSyncPayload",
    "StudentRosterResponse",
    "CourseScheduleResponse",
    "SessionHistoryResponse",
    "AttendanceSessionItemResponse",
    "PaginatedAttendanceSessionsResponse",
    "SessionStudentRecord",
    "SessionDetailResponse",
    # Audit
    "AuditLogItemResponse",
    "PaginatedAuditLogsResponse",
    # Corrections
    "CorrectionRequestCreate",
    "CorrectionDecisionPayload",
    "BulkCorrectionDecisionPayload",
    "CorrectionQueueResponse",
    # Dashboard
    "DashboardStatsResponse",
    "ActionItemsResponse",
    "AttendancePulseResponse",
    "SetupHealthResponse",
    # Student
    "SubjectAttendanceHistory",
    "StudentDashboardResponse",
    "CalendarClassItem",
    "CalendarDayData",
    "StudentCalendarResponse",
    # Timetable
    "TimetableSlotItem",
    "TeachingWorkloadItem",
    "TeacherTimetableResponse",
    "SyncApplyRequest",
    # Health
    "DatabaseCheck",
    "StorageCheck",
    "AppMetadata",
    "HealthCheckResponse",
]
