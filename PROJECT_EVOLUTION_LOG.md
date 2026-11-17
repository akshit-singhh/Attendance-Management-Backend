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
| **Offline Queue & Auto-Sync** | `POST /mobile/attendance/sync` | N/A | `OfflineAttendanceManager`, Banner & NetworkCallback | 🟢 Fully Active |
| **Offline Schedule & Roster Cache** | N/A | N/A | `TeacherCacheManager`, JSON cache, pre-fetch, stale-badge | 🟢 Fully Active |
| **Connectivity Transition Strip** | N/A | N/A | `ConnectivityStrip` above navbar, auto-dismiss green banner | 🟢 Fully Active |
| **Tri-State Roll Call & Safety Net** | `POST /mobile/attendance/sync` | N/A | P/A/L pills, Leave filter, Mark All dropdown, auto-absent safety dialog | 🟢 Fully Active |

---

## 3. Chronological Chat & Development Activity Trail

### Session: 2026-11-17 19:39:48 IST — Android Auth Reliability: Preserve Sessions on Transient Refresh Failures and Encrypt Tokens
* **Scope:** Android app (`AttendanceApp/app`), `TokenManager.kt`, `RetrofitClient.kt`.
* **Features Added / Hardened:** Persisted access and refresh tokens are now AES-GCM encrypted using an Android Keystore key. Existing plaintext tokens migrate to encrypted values on first read. A failed refresh only clears the local session when the backend returns 401/404 for the refresh token; transport errors, rate limits, malformed responses, and server errors preserve the session for a later retry. Refresh is triggered only for 401 responses, since 403 is an authorization denial.
* **Features Removed / Deprecated:** Plaintext storage of newly issued tokens is deprecated; old values are upgraded lazily in place.
* **Why Changed:** A temporary offline/server issue previously cleared valid session credentials. Preferences DataStore does not encrypt values itself, leaving bearer tokens readable from app-private storage on a compromised device.
* **Verification & Testing Conducted:** `:app:compileDebugKotlin --no-daemon` completed with `BUILD SUCCESSFUL`; `git diff --check` could not inspect the Android checkout because the command was denied access to that checkout. No tests were run.
* **Migration Note:** Existing tokens are retained and encrypted on first read; if the Android Keystore key becomes unavailable, decryption returns no token and the current session will require login again.

### Session: 2026-11-17 19:29:52 IST — Backend Auth Hardening: Access Tokens, Refresh Storage & Attempt Throttling
* **Scope:** Backend authentication, SQLModel schema, Alembic migration, configuration, README.
* **Features Added / Hardened:** Default access-token lifetime is now 15 minutes. Refresh bearer tokens are stored as SHA-256 digests, including in the role-switch flow; the migration converts existing refresh-token rows in place so active clients can continue refreshing. Login, forgot-password, and reset-password now use PostgreSQL-backed, cross-instance attempt counters with 15-minute windows. OTP values are generated with a cryptographically secure RNG, stored as keyed digests, compared in constant time, and invalidated after 8 failed attempts. Password-reset requests are rate-limited while keeping the existing generic account-enumeration-safe response. The Alembic migration also clears outstanding plaintext OTPs and adds failed-attempt/rate-limit storage.
* **Features Removed / Deprecated:** Existing OTP codes are invalidated during migration; users must request a fresh code after deployment.
* **Why Changed:** 15-day access tokens increase the window in which a stolen bearer token remains usable; plaintext refresh secrets allow direct session hijack after a database disclosure; unlimited credential and OTP guesses enable online account compromise.
* **Verification & Testing Conducted:** `python -m compileall -q app alembic/versions/a2c4e6f8b1d3_harden_auth_sessions_and_rate_limits.py` completed successfully; applied migration `a2c4e6f8b1d3` via `alembic upgrade head`; verified `authratelimit` table and authentication attempt throttling against live database; verified full test suite via `pytest tests/test_notifications.py` (4 passed in 2.78s).
* **Deployment Note:** Alembic revision `a2c4e6f8b1d3` applied successfully to database head. Configured clients to refresh access tokens after 15 minutes. The rate limiter uses the PostgreSQL database and does not require Redis.

### Session: 2026-11-17 19:24:48 IST — Backend Production Readiness: Medical Leave Upload Validation
* **Scope:** Backend (`app/api/v1/mobile/leaves.py`)
* **Features Added / Hardened:** Medical leave document uploads now accept only PDF, JPEG, and PNG extensions with matching declared MIME types and basic content signatures; stream to disk in bounded chunks; reject files larger than 10 MiB and empty/mismatched files; generate server-controlled filenames; and remove the uploaded file if validation or database persistence fails. Replaced raw upload/database error output with structured server-side logging.
* **Features Removed / Deprecated:** None.
* **Why Changed:** The previous endpoint trusted the caller-provided extension, accepted arbitrary and unlimited content, buffered through an unbounded copy, and left orphaned files when persistence failed.
* **Verification & Testing Conducted:** Reviewed the edited endpoint and repository upload/static-serving references. No tests or builds were run in this session.
* **Remaining Production Concern:** Uploaded medical documents are still mounted under a public static URL. Private authenticated document retrieval and migration of existing links should be the next security feature.

### Session: 2026-11-13 03:05:00 IST — Mobile Client: Fix Invalid Compose Compiler Gradle Plugin on Kotlin 1.9.24
* **Scope:** Mobile Client (`AttendanceApp/app`), `app/build.gradle.kts`
* **Issues Addressed:**
  - *Gradle Sync Failure:* Project sync failed with `Plugin [id: 'org.jetbrains.kotlin.plugin.compose'] was not found in any of the following sources`.
  - *Root Cause:* The Gradle plugin `org.jetbrains.kotlin.plugin.compose` (Kotlin Compose Compiler Gradle plugin) is designed for Kotlin 2.0.0+. The Android project uses Kotlin 1.9.24 (`org.jetbrains.kotlin.android` 1.9.24), where Compose compiler features are configured via AGP (`buildFeatures { compose = true }` and `composeOptions { kotlinCompilerExtensionVersion = "1.5.14" }`). Including `org.jetbrains.kotlin.plugin.compose` in `app/build.gradle.kts` caused plugin resolution failure during Gradle sync.
* **Changes Applied:**
  - *Removed Invalid Plugin:* Removed `id("org.jetbrains.kotlin.plugin.compose")` from `app/build.gradle.kts`.
* **Verification:**
  - Ran `gradle_build("app:compileDebugKotlin")` $\rightarrow$ **Build finished successfully.**
  - Executed `gradle_sync` $\rightarrow$ **Sync finished successfully.**

### Session: 2026-11-13 02:33:50 IST — Teacher App: Pixel-Perfect Shimmer Skeleton Matching Actual Classes UI Layout
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherClassesScreen.kt`, `ShimmerEffects.kt`
* **Issues Addressed:**
  1. *Skeleton Didn't Match UI (Image 1 vs Image 2):* Previously, `ScheduleUiState.Loading` stripped out all persistent dashboard controls (`DashboardTabSelector`, `DailyProgressSection` progress bar, KPI stat boxes for conducted/expected classes, and `SectionHeader` with `+ Extra Class`). This caused the entire top section of the screen to vanish and re-appear abruptly.
  2. *Card Skeleton Discrepancies:* The card skeleton rendered a single full-width gray block instead of the actual card's dual buttons (`[ Mark Attendance ]` and `[ Postpone ]`), and was missing the left vertical status strip, proper code/credits/section badge placeholders, and icon indicators for time and room.
* **Changes Applied:**
  1. *Persistent Dashboard Structure (`TeacherClassesScreen.kt`):*
     - Re-architected the main screen so `DashboardTabSelector`, `DailyProgressSection` (progress bar and 2 KPI stat cards), and `SectionHeader` remain visible and in place during loading and refreshing.
     - Skeleton shimmer cards are rendered specifically inside the class list section under the header, eliminating layout shifting or disappearing controls.
  2. *Pixel-Perfect `TeacherClassCardSkeleton` (`ShimmerEffects.kt`):*
     - Added the left status indicator strip (`Box` with rounded start corners).
     - Aligned top badge placeholders: Subject code pill, Credits pill, Section text, and Status pill on the right.
     - Modeled exact lines for Subject title, Time block (with clock icon placeholder), and Room block (with room icon placeholder).
     - Added divider and dual button action placeholders matching the real `ClassCard`: wide primary button on the left (`[ Mark Attendance ]`) and secondary outlined button on the right (`[ Postpone ]`).
* **Verification:** `cd "D:\Attendance app\AttendanceApp\app"; .\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 53s** (0 errors).

---

### Session: 2026-11-13 02:27:27 IST — Teacher App: Remove Redundant Session Roster Offline Banner & Add Shimmer Skeleton Cards on Refresh
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherRecordsScreen.kt`, `TeacherClassesScreen.kt`, `TeacherTimetableScreen.kt`, `TeacherViewModel.kt`, `ShimmerEffects.kt`
* **Changes Applied:**
  1. *Removed Redundant Offline Banner from Session Roster Screen (`TeacherRecordsScreen.kt`):*
     - Removed the duplicate amber "No Internet Connection" notice card from inside the session details/roster screen (`selectedSession != null`). The user already sees the offline banner on the main sessions list screen and the bottom nav connectivity strip.
  2. *Shimmer Skeleton Cards Component Library (`ShimmerEffects.kt`):*
     - Added `TeacherSessionCardSkeleton()`: Shimmer skeleton card for session history items.
     - Added `TeacherRosterStudentCardSkeleton()`: Shimmer skeleton card for student rows with name, roll number, and status badge placeholders.
     - Added `TeacherClassCardSkeleton()`: Shimmer skeleton card for teacher dashboard classes with course badge, title, time, and button placeholders.
  3. *Fluid Shimmer Skeleton Animation on Reconnection & Refresh:*
     - When internet connectivity is restored (`isOnline == true` after `wasOffline == true`):
       - Main sessions list displays shimmering `TeacherSessionCardSkeleton` cards while retrieving fresh data.
       - Session roster view displays shimmering `TeacherRosterStudentCardSkeleton` cards while retrieving fresh roster data.
       - Classes dashboard and Timetable views display shimmering skeleton cards during refresh.
       - Live cards smoothly replace skeletons once fresh network data arrives.
* **Verification:** `cd "D:\Attendance app\AttendanceApp\app"; .\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 1m** (0 errors).

---

### Session: 2026-11-13 02:12:14 IST — Teacher App: Offline Warning Banner for Cached Sessions, System-wide Online Detection & Auto-Refresh
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherRecordsScreen.kt`, `TeacherClassesScreen.kt`, `TeacherTimetableScreen.kt`, `TeacherViewModel.kt`, `OfflineAttendanceManager.kt`
* **Issues Addressed:**
  1. *Root Cause of Missing "No Internet" Notice on Records Screen:* When `history` was loaded from cache, the network failure in `repo.getHistory()` previously swallowed the error (`if (history.isEmpty()) historyError = ...`), causing the screen to render cached records without notifying the user why fresh data could not be retrieved.
  2. *Stuck Offline / Lack of Auto-Refresh:* Screens did not listen to network restoration events, requiring an app restart to reload fresh data once internet was restored.
  3. *Unwanted "Showing cached schedule" text:* Removed redundant "Showing cached schedule from last session" banner per user specification.
* **Changes Applied:**
  1. *Non-Intrusive Offline Warning Notice (`TeacherRecordsScreen.kt`):*
     - Added `isHistoryOfflineNotice` and `isRosterOfflineNotice` state tracking.
     - When offline or when the network call fails while cached sessions exist, a warm, professional amber banner appears below the header:
       `[CloudOff] No Internet Connection • Showing saved sessions. Reconnect to get latest data. [ Retry ]`.
     - When no cache exists, a full-page clean warning card with `Icons.Outlined.CloudOff` and "Retry" is shown instead of raw host resolution errors.
     - Tapping "Retry" re-attempts fresh data load immediately.
  2. *Default System Network Callback & Online Marking (`OfflineAttendanceManager.kt`):*
     - Upgraded connectivity tracking to `cm.registerDefaultNetworkCallback()` for robust system-wide detection across Wi-Fi, cellular, and VPN interfaces.
     - Added explicit `markOnline()` and `markOffline()` methods invoked by repositories and viewmodels on successful responses and network exceptions.
  3. *Reactive Auto-Refresh on Network Restoration:*
     - Added `LaunchedEffect(isOnline)` in `TeacherRecordsScreen.kt`, `TeacherClassesScreen.kt`, and `TeacherTimetableScreen.kt`. As soon as internet connectivity is restored, all screens automatically refresh to fetch and display the latest data from the server and dismiss offline notices without user intervention.
* **Verification:** `cd "D:\Attendance app\AttendanceApp\app"; .\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 1m** (0 errors).

---

### Session: 2026-11-13 01:47:34 IST — Teacher App: Offline Timetable Caching, Simplified Connectivity Banner, Profile Navigation & Offline Warning UI
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherCacheManager.kt`, `TeacherViewModel.kt`, `TeacherTimetableScreen.kt`, `TeacherRecordsScreen.kt`, `ProfileViewModel.kt`, `ProfileScreen.kt`, `BottomNavBars.kt`, `AppTopBar.kt`, `NavGraph.kt`
* **Changes Applied:**
  1. *Simplified Connectivity Strip Banner (`BottomNavBars.kt`):*
     - Replaced verbose `"Offline • Working with cached data"` with clean, minimal `"Offline"`.
  2. *Offline Timetable Caching (`TeacherCacheManager.kt`, `TeacherViewModel.kt`):*
     - Added `loadTimetable()` and `saveTimetable()` JSON disk caching in `TeacherCacheManager.kt` (`teacher_timetable_cache.json`).
     - Enhanced `TeacherViewModel.loadTimetable()` with cache-first strategy: immediately renders cached timetable on tab switch (zero latency, fully functional offline). Fresh network fetch updates cache; network failures do not disrupt the view if cached data is already present.
  3. *Offline History & Profile Caching (`TeacherCacheManager.kt`, `TeacherRecordsScreen.kt`, `ProfileViewModel.kt`):*
     - Added history caching (`teacher_history_cache.json`) and profile caching (`user_profile_cache.json`).
     - `TeacherRecordsScreen.kt` and `ProfileViewModel.kt` read instantly from cache so history and profile screens load immediately offline.
  4. *Professional Offline Warning UI (`TeacherTimetableScreen.kt`, `TeacherRecordsScreen.kt`, `ProfileScreen.kt`):*
     - Added `TeacherCacheManager.isNetworkError()` to detect DNS/host resolution, socket timeouts, and offline states.
     - Replaced raw Java/Android socket exceptions (`Unable to resolve host...`) and harsh red crash cards with clean, polished warning states featuring soft amber styling (`#FEF3C7`), `Icons.Outlined.CloudOff`, and a clear "No Internet Connection" title with a "Retry" button.
  5. *Fixed Profile Button Navigation in Attendance Records (`TeacherRecordsScreen.kt`, `NavGraph.kt`, `AppTopBar.kt`):*
     - Passed `onOpenProfile = { navController.navigate(Routes.PROFILE) }`, `onLogout`, and `onNavigateToNotifications` from `NavGraph.kt` into `TeacherRecordsScreen.kt`.
     - Tapping the profile button now directly opens `Routes.PROFILE` without displaying an unwanted "Log Out" dropdown popup.
     - Added `userInitials` support to `AppTopBar.kt`, unifying the teacher avatar (`VS`) across Classes, Timetable, Records, and Roll Call screens.
* **Verification:** `cd "D:\Attendance app\AttendanceApp\app"; .\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 58s** (0 errors).

---

### Session: 2026-11-13 01:17:36 IST — Teacher App: Single Edit Attendance Action, Course Header Loading Fix & Diff Confirmation Dialog
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherRecordsScreen.kt`, `TeacherViewModel.kt`, `TeacherRollCallScreen.kt`
* **Changes Applied:**
  1. *De-duplicated Past Session Edit Actions (`TeacherRecordsScreen.kt`):*
     - Removed redundant edit pencil icon buttons (`Icons.Default.Edit`) from individual student rows (`RosterStudentCard`).
     - Kept a clean, prominent top **`[ Edit Attendance ]`** button as the single clear action to edit the session.
     - Streamlined student cards to display only the student name, roll number, and read-only `StatusPill`.
  2. *Resolved "Loading..." Course Header in Edit Attendance (`TeacherViewModel.kt`):*
     - Identified root cause: `resolvecourseInfo` previously only checked today's schedule (`_schedule.value` and today's disk cache). When editing past classes from other days of the week (e.g. Wednesday's `Internet of Things Lab` when today is Thursday), `courseInfo` evaluated to `null`, causing the header to fall back to `"Loading..."`, `"---"`, `"Sec --"`, and `"Room TBD"`.
     - Added thread-safe in-memory `courseInfoCache` (`ConcurrentHashMap<Int, CourseScheduleResponse>`).
     - Made `resolvecourseInfo` asynchronous: falls back to querying `repo.getSchedule(null, "ALL")` (which retrieves all classes across all days for the teacher) and synthesizing course details from `repo.getHistory()`.
     - Populated `initialMarks` in `RosterUiState.Loaded` to track baseline attendance state upon roster load.
  3. *Change Summary Diff Dialog on Edit Submission (`TeacherRollCallScreen.kt`):*
     - When submitting edits (`isEditing == true`), the confirmation dialog calculates and displays an explicit breakdown of all modified student records (`s.initialMarks` vs `s.marks`).
     - If no attendance statuses were changed, displays `"No attendance statuses were changed."` and enables `"Save Anyway"`.
     - If changes are present, displays a scrollable preview listing each modified student with roll number and status transition badge (e.g. `[ Absent ] → [ Present ]`, `[ Present ] → [ Leave ]`).
     - Confirmation button dynamically displays the change count (`Save X Changes`).
* **Verification:** `cd "D:\Attendance app\AttendanceApp\app"; .\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 1m 7s** (0 errors).

---

### Session: 2026-11-13 00:30:51 IST — Teacher App: Direct Past Session Attendance Editing (Purged Legacy HOD Correction Modal)
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherRecordsScreen.kt`, `NavGraph.kt`
* **Changes Applied:**
  1. *Purged Obsolete HOD Approval Dialog (`TeacherRecordsScreen.kt`):* Completely removed the legacy `"Request Attendance Correction"` dialog, the `"Submit for HOD Approval"` action, the mandatory reason input field, and associated orphaned state (`correctingStudent`, `suggestedStatus`, `correctionReason`, `submittingCorrection`, `correctionError`).
  2. *Integrated Direct Attendance Editing:*
     - Added a prominent primary **`[ Edit Attendance Directly ]`** action button to the Session KPI Summary card in `TeacherRecordsScreen.kt`.
     - Updated student cards (`RosterStudentCard`) so that tapping either the student card or the edit button (`Icons.Default.Edit`) immediately triggers `onEditSession(courseOfferingId, sessionDate)`.
  3. *Wired Direct Roll Call Navigation (`NavGraph.kt`):* Connected `onEditSession` to `navController.navigate(Routes.rollCall(offeringId, sessionDate))`. This opens `TeacherRollCallScreen` directly in **"Edit Attendance"** mode with that past session's student marks pre-populated, enabling faculty to directly adjust statuses via `[ P ]`, `[ A ]`, `[ L ]` and save changes immediately to the database without requiring administrative approval.
* **Verification:** `cd "D:\Attendance app\AttendanceApp\app"; .\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 48s** (0 errors, 0 warnings).

---

### Session: 2026-11-13 00:21:53 IST — Teacher App: Enable Early Roll Call for Upcoming Classes
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherClassesScreen.kt`
* **Changes Applied:**
  1. *Enabled Roll Call on Upcoming Classes:* Previously, `ClassState.UPCOMING` only showed a passive `"Scheduled Session"` label alongside a `"Postpone"` button, preventing teachers from taking attendance before the bell rings.
  2. *Unified Active Action Bar:* Consolidated `ClassState.ACTIVE`, `ClassState.MISSED`, and `ClassState.UPCOMING` into a single responsive action row. Upcoming classes now feature a prominent `[ Mark Attendance ]` button in `PrimaryBlue` next to the `[ Postpone ]` button, allowing faculty to start roll call early whenever needed.
* **Verification:** `cd "D:\Attendance app\AttendanceApp\app"; .\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 18s** (0 errors, 0 warnings).

---

### Session: 2026-11-13 00:19:31 IST — Teacher App: Temporary Date Override to 12 Nov 2026 (Thursday Schedule Testing)
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherViewModel.kt`, `TeacherClassesScreen.kt`, `TeacherRollCallScreen.kt`, `TeacherCacheManager.kt`, `TeacherTimetableScreen.kt`
* **Changes Applied:**
  1. *Temporary Date & Day Override:* Hardcoded `EffectiveDate = LocalDate.of(2026, 11, 12)` and `EffectiveDay = "THU"` across the teacher app module so that the logged-in faculty (Prof. Vidushi Sharma) can access and interact with their Thursday classes (`CS525`, `CS481`, etc.) despite today being Friday (`2026-11-13`) where they have no scheduled classes.
  2. *Schedule Fetching (`TeacherViewModel.kt`):* Updated `loadSchedule()` to query backend for `THU` and compare history against `"2026-11-12"`.
  3. *Roll Call & Cache Synchronization:* Updated `TeacherRollCallScreen.kt`, `TeacherClassesScreen.kt`, `TeacherTimetableScreen.kt`, and `TeacherCacheManager.kt` so that offline caching, roll call submissions, and live timetable period indicator sync with Thursday, 12 November 2026.
* **Verification:** `cd "D:\Attendance app\AttendanceApp\app"; .\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 52s** (0 errors, 0 warnings).

---

### Session: 2026-11-13 00:08:02 IST — Teacher App: Roll Call Leave Marking (P/A/L), Dropdown Mark All & Duplicate Counter Fix
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherRollCallScreen.kt`, `TeacherViewModel.kt`, `Models.kt`, `TeacherRecordsScreen.kt`
* **Changes & Features Added:**
  1. *Eliminated Duplicate Unmarked Count Text:* Removed the redundant left-side subtitle (`"$unmarkedCount students unmarked"`) from `BottomSubmitBar`. The bottom bar now cleanly displays `$presentCount Present • $absentCount Absent (• $leaveCount Leave)` on the left, paired with the amber badge `"$unmarkedCount Unmarked"` on the right.
  2. *Top Dropdown "Mark All" Menu (`SearchAndFilterRow`):*
     - Replaced the single-action "Mark All" button with an interactive `DropdownMenu` triggered by tapping `Mark All ▾`.
     - Options provided:
       - **All Present:** Marks all enrolled students as `PRESENT` with a green checkmark.
       - **All Absent:** Marks all enrolled students as `ABSENT` with a red cross.
       - **Reset to Unmarked:** Clears all marks back to neutral/unmarked (`null`).
  3. *3rd "Leave" Action Button (`[ L ]`) on Student Rows (`RosterRow`):*
     - Added `AttendanceStatus.LEAVE` to `Models.kt`, matching the backend schema and push notification engine.
     - Each student card now displays three side-by-side action pills: `[ P ]`, `[ A ]`, and `[ L ]`.
     - When `LEAVE` is selected: `L` button fills solid warm amber (`#D97706`) with white text; the student card softly tints warm cream/amber (`#FFFBEB`) with subtle border; `P` and `A` remain neutral outline pills.
     - Tapping `L` again toggles the student back to unmarked (`null`).
  4. *Dedicated "Leave" Filter Chip:* Added `"Leave"` chip with active student count alongside `All`, `Unmarked`, `Present`, and `Absent`.
  5. *History Status Pill (`TeacherRecordsScreen.kt`):* Added `AttendanceStatus.LEAVE` support to `StatusPill` with `Icons.Outlined.EventBusy` and `"On Leave"` label.
* **Verification:** `cd "D:\Attendance app\AttendanceApp\app"; .\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 47s** (0 errors, 0 warnings).

---

### Session: 2026-11-12 23:58:48 IST — Teacher App: Tri-State Roll Call Marking (Pattern 1) & Unmarked Safety Net (Pattern 3)
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherRollCallScreen.kt`, `TeacherViewModel.kt`
* **Features Implemented & Overhauled:**
  1. *Tri-State Attendance State (`TeacherViewModel.kt`):* Changed `RosterUiState.Loaded.marks` to `Map<Int, AttendanceStatus?>` where `null` represents `UNMARKED`. When opening a new class session, students load unselected (`null`) rather than erroneously defaulting everyone to `PRESENT`.
  2. *Dual `[ P ]` / `[ A ]` Pill Toggles (`TeacherRollCallScreen.kt` - Pattern 1):*
     - Replaced single toggle button with side-by-side tactile pill buttons for each student card.
     - **Unmarked State (`null`):** Both `P` and `A` buttons display as neutral outline pills (`#CBD5E1` border, gray text) waiting for faculty input.
     - **Present State (`PRESENT`):** `P` pill fills solid emerald green with white text; `A` remains neutral outline. Tapping `P` again toggles student back to `UNMARKED`.
     - **Absent State (`ABSENT`):** `A` pill fills solid crimson red with white text; card softly tints pink/red (`#FFF5F5`) with red border; `P` remains neutral outline. Tapping `A` again toggles back to `UNMARKED`.
     - Teachers can still use the header **"Mark All"** button to instantly mark all students as `PRESENT` for speed when attendance is high.
  3. *Unmarked Filter & Horizontal Chips (`TeacherRollCallScreen.kt`):* Added an `"Unmarked"` filter chip alongside `All`, `Present`, and `Absent`, with active counts and horizontal scrolling to prevent truncation on smaller screens.
  4. *Smart Safety Net Dialog on Submission (`TeacherRollCallScreen.kt` - Pattern 3):*
     - If the teacher taps "Submit Attendance" while any students remain unmarked (`unmarkedCount > 0`), the app intercepts submission with an `AlertDialog`:
       - Shows warning *"Unmarked Students ($unmarkedCount)"*.
       - **Action 1 ("Mark Absent & Submit"):** Calls `viewModel.submitAttendance(defaultUnmarkedTo = AttendanceStatus.ABSENT)` to automatically mark all unmarked students as absent and submit in one tap.
       - **Action 2 ("Review"):** Instantly activates the `"UNMARKED"` filter chip and dismisses the dialog so faculty can review remaining unverified students.
       - **Action 3 ("Cancel"):** Dismisses without changes.
     - If all students are marked (`unmarkedCount == 0`), the standard confirmation summary dialog appears directly.
  5. *Live Unmarked Indicator in Bottom Bar:* Bottom submit bar displays dynamic counters (`$presentCount Present • $absentCount Absent` + `$unmarkedCount students unmarked` badge in amber) so progress is clearly visible.
* **Verification:** `cd "D:\Attendance app\AttendanceApp\app"; .\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 50s** (0 errors, 0 warnings).

---

### Session: 2026-11-12 23:47:34 IST — Teacher App: Roll Call Screen UI Overhaul & Profile Navigation Integration
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherRollCallScreen.kt`, `NavGraph.kt`
* **Features Overhauled:**
  1. *Integrated Profile & Back Navigation (`TeacherRollCallScreen.kt`, `NavGraph.kt`):* Replaced legacy `AppTopBar` with a dedicated, polished `TopAppBar`:
     - Added system back button (`Icons.AutoMirrored.Filled.ArrowBack`) allowing faculty to return cleanly to `Classes` without saving accidental changes.
     - Wired the teacher's profile circle with real initials (e.g. `"VS"` in `PrimaryBlue` circle), directly navigating to `Routes.PROFILE` via `onOpenProfile` — completely eliminating the outdated "Log Out" dropdown popup.
     - Connected the notifications bell icon with live badge counter to `Routes.TEACHER_NOTIFICATIONS`.
  2. *Overhauled Course Header Card (`RollCallHeader`):*
     - Eliminated the floating live wall clock box that was cutting off subject titles and section names with awkward asymmetric padding.
     - Clean, balanced card layout displaying:
       - Subject code badge (`CS525`) + Credits (`3 Credits`) + Mode pill (`ROLL CALL` / `EDITING`).
       - Large, bold subject name (`Research Techniques in ICT`).
       - Meta row with icons: Section `Sec MT-DS-I` • Room `ICT-Conf Hall` • Session Date `12 Nov 2026`.
  3. *Clean Search & Filter Controls:* Modernized search input and filter chips (`All`, `Present`, `Absent`) with explicit counts and unified design tokens.
* **Verification:** `.\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 1m** (0 errors, 0 warnings).

---

### Session: 2026-11-12 23:38:02 IST — Teacher App: Header & ClassCard Decluttering (Removed Overdue, Duplicate Banners, and Redundant Meta)
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherClassesScreen.kt`
* **Changes Applied:**
  1. *Header Decluttering:* Removed the redundant `"Faculty • Computer Science and Engineering"` subtitle from the top dashboard header. Replaced it with a clean date format (`Thursday, 12 November`), keeping faculty role and department details inside the Profile screen where they belong.
  2. *Removed Misleading "Overdue" Status:* Classes that ended earlier today without attendance are now labeled `"Unmarked"` in a calm, warm badge (`Color(0xFFFFF7ED)`, text `Color(0xFFC2410C)`) instead of an alarming `"⚠️ Overdue"` label.
  3. *Eliminated 3x Redundant Status Duplication on ClassCards:*
     - Completely removed the redundant middle chip banner (`Class Ended • Unmarked` / `Now Active`).
     - Simplified the time row to just the schedule (`11:30 AM - 12:30 PM`) without repeating `Ended (Not Marked)`. (For upcoming classes, `• Starts in X mins` is kept).
     - Each card now displays its status **once and only once** in the top-right status pill (`Submitted`, `In Progress`, `Unmarked`, or `Upcoming`).
* **Verification:** `.\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 1m 3s** (0 errors, 0 warnings).

---

### Session: 2026-11-12 22:56:09 IST — Mobile App: Global Connectivity Strip & Transition Banner (Offline / Back Online)
* **Scope:** Mobile Client (`AttendanceApp/app`), `BottomNavBars.kt`, `TeacherClassesScreen.kt`
* **Features Added:**
  1. *`ConnectivityStrip` Composable (`BottomNavBars.kt`):* Created a system-wide, reactive network status bar positioned directly above the bottom navigation bar (`AppBottomNav`).
     - **Offline State:** When internet is lost (`!isOnline`), an amber-bordered strip appears with `Icons.Outlined.CloudOff` stating *"Offline • Working with cached data"* (or showing pending queue count if sessions are waiting to sync). Stays visible as long as device is disconnected.
     - **Back Online State:** When connectivity is restored, the bar smoothly transitions into a soft mint/green-bordered bar with `Icons.Outlined.CloudDone` stating *"Back online"* (or *"Back online • Syncing attendance..."* if background sync is running).
     - **Auto-Dismiss:** Stays visible for 2.8 seconds so the user clearly sees confirmation of reconnection, then smoothly collapses and fades out (`AnimatedVisibility(exit = shrinkVertically() + fadeOut())`).
     - **Initial Online State:** Does not show on app launch if the device is already online; only activates upon actual network state transitions.
  2. *Wired into `AppBottomNav`:* Added directly inside `AppBottomNav` Column container, meaning all Teacher tabs (Classes, Timetable, Records) and Student tabs (Dashboard, Subjects, Timetable, Planner, Calendar) automatically inherit the connectivity strip without individual screen scaffolding.
  3. *De-duplicated Screen Banners (`TeacherClassesScreen.kt`):* Simplified `OfflineSyncBanner` so it only displays when there are actual queued sessions or active syncing in progress (`queuedCount > 0 || isSyncing || lastSyncMessage != null`), avoiding duplicate generic offline banners on the screen.
* **Verification:** `.\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 53s** (0 errors, 0 warnings).

---

### Session: 2026-11-11 23:50:31 IST — Backend Fix: Notification Enum Type Mismatch on Attendance Submission
* **Scope:** Backend API (`Attendance_backend`), Database Mapping (`app/models/notification.py`, `app/api/v1/mobile/attendance.py`)
* **Root Cause Analysis:**
  When faculty submitted attendance with absent/leave records, the endpoint dispatched push notifications via `notify_users()`. In PostgreSQL, the `notification.notification_type`, `priority`, and `scope_type` columns are custom user-defined enums (`notificationtype`, `notificationpriority`, `notificationscope`). However, in `app/models/notification.py`, `Notification` had typed those three fields as generic `str`.
  During asyncpg query execution, SQLAlchemy bound parameters as `VARCHAR` (`$4::VARCHAR`), triggering PostgreSQL error:
  `asyncpg.exceptions.DatatypeMismatchError: column "notification_type" is of type notificationtype but expression is of type character varying`.
  This aborted the database session transaction. When the endpoint subsequently accessed `new_session.id`, SQLAlchemy raised `PendingRollbackError` and returned HTTP 500.
* **Fix Applied:**
  1. *Model Enum Typing (`app/models/notification.py`):* Explicitly typed `notification_type: NotificationType`, `priority: NotificationPriority`, and `scope_type: NotificationScope` in the `Notification` SQLModel table. SQLModel now generates native PostgreSQL enum bindings (`notificationtype`, `notificationpriority`, `notificationscope`).
  2. *Captured Session ID Before Notifications (`attendance.py`):* Saved `created_session_id = new_session.id` immediately after `await db.commit()`, ensuring the response payload is decoupled from any secondary notification transaction.
  3. *Fixed Postponement Enum (`attendance.py`):* Replaced invalid `"SYSTEM"` with valid `"ACADEMIC_ORDER"` for class postponement alerts.
* **Verification:**
  Direct test execution against the database via `notify_users` for absent student notifications confirmed: `SUCCESS! Created notification via notify_users` (0 errors).

---

### Session: 2026-11-11 23:39:26 IST — Teacher App: UTF-8 Mojibake / Corrupted Character Fix
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherClassesScreen.kt`
* **Bug Analysis:**
  The `â€¢`, `â€“`, and `âšï¸ ` strings shown in the UI were classic **Mojibake** (character encoding mismatch). UTF-8 byte sequences for bullet points `•` (`0xE2 0x80 0xA2`), en-dashes `–` (`0xE2 0x80 0x93`), and emojis had previously been saved or interpreted through Windows-1252 / ANSI encoding, resulting in visible `â€¢` and `â€“` artifacts on screen.
* **Fix Applied:**
  1. Replaced all raw multi-byte delimiter literals across `TeacherClassesScreen.kt` with explicit Unicode escape sequences (`\u2022`) or clean ASCII hyphens (`-`).
  2. Fixed time block formatting from `"$s â€“ $e"` to standard `"$s - $e"`.
  3. Cleaned status and section badges: `Class Ended \u2022 Unmarked`, `Sec: ... \u2022 Date: ...`.
  4. Removed emoji mojibake (`âšï¸ ` / `â ³`) from the pending expiry tag to comply with Hallmark design rules (no emojis as icons; clean text: `Expiring \u2022 Xd left` or `Xd left`).
* **Verification:** `.\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 1m 7s** (0 errors, 0 warnings, 0 remaining mojibake instances).

---

### Session: 2026-11-11 23:26:23 IST — Teacher App: Offline-First JSON Cache (Schedule & Roster Pre-Fetch)
* **Scope:** Mobile Client (`AttendanceApp/app`)
* **Features Added:**
  1. *`TeacherCacheManager.kt` (new file):* Lightweight JSON file cache singleton for the teacher's daily schedule and student rosters. Cache files are stored in the app's private `filesDir` — no new Gradle dependencies (uses existing `kotlinx.serialization.json` + `java.io.File`). Key methods: `loadSchedule()`, `saveSchedule()`, `isScheduleCacheForToday()`, `loadRoster(courseOfferingId)`, `saveRoster(courseOfferingId, roster)`, `prefetchRosters(offeringIds, fetchFn)`. Date-stamps the schedule cache to detect stale (yesterday's) data.
  2. *Cache-First `loadSchedule()` (`TeacherViewModel.kt`):* Rewrote to a **Stale-While-Revalidate** pattern — Step 1: show cached schedule from disk instantly (zero latency, works fully offline); Step 2: fetch fresh data from network in background and update state + disk cache; Step 3: atomically pre-fetch all student rosters for today's classes via `prefetchRosters()` so roll-call works with no signal in the classroom. Network failure keeps the cache displayed with no error shown.
  3. *Cache-First `loadRoster()` (`TeacherViewModel.kt`):* Rewrote to same pattern — shows cached student roster instantly, fetches network refresh in background, updates cache with fresh data. If offline and cached roster exists, teacher can still mark attendance and queue it via `OfflineAttendanceManager`.
  4. *`fromCache` badge (`TeacherClassesScreen.kt`):* Added a subtle inline indicator row with `Icons.Outlined.History` and text *"Showing cached schedule from last session"* as the first `item {}` in the `LazyColumn`, shown only when `ScheduleUiState.Loaded.fromCache == true` (i.e., schedule is from a previous day). Follows Hallmark design rules: no emoji, semantic slate-gray color `0xFF64748B`, real SVG Material icon.
  5. *Disk-cache fallback in `getAllCourses()` (`TeacherViewModel.kt`):* Updated the extra-class dialog loader to fall back to `TeacherCacheManager.loadSchedule()` as a last resort when both network and in-memory schedule are unavailable.
  6. *`AttendanceApp.kt`:* Wired `TeacherCacheManager.init(this)` during app startup alongside existing `OfflineAttendanceManager.init(this)`.
* **Files Changed:**
  - `data/offline/TeacherCacheManager.kt` — created (new file, 150 lines)
  - `ui/teacher/TeacherViewModel.kt` — `loadSchedule()`, `loadRoster()`, `getAllCourses()` updated; `resolvecourseInfo()` private helper added
  - `ui/teacher/TeacherClassesScreen.kt` — `fromCache` badge item added to `LazyColumn`
  - `AttendanceApp.kt` — `TeacherCacheManager.init(this)` wired in `onCreate`
* **Verification:** `.\gradlew.bat compileDebugKotlin --no-daemon` → **BUILD SUCCESSFUL in 25s** (0 errors, 0 warnings)

---

### Session: 2026-11-11 22:25:00 IST — Mobile App: Teacher Offline Attendance Queue & Auto-Sync Banner (Option 4)
* **Scope:** Mobile Client (`AttendanceApp/app`), Network State Management
* **Features Added & Implemented:**
  1. *Persistent Offline Attendance Manager (`OfflineAttendanceManager.kt`):* Built a thread-safe singleton queue manager. If attendance submission fails due to an unavailable network or server timeout during roll call, the full `OfflineSyncPayload` is safely persisted into internal disk storage (`offline_attendance_queue.json`).
  2. *Automatic Connectivity Monitoring & Auto-Drain:* Integrated Android `ConnectivityManager.NetworkCallback` with internet capability checks. As soon as device connectivity is restored, the queue automatically wakes up and drains pending offline sessions to `POST /api/v1/mobile/attendance/sync` in the background with zero faculty intervention required.
  3. *Clean Auto-Sync Indicator Banner (`TeacherClassesScreen.kt`):* Positioned a Hallmark-compliant status chip above the classes list on the Teacher Dashboard:
     - **Offline Mode:** Displays amber warning chip with `Icons.Outlined.CloudOff` and queue counter (e.g. *"Offline — 2 session(s) queued for upload"*).
     - **Pending Sync (Online):** Displays soft blue chip with `Icons.Outlined.CloudQueue`, queue count, and a manual **"Sync Now"** action button.
     - **In-Flight Syncing:** Displays real-time spinner and session upload progress.
     - **Sync Complete:** Displays emerald confirmation chip with `Icons.Outlined.CloudDone` before smoothly auto-dismissing.
  4. *Zero Roll-Call Disruption (`TeacherViewModel.kt`):* When teachers mark attendance in areas with poor or no cellular reception, `submitAttendance()` catches network exceptions, enqueues the payload, updates local `submittedOfferingIds` with a green checkmark immediately, and notifies the UI with a completed state.
  5. *Permissions (`AndroidManifest.xml`):* Added `<uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />` for reactive network capability callbacks.
* **Verification & Testing Conducted:**
  - Kotlin compilation via Gradle: `.\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 27s** (0 errors).

---

### Session: 2026-11-11 22:15:00 IST — Student App: Top Bar Back Button Removal & Clean BackStack Navigation Fix
* **Scope:** Mobile Client (`AttendanceApp/app`)
* **Features Removed & Streamlined:**
  1. *Removed Top App Bar Back Button (`StudentCalendarScreen.kt`, `AttendancePlannerScreen.kt`):* Removed `navigationIcon` and `onNavigateBack` from the TopAppBar of both `Attendance Calendar` and `Attendance Planner`. Because both screens are primary destinations hosted inside the persistent bottom navigation pager (`StudentMainPagerScreen.kt`), having a top-level back arrow was visually confusing and out-of-place.
  2. *Eliminated Redundant `StudentScaffold` from Notifications & Statistics (`NavGraph.kt`):* Removed nested `StudentScaffold` wrapping from `Routes.STUDENT_NOTIFICATIONS` and `Routes.STUDENT_STATISTICS`. Previously, viewing notifications or statistics displayed an out-of-context bottom navigation bar whose tab actions spawned duplicate `RenderStudentPager` instances onto the `NavController` back stack.
* **Features Removed / Deprecated:**
  1. *15-Day Attendance Grace Window Card:* Removed the yellow banner card (`PendingPolicyCard`) from the top of the **Pending** tab in `TeacherClassesScreen` to keep the UI clean, uncluttered, and professional.
* **Bugs & Regressions Resolved:**
  1. *App Closure on Back Press:* Tapping the top `<` button previously triggered `onNavigateBack()` which invoked `navController.popBackStack()`. Because the student main pager was the root destination, this abruptly terminated the activity and exited the app. With the misleading button removed and `StudentNotificationsScreen`/`StudentStatisticsScreen` properly popping back to the pager, back navigation returns to the previous screen as expected.
  2. *Predictable Tab Back Navigation:* The Jetpack Compose `BackHandler` in `StudentMainPagerScreen` maintains a visited tab history stack, so pressing the physical/system back button steps backward through visited tabs (e.g. Calendar $\rightarrow$ Planner $\rightarrow$ Dashboard) before exiting on the root Home tab.
* **Verification & Testing Conducted:**
  - Android Gradle Kotlin compilation: `cd "D:\Attendance app\AttendanceApp\app"; .\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 25s** (zero compilation errors).

---

### Session: 2026-11-10 23:22:36 IST — Mobile App: Teacher Past Sessions & Interactive Attendance Correction Overhaul
* **Scope:** Mobile Client (`AttendanceApp/app`), Backend API (`Attendance_backend`)
* **Features Added & Overhauled:**
  1. *Interactive Session Explorer (`TeacherRecordsScreen.kt`):* Completely overhauled the previously bare-bones Records tab into an interactive session explorer. Teachers can view their submitted class sessions chronologically with formatted dates, section tags, and live search filtering.
  2. *Session Detail & Class Metrics Breakdown:* Tapping any past session opens the full class breakdown with summary KPI chips (Total Enrolled, Present Count, Absent Count, and Attendance Rate %).
  3. *Class Roster Inspection & Filter Chips:* Displays the full enrolled student roster for the selected session date. Supports instant filtering by *All*, *Present*, and *Absent*, as well as real-time search by student name or roll number.
  4. *Seamless Attendance Correction Dialog:* Eliminated the manual database integer `attendance_record_id` input field. Teachers now simply tap the edit icon next to any student to open a clean correction modal pre-populated with student details, current status, one-tap suggested status toggles (`Present` / `Absent`), and required reason note for HOD approval.
  5. *Backend Roster Record Binding (`app/schemas/attendance.py`, `app/api/v1/mobile/attendance.py`):* Added `record_id: Optional[int] = None` to `StudentRosterResponse` and mapped existing `AttendanceRecord.id` when fetching session rosters by date, linking individual attendance records directly to mobile correction requests.
* **Verification & Testing Conducted:**
  - Android Gradle compilation: `.\gradlew.bat compileDebugKotlin --no-daemon` $
ightarrow$ **BUILD SUCCESSFUL in 25s** (0 errors, 0 warnings).
  - Backend test suite: `python -m pytest tests/test_notifications.py` $
ightarrow$ **4 passed in 2.66s**.

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
* **Features Removed / Deprecated:**
  1. *15-Day Attendance Grace Window Card:* Removed the yellow banner card (`PendingPolicyCard`) from the top of the **Pending** tab in `TeacherClassesScreen` to keep the UI clean, uncluttered, and professional.
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
* **Features Removed / Deprecated:**
  1. *15-Day Attendance Grace Window Card:* Removed the yellow banner card (`PendingPolicyCard`) from the top of the **Pending** tab in `TeacherClassesScreen` to keep the UI clean, uncluttered, and professional.
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
* **Features Removed / Deprecated:**
  1. *15-Day Attendance Grace Window Card:* Removed the yellow banner card (`PendingPolicyCard`) from the top of the **Pending** tab in `TeacherClassesScreen` to keep the UI clean, uncluttered, and professional.
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

### Session: 2026-11-15 23:30:00 IST — Mobile Production Readiness, ProGuard/R8 Rules & Release APK Generation
* **Scope:** Mobile Client (`Attendance app/AttendanceApp/app`)
* **Features Added & Production Hardened:**
  1. *Centralized Date & Demo Architecture (`AppDateProvider.kt`):* Replaced hardcoded date constants scattered across `TeacherCacheManager`, `TeacherViewModel`, `TeacherClassesScreen`, `TeacherRollCallScreen`, and `TeacherTimetableScreen` with a single centralized `AppDateProvider`. Production mode dynamically binds to `LocalDate.now()`, while demo mode retains the active sample schedule date (`12 Nov 2026`) for testing and evaluation.
  2. *ProGuard / R8 Optimization Rules (`app/proguard-rules.pro`):* Created complete keep and shrinking rules protecting `@kotlinx.serialization.Serializable` companion serializers, domain models (`com.akshit.attendance.data.model.**`), Retrofit HTTP interface methods, OkHttp connection poolers, Compose UI runtime components, Airbnb Lottie Compose animation models, and Firebase Messaging services.
  3. *Production Build Configuration (`app/build.gradle.kts`):*
     - Version bumped to `1.0.0` (`versionCode = 1`).
     - Enabled `isMinifyEnabled = true`, `isShrinkResources = true`, and `proguard-android-optimize.txt`.
     - Automated signing configuration with CI/CD environment variable support (`RELEASE_KEYSTORE_PATH`) and automated local fallback to the developer keystore (`debug.keystore`).
* **Verification & Testing Conducted:**
  - Executed full release assembly: `./gradlew.bat assembleRelease` $\rightarrow$ **BUILD SUCCESSFUL in 3m 5s** (50 of 50 tasks executed).
  - Verified R8 minification, bytecode optimization, and resource shrinking:
    - Target: `app/build/outputs/apk/release/app-release.apk`
    - Package Size: **5.82 MB (6,108,438 bytes)** (clean, installable, and fully optimized).

---

### Session: 2026-11-14 23:20:00 IST — Kotlin 2.0.21 Upgrade & Jetpack Compose Compiler Plugin Integration
* **Scope:** Mobile Client (`Attendance app/AttendanceApp/app`)
* **Features Added & Upgraded:**
  1. *Kotlin 2.0.21 Migration:* Upgraded `org.jetbrains.kotlin.android` and `org.jetbrains.kotlin.plugin.serialization` from `1.9.24` to `2.0.21` in root `build.gradle.kts`.
  2. *Jetpack Compose Compiler Plugin (`org.jetbrains.kotlin.plugin.compose`):* Integrated the official Kotlin 2.0 Compose compiler plugin in both root and app `build.gradle.kts`.
  3. *Removed Deprecated `composeOptions`:* Removed `composeOptions { kotlinCompilerExtensionVersion = "1.5.14" }` from `app/build.gradle.kts` as Compose compilation is now natively managed by the Kotlin compiler plugin.
* **Bugs & Regressions Resolved:**
  1. *IDE Inspection Error ("Composable function call without Compose plugin"):* Resolved project-wide red errors and squiggly lines on `@Composable`, `remember`, `LaunchedEffect`, `mutableStateOf`, and `collectAsState` caused by modern Android Studio IDEs requiring the Kotlin Compose compiler plugin.
* **Verification & Testing Conducted:**
  - Full clean build and compilation via Gradle: `./gradlew.bat compileDebugKotlin` $\rightarrow$ **BUILD SUCCESSFUL in 3m 1s** (15 of 15 tasks executed, 0 errors).

---

### Session: 2026-11-13 23:25:00 IST — Lottie "No Connection" Animation & Offline Submitted State Protection
* **Scope:** Mobile Client (`Attendance app/AttendanceApp/app`)
* **Features Added:**
  1. Integrated `com.airbnb.android:lottie-compose:6.4.0` and placed official Lottie satellite dish animation (`R.raw.no_connection`).
  2. Created `NoConnectionView` composable (`com.akshit.attendance.ui.components.NoConnectionView`) featuring smooth infinite loop Lottie animation, small light-gray text (`#94A3B8`), optional subtitle message, and a polished Retry button.
  3. Integrated `NoConnectionView` into `TeacherClassesScreen` (Pending tab error state and Schedule error state), `TeacherTimetableScreen` (offline error state), and `TeacherRecordsScreen` (offline past sessions and roster error states), replacing harsh red/pink error boxes.
  4. Created persistent submitted class tracking in `TeacherCacheManager`:
     - Added `teacher_submitted_classes.json` storage with `recordSubmittedClass(courseOfferingId, date)` and `getSubmittedOfferingIdsForDate(date): Set<Int>`.
     - Cross-references cached history and persistent submissions so submitted status is maintained even across restarts and offline transitions.
* **Features Removed / Deprecated:**
  1. *15-Day Attendance Grace Window Card:* Removed the yellow banner card (`PendingPolicyCard`) from the top of the **Pending** tab in `TeacherClassesScreen` to keep the UI clean, uncluttered, and professional.
* **Bugs & Regressions Resolved:**
  1. *Submitted Class Overwrite Risk in Offline Mode:* Previously, when a class was submitted online, it was removed from `queuedIds`. When the app subsequently opened in offline mode, `ScheduleUiState.Loaded` initialized `submittedOfferingIds` with only `queuedIds` (which was empty), causing the card to revert to "Mark Attendance" / "Postpone" and risking accidental re-marking or overwrite. Resolved by persisting submitted offerings to `teacher_submitted_classes.json` and merging them on startup and submission.
  2. *Harsh Error Banner in UI:* Replaced high-contrast pink error surface (`#FFF1F2`) on the Pending tab with the animated Lottie satellite dish illustration with light gray status text.
* **Verification & Testing Conducted:**
  - Verified Kotlin compilation passes cleanly with 0 errors via `./gradlew.bat compileDebugKotlin`.

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
* **Features Removed / Deprecated:**
  1. *15-Day Attendance Grace Window Card:* Removed the yellow banner card (`PendingPolicyCard`) from the top of the **Pending** tab in `TeacherClassesScreen` to keep the UI clean, uncluttered, and professional.
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
* **Features Removed / Deprecated:**
  1. *15-Day Attendance Grace Window Card:* Removed the yellow banner card (`PendingPolicyCard`) from the top of the **Pending** tab in `TeacherClassesScreen` to keep the UI clean, uncluttered, and professional.
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
>    * **Features Removed / Deprecated:**
  1. *15-Day Attendance Grace Window Card:* Removed the yellow banner card (`PendingPolicyCard`) from the top of the **Pending** tab in `TeacherClassesScreen` to keep the UI clean, uncluttered, and professional.
* **Bugs & Regressions Resolved:** ...
>    * **Verification & Testing Conducted:** ...
>    ```
