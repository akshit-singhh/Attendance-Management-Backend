# Attendance Management Backend

This repository contains the enterprise backend for the university attendance management system built with FastAPI, SQLModel, and PostgreSQL. It supports multi-school academic hierarchies, student attendance tracking, teacher class allocation, timetable scheduling with lab group management, faculty workload calculation, role-based access control, live session monitoring, administrative workflows, and mobile client APIs.

The backend is tailored for university environments featuring multiple schools (e.g., School of ICT, School of Engineering, School of Management, etc.) where users are assigned roles such as Admin, Dean, HOD, Coordinator, Teacher, and Student. It exposes high-performance mobile APIs for Android and iOS clients alongside comprehensive web management endpoints.

---

## 1. What this project is for

This project is an end-to-end academic operations and attendance intelligence platform that handles:

- **Authentication & Extended Session Management**: Secure JWT token-based authentication with token rotation, unified `/auth/me` profile resolution, password management, and extended refresh longevity.
- **Hierarchical University Academic Structure**: Full 6-tier institutional chain:
  $$\text{School} \longrightarrow \text{Department} \longrightarrow \text{Programme} \longrightarrow \text{Specialization} \longrightarrow \text{Batch} \longrightarrow \text{Section}$$
- **Consolidated Class Sections & Group Labels (`batch_label`)**: Timetable entries and practical sessions support `batch_label` (`G-1`, `G-2`, etc.) without splintering classes into phantom duplicate sub-batch sections.
- **Paginated User Management with Deep Search & Role Filters**: Debounced, production-grade search across users (`email`, `full_name`, `roll_number`) and role assignment filtering with subquery optimization.
- **Cascading Filters**: Query endpoints dynamically filtered by parent identifiers (e.g., `school_code` for departments, `department_code` for programmes).
- **Teacher Allotments & Reassignment**: Allocation of faculty members to course offerings, with support for seamless reassignment across terms.
- **University Timetable Integration**: Live synchronization and management of schedule slots, rooms, periods, sections, and faculty allotments (including official Samay timetable integration with automatic rebuild utilities).
- **Credits & Faculty Workload Management**: Credit auditing for students and automatic workload calculation (lecture, lab, practical, hybrid) for faculty members.
- **Mobile Student Intelligence**: Real-time student dashboard, live day schedule, subject-wise attendance history, safe bunk calculation buffer, and calendar attendance tracking.
- **Offline Attendance Sync & Validation**: Conflict-resilient offline capture on mobile devices with idempotency checks, device clock-skew detection, and duplicate session prevention.
- **Live Attendance Sessions & Flagging**: Real-time tracking of marked sessions, attendance percentages, student flags, and historical status audit logs.
- **Attendance Corrections & Leave Automation**: Student medical leave workflows with document upload, and automated status overrides from `ABSENT` to `LEAVE` with full audit trails.
- **Admin Dashboard Intelligence**: Real-time stats, setup health diagnostics, attendance pulse (daily overall percentages), and pending action items.

---

## 2. Tech Stack

- **Runtime & Language**: Python 3.10+
- **Web Framework**: FastAPI (Async)
- **Database & ORM**: PostgreSQL with SQLModel & SQLAlchemy (AsyncPG engine)
- **Database Migrations**: Alembic
- **Authentication & Security**: PyJWT, Passlib, Bcrypt password hashing
- **Server**: Uvicorn ASGI
- **Data Validation & Settings**: Pydantic v2 & Pydantic-Settings
- **Containerization**: Docker (Production ready with automated migrations)

---

## 3. Project Architecture

The application follows an asynchronous, layered architecture:

```
Attendance_backend/
├── app/
│   ├── main.py                  # Application bootstrap, router registration, CORS, static uploads
│   ├── create_admin.py          # Initial root admin account provisioning
│   ├── core/
│   │   ├── config.py            # Pydantic environment configuration (.env)
│   │   ├── database.py          # Async PostgreSQL engine and session generator
│   │   └── security.py          # Password hashing, JWT token issue/verification
│   ├── api/
│   │   ├── deps.py              # Role guards (require_admin, require_hod, require_teacher, etc.)
│   │   └── v1/
│   │       ├── auth.py          # Login, token refresh rotation, /auth/me profile, change-password
│   │       ├── mobile/          # Mobile client endpoints
│   │       │   ├── attendance.py# Offline sync, teacher rosters, live roll call
│   │       │   ├── student.py   # Student dashboard, live schedule, attendance history
│   │       │   ├── timetable.py # Mobile timetable slot retrieval with group filters
│   │       │   └── leaves.py    # Student medical leave applications
│   │       └── web/             # Web admin, coordinator, and HOD management endpoints
│   │           ├── management.py# Schools, departments, programmes, batches, users, offerings
│   │           ├── timetable.py # University timetable matrix and slot scheduling
│   │           ├── credits.py   # Faculty workload, subject credits, student audit
│   │           ├── audit.py     # System audit logs and live attendance sessions
│   │           ├── dashboard.py # System stats, action items, health diagnostics
│   │           ├── corrections.py# Attendance record correction approvals
│   │           └── leaves.py    # Staff review and approval of leave applications
│   ├── models/                  # SQLModel table definitions
│   │   ├── academic.py          # School, Department, Programme, Batch, Section, Offering, Timetable
│   │   ├── attendance.py        # AttendanceSession, Record, AuditLog, Correction, Leave
│   │   └── auth.py              # User, RoleAssignment, StudentProfile, RefreshToken
│   └── schemas/                 # Pydantic request/response schemas
├── alembic/                     # Database migration revisions
├── rebuild_and_sync.py          # Full timetable & section consolidation sync utility
├── sync_full_timetable.py       # Comprehensive timetable sync script
├── sync_samay_timetable.py      # University Samay timetable synchronization tool
└── uploads/                     # Storage for student medical proofs and certificates
```

### Complete API Router Groups

1. `/api/v1/auth` — Login, token refresh with rotation, `/auth/me` complete user profile resolution, password changes.
2. `/api/v1/web/management` — Academic hierarchy (Schools, Departments, Programmes, Specializations, Batches, Sections), paginated user search & onboarding, course offerings, and faculty reassignment.
3. `/api/v1/web/timetable` — Full timetable matrix management, slot allocation, and university timetable synchronization.
4. `/api/v1/web/credits` — Faculty workload computation, subject credit synchronization, and student credit audit.
5. `/api/v1/web/audit-logs` — Administrative audit logs recording critical user and data actions.
6. `/api/v1/web/attendance-sessions` — Real-time attendance registers with present/absent/leave breakdowns and anomaly flags.
7. `/api/v1/admin/dashboard` — Analytical pulse, setup health verification, summary metrics, and pending actions.
8. `/api/v1/web/corrections` — HOD/Coordinator queue for reviewing attendance dispute requests.
9. `/api/v1/web/leaves` — Staff review, approval, and rejection of student leave requests with automatic attendance updates.
10. `/api/v1/mobile/attendance` — High-speed offline sync for mobile devices, teacher class rosters, and live roll calls.
11. `/api/v1/mobile/student` — Student dashboard with safe bunk calculation buffer, live weekly schedule with lab groups, and subject history.
12. `/api/v1/mobile/timetable` — Day-by-day timetable schedule lookup for students and faculty.
13. `/api/v1/mobile/leaves` — Student leave submission with multipart file upload.

---

## 4. Academic Hierarchy, User Search & Sub-Batch Architecture

### 1. Unified Section Architecture (`batch_label`)
Previously, timetable synchronization occasionally split lab sessions into artificial phantom sections (e.g. `CS-IV-A (Batch 1)` and `CS-IV-A (Batch 2)`), fragmenting enrollment.

The system now enforces **Sub-Batch Consolidation**:
- Students remain cleanly enrolled in their true parent class section (e.g., `CS-IV-A`, `BCS-IV-A`).
- Timetable slots store an explicit `batch_label` (e.g., `G-1`, `G-2`, or `null`).
- In the frontend and mobile apps, lab sessions clearly indicate their assigned group tag (`G-1`, `G-2`) while keeping all student enrollments unified under the primary section.

### 2. User Management & Deep Search (`GET /web/management/users`)
The user management endpoint supports high-volume paginated queries with server-side filtering:

- **Parameters**:
  - `page`: Page index (default: `1`).
  - `size`: Items per page (default: `25`).
  - `search`: Debounced search string matching `email`, `full_name`, and `roll_number` (case-insensitive `ILIKE`).
  - `role`: Filter by user role (`ADMIN`, `DEAN`, `HOD`, `COORDINATOR`, `TEACHER`, `STUDENT`) via `RoleAssignment` subqueries.
- **Response**:
  ```json
  {
    "items": [
      {
        "id": 1,
        "email": "admin@gbu.ac.in",
        "full_name": "System Administrator",
        "is_active": true,
        "role": "ADMIN",
        "roles": ["ADMIN"],
        "scope_type": "UNIVERSITY",
        "scope_id": null
      }
    ],
    "total": 407,
    "page": 1,
    "size": 25
  }
  ```

---

## 5. Credits & Faculty Workload Management

Located under `/api/v1/web/credits`:

- **Faculty Workload Analysis (`GET /faculty-workload`)**: Computes total teaching hours, theory lectures, lab sessions, and allocated credits per faculty member for a given academic term.
- **Student Credit Audit (`GET /student-credit-audit`)**: Audits student enrollment credits against prescribed semester requirements to detect under-credited or over-credited students.
- **Timetable Credit Synchronization (`POST /sync-from-timetable`)**: Automatically scans scheduled timetable slots and updates subject credit allocations based on contact hours.
- **Subject Classification (`PUT /subjects/{id}/credits`)**: Allows setting course classification:
  - `LECTURE` (Theory)
  - `LAB` (Practical)
  - `PROJECT` (Minor / Major Projects)
  - `TRAINING` (Industrial / Internship)
  - `SEMINAR`

---

## 6. University Timetable Engine & Sync Tools

### Timetable Features:
- Weekly timetable scheduling across days (`MONDAY` through `SATURDAY`), periods (`1` through `8`), and rooms.
- Prevents faculty double-booking and room collision at the database level.
- Provides unified views:
  - **By Section**: View full weekly schedule for a class section with `G-1` / `G-2` group indicators.
  - **By Faculty**: View personal timetable for a teacher.
  - **By Room / Department**: View room occupancy.

### Timetable Synchronization Utilities:
- **`rebuild_and_sync.py`**:
  ```bash
  python rebuild_and_sync.py
  ```
  Consolidates phantom sub-batches, preserves real sections, fetches live schedule data from the external Samay API, extracts group tags (`[G-1]`, `[G-2]`), and updates all timetable slot entries in a clean transaction.
- **`sync_samay_timetable.py`**:
  Fetches and maps university timetable records into the database.
- **`sync_full_timetable.py`**:
  Performs complete catalog and timetable synchronization across all academic departments.

---

## 7. Mobile Endpoints (Student & Teacher)

### Student Intelligence (`/api/v1/mobile/student`)
- **`GET /dashboard`**: Returns the complete student dashboard:
  - Overall attendance percentage and target compliance.
  - Total attended vs. total periods held.
  - **Safe Bunk Calculation Buffer**: Calculates how many classes a student can safely miss before dropping below the required 75% limit, or how many consecutive classes are required to recover.
  - Attendance alerts (`On Track`, `Needs Attention`, `No Sessions`).
- **`GET /schedule`**: Returns the student's live weekly timetable schedule, sorted by time, with room and group badges (`G-1` / `G-2`).
- **`GET /history`**: Comprehensive subject-wise breakdown of attendance history, including monthly breakdown and session timestamps.

### Teacher Attendance & Roll Call (`/api/v1/mobile/attendance`)
- **`GET /classes`**: Retrieves all class offerings allotted to the teacher.
- **`POST /roll-call`**: Submits live roll call attendance for a specific session with immediate record persistence.
- **`POST /sync`**: Reconciles batch offline attendance sessions captured on mobile devices.

---

## 8. Authentication & Profile Management

Located in `app/api/v1/auth.py` and `app/core/security.py`:

- **Unified Profile (`GET /api/v1/auth/me`)**:
  Resolves the current authenticated user's profile including primary role, array of active roles (`roles`), academic scope (`DEPARTMENT`, `PROGRAM`, `SCHOOL`, `UNIVERSITY`), student roll number, program name, department name, school, batch, and faculty employee code.
- **Password Management (`POST /api/v1/auth/change-password`)**:
  Allows users to securely change their password by validating their old password against stored bcrypt hashes.
- **JWT Dual-Token Strategy**: Returns a short-lived `access_token` and a persistent `refresh_token`.
- **Extended Token Longevity**: Designed for long active sessions (up to 60 days with periodic refresh) to ensure faculty and mobile users are not disrupted by repetitive logins.
- **Token Rotation**: Each refresh generates a new secure refresh token while invalidating the old token to prevent replay attacks.
- **Role Hierarchy**:
  - `ADMIN`: University-wide scope.
  - `DEAN`: University school scope.
  - `HOD`: Department scope.
  - `COORDINATOR`: Programme/Batch scope.
  - `TEACHER`: Assigned course offering scope.
  - `STUDENT`: Enrolled section scope.

---

## 9. Offline Attendance Synchronization Flow

Implemented in `app/api/v1/mobile/attendance.py`:

1. Teachers take attendance offline on mobile devices when in classrooms with poor connectivity.
2. The client packages records into an `OfflineSyncPayload` containing a unique `session_uuid`.
3. When connected, `POST /api/v1/mobile/attendance/sync` reconciles the session:
   - **Idempotency**: Existing `session_uuid` records are identified to prevent duplicate insertions.
   - **Authorization**: Verifies that the user is the allotted faculty or an approved substitute.
   - **Roster Validation**: Verifies enrolled students against current sections.
   - **Clock Skew Check**: Flags timestamps that diverge significantly from server time.
   - Atomic database commit of `AttendanceSession` and individual `AttendanceRecord` items.

---

## 10. Configuration & Environment Variables

Create a `.env` file in the project root with the following variables:

```env
# Database
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/attendance_db

# Security & Tokens
SECRET_KEY=your-super-secret-key-32-chars-minimum
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440
REFRESH_TOKEN_EXPIRE_DAYS=60

# Initial Admin Setup
ADMIN_EMAIL=admin@gbu.ac.in
ADMIN_PASSWORD=AdminPassword123!
ADMIN_NAME="System Administrator"

# External Timetable Integration (Optional)
SAMAY_API_URL=https://samay.university.edu/api
```

> [!IMPORTANT]
> `DATABASE_URL` must use the async driver format: `postgresql+asyncpg://`.

---

## 11. Local Development Setup

### 1. Install Dependencies
```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
source .venv/bin/activate    # Linux / macOS
pip install -r requirements.txt
```

### 2. Run Database Migrations
```bash
alembic upgrade head
```

### 3. Start Development Server
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. Interactive Documentation
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **OpenAPI JSON**: `http://localhost:8000/api/v1/openapi.json`

---

## 12. Production Docker Deployment

```bash
# Build the image
docker build -t attendance-backend .

# Run the container with environment file
docker run -d --name attendance-api --env-file .env -p 8000:8000 attendance-backend
```

### Cloud Deployments (Render / Coolify / AWS ECS)
- Supply environment variables directly in the hosting dashboard.
- The entrypoint runs `alembic upgrade head` automatically.
- Mount a persistent volume for the `uploads/` directory if storing medical leave documents locally.

---

## 13. Summary of API Endpoints

| Category | Method | Path | Description |
|---|---|---|---|
| **Auth** | `POST` | `/api/v1/auth/login` | Email/password login with JWT access & refresh tokens |
| **Auth** | `POST` | `/api/v1/auth/refresh` | Refresh access token with rotation |
| **Auth** | `POST` | `/api/v1/auth/logout` | Revoke active refresh token |
| **Auth** | `GET` | `/api/v1/auth/me` | Fetch authenticated user profile, roles, and academic metadata |
| **Auth** | `POST` | `/api/v1/auth/change-password` | Update account password with old password verification |
| **Academic** | `GET/POST`| `/api/v1/web/management/schools` | List and create university schools |
| **Academic** | `GET/POST`| `/api/v1/web/management/departments` | List (with `?school_code`) and create departments |
| **Academic** | `GET/POST`| `/api/v1/web/management/programmes` | List (with `?department_code`) and create programmes |
| **Academic** | `GET/POST`| `/api/v1/web/management/specializations`| List and create specializations |
| **Academic** | `GET/POST`| `/api/v1/web/management/batches` | List (with `?specialization_id`) and create batches |
| **Academic** | `GET/POST`| `/api/v1/web/management/sections` | List and create class sections |
| **Users** | `GET` | `/api/v1/web/management/users` | Paginated users list with `search`, `role`, `page`, and `size` |
| **Users** | `POST` | `/api/v1/web/management/users` | Create a new user with initial role & scope |
| **Offerings** | `POST` | `/api/v1/web/management/offerings/reassign` | Reassign course offering to a new teacher |
| **Credits** | `GET` | `/api/v1/web/credits/faculty-workload` | Detailed workload breakdown per faculty |
| **Credits** | `GET` | `/api/v1/web/credits/student-credit-audit` | Student credit status report |
| **Credits** | `POST` | `/api/v1/web/credits/sync-from-timetable` | Sync credits directly from timetable contact hours |
| **Timetable** | `GET/POST`| `/api/v1/web/timetable/slots` | Timetable matrix slots and scheduling |
| **Mobile Student** | `GET` | `/api/v1/mobile/student/dashboard` | Student dashboard metrics & safe bunk buffer calculation |
| **Mobile Student** | `GET` | `/api/v1/mobile/student/schedule` | Student weekly class schedule with group badges (`G-1`, `G-2`) |
| **Mobile Student** | `GET` | `/api/v1/mobile/student/history` | Historical attendance records by subject and month |
| **Mobile Attendance** | `GET` | `/api/v1/mobile/attendance/classes` | Teacher class list and active offerings |
| **Mobile Attendance** | `POST` | `/api/v1/mobile/attendance/roll-call` | Live attendance roll call submission |
| **Mobile Attendance** | `POST` | `/api/v1/mobile/attendance/sync` | High-speed offline attendance sync |
| **Attendance**| `GET` | `/api/v1/web/attendance-sessions` | Real-time session monitoring with anomaly flags |
| **Audit** | `GET` | `/api/v1/web/audit-logs` | System-wide administrative audit trail |
| **Dashboard** | `GET` | `/api/v1/admin/dashboard/stats` | High-level user counts and summary metrics |
| **Dashboard** | `GET` | `/api/v1/admin/dashboard/action-items` | Actionable pending items (leaves, unassigned, etc.) |
| **Dashboard** | `GET` | `/api/v1/admin/dashboard/attendance-pulse` | Daily classes held and attendance percentage |
| **Dashboard** | `GET` | `/api/v1/admin/dashboard/setup-health` | Academic setup diagnostics |
| **Leaves** | `POST` | `/api/v1/mobile/leaves/apply` | Student leave application with file upload |
| **Leaves** | `POST` | `/api/v1/web/leaves/{id}/approve` | Approve leave & auto-override matching absences |
