# Attendance Management Backend

This repository contains the backend for an attendance management system built with FastAPI, SQLModel, and PostgreSQL. It supports student attendance tracking, teacher-side class management, academic setup, role-based access control, and admin workflows.

The backend is designed for a university-style environment where users can be assigned roles such as Admin, HOD, Coordinator, Teacher, and Student. It exposes both mobile-oriented attendance APIs and web/admin management endpoints.

---

## 1. What this project is for

This project is not just a simple CRUD API. It handles:

- User authentication and token-based authorization
- Role-based access control for different staff/student flows
- Academic setup: terms, courses, subjects, sections, and course offerings
- Student enrollment and academic mapping
- Teacher attendance sessions and sync logic
- Offline attendance capture and server reconciliation
- Admin and HOD management operations
- Upload support for documents such as medical certificates

In practical terms, the system is built to support a campus attendance workflow where:

- Students are enrolled in subjects/sections
- Teachers take attendance for each scheduled class
- Attendance data can be synced from offline mobile devices
- HOD/Admin manage users, courses, and academic setup
- Attendance records can be flagged for anomalies such as duplicate sessions or stale rosters

---

## 2. Tech stack

- Python 3.10+
- FastAPI
- SQLModel + SQLAlchemy async ORM
- PostgreSQL
- Alembic for migrations
- JWT-based authentication with PyJWT
- Passlib + bcrypt for password hashing
- Uvicorn for running the app
- Pydantic Settings for environment config
- Docker for repeatable deployment

Dependencies are listed in requirements.txt.

---

## 3. Project architecture

The application follows a typical FastAPI layered structure:

- app/main.py: app bootstrap, router registration, CORS, static file mounting
- app/core/config.py: environment variables and app settings
- app/core/database.py: async database engine and session provider
- app/core/security.py: password hashing and JWT creation/verification
- app/api/: API routes grouped by version and feature area
- app/models/: SQLModel database tables and schemas
- app/services/: application service layer (currently minimal / open for extension)
- alembic/: database migration scripts
- uploads/: user-uploaded files such as medical certificates

### Main API groups

- /api/v1/auth
  - Login
  - Refresh token
  - Logout

- /api/v1/mobile/attendance
  - Teacher schedule lookup
  - Student roster retrieval
  - Offline attendance sync

- /api/v1/mobile/student
  - Student-related endpoints

- /api/v1/web/management
  - User creation, student profile linking, and activation/deactivation
  - Paginated user listing with optional role filtering
  - Academic hierarchy: departments, programmes, specializations, batches, and sections
  - Academic setup: terms, subjects, course offerings, faculty, and enrollments
  - Hierarchy and academic record deletion

- /api/v1/web/corrections
  - Correction management flows

- /api/v1/web/leaves
  - Leave management endpoints

- /api/v1/mobile/leaves
  - Student medical leave application with document upload

---

## 4. Runtime flow

At application startup:

1. FastAPI app is created in app/main.py
2. Startup lifespan hook runs init_admin() from app/create_admin.py
3. The app loads settings from .env
4. CORS is enabled
5. Static uploads are mounted under /static/uploads
6. All API routers are registered

The root endpoint is:

- GET /
  - returns a basic health/status message: "Attendance API is running"

---

## 5. Authentication and authorization model

Authentication is token-based using JWT.

### Core pieces

- app/core/security.py handles the token logic and password hashing
- app/api/deps.py defines protected route dependencies
- app/models/user.py defines the users, roles, scopes, and refresh tokens

### Role system

The system uses role-based guards such as:

- require_student
- require_teacher
- require_coordinator
- require_hod
- require_admin

Examples from the code:

- Teachers, coordinators, HODs, and admins can act under teacher permission
- HOD and admin are allowed to manage academic setup
- Only admin can perform admin-level account actions

This is important because most APIs are protected and do not accept unauthenticated requests.

---

## 6. Database design overview

The project uses SQLModel with async PostgreSQL sessions.

### Database session setup

In app/core/database.py:

- An async engine is created with settings.DATABASE_URL
- get_session() yields an AsyncSession for each request

This pattern is used in all route files via Depends(get_session).

### Key models

From app/models/user.py:

- User
- RoleAssignment
- StudentProfile
- RefreshToken

From app/models/academic.py:

- Department -> Programme -> Specialization -> Batch -> Section hierarchy
- AcademicTerm
- Subject
- Section
- CourseOffering
- StudentSubjectMap
- SubstituteGrant
- TimetableEntry

From app/models/attendance.py:

- AttendanceSession
- AttendanceRecord
- AttendanceStatus values: PRESENT, ABSENT, and LEAVE
- AttendanceAuditLog for historical status changes
- CorrectionRequest
- LeaveRequest, LeaveStatus, and LeaveType
- OfflineSyncPayload and response models
- Session status enums and historical tracking objects

These models combine to represent the academic structure and attendance lifecycle.

---

## 7. Main business workflows

### 7.1 User login and refresh

The auth flow is implemented in app/api/v1/auth.py.

Workflow:

1. User submits email and password to /api/v1/auth/login
2. System finds the user and verifies the password hash
3. Checks whether the user is active
4. Removes expired/revoked refresh tokens for that user
5. Creates access + refresh JWTs
6. Stores the refresh token in the database and returns both tokens

Refresh-token logic:

- validates token existence
- ensures it is not revoked
- ensures it is not expired
- rotates the token by creating a new one and revoking the old one

---

### 7.2 Teacher attendance sync

The main mobile teacher attendance flow is implemented in app/api/v1/mobile/attendance.py.

The key endpoint is:

- POST /api/v1/mobile/attendance/sync

This endpoint is built to process attendance captured offline on a device and reconcile it on the server.

It does several important checks:

- Session idempotency: prevents duplicate sync of the same session UUID
- Authorization: only the assigned teacher or a valid substitute can sync attendance
- Duplicate session check: flags if another session for the same class/date already exists
- Enrolled student validation: flags records for stale or invalid student IDs
- Clock skew detection: flags suspicious device timestamps
- Creates AttendanceSession and AttendanceRecord rows

This is a strong indicator that the system expects mobile attendance data to be captured offline and then synced later, which is common for low-connectivity classroom environments.

---

### 7.3 Academic and department setup

Web admin/HOD flows are implemented in app/api/v1/web/management.py.

Examples of operations:

- Create users and assign a role and scope
- Provision students with a roll number and batch assignment
- Enforce role scope rules: HODs belong to a department, coordinators to a programme, teachers to an assignment scope, and admins to the university scope
- List users with optional role filtering and page/size pagination
- Activate/deactivate accounts
- Create the department, programme, specialization, batch, and section hierarchy
- Create academic terms and subjects
- Assign teachers to course offerings
- Bulk-enroll students in course offerings

Most management endpoints use HOD authorization. User creation and account activation/deactivation remain admin-only where enforced by the route dependency.

This layer is the institutional setup layer for the system.

---

### 7.4 Attendance correction flows

The app also exposes correction-related APIs under app/api/v1/web/corrections.py, which likely supports manual approval or correction of attendance issues.

These endpoints are beyond the immediate startup/auth flow, but they are part of the management and audit cycle of the system.

### 7.5 Leave application and approval

Students can submit leave requests through `/api/v1/mobile/leaves/apply` using `multipart/form-data`. The request includes a date range, reason, and supporting document. The document is saved under `uploads/`, and the resulting public path is stored with the leave request.

Authorized coordinators, HODs, and admins can approve pending requests through the web leaves route. When a leave is approved:

1. The leave request changes from `PENDING` to `APPROVED`
2. Matching `ABSENT` attendance records within the leave date range are changed to `LEAVE`
3. Each changed attendance record is marked as overridden
4. An `AttendanceAuditLog` entry records the previous status, new status, reviewer, and reason

This keeps attendance history traceable instead of silently changing old records.

---

## 8. Important configuration

The application expects environment settings in a .env file.

No credentials or secret values are stored in this README. Configure these variables in a local `.env` file or in the deployment platform's secret/environment settings:

- `SECRET_KEY`
- `ALGORITHM`
- `DATABASE_URL`
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`
- `ADMIN_NAME`

`DATABASE_URL` must use the async PostgreSQL driver format beginning with `postgresql+asyncpg://`.

Important note:

- SECRET_KEY is required
- DATABASE_URL is required
- The project uses BaseSettings with env_file = ".env"

The app also expects a PostgreSQL database to be available before startup.

---

## 9. Running the project locally

### Install dependencies

```bash
pip install -r requirements.txt
```

### Create environment file

Create a .env file in the project root with the values shown above.

### Start the app

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### API docs

Once running, FastAPI provides Swagger UI and OpenAPI docs:

- Swagger: http://localhost:8000/docs
- OpenAPI JSON: http://localhost:8000/api/v1/openapi.json

The interactive Swagger documentation is the source of truth for exact request and response schemas. Leave application requires multipart form data and a file upload rather than a JSON body.

---

## 10. Deploying with Docker

The repository includes a production Dockerfile. The container performs these steps when it starts:

1. Runs `alembic upgrade head` against the configured database
2. Starts Uvicorn on `0.0.0.0`
3. Uses the platform-provided `PORT` value, or port `8000` locally

Build and run it locally:

```bash
docker build -t attendance-backend .
docker run --env-file .env -p 8000:8000 attendance-backend
```

The container requires `DATABASE_URL` to point to PostgreSQL using the async driver format beginning with `postgresql+asyncpg://`.

Do not bake `.env` into the image. It is excluded by `.dockerignore`; configure secrets in the hosting platform instead.

### Render

The included `render.yaml` can be used as a Blueprint. Create or connect a PostgreSQL database, then configure these environment variable names on the web service:

- `DATABASE_URL`
- `SECRET_KEY`
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`
- `ADMIN_NAME`

Render supplies `PORT` automatically. The Docker command uses it, so no hard-coded production port is required.

### Coolify and similar platforms

Create a service from this Git repository and choose Dockerfile deployment. Configure the same environment variables listed above. Set the health check path to `/` if the platform asks for one.

If the platform provides a PostgreSQL service, use its internal hostname and credentials in `DATABASE_URL`. The application runs migrations automatically before starting the API.

### Uploaded files in production

The `uploads/` directory is local container storage. Container filesystems may be replaced during redeploys or restarts. Configure a persistent volume or object storage before relying on uploaded medical certificates in production.

---

## 11. Database migrations

The repository includes Alembic migration support.

Typical migration commands:

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

If database schema changes are required, the migration files under alembic/versions are the place to review them.

---

## 12. Admin bootstrap

At app startup, init_admin() is called. That function is likely responsible for ensuring an initial admin account exists.

This pattern is useful because it removes the need to manually create the first system admin in a fresh environment.

See:

- app/create_admin.py
- app/main.py

---

## 13. Uploads and static files

The app mounts static uploads using:

```python
app.mount("/static/uploads", StaticFiles(directory="uploads"), name="uploads")
```

This means uploaded files can be served through the web under /static/uploads/...

The uploads directory is created automatically if it does not exist.

---

## 14. Security and production considerations

This project is useful as a backend foundation, but a few things should be checked before production deployment:

- Replace default admin credentials in environment variables
- Ensure SECRET_KEY is strong and environment-specific
- Restrict CORS in production instead of allowing all origins
- Use a managed PostgreSQL instance instead of a local dev database
- Add proper logging, monitoring, and health checks
- Review whether any route depends on insecure defaults or open CORS

The current code intentionally sets CORS to allow all origins for development, which is convenient but not production-safe.

---

## 15. How to navigate this repository

If you are a new developer or AI tool trying to understand the code quickly, start in this order:

1. app/main.py
   - app bootstrap and routers
2. app/core/config.py
   - app settings and environment variables
3. app/core/database.py
   - DB engine and async session setup
4. app/api/deps.py
   - authentication and role guards
5. app/api/v1/auth.py
   - login/refresh/logout flow
6. app/api/v1/mobile/attendance.py
   - primary attendance sync logic
7. app/api/v1/web/management.py
   - admin/HOD academic operations
8. app/models/\*
   - data model definitions and domain understanding
9. app/create_admin.py
   - bootstrap logic for the first admin user

This order gives the best high-level understanding before diving into lower-level features.

---

## 16. Summary

This repo is a role-based university attendance backend built around FastAPI and async SQLModel. It supports:

- Authentication and JWT-based role access
- Teacher attendance capture and offline sync
- Academic structure management
- Student enrollment tracking
- Admin and HOD workflows
- Uploaded document support

It is a complete starting point for a campus attendance system and is organized in a way that is easy to extend with new modules, reports, dashboard APIs, and student workflows.

---

## 17. Quick start checklist

- Install requirements
- Create .env file
- Ensure PostgreSQL is running
- Run Alembic migrations
- Start app with uvicorn
- Open Swagger docs at /docs
- Log in with the configured admin or create users through the admin flows

---
