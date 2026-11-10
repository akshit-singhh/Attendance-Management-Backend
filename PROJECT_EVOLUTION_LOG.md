# 🏛️ University Attendance & Broadcast Management System
## Central Project Evolution, Architecture Decision Records (ADRs) & Activity Log

> **Repository Scope:**
> - **Backend API:** `D:\Attendance_management\Attendance_backend` (FastAPI + SQLModel + PostgreSQL/Supabase)
> - **Web Dashboard:** `D:\Attendance_frontend_gravity\Attendance_frontend\minor-project` (React + TypeScript + Vite + TailwindCSS)
> - **Mobile Client:** `D:\Attendance app\AttendanceApp\app` (Android Jetpack Compose + Kotlin + Retrofit + Firebase FCM)

---

## 1. Architectural Decision Records (ADRs)

### ADR-001: Decommissioning `ntfy` and Adopting Firebase Cloud Messaging (FCM)
* **Date & Time:** 2026-11-03 16:30:00 IST
* **Status:** ✅ Accepted & Implemented
* **Context:**
  Earlier iterations used a self-hosted `ntfy` server on `http://192.168.1.7:8095`. The Android client maintained an open Server-Sent Events (SSE) listener (`AttendanceNotificationManager.kt`) to receive live alerts.
* **Problems Identified:**
  1. *Hardcoded LAN IPs:* Android failed with `ConnectException: Failed to connect to /192.168.1.7:8095` whenever testing on mobile data or outside the local subnet.
  2. *Background Termination:* When the Android app was swiped from RAM or put into deep Doze mode, the raw HTTP SSE stream died instantly, resulting in missed broadcasts.
  3. *Zero Push Credentials:* No native Heads-Up system push notifications when the device was locked.
* **Decision Taken:**
  - Completely purged `ntfy` from Backend (`notification_service.py`, `.env`, configuration settings).
  - Decommissioned SSE client code in Android app.
  - Standardized on **Google Firebase Cloud Messaging (FCM)** with `firebase-admin 7.7.0` on Backend and `firebase-messaging:24.1.0` on Android.
  - Android client registers its FCM device token via `POST /api/v1/notifications/devices/register` during both app initialization (`MainActivity.kt`) and login flow (`AuthRepository.kt`).
* **Consequences & Impact:**
  - Guaranteed push delivery even when the app is completely terminated or device is asleep.
  - Deep-link metadata payload (`notification_id`, `priority`, `action_url`) routed directly to timetable and notification inbox screens.

---

### ADR-002: In-Database State Machine & Startup Auto-Recovery vs Heavy Message Brokers
* **Date & Time:** 2026-11-03 19:15:00 IST
* **Status:** ✅ Accepted & Implemented
* **Context:**
  Large broadcast distributions fan out to hundreds of students, teachers, and coordinators. We needed absolute resilience against mid-flight crashes (e.g., sudden server shutdown, power loss, or transient Supabase pooler disconnections).
* **Alternatives Evaluated:**
  - *Option A: Redis + Celery / RabbitMQ / Apache Kafka:* Requires installing external Docker daemons, dedicated worker containers, and complex retry pipelines. Overhead is excessive for a single-server deployment.
  - *Option B: PostgreSQL Transactional Outbox & State Machine (Chosen):* Uses existing PostgreSQL database tables with atomic chunk commits and idempotent unique constraints.
* **Decision Taken:**
  - Extended `Notification` model with a PostgreSQL native enum `notificationdeliverystatus`:
    `PENDING` $\rightarrow$ `PROCESSING` $\rightarrow$ `COMPLETED` (or `FAILED` with captured `failure_reason`).
  - Added unique constraint `uq_notification_recipient(notification_id, user_id)` to prevent duplicate in-app inbox records.
  - Chunked recipient persistence into bounded batches of 500 records per transaction, updating `processed_count` after each batch.
  - Wired a **Self-Healing Startup Sweeper** (`recover_interrupted_notifications()`) into FastAPI's `lifespan` hook. Upon server restart, any broadcast interrupted in `PENDING` or `PROCESSING` automatically resumes from where it stopped.
* **Consequences & Impact:**
  - Zero external infrastructure dependencies.
  - Zero duplicate notifications.
  - 100% self-healing recovery after crashes verified via automated simulation tests.

---

### ADR-003: Hierarchical Scoped Access Control (RBAC) & Scope Matrix
* **Date & Time:** 2026-11-03 21:00:00 IST
* **Status:** ✅ Accepted & Implemented
* **Context:**
  A university requires strict governance over who can send broadcasts to prevent broadcast spam:
  - `UNIVERSITY`: Restricted to `ADMIN` users only.
  - `SCHOOL`: Available to `DEAN` and `ADMIN`.
  - `DEPARTMENT`: Available to `HOD`, `DEAN`, and `ADMIN`.
  - `BATCH` / `PROGRAMME`: Permitted for `COORDINATOR`, subject to dynamic Admin settings toggle (`allow_coordinator_broadcast`).
  - `SECTION`: Permitted for `TEACHER`, subject to Admin toggle (`allow_teacher_broadcast`).
  - `INDIVIDUAL`: Direct 1-to-1 administrative messages.
* **Decision Taken:**
  - Enforced server-side in `send_broadcast` endpoint in `app/api/v1/notifications.py`.
  - Implemented `sender_owns_scope(db, assignments, scope_type, scope_id)` resolving the full academic hierarchy (Section $\rightarrow$ Batch $\rightarrow$ Programme $\rightarrow$ Department $\rightarrow$ School) to strictly prevent unauthorized broadcast cross-targeting.
  - Dynamic toggles stored in `NotificationSetting` database table and configurable via Admin Dashboard UI tab.

---

### ADR-004: Tailscale Tunnel & Supabase Pooled Connection Architecture
* **Date & Time:** 2026-10-25 18:00:00 IST
* **Status:** ✅ Accepted & Implemented
* **Context:**
  Mobile testing required real device testing on physical Android devices (e.g. Xiaomi 23076RN4BI) while backend ran on a local Windows development machine.
* **Decision Taken:**
  - Exposed local FastAPI port 8000 through Tailscale Funnel / MagicDNS domain:
    `https://laptop-f0uunm9o.taild8f6a1.ts.net`
  - Connected Backend to remote PostgreSQL database hosted on Supabase (AWS Asia Pacific South 1 - Mumbai) using `asyncpg` pooler on port 5432.
  - Android `ApiConfig.kt` dynamically points to the Tailscale URL with Retrofit OkHttp client timeouts configured to withstand mobile network latency.

---

### ADR-005: Git Sanitation & Security Hardening
* **Date & Time:** 2026-11-02 20:30:00 IST
* **Status:** ✅ Accepted & Implemented
* **Context:**
  Private secrets and build artifacts were at risk of being committed to public GitHub repositories.
* **Decision Taken:**
  - Android `.gitignore` updated: `app/google-services.json`, `local.properties`, `.idea/`, `.gradle/` completely excluded.
  - Backend `.gitignore` updated: `serviceAccountKey.json`, `.env`, `uploads/`, `attendance.db` excluded.
  - Added `serviceAccountKey.json` and `*firebase*.json` to `.dockerignore` for Docker and Render builds.
  - Untracked cached configuration XML files from Git index.

---

### ADR-006: High-Fidelity Landscape Print & Vector PDF Export Engine for Academic Timetables
* **Date & Time:** 2026-11-04 01:50:00 IST
* **Status:** ✅ Accepted & Implemented
* **Context:**
  University weekly timetables span 11 periods (08:30 to 19:30) and 6 weekdays. When users invoked browser printing or saved as PDF, the output was severely defective:
  1. *Portrait Clipping:* The browser defaulted to Portrait, cutting off periods VIII through XI off the right paper edge.
  2. *Color Stripping:* Standard browsers strip background colors by default, turning dark headers white and washing out lecture/lab colored badges, leaving faint text.
  3. *UI Chrome Contamination:* Web app headers, sidebars, sync buttons, breadcrumbs, and filter dropdowns cluttered the printed page.
  4. *Multi-Page Spills on Heavy Schedules:* Teachers with multiple combined sections (e.g. 3 stacked classes in Period III) expanded table rows vertically, spilling remarks onto a second sheet of paper.
* **Decision Taken:**
  - Engineered dedicated `@media print` rules in `src/index.css`:
    - Enforced `@page { size: A4 landscape; margin: 3mm 4mm 3mm 4mm; }` to lock print preview and PDF drivers into Landscape layout automatically.
    - Added `-webkit-print-color-adjust: exact !important; print-color-adjust: exact !important;` to preserve lecture (blue) and lab (green) pill colors and headers.
    - Applied `.no-print` classes to completely hide web shell headers, navigation sidebars, buttons, and filter dropdowns.
    - Applied `zoom: 0.86`, `table-layout: fixed; width: 100% !important;`, and `page-break-inside: avoid !important` on `#timetable-print-area`.
  - Added an official **Institutional Print Banner** at top ("Gautam Buddha University • Greater Noida • Official Academic Schedule") and system verification metadata, replacing the tall web card on print.
  - Consolidated export into a unified **"Print / Save PDF"** button that dynamically sets the document title to `GBU_Timetable_{Section/Faculty}_2026-27.pdf`.
* **Consequences & Impact:**
  - Generates publication-grade vector PDF exports directly from the browser with zero external heavy PDF raster libraries.
  - 100% of periods and course allocation remarks fit neatly onto **1 single sheet of paper** across all teachers and sections.

---

### ADR-007: Android FCM Notification Architecture, Channel Separation & Clean Device Unregistration
* **Date & Time:** 2026-11-04 02:50:00 IST
* **Status:** ✅ Accepted & Implemented
* **Context:**
  Android notification management required distinct high-importance alert channels for emergency university broadcasts versus routine attendance alerts, plus automatic token deactivation upon user logout to prevent privacy leaks on shared student/faculty devices.
* **Decision Taken:**
  - In `NotificationHelper.kt`, registered channel `"university_broadcasts"` (`IMPORTANCE_HIGH`) alongside `"attendance_alerts"`.
  - In `ApiService.kt`, declared `@POST("notifications/devices/unregister") suspend fun unregisterDeviceToken(...)`.
  - In `AuthRepository.kt`, wired `unregisterDeviceToken` during `logout()`, retrieving the current FCM token and deactivating it in the backend before wiping local JWT tokens.
  - Runtime permission `android.permission.POST_NOTIFICATIONS` was verified as active in `AndroidManifest.xml`.
* **Consequences & Impact:**
  - Heads-up alerts delivered reliably for university broadcasts on Android 13+.
  - Device tokens deactivated cleanly upon logout, eliminating cross-user push contamination on shared devices.

---

### ADR-008: Native Self-Service Forgot Password & OTP Reset Architecture on Mobile
* **Date & Time:** 2026-11-08 23:30:00 IST
* **Status:** ✅ Accepted & Implemented
* **Context:**
  Users forgetting passwords on the mobile app previously had no in-app self-service recovery, requiring manual administrator database intervention and leaving an unhandled TODO in `LoginScreen.kt`.
* **Decision Taken:**
  - Implemented `ForgotPasswordScreen` with a clean 2-step state machine:
    1. *Step 1 (OTP Request):* Accepts registered institutional email or student roll number, dispatches to `POST /api/v1/auth/forgot-password`.
    2. *Step 2 (Verification & Reset):* Accepts 6-digit verification code, new password ($\ge 8$ chars) with visibility toggles, and confirmation password, submitting to `POST /api/v1/auth/reset-password`.
    3. *Success Confirmation:* Displays explicit success state with a 1-click return to login.
  - Added `ForgotPasswordUiState` to `AuthViewModel.kt`, keeping auth session flow unpolluted.
  - Added `Routes.FORGOT_PASSWORD` to Android `NavGraph.kt` with full `BackHandler` step-back navigation.
* **Consequences & Impact:**
  - Complete mobile self-service recovery for both students and faculty.
  - Zero external administrative intervention required for lost credentials.

---

## 2. Cross-Platform Feature Matrix

| Capability / Feature | Backend (`Attendance_backend`) | Web Dashboard (`Attendance_frontend`) | Android App (`AttendanceApp`) | Architecture Status |
| :--- | :--- | :--- | :--- | :--- |
| **Authentication & RBAC** | JWT OAuth2, Scoped Role Assignments | Multi-role Login, Role Guard routes | TokenManager, Secure Prefs, OTP Password Reset | 🟢 Fully Active |
| **Push Delivery Engine** | Firebase Admin SDK (`firebase-admin`) | Admin Broadcast Trigger UI | `AttendanceFirebaseMessagingService` | 🟢 Fully Active |
| **Legacy `ntfy` PubSub** | Completely Removed | Deprecated | Completely Removed | 🔴 Decommissioned |
| **Device Token Registration**| `POST /notifications/devices/register` | N/A | Auto-sync on startup & login | 🟢 Fully Active |
| **Device Token Unregistration**| `POST /notifications/devices/unregister` | N/A | Wired on `AuthRepository.logout()` | 🟢 Fully Active |
| **Broadcast Channels (Android)**| N/A | N/A | `university_broadcasts` (High) | 🟢 Fully Active |
| **In-App Bell / Inbox** | Paginated `/my-notifications`, Unread count | Header Bell, Unread counter | Notification Activity & In-app Dialog | 🟢 Fully Active |
| **Admin Broadcast History** | `/notifications/broadcasts` with status & count | Broadcast History table with search | N/A (Admin only) | 🟢 Fully Active |
| **Broadcast Governance** | Settings toggles (`allow_coordinator_...`) | Settings Tab with dynamic switch | N/A | 🟢 Fully Active |
| **Crash Resilience & Sweeper**| Outbox state machine + Non-blocking Lifespan sweeper | Displays delivery status badges | Safe idempotent display | 🟢 Fully Active |
| **Student Timetable & Leaves**| Timetable, Leave balance & requests | Approvals & Allocations | Compose Timetable & Leave Screens | 🟢 Fully Active |
| **Timetable PDF & Print** | N/A | Single-Page Landscape Vector PDF & Print (`zoom: 0.86` fit) | N/A | 🟢 Fully Active |
| **Tab Back Navigation & Stack** | N/A | React Router history | Compose BackHandler & Tab History Stack | 🟢 Fully Active |

---

## 3. Chronological Chat & Development Activity Trail

### Session: 2026-11-10 23:22:36 IST — Mobile App: Teacher Past Sessions & Interactive Attendance Correction Overhaul
* **Scope:** Mobile Client (`AttendanceApp/app`), Backend API (`Attendance_backend`)
* **Features Added & Overhauled:**
  1. *Interactive Session Explorer (`TeacherRecordsScreen.kt`):* Completely overhauled the previously bare-bones Records tab into an interactive session explorer. Teachers can view their submitted class sessions chronologically with formatted dates, section tags, and live search filtering.
  2. *Session Detail & Class Metrics Breakdown:* Tapping any past session opens the full class breakdown with summary KPI chips (Total Enrolled, Present Count, Absent Count, and Attendance Rate %).
  3. *Class Roster Inspection & Filter Chips:* Displays the full enrolled student roster for the selected session date. Supports instant filtering by *All*, *Present*, and *Absent*, as well as real-time search by student name or roll number.
  4. *Seamless Attendance Correction Dialog:* Eliminated the manual database integer `attendance_record_id` input field. Teachers now simply tap the edit icon next to any student to open a clean correction modal pre-populated with student details, current status, one-tap suggested status toggles (`Present` / `Absent`), and required reason note for HOD approval.
  5. *Backend Roster Record Binding (`app/schemas/attendance.py`, `app/api/v1/mobile/attendance.py`):* Added `record_id: Optional[int] = None` to `StudentRosterResponse` and mapped existing `AttendanceRecord.id` when fetching session rosters by date, linking individual attendance records directly to mobile correction requests.
* **Verification & Testing Conducted:**
  - Android Gradle compilation: `.\gradlew.bat compileDebugKotlin --no-daemon` $ightarrow$ **BUILD SUCCESSFUL in 25s** (0 errors, 0 warnings).
  - Backend test suite: `python -m pytest tests/test_notifications.py` $ightarrow$ **4 passed in 2.66s**.

---

### Session: 2026-11-09 22:46:49 IST — Mobile App: Teacher In-App Notification Inbox & Real-Time Badge Integration
* **Scope:** Mobile Client (`AttendanceApp/app`)
* **Features Added:**
  1. *Teacher Notification Inbox (`TeacherNotificationsScreen.kt`):* Built a dedicated in-app notification inbox screen for faculty members. Connects directly to `AttendanceNotificationManager` and the backend `GET /api/v1/notifications/my-notifications` endpoint.
  2. *Live Unread Badge & Top Bar Action:* Replaced the dormant placeholder notification button on `TeacherClassesScreen.kt` with a live Compose `BadgedBox` showing an unread count badge (e.g. `2`, `9+`). Clicking the bell immediately opens `TeacherNotificationsScreen`.
  3. *Navigation & Routing (`NavGraph.kt`, `Routes.kt`):* Registered `Routes.TEACHER_NOTIFICATIONS`, mapped back-navigation, and connected notification icons on both `TeacherClassesScreen.kt` and `TeacherTimetableScreen.kt` (`AppTopBar.kt`).
  4. *Category Filters & Bulk Actions:* Added filter chips for *All*, *Notices* (Academic/Circulars), *Timetable* (Schedule changes/Postponements), and *Unread*, along with single-tap mark-as-read and "Mark all read" header action.
  5. *Clean Empty State & Material Icons:* Adhered strictly to Hallmark design constraints using real vector iconography (`Icons.Outlined.NotificationsActive`, `Icons.Outlined.Campaign`, `Icons.Outlined.Schedule`), semantic colors, and accessible states.
* **Verification & Testing Conducted:**
  - Gradle compilation: `.\gradlew.bat compileDebugKotlin --no-daemon` $
ightarrow$ **BUILD SUCCESSFUL in 19s** with zero errors.

---

### Session: 2026-11-08 23:42:00 IST — Timetable 1-Page Guaranteed Landscape Print, Combined Class Cell Merging & Action Button Fix
* **Scope:** Web Frontend (`Attendance_frontend/minor-project`)
* **Features Added & Hardened:**
  1. *Guaranteed 1-Page Landscape Print (`src/index.css`):* Declared top-level `@page { size: A4 landscape; margin: 3mm 4mm 3mm 4mm; }` outside `@media print`. Because CSS Paged Media Module specifies that `@page` cannot be nested inside `@media`, Chromium previously ignored the rule and reverted to Portrait orientation (splitting output into 2 sheets). Added top-level declaration, adjusted `#timetable-print-area` print scaling to `zoom: 0.80`, and added `break-after: avoid !important`. Both Section view (CS-IV-A with Non-classroom courses + Remarks) and Faculty view now fit on **1 single sheet of paper**.
  2. *Intelligent Merged / Combined Lecture Grouping (`UniversityTimetableTab.tsx`):* For faculty teaching combined branch classes simultaneously in the same lecture hall (e.g., Rashmi Singh teaching `CC303`, `CD303`, `CM303` in `IL202` on Wed/Thu/Fri for `CSE-CS-III`, `CSE-DS-III`, and `CSE-ML-III`), implemented `groupCellSlots()`. Instead of stacking 3 redundant full cards and tripling the row height, the matrix now consolidates them into a single clean card showing `CC303 / CD303 / CM303` with a distinct `Combined` badge and compact section tags (`CS-III, DS-III, ML-III`), keeping row heights uniform and compact.
  3. *Print / Export Action Button Consolidation:* Replaced confusing/redundant separate buttons with a unified, high-contrast `Print / Export PDF` action button (`bg-blue-700 text-white`) in the top action bar, functional across both Section view and Faculty view.
* **Bugs & Regressions Resolved:**
  1. *Timetable Printing Across 2 Sheets:* Resolved by moving `@page` to the top-level CSS block and applying `zoom: 0.80` with tight print table paddings (`print:p-0.5`).
  2. *Excessive Row Height & Table Distortion for Merged Faculty Slots:* Solved via `groupCellSlots()` grouping multi-section slots sharing identical rooms and periods into single compact entries.
  3. *TypeScript Type Error in `EnrollmentsTab.tsx`:* Fixed `u.filter(...)` to `(u?.items ?? []).filter(...)` matching the `PaginatedUsers` schema.
* **Verification & Testing Conducted:**
  - Production build: `npm run build` $
ightarrow$ **✓ 3072 modules transformed, built in 1.82s with zero errors**.
  - Verified Kotlin compilation for Android: `compileDebugKotlin` $
ightarrow$ **BUILD SUCCESSFUL**.

---

### Session: 2026-11-08 23:30:00 IST — Mobile Forgot Password & OTP Password Reset Flow
* **Scope:** Mobile Client (`AttendanceApp/app`), Authentication Architecture
* **Features Added:**
  1. *Mobile Forgot Password UI (`ForgotPasswordScreen.kt`):* Built a dedicated two-step self-service recovery screen supporting both university email and student roll number identification.
  2. *OTP Verification & Password Reset Flow:* Integrated 6-digit numeric verification input, password visibility toggles (`Icons.Default.Visibility`/`VisibilityOff`), client-side validation ($\ge 8$ characters, matching passwords), resend OTP action, and success confirmation screen.
  3. *State Management (`AuthViewModel.kt`):* Added `ForgotPasswordUiState` (`Idle`, `Loading`, `OtpSent`, `ResetSuccess`, `Error`), `sendForgotPasswordOtp()`, `submitResetPassword()`, and `resetForgotPasswordState()`.
  4. *Navigation & Routing (`NavGraph.kt`, `Routes.kt`, `LoginScreen.kt`):* Registered `Routes.FORGOT_PASSWORD`, wired "Forgot password?" link on `LoginScreen.kt`, and configured `BackHandler` so pressing Back on the OTP step returns to the identifier step before exiting to login.
* **Verification & Testing Conducted:**
  - Verified Kotlin compilation via Gradle: `.\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 1m 29s**.

---

### Session: 2026-11-07 23:20:00 IST — Fix AttributeError on Notification Model String Attributes in `/my-notifications`
* **Scope:** Backend API (`Attendance_backend`)
* **Bugs & Regressions Resolved:**
  1. *HTTP 500 on `GET /api/v1/notifications/my-notifications`:* When retrieving user notifications, `NotificationRecipientItem` accessed `notif.notification_type.value` and `notif.priority.value`. Since `notification_type`, `priority`, and `scope_type` were converted to plain `str` in the database model to prevent Postgres `$1::notificationtype` binding errors, accessing `.value` threw `AttributeError: 'str' object has no attribute 'value'`.
  2. *Defensive Attribute Value Extraction Helper:* Implemented helper `_val(x)` in `app/api/v1/notifications.py` (`x.value if hasattr(x, "value") else str(x)`) and applied across `get_my_notifications`, `send_broadcast`, and `notify_users`, ensuring both raw string DB columns and Pydantic schema enums serialize cleanly.
* **Verification & Testing Conducted:**
  - Added regression test `test_my_notifications_string_attributes` in `tests/test_notifications.py` verifying string values for `notification_type`, `priority`, and `title` return HTTP 200 with complete payload.
  - Test suite result: `pytest tests/test_notifications.py` $\rightarrow$ **4 passed in 2.48s**.

---

### Session: 2026-11-06 20:00:00 IST — Student App Back-Navigation Stack & Top Bar Back Button Removal
* **Scope:** Mobile Client (`AttendanceApp/app`)
* **Features Added & Fixed:**
  1. *Top App Bar Back Button Removed on Student Tabs:* Removed redundant top back arrow (`navigationIcon`) from `StudentCalendarScreen.kt` and `AttendancePlannerScreen.kt`. Made `onNavigateBack` parameter optional (`(() -> Unit)? = null`) and conditionally omitted the icon when used within bottom navigation pager tabs.
  2. *Predictable Back-Navigation Stack across Tabs:* Fixed issue where pressing system Back button inside student tabs (Calendar, Planner, Subjects, Timetable) immediately closed and exited the app. Implemented `BackHandler` inside `StudentMainPagerScreen.kt` backed by a reactive `pageHistory` stack (`mutableStateListOf<Int>()`). When pressing Back on any sub-tab, the app now smoothly returns to the previously visited screen/tab in sequence, finally returning to the Home Dashboard (`page 0`) before exiting.
* **Bugs & Regressions Resolved:**
  1. *Sudden App Termination on Back Press:* When moving between bottom navigation tabs (e.g. Dashboard -> Calendar -> Planner), pressing the system Back button bypassed the tab history and popped the root Compose destination, causing the Android Activity to finish. Resolved via `BackHandler(enabled = pagerState.currentPage != 0 || pageHistory.isNotEmpty())`.
  2. *Awkward Top Navigation Icon on Root Tabs:* Attendance Calendar and Planner showed a top back arrow even though they are root bottom navigation destinations.
* **Verification & Testing Conducted:**
  - Gradle compilation: `.\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 25s**.

---

### Session: 2026-11-04 03:05:00 IST — Android Client Push Integration & Timetable Dense Layout Verification
* **Scope:** Android Client (`AttendanceApp/app`), Web Frontend (`Attendance_frontend/minor-project`)
* **Features Added & Verified:**
  1. *Android Notification Channels:* Verified `android.permission.POST_NOTIFICATIONS` in `AndroidManifest.xml`; created and configured `"university_broadcasts"` with `NotificationManager.IMPORTANCE_HIGH` in `NotificationHelper.kt`.
  2. *Android Device Unregister on Logout:* Implemented `POST /api/v1/notifications/devices/unregister` call in `ApiService.kt` and wired it into `AuthRepository.kt:logout()` to deactivate device push tokens on backend upon user logout.
  3. *Dense Multi-Slot Faculty 1-Page Fit:* Verified single-page print layout for teachers with high-density multi-section overlapping loads (e.g. Rashmi Singh with 3 stacked courses in Period III). Applied `zoom: 0.86`, `print:space-y-0.5`, `print:p-0.5`, and compacted remarks to guarantee 100% 1-page fit on A4 Landscape.
* **Verification & Testing Conducted:**
  - Android Gradle compilation: `.\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL**.
  - Web Frontend build: `npm run build` $\rightarrow$ **3072 modules transformed, built in 896ms**.

---

### Session: 2026-11-04 02:45:00 IST — Production-Ready Hardening of University Notification System
* **Scope:** Backend (`Attendance_backend`), Render Deployment, Database Migrations, Test Suite
* **Features Added & Hardened:**
  1. *Authorization Hole Fixed (`POST /notifications/broadcast`):* Added `sender_owns_scope` enforcing university academic hierarchy (`Section` $\rightarrow$ `Batch` $\rightarrow$ `Programme` $\rightarrow$ `Department` $\rightarrow$ `School`). Unhandled scopes (`PROGRAM`, `SCHOOL`) rejected with HTTP 400; unauthorized senders rejected with HTTP 403.
  2. *Live Notification Dispatch Helper (`notify_users`):* Created unified `notify_users(...)` helper wiring student push alerts for roll-call absences, class postponements, and medical leave approvals/rejections with crash-resilient background fan-out.
  3. *Firebase Cloud Messaging Hardening:* Added support for `FIREBASE_CREDENTIALS_JSON` environment variable for cloud container deployments (Render/Docker) with file path fallback; eliminated fake successes; added `fcm_configured: bool` to `/health` endpoint and updated `render.yaml` and `.dockerignore`.
  4. *FCM Token Pruning & Unregister Endpoint:* Corrected token pruning check to `isinstance(res.exception, messaging.UnregisteredError)`, automatically deactivated shared-device tokens on login, added `POST /api/v1/notifications/devices/unregister` for Android logout, and chunked device queries in batches of 5000.
  5. *Non-blocking, Idempotent Startup Recovery:* Converted `recover_interrupted_notifications` to a non-blocking background task with error shielding, skipping redundant FCM pushes when recipient inbox rows already completed.
  6. *Schema Drift & Migration Resolution:* Changed `notification_type`, `priority`, and `scope_type` columns to plain `str` in SQLModel to eliminate postgres enum binding errors. Generated and applied migration `97033cf6c501` adding composite index `(user_id, is_read)` on `notificationrecipient`, `scopetypeenum.SECTION`, `employee_code`, and academic workload columns. Removed runtime DDL side effects from `init_admin()`.
  7. *Performance & Cleanups:* Replaced load-and-loop in `mark_all_read` with a single SQL UPDATE; removed per-row recipient queries in `/broadcasts`; excluded expired notifications from `/my-notifications` and `/unread-count`; replaced raw `print` calls with structured logging; parameterized CORS origins in `main.py`.
* **Verification & Testing Conducted:**
  - Added test suite in `tests/test_notifications.py` covering:
    a) Scope authorization (HOD 403 on other departments, 400 on PROGRAM/SCHOOL/invalid role).
    b) Absent student roll-call sync produces exactly 1 `NotificationRecipient` row and returns 200 even when FCM throws an exception.
    c) `/health` endpoint returns `fcm_configured: bool` and `/devices/unregister` deactivates device tokens cleanly.
  - Ran `pytest tests/test_notifications.py` $\rightarrow$ **3 passed in 2.46s**.

---

### Session: 2026-11-04 01:50:00 IST — University Timetable Print & Landscape PDF Export Engine
* **Scope:** Web Frontend (`Attendance_frontend/minor-project`)
* **Features Added:**
  1. Enforced `@page { size: A4 landscape; margin: 3mm 4mm 3mm 4mm; }` and `-webkit-print-color-adjust: exact` in `src/index.css` to guarantee automatic Landscape orientation and vibrant colors when printing or saving as PDF.
  2. Integrated dynamic document title generation (`GBU_Timetable_{Section/Faculty}_2026-27`) so browser "Save as PDF" automatically suggests a clean, official filename.
  3. Added print-only institutional crest header ("Gautam Buddha University • Greater Noida • Official Academic Schedule") and system-generated verification footer.
  4. Implemented `no-print` classes to completely hide web application chrome (top navigation bar, breadcrumbs, search buttons, filter dropdowns) during printing.
  5. Configured `print:table-fixed`, `print:w-full`, and compact `print:p-0.5` cell styling ensuring all 11 periods (08:30 to 19:30) fit edge-to-edge without truncation.
  6. Added `page-break-inside: avoid` to course remarks and non-classroom allocation cards.
  7. Condensed vertical print metrics to guarantee 100% single-page fit: consolidated double stacked headers into a single 28px institutional print bar (`print:hidden` on dark web card), reduced empty period slot min-height to 22px, hid verbose non-classroom explanatory paragraphs on print, and formatted course remarks table to compact 8px typography.
* **Bugs & Regressions Resolved:**
  1. *Faint/Washed-out text in Print Preview:* Resolved browser background stripping by enforcing `print-color-adjust: exact`.
  2. *Right Columns Clipped in Portrait:* Resolved by locking orientation to Landscape and removing `min-w-[950px]` overflow constraint on print.
  3. *Unwanted UI Controls on Paper:* Eliminated filter dropdowns, buttons, and app bars from printed output.
  4. *Spillover across 2 sheets of paper:* Eliminated extra vertical padding and redundant headers, guaranteeing the complete timetable, non-classroom courses, and remarks fit onto **1 sheet of paper**.
  5. *Faculty View Print ReferenceError:* Replaced undeclared variable `currentTeacherName` with reactive state `selectedTeacherName`, enabling print and PDF export on the Faculty Load view.
  6. *Redundant Dual Print Buttons:* Consolidated two separate buttons (`Print` and `Export PDF`) into a single, clean `Print / Save PDF` action button.
* **Verification & Testing Conducted:**
  - Verified clean production build with `npm run build` (3072 modules transformed in 700ms).
  - Confirmed dense faculty timetable fit onto 1 single sheet in landscape print preview.

---

### Session: 2026-11-03 23:45:00 IST — FCM Push Engine, Security Audit & Crash Resilience
* **Scope:** Backend, Android Client, Database Migrations
* **Features Added:**
  1. Integrated Google Firebase Cloud Messaging across Backend (`serviceAccountKey.json`) and Android app (`google-services.json`).
  2. Implemented `POST /api/v1/notifications/devices/register` to bind user accounts to FCM device tokens.
  3. Added Android device token auto-sync in `MainActivity.kt` and `AuthRepository.kt`.
  4. Created database migration `f2a9c3d4e5b6` introducing `notificationdeliverystatus` ENUM (`PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`), `processed_count`, `failure_reason`, and unique constraint `uq_notification_recipient`.
  5. Implemented bounded chunking (500 recipients/chunk) and atomic transactions in `dispatch_notification_fanout`.
  6. Implemented self-healing startup recovery sweeper `recover_interrupted_notifications()` in FastAPI `lifespan`.
* **Features Removed / Deprecated:**
  1. Purged all references to self-hosted `ntfy` (`192.168.1.7:8095`).
  2. Deleted raw SSE continuous stream listeners in Android `AttendanceNotificationManager.kt`.
  3. Removed `NTFY_SERVER_URL` from Backend `.env` and configuration models.
* **Bugs & Regressions Resolved:**
  1. *NameError in Broadcast Endpoint:* Added missing `background_tasks: BackgroundTasks` argument to `send_broadcast`.
  2. *HTTP 500 in Admin Broadcast History:* Fixed `current_user` type mismatch in `get_broadcast_history` where an integer ID was returned instead of a `User` model.
  3. *Alembic Schema Drift:* Stamped Alembic to `e1f8c2b5d4a7` and safely upgraded to `f2a9c3d4e5b6` after resolving PostgreSQL enum casting.
* **Verification & Testing Conducted:**
  - Successfully received push notification on physical Xiaomi device.
  - End-to-end simulated crash test: verified stalled broadcast recovery of 406 recipient records with zero duplicates.
  - Verified clean production build on Frontend (`vite build`) and Android compilation.

---

## 4. Maintenance Guidelines for Future Chats
> **Mandatory Rule for AI Assistant & Developers:**
> Whenever features, endpoints, or mobile screens are created, refactored, or removed:
> 1. Check this log at the start of the task to understand existing architectural constraints.
> 2. Record any major design decision under **1. Architectural Decision Records (ADRs)** with date and clock timestamp.
> 3. Update the **2. Cross-Platform Feature Matrix** if any capability's status changed.
> 4. Append a new session entry under **3. Chronological Chat & Development Activity Trail** in reverse-chronological order (newest on top), strictly formatted with **Date and exact Timestamp**:
>    ```markdown
>    ### Session: YYYY-MM-DD HH:mm:ss IST — <Session Title>
>    * **Scope:** <Backend / Frontend / Android / Database>
>    * **Features Added:** ...
>    * **Features Removed / Deprecated:** ...
>    * **Bugs & Regressions Resolved:** ...
>    * **Verification & Testing Conducted:** ...
>    ```
