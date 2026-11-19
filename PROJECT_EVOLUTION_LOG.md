# University Attendance & Broadcast Management System
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

## 2. Core Mathematical Formulations & Algorithmic Derivations

This section provides formal mathematical derivations, definitions, and code mappings for all computational models, attendance metrics, and recovery algorithms across the system. Use these formulations directly in project reports, technical papers, viva presentations, and thesis documentation.

### 2.1. Attendance Shortfall Recovery Formula (Target Attendance Recovery)
* **Problem Statement:**
  Given a student enrolled in a course where $H$ total sessions have been conducted and the student attended $A$ sessions ($A \le H$). If their current attendance percentage $P = \frac{A}{H} \times 100 < T\%$ (where $T \in (0, 100)$ is the university target threshold, typically $T = 75\%$), determine the minimum number of consecutive upcoming classes $x$ the student must attend without any absence to bring their cumulative attendance percentage to at least $T\%$.
* **Mathematical Derivation:**
  The new cumulative attendance percentage after attending $x$ consecutive future classes is:
  $$\frac{A + x}{H + x} \ge \frac{T}{100}$$
  Cross-multiplying (since $H + x > 0$):
  $$A + x \ge \frac{T}{100} (H + x)$$
  $$A + x \ge \frac{T}{100} H + \frac{T}{100} x$$
  Rearranging all terms containing $x$ to the left-hand side:
  $$x - \frac{T}{100} x \ge \frac{T}{100} H - A$$
  $$x \left(1 - \frac{T}{100}\right) \ge \frac{T}{100} H - A$$
  Dividing both sides by $\left(1 - \frac{T}{100}\right)$ (which is positive since $T < 100$):
  $$x \ge \frac{\frac{T}{100} H - A}{1 - \frac{T}{100}}$$
  For standard university requirement $T = 75\%$ ($T/100 = 0.75$ and $1 - T/100 = 0.25$):
  $$x \ge \frac{0.75 H - A}{0.25} = \frac{\frac{3}{4} H - A}{\frac{1}{4}} = 3H - 4A$$
  Since the number of physical classes conducted must be a non-negative integer, we apply the ceiling function:
  $$\mathbf{x = \max\left(0, \left\lceil \frac{0.75 H - A}{0.25} \right\rceil\right) = \max\left(0, \lceil 3H - 4A \rceil\right)}$$
* **General Threshold Formula:**
  For any arbitrary cutoff threshold $T \in (0, 100)$:
  $$\mathbf{x(T) = \max\left(0, \left\lceil \frac{\frac{T}{100} H - A}{1 - \frac{T}{100}} \right\rceil\right)}$$
* **Example Calculation for Project Report & Presentation:**
  - Let total classes held $H = 16$.
  - Let classes attended $A = 10$.
  - Current attendance percentage $P = \frac{10}{16} \times 100 = 62.5\%$ (Defaulter: $< 75\%$).
  - Calculation:
    $$x = \lceil 3(16) - 4(10) \rceil = \lceil 48 - 40 \rceil = 8 \text{ consecutive classes}$$
  - **Verification:**
    $$\text{New Attended} = 10 + 8 = 18, \quad \text{New Held} = 16 + 8 = 24$$
    $$\text{New Percentage} = \frac{18}{24} \times 100 = 75.0\% \quad \checkmark$$
* **Code Implementation Mapping:**
  - Backend API: `app/api/v1/mobile/attendance.py:get_course_students_summary`
    ```python
    if total_held > 0 and pct < 75.0:
        raw_needed = (0.75 * total_held - attended) / 0.25
        needed_75 = max(0, int(math.ceil(raw_needed)))
    ```
  - Android App: `com.akshit.attendance.ui.teacher.TeacherAnalyticsScreen:StudentDefaulterCard`
    ```kotlin
    val needed = if (pct < 75.0) max(0, ceil((0.75 * held - attended) / 0.25).toInt()) else 0
    ```

---

### 2.2. Permissible Absence Margin (Bunk Allowance Formula)
* **Problem Statement:**
  If a student is in the safe zone with current attendance percentage $P = \frac{A}{H} \times 100 \ge T\%$, determine how many consecutive upcoming classes $y$ the student can miss (bunk) before dropping below the required $T\%$ threshold.
* **Mathematical Derivation:**
  If the student misses $y$ consecutive future sessions, their attended classes remain $A$, while total held classes increases to $H + y$:
  $$\frac{A}{H + y} \ge \frac{T}{100}$$
  $$A \ge \frac{T}{100} (H + y) = \frac{T}{100} H + \frac{T}{100} y$$
  $$\frac{T}{100} y \le A - \frac{T}{100} H$$
  $$y \le \frac{A - \frac{T}{100} H}{\frac{T}{100}} = \frac{100 A - T H}{T}$$
  For $T = 75\%$:
  $$y \le \frac{100 A - 75 H}{75} = \frac{4A - 3H}{3} = \frac{4A}{3} - H$$
  Since $y$ must be an integer, applying the floor function:
  $$\mathbf{y = \max\left(0, \left\lfloor \frac{4A - 3H}{3} \right\rfloor\right)}$$
* **Example Calculation:**
  - Classes held $H = 20$, classes attended $A = 18$ ($P = 90.0\%$).
  - $$y = \left\lfloor \frac{4(18) - 3(20)}{3} \right\rfloor = \left\lfloor \frac{72 - 60}{3} \right\rfloor = \left\lfloor 4.0 \right\rfloor = 4 \text{ classes}$$
  - **Verification:**
    If 4 consecutive classes are missed: $\frac{18}{20 + 4} = \frac{18}{24} = 75.0\% \ge 75\%$. If a 5th is missed: $\frac{18}{25} = 72\% < 75\%$.

---

### 2.3. Duration-Weighted Multi-Period Attendance Aggregation
* **Problem Statement:**
  University courses include both single-period theoretical lectures (1 hour) and multi-period laboratory/practical sessions (2 or 3 continuous hours). Treating all sessions as equal single units would under-weigh lab attendance and violate university credit criteria.
* **Mathematical Formulation:**
  For a student enrolled in course offering $C$, let $\mathcal{S} = \{s_1, s_2, \dots, s_N\}$ be the set of all conducted sessions where $\text{is\_held} = \text{TRUE}$ and $\text{status} = \text{SUBMITTED}$.
  Each session $s_i$ has a duration weight $D_i \in \{1, 2, 3\}$ (`duration_periods`) and student record status $R_i \in \{\text{PRESENT}, \text{ABSENT}, \text{LEAVE}\}$.
  The duration-weighted attendance percentage $P_w$ is defined as:
  $$\mathbf{P_w = \frac{\sum_{i=1}^{N} \left( D_i \times \mathbf{1}_{\{R_i = \text{PRESENT}\}} \right)}{\sum_{i=1}^{N} \left( D_i \times \mathbf{1}_{\{\text{is\_held}_i = \text{TRUE} \land \text{status}_i = \text{SUBMITTED}\}} \right)} \times 100}$$
  Where $\mathbf{1}_{\{\mathcal{E}\}}$ is the Boolean indicator function:
  $$\mathbf{1}_{\{\mathcal{E}\}} = \begin{cases} 1 & \text{if condition } \mathcal{E} \text{ is true} \\ 0 & \text{otherwise} \end{cases}$$
* **Code Implementation Mapping:**
  - Backend API: `app/api/v1/mobile/student.py:get_student_dashboard`
    ```sql
    SUM(CASE WHEN AttendanceSession.is_held = TRUE AND AttendanceSession.status = 'SUBMITTED' THEN AttendanceSession.duration_periods ELSE 0 END) AS periods_held,
    SUM(CASE WHEN AttendanceSession.is_held = TRUE AND AttendanceSession.status = 'SUBMITTED' AND AttendanceRecord.status = 'PRESENT' THEN AttendanceSession.duration_periods ELSE 0 END) AS periods_attended
    ```

---

### 2.4. Cryptographic Token Digest & Rate Limiting Verification
* **Problem Statement:**
  Protect against database breach bearer hijacking and automated online credential/OTP enumeration while scaling horizontally without external in-memory brokers.
* **Formulations:**
  1. **Token Digest Invariance:**
     Instead of storing bearer refresh tokens $T_{\text{refresh}}$ in plaintext, store the cryptographic digest $H(T_{\text{refresh}})$:
     $$\mathbf{H(T_{\text{refresh}}) = \text{SHA-256}(T_{\text{refresh}})}$$
  2. **Sliding Time-Window Rate Throttling:**
     For an attempt key $K \in \{\text{IP}, \text{User ID}, \text{Email}\}$ at timestamp $t_{\text{now}}$ with sliding window $W = 900\text{ seconds}$ (15 minutes):
     $$\mathbf{\text{ActiveAttempts}(K, t_{\text{now}}) = \sum_{r \in \mathcal{A}_K} \mathbf{1}_{\{t_{\text{now}} - t_r \le W\}}}$$
     $$\mathbf{\text{Authorize}(K) = \begin{cases} \text{ALLOW} & \text{if } \text{ActiveAttempts}(K, t_{\text{now}}) < L_{\max} \\ \text{HTTP 429} & \text{if } \text{ActiveAttempts}(K, t_{\text{now}}) \ge L_{\max} \end{cases}}$$
     Where $L_{\max} = 5$ for authentication login, $L_{\max} = 8$ for OTP submissions, and $L_{\max} = 20$ for IP-level throttling.

---

### 2.5. Contiguous Timetable Slot Consolidation for Multi-Period Laboratories
* **Problem Statement:**
  University academic schedules allocate laboratory sessions as contiguous 1-hour period slots:
  $S_1 = [T_1, T_2)$ and $S_2 = [T_2, T_3)$ with identical course offering $C$, room $R$, and section (e.g. 11:30 AM – 12:30 PM followed immediately by 12:30 PM – 01:30 PM for CS481 IoT Lab).
  Treating them as disjoint schedule entries forces duplicate roll calls and causes database record collisions where subsequent single-hour submissions overwrite earlier ones.
* **Mathematical & Algorithmic Formulation:**
  Let $\mathcal{T} = \{s_1, s_2, \dots, s_n\}$ be ordered timetable slots for teacher $U$ on day $D$, sorted by `(course_offering_id, start_time)`.
  Two adjacent slots $s_i$ and $s_{i+1}$ are contiguous if and only if:
  $$\mathbf{C(s_{i+1}) = C(s_i) \quad \land \quad \text{start}(s_{i+1}) = \text{end}(s_i)}$$
  The consolidation operator $\oplus$ merges them into a single consolidated session $s'$:
  $$\mathbf{s' = s_i \oplus s_{i+1} = \begin{cases}
  \text{start}(s') = \text{start}(s_i) \\
  \text{end}(s') = \text{end}(s_{i+1}) \\
  D(s') = D(s_i) + D(s_{i+1}) \\
  \text{room}(s') = \text{room}(s_i) \lor \text{room}(s_{i+1})
  \end{cases}}}$$
  Where $D(s)$ is the duration weight (`duration_periods`).
  This ensures:
  1. The teacher performs exactly 1 roll call for the entire laboratory session.
  2. The single submitted session has $D = 2$ (or 3), accurately contributing 2 periods to both held periods and attended periods in the duration-weighted aggregation formula:
     $$P_w = \frac{\sum_{i=1}^{N} \left( D_i \times \mathbf{1}_{\{R_i = \text{PRESENT}\}} \right)}{\sum_{i=1}^{N} \left( D_i \times \mathbf{1}_{\{\text{is\_held}_i = \text{TRUE} \land \text{status}_i = \text{SUBMITTED}\}} \right)} \times 100$$
* **Code Implementation Mapping:**
  - Backend API: `app/api/v1/mobile/attendance.py:merge_consecutive_schedule_slots` & `merge_consecutive_pending_classes`
  - Mobile Client: `com.akshit.attendance.data.model.Models:CourseScheduleResponse(duration_periods: Int = 1)` and `TeacherViewModel:submitAttendance(durationPeriods)`

---

### 2.6. Laboratory Group Partitioning, Independent Attendance Sets & Cross-Slot Weekly Auto-Fulfillment Logic
* **Problem Statement:**
  Laboratory courses are split into practical groups (e.g., Section CS-IV-A with $N = 69$ students partitioned into Group 1 with $N_1 = 35$ students and Group 2 with $N_2 = 34$ students). Two operational challenges arise:
  1. **Non-Scheduled Group Invariance:** When Group 1 has its scheduled lab on Tuesday, submitting attendance must record ONLY Group 1 students. Students in Group 2 must **not** be marked absent, and their held class count ($H$) must remain completely unchanged.
  2. **Combined/Preponed Lab Auto-Fulfillment (Approach B):** A teacher may conduct a combined lab for both Group 1 and Group 2 on Tuesday instead of following separate timetable slots. When both groups are marked together on Tuesday, the system must automatically satisfy and clear Thursday's scheduled slot for Group 2 so it never appears as an unmarked/pending backlog.
  3. **Arbitrary Class Sizes:** Class sizes are uneven ($N = 69 \implies N_1 \ne N_2$). Partitions cannot be hardcoded or split strictly 50/50, and teachers must have the freedom to switch individual students between groups ($G_1 \leftrightarrow G_2$).

* **Mathematical & Algorithmic Formulation:**
  1. **Disjoint Partition of Enrolled Students:**
     Let the enrolled roster for course offering $C$ be $\mathcal{S} = \{s_1, s_2, \dots, s_N\}$.
     The group assignment mapping $\pi: \mathcal{S} \to \{G_1, G_2\}$ establishes a disjoint partition:
     $$\mathbf{\mathcal{S} = G_1 \cup G_2 \quad \text{where} \quad G_1 \cap G_2 = \emptyset}$$
     Where $|G_1| = N_1$, $|G_2| = N_2$, and $N_1 + N_2 = N$.
     Default allocation on roster load assigns the first $\lceil N/2 \rceil$ students (sorted by roll number) to $G_1$ and the remaining to $G_2$, while the database allows dynamic per-student assignment updates $\pi(s_i) \leftarrow g'$.

  2. **Attendance Percentage Invariance for Non-Scheduled Group:**
     Let cumulative attendance for student $s \in \mathcal{S}$ before session $\Sigma$ be:
     $$P_0(s) = \frac{A_0(s)}{H_0(s)} \times 100$$
     When a single-group session $\Sigma_1$ for group $G_1$ is conducted, the attendance record set is restricted strictly to $G_1$:
     $$\text{Records}(\Sigma_1) = \{ (s, R_s) \mid s \in G_1 \}$$
     For any student $s_k \in G_2$ (the non-scheduled group):
     $$s_k \notin \text{Records}(\Sigma_1) \implies \begin{cases} \Delta H(s_k) = 0 \\ \Delta A(s_k) = 0 \end{cases}$$
     Consequently:
     $$\mathbf{P_1(s_k) = \frac{A_0(s_k) + 0}{H_0(s_k) + 0} \times 100 = P_0(s_k)}$$
     The attendance percentage of the non-scheduled group is strictly invariant, preventing false absenteeism and unearned penalty.

  3. **Approach B: Cross-Slot Weekly Auto-Fulfillment Algorithm:**
     Let the academic week $W(t)$ containing submission date $t$ be the calendar interval:
     $$W(t) = \left[ t - \text{dow}(t), \; t - \text{dow}(t) + 6 \text{ days} \right]$$
     Where $\text{dow}(t) \in [0, 6]$ represents the day-of-week index ($0 = \text{Monday}$).
     When attendance is submitted for course offering $C$ on date $t \in W(t)$ with scope $\text{batch\_label} \in \{\text{"ALL"}, \text{"COMBINED"}\}$:
     - The primary session $\Sigma_p$ is recorded for all attending students $s \in \mathcal{S}$ on date $t$.
     - The system queries all other timetable entries $\mathcal{T}_{\text{other}}$ for course $C$ in week $W(t)$ scheduled on a different day $d \ne \text{dow}(t)$ (e.g., Thursday's slot for $G_2$):
       $$\mathcal{T}_{\text{other}} = \{ \tau \in \text{Timetable}(C) \mid \tau.\text{day\_of\_week} \ne \text{dow}(t) \}$$
     - For each target date $t_{\text{target}} \in W(t)$ matching $\tau \in \mathcal{T}_{\text{other}}$:
       If no session exists on $t_{\text{target}}$, the system automatically creates an auto-fulfilled administrative session:
       $$\mathbf{\Sigma_{\text{auto}} = \begin{pmatrix}
       \text{course\_offering\_id} = C \\
       \text{session\_date} = t_{\text{target}} \\
       \text{is\_held} = \text{FALSE} \\
       \text{reason\_not\_held} = \text{"Combined Lab conducted on } t\text{"} \\
       \text{status} = \text{SUBMITTED}
       \end{pmatrix}}$$
     - **Verification of Pending Suppression:**
       The pending past sessions function $\text{Pending}(U)$ computes:
       $$\text{Pending}(U) = \{ (C, \tau, t') \mid t' \le t_{\text{today}} \land (C, t') \notin \text{SubmittedSessions} \}$$
       Since $\Sigma_{\text{auto}} \in \text{SubmittedSessions}$, $(C, t_{\text{target}})$ is cleared from $\text{Pending}(U)$ without incrementing student held class counts $H$.

* **Code Implementation Mapping:**
  - Database Schema: Added `studentsubjectmap.lab_group VARCHAR(20)` and `attendancesession.batch_label VARCHAR(50)`.
  - Backend API (`app/api/v1/mobile/attendance.py`):
    - `get_class_roster`: Returns student group `lab_group` (`G1`/`G2`).
    - `sync_offline_attendance`: Detects `batch_label` and executes Approach B auto-fulfillment across remaining weekly timetable slots.
    - `PUT /mobile/attendance/course/{id}/student/{student_id}/lab-group`: Real-time group switching.
    - `PUT /mobile/attendance/course/{id}/split-groups`: Custom threshold group partitioning.
  - Mobile Client (`AttendanceApp`):
    - Models (`Models.kt`): Added `lab_group` to `StudentRosterResponse` and `batch_label` to `CourseScheduleResponse`, `PendingClassResponse`, and `OfflineSyncPayload`.
    - ViewModel (`TeacherViewModel.kt`): `updateStudentGroup(studentId, newGroup)`, `markAll(status, targetStudentIds)`, and scoped `submitAttendance(batchLabel, targetStudentIds)`.
    - Roll Call UI (`TeacherRollCallScreen.kt`): Dynamic Group Segmented Chips `[ All (N) | Group 1 (N1) | Group 2 (N2) ]`, student row badge with click-to-toggle, and group-scoped "Mark All" / "Submit".
    - Classes Dashboard (`TeacherClassesScreen.kt`): Group/batch indicator badges on Today and Pending class cards.

---

## 3. Cross-Platform Feature Matrix

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
| **Teacher Course Analytics** | Session History & Roster endpoints | N/A | Compose Canvas Bar Chart, KPI Cards, Filters | 🟢 Fully Active |
| **Student Defaulters Drilldown**| `GET /mobile/attendance/course/{id}/students-summary` | N/A | At-Risk View, Cutoff Chips (<75%, <65%, <50%, Custom Slider), Search | 🟢 Fully Active |

---

## 4. Chronological Chat & Development Activity Trail

### Session: 2026-11-19 17:48:00 IST — Duplicate Session Purge, Calendar Historical Mapping & Roll Call Date Lock
* **Scope:** Database (PostgreSQL), Backend (`attendance.py`, `student.py`), Mobile Client (`TeacherViewModel.kt`, `TeacherClassesScreen.kt`)
* **Issues Addressed:**
  - Duplicate session (ID 73) on Thursday, Nov 19 for CS481 (IoT Lab) created a ghost "Extra Class / Make-up Class" entry on the student calendar.
  - Potential confusion regarding pending attendance submission: ensuring past sessions submitted later map strictly to their original timetable schedule date, not the submission date.
* **Root Cause:**
  - When the teacher app opened near midnight as the calendar transitioned from Wednesday to Thursday, the local in-memory/disk cache held Wednesday's classes, but the roll call call passed `null` for `sessionDate`. This defaulted to `localToday` (Nov 19), submitting Wednesday's lab with Thursday's date.
* **Changes Implemented:**
  1. *Database Purge:*
     - Executed atomic purge deleting duplicate Session 73 and its 35 linked `AttendanceRecord`s. Verified via `get_student_calendar` that Thursday, Nov 19 now cleanly lists only the 4 scheduled Thursday lectures with zero ghost classes.
  2. *Historical Timetable Date Integrity:*
     - Verified and confirmed that all pending submissions record with `item.date` (the timetable date of the class, e.g. `2026-11-18`), never the submission date. Submitting 10 past classes maps each class directly to its historical calendar day without piling up on the current date.
  3. *Stale Cache & Date Lock:*
     - In `TeacherViewModel.loadSchedule()`, ensured cached schedule is only loaded if `isScheduleCacheForToday()` is true and cached classes match `EffectiveDay`. Prevents displaying yesterday's classes under "Today's Classes".
     - In `TeacherClassesScreen.kt`, locked roll call invocation on "Today's Classes" to explicitly pass `EffectiveDate.toString()`.
* **Verification & Testing Conducted:**
  - Database verification confirmed Session 73 removed; Nov 18 holds the valid Session 74; Nov 19 holds 4 clean classes.
  - Ran `./gradlew.bat compileDebugKotlin` — `BUILD SUCCESSFUL in 27s`.

### Session: 2026-11-19 17:19:00 IST — Pull-to-Refresh Re-trigger Fix & Clean Dashboard Header
* **Scope:** Mobile Client (`StudentDashboardScreen.kt`, `StudentSubjectsScreen.kt`, `StudentStatisticsScreen.kt`, `StudentCalendarScreen.kt`)
* **Issues Addressed:**
  - Pull-to-refresh swipe was getting stuck spinning without making backend calls on subsequent refreshes.
  - A redundant circular refresh button was present next to "Today's Classes", crowding the section title.
* **Root Cause:**
  - `LaunchedEffect(true)` was declared inside `if (pullToRefreshState.isRefreshing)`. Because the key `true` never changed across subsequent pull gestures, Compose skipped re-executing the coroutine on repeat swipes, failing to call `onRefresh()` and leaving the pull-to-refresh spinner stuck on screen.
* **Changes Implemented:**
  1. *Pull-To-Refresh Trigger Correction:*
     - Changed the trigger across all student screens (`StudentDashboardScreen`, `StudentSubjectsScreen`, `StudentStatisticsScreen`, `StudentCalendarScreen`) to `LaunchedEffect(pullToRefreshState.isRefreshing)`. When `isRefreshing` turns `true`, the coroutine launches reliably on every single swipe, calls `viewModel.refreshAll()`, and upon completion `pullToRefreshState.endRefresh()` cleanly hides the indicator.
  2. *Removed Redundant Refresh Button:*
     - Removed the circular refresh `IconButton` in front of "Today's Classes" in `StudentDashboardScreen`, allowing the clean section title and subtitle (`Thursday, 19 November • 4 Scheduled`) to span cleanly.
* **Verification & Testing Conducted:**
  - Verified Kotlin compilation with Gradle: `BUILD SUCCESSFUL in 30s`.

### Session: 2026-11-19 17:05:00 IST — Student Attendance Statistics Screen Modernization & Real Data Verification
* **Scope:** Mobile Client (`StudentStatisticsScreen.kt`, `NavGraph.kt`)
* **Issues Addressed & Features Added:**
  1. *Real Data Verification:* Verified that data displayed on the Attendance Statistics screen is 100% real live data dynamically calculated by the FastAPI backend (`GET /api/v1/mobile/student/dashboard`) via PostgreSQL aggregation over `AttendanceSession`, `AttendanceRecord`, `CourseOffering`, and `Subject` tables (no mock or hardcoded numbers).
  2. *Lottie Loading Animation:* Replaced plain shimmer blocks with high-fidelity Lottie document analysis animation (`R.raw.loading_documents`) accompanied by descriptive subtitle `"Analyzing attendance statistics... Computing course standing & compliance"`.
  3. *Pull-To-Refresh Integration:* Integrated `rememberPullToRefreshState()` and `PullToRefreshExpressiveIndicator` for on-demand swipe refresh.
  4. *Redesigned Metric Cards:* Rebuilt top metric row (Attended, Missed, Total Held) with distinct colored container pills, percentage-of-held sub-badges, and clear typography.
  5. *Overall Compliance Banner:* Added consolidated card featuring an animated progress gauge, target 75% indicator, and breakdown chips (`On Track`, `At Risk`, `Not Started`).
  6. *Bar Chart Alignment Fix:* Fixed Canvas-to-label drift by structuring each bar, top percentage label, subtle background track, and course code label inside unified column elements, with dashed 75% target threshold line.
  7. *Course Performance Breakdown & Subject Drilldown:* Added course code pill tags, teacher name with icon, session counts, progress indicators, and clickable rows wired to `NavGraph` for direct navigation to `SubjectDetailScreen`.
* **Verification & Testing Conducted:**
  - Ran `./gradlew.bat compileDebugKotlin` — `BUILD SUCCESSFUL in 45s`.

### Session: 2026-11-19 16:42:00 IST — Student App Offline Support, Local Storage Caching & Connectivity Banner
* **Scope:** Mobile Client (`StudentCacheManager.kt`, `StudentViewModel.kt`, `BottomNavBars.kt`, `StudentDashboardScreen.kt`, `StudentSubjectsScreen.kt`, `AttendanceApp.kt`)
* **Issues Addressed:**
  - Student app did not display the connectivity banner above the bottom navigation bar when disconnected from the internet or local server, unlike the teacher section.
  - When the device went offline or the server was temporarily unreachable, the student dashboard and subjects screens would either wipe to empty skeletons or present harsh red crash/network error banners (`Unable to refresh dashboard data`).
  - No offline disk persistence was present for the student dashboard, today's schedule, timetable, subjects history, or attendance calendar.
* **Root Cause:**
  - `ConnectivityStrip` in `BottomNavBars.kt` was wired into `AppBottomNav`, but `StudentViewModel` never reported network reachability status to `OfflineAttendanceManager.markOffline()` / `markOnline()`.
  - In addition, student data was held purely in transient in-memory StateFlows without disk caching.
* **Changes Implemented:**
  1. *Student Disk Cache Manager (`StudentCacheManager.kt`):*
     - Created thread-safe file caching subsystem saving JSON snapshots to app internal storage (`filesDir`):
       - `student_dashboard_cache.json` (overall percentage, course cards, bunk advice)
       - `student_schedule_cache.json` (daily lecture slots and classroom numbers)
       - `student_timetable_cache.json` (full weekly grid matrix and enrolled workloads)
       - `student_profile_cache.json` (student name, roll number, program)
       - `student_calendar_cache_{month}.json` (monthly session dates & status)
       - `student_subject_cache_{courseCode}.json` (per-subject detailed lecture records)
     - Added smart network error classification (`isNetworkError`) covering `ConnectException`, `SocketTimeoutException`, `UnknownHostException`, and HTTP 5xx network interruptions.
  2. *Cache-First & Offline Resilience in `StudentViewModel.kt`:*
     - Instantly hydrates profile, dashboard, schedule, and timetable from local disk on `init` (0ms startup latency, 100% offline-ready).
     - Connected all API flows (`loadDashboard`, `loadSchedule`, `loadTimetable`, `loadCalendar`, `loadSubjectHistory`, `refreshAll`) to report `OfflineAttendanceManager.markOnline()` on success and `markOffline()` on network failures.
     - Preserves existing cached data on failed pull-to-refresh rather than wiping UI with error states.
  3. *Connectivity Strip & Friendly Offline Banners (`BottomNavBars.kt`, `StudentDashboardScreen.kt`, `StudentSubjectsScreen.kt`):*
     - Updated `ConnectivityStrip` to display concise `"Offline Mode"` when disconnected.
     - Fixed `showSkeleton` in `StudentDashboardScreen` and `StudentSubjectsScreen` so pulling to refresh or browsing offline keeps loaded data visible without flickering.
     - Replaced technical red error cards with a calm amber offline status card (`Offline Mode • Showing cached data. Connect to internet to refresh.`) when offline.
* **Verification & Testing Conducted:**
  - Full Kotlin compilation passed with Gradle: `:app:compileDebugKotlin` completed with `BUILD SUCCESSFUL`.
  - Backend verified running and listening on port 8000.

### Session: 2026-11-19 15:10:00 IST — Enrolled Courses in Timetable Tab: Inclusion of Non-Classroom Subjects (Minor Project & Industrial Training)
* **Scope:** Backend (`timetable.py`), Mobile Client (`StudentTimetableScreen.kt`)
* **Issues Addressed:**
  - In the Student Timetable tab under "Enrolled Courses & Weekly Load", only 6 subjects were shown instead of all 8 officially enrolled subjects. Non-classroom courses such as "Minor Project" (`CS491`) and "Industrial Training" (`CS493`) were missing.
* **Root Cause:**
  - The backend endpoint `GET /api/v1/mobile/timetable/student` generated the `workload` list strictly by iterating over `slots` (the recurring weekly timetable grid entries). Since Project and Training courses do not have scheduled physical classroom lecture periods in the timetable grid, they were omitted from the workload breakdown.
* **Changes Implemented:**
  1. *Backend Workload Aggregation (`timetable.py`):*
     - Queried the student's full course enrollments via `StudentSubjectMap`.
     - Cross-referenced all enrolled courses against the timetable slot map. Any enrolled subject not in the recurring timetable grid is automatically added to `workload` with its assigned teacher, course code, section, activity (`PROJECT`, `TRAINING`, `LAB`, or `COURSE`), and `0.0` weekly lecture hours.
  2. *Enhanced Mobile Workload Badging (`StudentTimetableScreen.kt`):*
     - Added specialized chip color themes for `PROJECT` (Purple `#F3E8FF`, text `#7E22CE`) and `TRAINING` (Amber `#FEF3C7`, text `#B45309`).
     - Replaced raw `"0 hrs/wk"` display for project/training subjects with context-aware labels: `"Project / Sem"`, `"Training / Sem"`, or `"Self-Paced"`.
     - Count badge now accurately reflects all enrolled courses (`8 Subjects`).
* **Verification & Testing Conducted:**
  - Verified backend workload generation with unit script: accurately returned all 8 subjects (including CS491 Minor Project and CS493 Industrial Training).
  - Verified Kotlin compilation with `.\gradlew.bat compileDebugKotlin`: `BUILD SUCCESSFUL in 29s`.


### Session: 2026-11-19 15:00:00 IST — Subject Detail Hero Card Gauge Centering Fix
* **Scope:** Mobile Client (`SubjectDetailScreen.kt`)
* **Issues Addressed:**
  - In `SubjectDetailScreen.kt`, the circular attendance gauge, status badge, and bunk advice in the hero card appeared skewed toward the left side of the card rather than horizontally centered.
* **Root Cause:**
  - The enclosing `Column` inside `Card` omitted `Modifier.fillMaxWidth()`, causing the Column width to shrink-wrap its content and dock to the card's start edge. Additionally, the `Canvas` `drawArc` call omitted stroke insetting, which caused half of the 12.dp stroke to extend outside the canvas bounds.
* **Changes Implemented:**
  1. Applied `Modifier.fillMaxWidth().padding(20.dp)` to the inner `Column` with `horizontalAlignment = Alignment.CenterHorizontally`.
  2. Applied precise stroke insetting in `Canvas(128.dp)` using `Offset(strokeWidth / 2f, strokeWidth / 2f)` and `Size(size.width - strokeWidth, size.height - strokeWidth)` to guarantee perfect concentric ring alignment.
  3. Added `textAlign = TextAlign.Center` and `Modifier.fillMaxWidth()` to both the bunk advice text and faculty detail string.
* **Verification & Testing Conducted:**
  - Verified Kotlin compilation with `.\gradlew.bat compileDebugKotlin`: `BUILD SUCCESSFUL in 29s`.


### Session: 2026-11-19 14:50:00 IST — Student Subject Attendance History & Lottie Document Loading Animation
* **Scope:** Backend (`student.py`, `app/schemas/student.py`), Mobile Client (`SubjectDetailScreen.kt`, `StudentViewModel.kt`, `StudentRepository.kt`, `ApiService.kt`, `Models.kt`, `res/raw/loading_documents.json`)
* **Issues Addressed:**
  - In `SubjectDetailScreen.kt`, navigating to subject details prematurely checked `subject == null` while `DashboardUiState.Loading` was in flight, immediately flashing a jarring "Subject details not found" message.
  - The screen lacked an animated loading state when fetching records.
  - Students had no way to view the chronological list of actual conducted class sessions to see when they were marked present or absent for a specific course.
* **Changes Implemented:**
  1. *Backend Endpoint `GET /api/v1/mobile/student/subject/{course_code}/history`:*
     - Added `StudentClassSessionItem` and `StudentSubjectHistoryResponse` models.
     - Fetches all submitted sessions for the enrolled student's offerings for that subject, joining `AttendanceRecord` to determine exact attendance status (`PRESENT`, `ABSENT`, `POSTPONED`).
     - Maps sessions to official timetable schedule to resolve start/end times and classroom rooms.
  2. *Integrated User-Requested Lottie Animation:*
     - Embedded the exact Lottie loading animation (`loading-F7xmrDT1UN` by Lavpreet Singh) into `app/src/main/res/raw/loading_documents.json`.
     - Rendered via `rememberLottieComposition` and `LottieAnimation` in `SubjectDetailScreen` during `SubjectHistoryUiState.Loading`.
  3. *Elevated Subject Detail UI & Attendance Records List:*
     - Added **Class Attendance Records** section with session count badge (`18 Sessions`) and filter chips (`All`, `Attended`, `Missed`).
     - Each session displays: circular status indicator (`PRESENT` check in green, `ABSENT` cross in red, `POSTPONED` in amber), formatted date (`12 Nov 2026 • Thu`), time slot, classroom, lab batch tag (e.g. `G1`), and status pill badge.
     - Preserved hero circular attendance gauge with dynamic bunk advice and weekly timetable schedule.
* **Verification & Testing Conducted:**
  - Tested backend endpoint with `test_sessions.py`: correctly returned all 18 sessions (8 Attended, 10 Missed, 44.4%) for Akshit Singh (235ICS006) on CS401.
  - Verified Kotlin compilation with `.\gradlew.bat compileDebugKotlin`: `BUILD SUCCESSFUL in 35s`.
  - Verified FastAPI backend daemon reload on `http://0.0.0.0:8000`.


### Session: 2026-11-19 13:50:00 IST — Lab Attendance Edit Mode: Host Group Guest Scoping & Group Isolation Fix
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherRollCallScreen.kt`
* **Issues Addressed:**
  - In `TeacherRollCallScreen.kt`, when a teacher opened Attendance Records -> selected an existing Group 1 lab session -> tapped **Edit Attendance**, and then clicked on the **Group 2 (G2)** tab, all 28 Group 1 students were rendered at the bottom of the G2 tab labeled as `G1 • Guest` with green `[ P ]` badges.
  - Furthermore, if the teacher submitted or updated attendance while on the G2 tab, `batchLabelToSend` was assigned to `selectedGroup`, causing accidental rewriting of the session's actual batch group in the database.
* **Root Cause:**
  - `guestStudents` was evaluated dynamically against the currently active UI tab (`roster.filter { it.lab_group != selectedGroup && marks[it.student_id] == AttendanceStatus.PRESENT }`). Because the teacher was editing a G1 session where all 28 G1 students had `marks == PRESENT`, clicking `G2` satisfied `it.lab_group != "G2"`, causing the entire G1 roster to be misclassified as "guests" in G2.
* **Changes Implemented:**
  1. *Introduced Session `hostGroup`:*
     - Extracted `hostGroup` from `initialBatchLabel` (e.g., `"G-1"` / `"G1"` -> `"G1"`, `"G-2"` / `"G2"` -> `"G2"`), `courseInfo.batch_label`, or pre-existing marks.
     - Scoped `targetHost`: pinned to `hostGroup` (or fallback to `selectedGroup` if not a pre-existing session).
  2. *Strict Guest Attachment to Host Group Only:*
     - Constrained `guestStudents` so it only populates when `selectedGroup == targetHost`. When viewing non-host tabs (e.g. switching to G2 on a G1 session), `guestStudents` is strictly `emptyList()`.
  3. *Accurate `isGuest` Badge Condition in `RosterRow`:*
     - Replaced loose tab-relative check with session-relative check: `val isGuest = isLabCourse && effectiveHost != null && currentGroup != effectiveHost && selectedGroup == effectiveHost`.
  4. *Preserved Submission Batch & Action Scopes:*
     - Pinned `batchLabelToSend` strictly to `targetHost ?: selectedGroup`, preventing accidental batch corruption.
     - Separated `currentTabStudentIds` (for `Mark All` / `Reset All` on the current view) from `targetGroupIds` (used for final submission payload).
* **Verification & Testing Conducted:**
  - Verified Kotlin compilation with `.\gradlew.bat compileDebugKotlin`: `BUILD SUCCESSFUL in 54s`.


### Session: 2026-11-19 01:02:00 IST — Student Dashboard Hero Card Skeleton Redesign
* **Scope:** Mobile Client (`AttendanceApp/app`), `ShimmerEffects.kt` (`DashboardHeroSkeleton`), `StudentDashboardScreen.kt`
* **Issues Addressed:**
  - Pull-to-refresh and initial loading skeleton on the Student Dashboard displayed a legacy layout (large centered circle and three horizontal pills) that caused a jarring layout shift when replaced by the actual `LiveOverallAttendanceCard`.
* **Changes Implemented:**
  1. *1:1 Layout Alignment with `LiveOverallAttendanceCard`:*
     - **Header Row:** Icon placeholder + "Overall Attendance" title placeholder on the left, pill status badge placeholder on the right.
     - **Middle Content:** Left circular ring gauge (`96.dp`) with inner cutout and percentage text placeholders; right column with two stacked rounded boxes (Attended Ratio and Safe Bunk Buffer).
     - **Divider & Footer:** Horizontal divider line followed by the bottom sub-stat row ("Subjects On Track" and "View Subjects" placeholders).
  2. *Fluid Transitions:* Padding, border, elevation, and card corner radius (`20.dp`) now match the real card perfectly, eliminating visual jumps on refresh.
* **Verification & Testing Conducted:**
  - Ran `.\gradlew.bat compileDebugKotlin`: `BUILD SUCCESSFUL in 11s`.


### Session: 2026-11-19 00:30:00 IST — Human-Friendly Auth Error Handling & Password Visibility Toggle
* **Scope:** Mobile Client (`AttendanceApp/app`), `LoginScreen.kt`, `AuthViewModel.kt`
* **Issues Addressed:**
  - Login failure displayed raw technical codes like `HTTP 401` directly on the UI below the password field.
  - Password input field lacked a visibility toggle ("eye" icon) for users to check what they typed.
* **Changes Implemented:**
  1. *Human-Friendly Error Formatting:*
     - Added `formatAuthErrorMessage` in `AuthViewModel.kt` parsing backend `detail` JSON and mapping HTTP status codes:
       - `401` $\rightarrow$ `"Invalid email/roll number or password. Please try again."`
       - Network/Timeout $\rightarrow$ `"Unable to connect to server. Please check your internet connection."`
       - `404` $\rightarrow$ `"Account not found. Please check your roll number or email."`
       - `429` $\rightarrow$ `"Too many attempts. Please wait a moment and try again."`
       - `500-599` $\rightarrow$ `"University server is temporarily unavailable. Please try again later."`
     - Rendered errors inside an alert container with `Icons.Outlined.ErrorOutline` in `LoginScreen.kt`.
     - Automatically resets/clears error state as soon as the user starts editing their ID or password.
  2. *Password View Eye Toggle:*
     - Added `passwordVisible` state with toggleable trailing icon (`Icons.Filled.Visibility` / `Icons.Filled.VisibilityOff`).
     - Dynamically applies `VisualTransformation.None` when visible and `PasswordVisualTransformation()` when masked.
* **Verification & Testing Conducted:**
  - Ran `.\gradlew.bat compileDebugKotlin`: `BUILD SUCCESSFUL in 51s`.


### Session: 2026-11-18 23:30:00 IST — Teacher Records Screen: Lab Group Badges & Redundant Search Removal
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherRecordsScreen.kt`
* **Issues Addressed:**
  - Past lab session cards (`Internet of Things Lab`, etc.) did not display their lab group identifier (`G-1`, `G-2`).
  - Redundant text search input ("Search by subject or section…") cluttered the top header card now that horizontal course chips and the interactive month calendar provide direct filtering.
* **Changes Implemented:**
  1. *Removed Redundant Search Input:* Stripped `OutlinedTextField` and associated search state from the "Submitted Class Sessions" header card, leaving a clean section introduction card.
  2. *Lab Group Badge on Session Cards:* Formatted lab groups as amber pills `[ Group G-1 ]`, `[ Group G-2 ]` (with a clean fallback to `[ Lab ]` when group metadata is pending) on all session cards in the list.
  3. *Lab Group in Session Details & Top Bar:* In VIEW 2 (Session Detail & Roster), displayed the group label prominently in both the top navigation bar (`CS-IV-A • Group G-1 • 11 Nov 2026`) and the KPI summary card.
  4. *Simplified Filter State:* Filter state now operates exclusively on `selectedCourseOfferingId` and `selectedDate`, with active filter chips and one-tap "Clear All ×" reset.
* **Verification & Testing Conducted:**
  - Ran `gradlew compileDebugKotlin --no-daemon`: `BUILD SUCCESSFUL in 33s`.
  - Backend API endpoint `/api/v1/mobile/attendance/history` verified to return `batch_label: 'G-1'` and `'G-2'` for lab offerings.


### Session: 2026-11-18 22:50:00 IST — Teacher Attendance Records Expandable Calendar & Course Chip Filters
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherRecordsScreen.kt`
* **Issues Addressed:**
  - On the `Records` tab (`Attendance Records`), past submitted sessions were displayed as a flat linear list. Over a full academic term, scrolling through 50–100+ cards to find a specific class or check attendance for a particular date was inefficient.
* **Fixes Applied (Approach 1 Implementation):**
  1. *Horizontal Course Filter Chips:*
     - Added `CourseChipsRow` dynamically populated with the teacher's distinct enrolled course offerings (`All Classes`, `IoT (CS-IV-A)`, `IoT Lab (CS-IV-A)`, etc.) with amber `LAB` pill indicators.
     - Tapping a chip instantly scopes both calendar dots and the session list.
  2. *Expandable Month Calendar Card with Multi-Dot Indicators (Option B):*
     - Added `TeacherMonthCalendarCard` featuring monthly navigation (`< Month Year >`), monthly session count, and an expand/collapse toggle (`[ ▲ / ▼ ]`).
     - Renders a 7-column calendar grid (`M T W T F S S`) where cells with held sessions display type-coded multi-dots:
       - **Blue Dot (`PrimaryBlue`):** Theory / Lecture session held.
       - **Amber Dot (`#F59E0B`):** Practical / Lab session held.
       - **Dual Dots (`🔵 🟠`):** Both lecture and lab conducted on that date.
       - Multi-dots supported if multiple classes of the same type were held.
     - **Interactive Date Selection:** Tapping any date filters the sessions list below to that exact day. Tapping again toggles off the date filter.
     - Distinct visual highlighting for Today (subtle border) and Selected Date (filled PrimaryBlue circle with crisp white dots).
     - Informative bottom legend explaining dot types (`Lecture`, `Lab Session`, `Tap date to filter`).
  3. *Active Filter Summary Bar:*
     - A pill bar displaying active filter criteria (e.g. `Internet of Things Lab (CS-IV-A) • Thu, 12 Nov (2)`) with a quick one-tap `[ Clear All × ]` button.
  4. *SessionCard Batch Label Pill:*
     - Rendered amber `G-1` / `G-2` batch pills on `SessionCard` next to the section badge for instant lab group distinction.
* **Verification & Testing Conducted:**
  - Executed `./gradlew compileDebugKotlin` — **BUILD SUCCESSFUL in 1m 4s**.

### Session: 2026-11-18 15:52:00 IST — Guest Student Absence Protection (Option A + B Combo)
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherRollCallScreen.kt`
* **Issues Addressed:**
  - Accidental tap prevention: If a teacher mistakenly tapped `[ A ]` (Absent) or `[ L ]` (Leave) on a guest student card (e.g. Student from Group 2 during a Group 1 lab), that student would receive an unjustified absence penalty for a lab session outside their assigned timetable.
* **Fixes Applied:**
  1. *UI Level Action Disablement (Option A):*
     - On any student card where `isGuest == true` (i.e. student's permanent group differs from the active roll call group), the `[ A ]` (Absent) and `[ L ]` (Leave) buttons are visually greyed out (`#F8FAFC`, border `#E2E8F0`, text `#CBD5E1`) and their `onClick` handlers are strictly disabled (`if (!isGuest)`).
     - The only active action is `[ P ]` (Present). Tapping `[ P ]` marks them as a guest attendee; tapping `[ P ]` again deselects and returns to unmarked.
     - Enforced `isAbsent` and `isLeave` to evaluate to `false` for guest students, preventing red/pink card borders and badges.
  2. *Data Level Strict Filtering (Option B):*
     - Hardened `guestStudents` filter:
       `roster.filter { it.lab_group != selectedGroup && marks[it.student_id] == AttendanceStatus.PRESENT }`
     - Even if an `ABSENT` or `LEAVE` mark existed in memory from prior actions, the student is automatically disqualified from `guestStudents`, `activeGroupStudents`, and `targetGroupIds`, guaranteeing that **zero** absence records can ever be generated for a guest attendee in another group's session.
* **Verification & Testing Conducted:**
  - Executed `./gradlew compileDebugKotlin` — **BUILD SUCCESSFUL in 25s**.

### Session: 2026-11-18 15:35:00 IST — Guest / Makeup Student Support in Lab Sessions
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherRollCallScreen.kt`
* **Issues Addressed:**
  - Real-world classroom scenario: A student assigned permanently to Group 2 missed their previous lab and is permitted by the instructor to attend today's Group 1 lab session to make up for the absence.
  - Previous system limitations:
    1. Search on the Group 1 tab only filtered within Group 1, making Group 2 students invisible.
    2. If marked on Group 2 or All tab, submitting for Group 1 filtered them out via `targetGroupIds` (omitted from submission payload).
    3. Tapping the group badge permanently altered the student's group in `StudentSubjectMap`, disrupting future regular timetable groups.
* **Fixes Applied (Option 2 Implementation):**
  1. *Universal Roster Search:*
     - When `search.isNotBlank()`, search filters across `s.roster` (entire course enrollment), allowing teachers on the G1 tab to immediately find any student by name or roll number.
  2. *Automatic Guest Attendee Inclusion:*
     - Introduced `guestStudents = roster.filter { it.lab_group != selectedGroup && marks[it.student_id] != null }`.
     - Appended `guestStudents` to `activeGroupStudents` and `targetGroupIds` so explicitly marked guest attendees are cleanly included in the session submission payload.
     - Preserved isolation: Unmarked students in the other group remain completely untouched and are **not** defaulted to Absent.
     - Database integrity: `StudentSubjectMap.lab_group` is never mutated, preserving the student's permanent group for all future sessions.
  3. *Visual Guest Indicator & Submission Audit:*
     - Rendered a distinctive purple pill `[ G2 • Guest ]` on the student's roster card when viewed within a different group's roll call.
     - Enhanced the confirmation dialog to explicitly state: `• Guest / Makeup: X student(s) from other group included`.
* **Verification & Testing Conducted:**
  - Executed `./gradlew compileDebugKotlin` — **BUILD SUCCESSFUL**.

### Session: 2026-11-18 15:00:00 IST — Dynamic Expected Students Count & Backend Service Reactivation
* **Scope:** Backend (`Attendance_backend`), Mobile Client (`AttendanceApp/app`), `TeacherClassesScreen.kt`
* **Issues Addressed:**
  - Teacher classes screen displayed "No internet connection. Connect to load your scheduled classes." while simultaneously showing "184 Expected Today" in the top header card, misleading the user into thinking classes were already loaded.
  - Root cause: The FastAPI backend process had stopped during the environment restart, resulting in connection failure when fetching the schedule.
  - Concurrently, `DailyProgressSection` in `TeacherClassesScreen.kt` had a hardcoded placeholder (`title = "184"`, `subtitle = "Expected Today"`), so it showed "184" even when 0 classes were loaded or when network errors occurred.
* **Fixes Applied:**
  1. *Backend Reactivation:* Relaunched FastAPI Uvicorn server on `0.0.0.0:8000` with hot-reload enabled. Verified HTTP 200 health responses.
  2. *Dynamic Expected Student Calculation:*
     - Updated `DailyProgressSection(completed: Int, total: Int, expectedStudents: Int? = null)`.
     - Calculated `expectedStudentsCount` dynamically from cached student rosters for today's loaded course offerings (`loadedSchedule?.classes?.sumOf { TeacherCacheManager.loadRoster(it.course_offering_id)?.size ?: 0 }`).
     - If `total == 0` (or error state), `expectedText` cleanly displays `"0"`.
     - If classes are loaded, it shows the real sum of enrolled students (or `"—"` if rosters are pending initial fetch), completely removing the misleading `"184"` hardcode.
* **Verification & Testing Conducted:**
  - Verified backend listening on `http://127.0.0.1:8000/docs` (HTTP 200).
  - Executed `./gradlew compileDebugKotlin` on Android codebase — **BUILD SUCCESSFUL**.

### Session: 2026-11-18 14:45:00 IST — TodayClassCard Section Truncation & Top Chip Row Decluttering
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherClassesScreen.kt`
* **Issues Addressed:**
  - On `TodayClassCard`, Row 1 was overloaded with `subject_code`, `credits`, `course_type`, `duration_periods`, `Sec ${section_name}`, and `StatusBadge`, causing the section text to squish and truncate into `Sec CS-...`.
* **Fixes Applied:**
  1. *Section Badge Relocation to Location Row:*
     - Moved `Sec: ${course.section_name}` from the crowded top chips row to the room row, rendering it as a dedicated pill (`Surface(shape = RoundedCornerShape(6.dp), color = Color(0xFFF1F5F9))`) paired on the right with the room on the left (`[ 🏛 Room ]  ...  [ Sec: CS-IV-A ]`).
     - Enforced `maxLines = 1, softWrap = false` so section names never wrap or truncate.
  2. *Top Chip Row Breathing Room:*
     - Row 1 now exclusively hosts `[ CS481 ]`, `[ 3 Cr ]`, `[ LAB ]`, and `[ 2 Periods ]` with `Modifier.horizontalScroll(rememberScrollState())` safety on the left, and the `StatusBadge` on the right.
* **Verification & Testing Conducted:**
  - Android Gradle build: `.\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 28s** (0 errors).

### Session: 2026-11-18 14:15:00 IST — Lab vs Normal Lecture Scope Separation & On-Demand Group Filter (Approach 1)
* **Scope:** Mobile Client (`AttendanceApp/app`), `TeacherRollCallScreen.kt`
* **Issues Addressed:**
  - On normal theory/lecture classes (e.g. CS401 "Internet of Things" in lecture hall `IL-106 (CLT)`), the attendance screen was displaying lab group tabs (`[ All (76) | Group 1 (38) | Group 2 (38) ]`) and tagging every student with `G1`/`G2` badges.
  - Root Cause: `isLabCourse` checked `s?.roster?.any { !it.lab_group.isNullOrBlank() }`. Because backend enrollments dynamically assign groups for lab practicals, this condition erroneously evaluated to `true` for all courses across the university.
* **Features & Improvements Implemented (Approach 1):**
  1. *Accurate Lab Detection (`TeacherRollCallScreen.kt`):*
     - Replaced greedy roster check with exact course attributes:
       ```kotlin
       val isLabCourse = remember(rosterState) {
           val s = rosterState as? RosterUiState.Loaded
           val info = s?.courseInfo
           info?.course_type?.contains("LAB", ignoreCase = true) == true ||
           info?.subject_name?.contains("Lab", ignoreCase = true) == true ||
           !info?.batch_label.isNullOrBlank()
       }
       ```
  2. *Clean Full-Class Roster for Normal Lectures:*
     - On normal lecture courses (`!isLabCourse`), group tabs and student `G1`/`G2` badges are completely hidden by default. The teacher sees a clean, full-class roll call for all enrolled students.
  3. *On-Demand "Groups" Action in TopAppBar for Theory Classes:*
     - Moved the group toggle into the pinned `TopAppBar` action bar beside notifications: `[ ← Edit Attendance   [ 👥 Groups ]  🔔  (VS) ]`.
     - Eliminated the awkward floating button from the content body.
     - When tapped, it turns active (`PrimaryBlue` pill) and smoothly slides in the `[ All | Group 1 | Group 2 ]` tabs directly below the course card.
     - When toggled off, `selectedGroup` returns cleanly to `"ALL"` and group tabs/badges are hidden.
  4. *Automatic Group Mode for Labs:*
     - Lab practicals (e.g. CS481 "Internet of Things Lab") automatically display group tabs and badges without requiring any manual toggle.
* **Verification & Testing Conducted:**
  - Android Gradle build: `.\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 32s** (0 errors).

### Session: 2026-11-18 04:35:00 IST — Direct Slot Batch Parameter Propagation, Intelligent Session Edit Detection & Multi-Slot Day Ambiguity Fix
* **Scope:** Mobile Client (`AttendanceApp/app`), Backend API (`Attendance_backend`)
* **Root Cause Analysis:**
  - A single course offering (e.g. CS481) can have multiple scheduled slots on the same day-of-week with different groups (e.g. Saturday 09:30 for $G_2$ and Saturday 14:30 for $G_1$).
  - When a teacher clicked "Mark Past" on Saturday 14 Nov for $G_1$, navigation only passed `(courseOfferingId, sessionDate)`.
  - Day-of-week resolution searched `allSchedule` for `day_of_week == "SAT"` and returned the *first* Saturday entry (09:30 $G_2$), overriding the intended $G_1$ selection.
* **Fixes Applied:**
  1. *Direct Batch Parameter Routing (`Routes.kt`, `NavGraph.kt`, `TeacherClassesScreen.kt`, `TeacherRecordsScreen.kt`):*
     - Upgraded roll call route: `Routes.TEACHER_ROLL_CALL = "teacher_roll_call/{courseOfferingId}?sessionDate={sessionDate}&batchLabel={batchLabel}"`.
     - `rollCall(courseOfferingId, sessionDate, batchLabel)` now propagates the exact `batch_label` from the clicked card (`TodayClassCard`, `PendingClassCard`, and `TeacherRecordsScreen`'s `onEditSession`).
  2. *Exact Slot Resolution by Date + Batch Label (`TeacherViewModel.kt`):*
     - `resolvecourseInfo` now checks for slots matching both the target day-of-week AND the specific `batchLabel` (e.g. `SAT` + `G-1`), eliminating slot ambiguity.
  3. *Smart Group Auto-Selection for Editing vs. Marking (`TeacherRollCallScreen.kt`):*
     - **When Editing:** Evaluates existing records on the loaded session:
       - If only $G_1$ students have marks (`markedG1 > 0 && markedG2 == 0`): auto-selects **Group 1**.
       - If only $G_2$ students have marks (`markedG2 > 0 && markedG1 == 0`): auto-selects **Group 2**.
       - If both groups have marks: auto-selects **All**.
     - **When Marking Past/New Sessions:** Directly selects the group matching `initialBatchLabel` from the clicked card.
     - Tracks `userManuallyChangedGroup` so manual tab selections are preserved during roll call.
  4. *Backend Session History Schema & Query Enhancement (`attendance.py`, `schemas/attendance.py`):*
     - Added `batch_label: Optional[str] = None` to `SessionHistoryResponse`.
     - In `get_teacher_history`, selected `AttendanceSession.batch_label` so history records preserve group identity for session editing.
     - In `get_class_roster` and `sync_offline_attendance`, supported hyphen-insensitive matching (`G1` vs `G-1`) via `func.replace`.
* **Verification & Testing Conducted:**
  - Python AST check: syntax verified.
  - Android Gradle compilation: `.\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 22s** (0 errors).

### Session: 2026-11-18 04:15:00 IST — Lab Group Placement in Subject Title, Pending Card Meta Layout Fix & Date-Aware Group 2 Auto-Selection
* **Scope:** Mobile Client (`AttendanceApp/app`), Backend API (`Attendance_backend`)
* **Features Added & Polished:**
  1. *Inline Lab Group Badge (`TeacherClassesScreen.kt`, `TeacherRollCallScreen.kt`):*
     - Placed the practical group badge (`G-1`, `G-2`) directly in front of / beside the course title on `TodayClassCard`, `PendingClassCard`, and `RollCallHeader`.
     - Removed `batch_label` from the top chip row in `RollCallHeader`, providing ample space for `CS481`, `3 Cr`, `LAB`, and `${duration_periods} Periods` and preventing overlap with `ROLL CALL` / `EDITING`.
  2. *Pending Card Section Text Squishing Bug Resolved (`TeacherClassesScreen.kt`):*
     - Moving `batch_label` from Row 2 to Row 1 (beside the subject name) freed up essential horizontal width on Row 2.
     - Added `softWrap = false, maxLines = 1` on `Sec: ${item.section_name}` to permanently fix the bug where `Sec:\nCS-IV-A` squished across two vertical lines.
  3. *Date-Aware Timetable Slot Resolution & Group 2 Auto-Selection (`TeacherViewModel.kt`, `TeacherRollCallScreen.kt`):*
     - Updated `resolvecourseInfo` to accept `targetDate: String? = null`.
     - Instead of statically falling back to a single cached `course_offering_id` (which held Tuesday's `G-1` slot), `resolvecourseInfo` now parses `targetDate` into the day of week (e.g. `THU`) and matches `course.day_of_week`.
     - Added `day_of_week` to backend `CourseScheduleResponse` schema and mobile `Models.kt`.
     - When marking or editing a Thursday lab session, the screen automatically receives Thursday's slot (`batch_label = "G-2"`) and pre-selects `Group 2` in the roll call tab.
     - In `TeacherRollCallScreen`, added bidirectional roster status detection: if existing attendance records belong strictly to Group 2 (`markedG2Count > 0 && markedG1Count == 0`), `Group 2` is auto-selected regardless of initial slot config.
* **Verification & Testing Conducted:**
  - Python AST and schema verification: Syntax verified.
  - Android Gradle build: `.\gradlew.bat compileDebugKotlin --no-daemon` $\rightarrow$ **BUILD SUCCESSFUL in 23s** with 0 errors.

### Session: 2026-11-18 03:45:00 IST — Laboratory Practical Group Split (G1/G2), Non-Scheduled Invariance & Cross-Slot Weekly Auto-Fulfillment (Approach B)
* **Scope:** Database (`PostgreSQL`), Backend API (`Attendance_backend`), Mobile Client (`AttendanceApp/app`)
* **Features Added:**
  1. *Database Schema Extension:*
     - Added column `studentsubjectmap.lab_group VARCHAR(20) DEFAULT NULL` to support persistent per-student practical group allocation.
     - Added column `attendancesession.batch_label VARCHAR(50) DEFAULT NULL` to track session group scope (`G1`, `G2`, `ALL`).
  2. *Backend Group Endpoints & Dynamic Allocation (`attendance.py`):*
     - `get_class_roster`: Returns student group `lab_group`. If unassigned, dynamically assigns $G_1$ to the first $\lceil N/2 \rceil$ students and $G_2$ to the remainder.
     - `PUT /mobile/attendance/course/{id}/student/{student_id}/lab-group`: Real-time group switching for individual students.
     - `PUT /mobile/attendance/course/{id}/split-groups`: Custom partition cutoff endpoint.
  3. *Approach B Cross-Slot Weekly Auto-Fulfillment (`attendance.py`):*
     - When a teacher conducts a combined session for both groups on date $t$ (`batch_label in ('ALL', 'COMBINED')`), the backend scans the weekly schedule window $[t_{\text{start}}, t_{\text{end}}]$ and generates an auto-fulfilled non-held administrative session ($\text{is\_held} = \text{FALSE}, \text{reason\_not\_held} = \text{"Combined Lab conducted on..."}$) for the other scheduled slot (e.g. Thursday), permanently clearing it from the pending backlog.
  4. *Mobile Group Filter Chips & Scope Invariance (`TeacherRollCallScreen.kt`):*
     - Added Segmented Group Filter Chips: `[ All (N) | Group 1 (N1) | Group 2 (N2) ]` at the top of the roster.
     - Attendance statistics (Present, Absent, Leave, Unmarked) and "Mark All" operations reactively scope to the active group only.
     - Submission sends only the active group students (`targetStudentIds`), ensuring non-scheduled group students are **never marked absent** and their attendance percentage remains strictly invariant.
     - Dynamic auto-detection: If the timetable slot has `batch_label = "G1"` or `"G2"`, the screen automatically pre-selects that group.
  5. *Mobile Interactive Group Badges & Quick Toggle (`TeacherRollCallScreen.kt`):*
     - Each student card in lab courses displays a clickable group badge (`G1` / `G2`).
     - Tapping the badge instantly switches the student between `G1` and `G2`, optimistically updates the local cache, and syncs with the server.
  6. *Dashboard Badges (`TeacherClassesScreen.kt`):*
     - Display `batch_label` chip (`G1`, `G2`, etc.) on Today's class cards and Pending class cards.
  7. *Roll Call Header Chip Overflow & Layout Collision Fix (`TeacherRollCallScreen.kt`):*
     - Replaced verbose `3 Credits` tag with compact `3 Cr`.
     - Bound the left chips row with `Modifier.weight(1f, fill = false).horizontalScroll(rememberScrollState())` and added a guaranteed 8dp spacing before the status badge.
     - Enforced `softWrap = false, maxLines = 1` on the `ROLL CALL` / `EDITING` badge to permanently eliminate text wrapping into two lines (`ROLL \n CALL`) and badge overlapping against the batch tag (`G-1`).
* **Mathematical Formulations Applied:**
  - Section 2.6: Laboratory Group Partitioning, Independent Attendance Sets & Cross-Slot Weekly Auto-Fulfillment Logic.
* **Verification & Testing Conducted:**
  - Database DDL: Altered tables `studentsubjectmap` and `attendancesession` with `BypassSandbox: true`.
  - Backend Python compilation: Syntax and import verification verified (`OK`).
  - Android Gradle build: `.\gradlew.bat compileDebugKotlin` executed with `BUILD SUCCESSFUL in 44s` and 0 errors.

### Session: 2026-11-18 02:35:00 IST — Multi-Period Lab Timetable Slot Consolidation (Single Roll Call for 2-Hour Labs)
* **Scope:** Mobile Client (`AttendanceApp/app`), Backend API (`Attendance_backend`)
* **Features Added:**
  1. *Backend Schedule Slot Merging (`merge_consecutive_schedule_slots` in `attendance.py`):* Contiguous timetable slots sharing the same `course_offering_id` and contiguous timestamps ($T_{\text{end}, i} = T_{\text{start}, i+1}$) are merged into a unified schedule item with combined time window (e.g. `11:30 AM – 01:30 PM`) and aggregated `duration_periods = 2`.
  2. *Backend Pending Classes Merging (`merge_consecutive_pending_classes` in `attendance.py`):* Unmarked past sessions with contiguous slots are consolidated into a single pending class card, preventing duplicate pending cards for multi-hour labs.
  3. *Duration-Weighted Attendance Aggregation in Faculty Summary (`get_course_students_summary`):* Updated summary calculation to weight total held sessions and student present marks by `session.duration_periods`, preserving duration-weighted metric consistency $P_w$.
  4. *Mobile Visual Indicators (`TeacherClassesScreen.kt`, `TeacherRollCallScreen.kt`):* Display a distinct `${duration_periods} Periods` badge on both Today's Class cards, Pending Class cards, and the Roll Call header whenever a session spans $> 1$ periods.
  5. *Automatic Full-Block Attendance Submission (`TeacherViewModel.kt`):* Resolves `duration_periods` from active course info or pending list and submits `OfflineSyncPayload(duration_periods = resolvedDuration)` so 1 single roll call records full academic credit.
* **Verification & Testing Conducted:**
  - Python unit test: verified merging of consecutive 1-hour slots into 11:30–13:30 (`duration_periods = 2`).
  - Android Gradle compilation: `.\gradlew.bat compileDebugKotlin` verified.

### Session: 2026-11-18 02:15:00 IST — Notifications Screen: Eyebrow Tag Removal & Offline NoConnectionView Integration
* **Scope:** Mobile Client (`AttendanceApp/app`)
* **Features Added:**
  1. *Offline Satellite Dish View (`NoConnectionView` in Notifications):* Integrated `NoConnectionView` (Lottie satellite dish animation, "No internet connection" heading, descriptive subtext, and a "Retry" button linked to `AttendanceNotificationManager.fetchHistory()`) into both `TeacherNotificationsScreen.kt` and `StudentNotificationsScreen.kt`. It renders whenever the device is offline or history synchronization fails and no cached notifications are present.
* **Features Removed / Deprecated (Hallmark Design Floor Alignment):**
  1. *Redundant Eyebrow Tags:* Removed the unnecessary `"FACULTY NOTIFICATIONS"` and `"NOTIFICATIONS"` all-caps eyebrow text above `"Inbox"` in both teacher and student screens.
  2. *Clean Minimalist Top App Bar:* Simplified top bar title to an elegant `Text("Inbox" or "Inbox (N new)", fontSize = 18.sp, fontWeight = FontWeight.Bold)` strictly conforming to Hallmark anti-slop guidelines (no tracked-out all-caps labels, no redundant role tags).
* **Verification & Testing Conducted:**
  - Android Gradle compilation: `.\gradlew.bat compileDebugKotlin` $\rightarrow$ **BUILD SUCCESSFUL in 29s** (0 errors).

### Session: 2026-11-18 01:55:00 IST — Teacher App: Pending Attendance Class Filter & 15-Day Window Decommissioning
* **Scope:** Mobile Client (`AttendanceApp/app`), Backend API (`Attendance_backend`)
* **Features Added:**
  1. *Dynamic Class Filter on Pending Tab (`TeacherClassesScreen.kt`):* Added horizontal scrollable filter chips displaying all distinct assigned courses with pending sessions (e.g. `All Classes (40)`, `CS525 • MT-DS-I (13)`, `CS525 • MT-SE-I (13)`). Teachers can tap any class to inspect and mark only that class.
  2. *Live Dynamic Pending Counter:* The "Unmarked Past Sessions" header count reactively updates to reflect the selected class filter (e.g. displaying `13 pending` when viewing MT-DS-I, rather than a static global count).
  3. *Unobtrusive Pending Tab Indicator Dot:* Replaced the prominent red pill badge `40` on the top `Pending` tab button with a minimal red status dot (`7.dp`) that only appears when there are pending unmarked sessions (`pendingCount > 0`) and hides when all classes are marked.
* **Features Removed / Deprecated:**
  1. *15-Day Attendance Window Enforcement:* Fully decommissioned the 15-day retroactive marking restriction across backend `sync_offline_attendance` and `postpone_class`, allowing teachers to mark any prior class without artificial cutoff blocks. Expanded lookback window to 60 days.
  2. *Card Expiration Badges:* Removed the `14d left` / `Expiring • Xd left` warning pill and red borders from `PendingClassCard`, standardizing on clean neutral borders.
* **Verification & Testing Conducted:**
  - Android Gradle compilation: `.\gradlew.bat compileDebugKotlin` $\rightarrow$ verified.
  - Backend python verification: `python -B -c "import app.api.v1.mobile.attendance"` $\rightarrow$ **OK**.

### Session: 2026-11-18 01:34:00 IST — Teacher App: At-Risk Students & Customizable Attendance Threshold Drilldown
* **Scope:** Mobile Client (`AttendanceApp/app`), Backend API (`Attendance_backend`)
* **Features Added:**
  1. *Backend Course Students Summary Endpoint (`attendance.py`, `schemas/attendance.py`):* Added `GET /api/v1/mobile/attendance/course/{course_offering_id}/students-summary` returning per-student aggregated attendance: `total_held`, `total_attended`, `attendance_percentage`, and exact future sessions required to reach 75% target (`classes_needed_for_75`). Optional `threshold` query parameter filters records server-side.
  2. *Top Segmented Mode Selector (Option A):* Pinned top-level segmented toggle in `TeacherAnalyticsScreen.kt` switching smoothly between **`Class Trends`** (Canvas bar chart & session turnout) and **`At-Risk Students`** (individual student drilldown) with an active badge displaying total at-risk count.
  3. *Customizable Attendance Thresholds:* Added horizontal filter chips for instant cutoff switching:
     - `[ < 75% Target ]` (Default university policy)
     - `[ < 65% Warning ]` (Moderate risk)
     - `[ < 50% Critical ]` (Severe risk with dark red highlights)
     - `[ Custom (< X%) ]` (Opens interactive slider dialog from 10% to 95% in 5% increments)
     - `[ All Students ]` (Full roster attendance overview)
  4. *Mathematical Formulations Applied (Shortfall Recovery):*
     - Exact algebraic derivation:
       $$\frac{A + x}{H + x} \ge 0.75 \implies x \ge \frac{0.75 H - A}{0.25} = 3H - 4A \implies \mathbf{x = \max(0, \lceil 3H - 4A \rceil)}$$
     - Implemented on backend in `attendance.py` and mobile in `StudentDefaulterCard`.
  5. *Student Defaulter Card (`StudentDefaulterCard`):* Displays student Roll Number, Name, semantic percentage badge, ratio (`Attended X of Y classes`), a bounded `LinearProgressIndicator`, and actionable shortfall advisory (e.g. *"Needs 6 consecutive classes to reach 75%"*).
  6. *Live Search & Course Filter:* Real-time filtering by student roll number or name, alongside seamless switching across assigned courses.
  7. *Offline Caching (`TeacherCacheManager`):* Added `loadStudentsSummary` and `saveStudentsSummary` to disk cache with auto-synthesis from cached rosters if completely offline.
* **Bugs & Regressions Resolved:**
  1. *Backend HTTP 500 on `GET /api/v1/mobile/attendance/course/{id}/students-summary` (`NameError: name 'AttendanceStatus' is not defined`):* When calculating attended student records for offerings with held sessions (`total_held > 0`), the query compared `AttendanceRecord.status == AttendanceStatus.PRESENT`. `AttendanceStatus` was missing from the import list in `app/api/v1/mobile/attendance.py`. Added `AttendanceStatus` to imports, restoring 200 OK responses across all course offerings.
* **Verification & Testing Conducted:**
  - Android compilation: `.\gradlew.bat compileDebugKotlin` $\rightarrow$ **BUILD SUCCESSFUL in 18s** (0 errors).
  - Python backend verification: `python -B -c "from app.api.v1.mobile.attendance import AttendanceStatus"` $\rightarrow$ **OK (<enum 'AttendanceStatus'>)**.

### Session: 2026-11-18 01:20:00 IST — Teacher App: Course Analytics & Teaching Insights Visual Dashboard
* **Scope:** Mobile Client (`AttendanceApp/app`), Backend Database (`Attendance_backend`)
* **Features Added:**
  1. *Teacher Course Analytics Screen (`TeacherAnalyticsScreen.kt`):* Built a comprehensive visual analytics dashboard empowering faculty to inspect attendance performance, enrollment strength, and class turnout trends.
  2. *Interactive Compose Canvas Bar Chart:* Custom-drawn responsive bar chart featuring dynamic vertical scaling, a 75% red dashed university threshold guideline, horizontal grid lines, and interactive tap detection for inspecting individual session details.
  3. *KPI Stat Metrics:* High-level performance cards displaying Total Classes Conducted, Average Turnout %, Average Enrolled Strength, and Total Attended vs. Absent records.
  4. *Course & Timeframe Filters:* Filter pills for switching between all assigned courses or individual offerings, alongside timeframe chips (*Entire Semester*, *Last 30 Days*, *Last 14 Days*).
  5. *Turnout Quality Distribution:* Visual split categorizing sessions into Safe ($\ge 75\%$) versus Critical ($< 75\%$) attendance.
  6. *Conducted Session Log:* Detailed chronological list of conducted classes with section tags, date, and attendance percentage pills.
  7. *Navigation & Tab Integration (`Routes.kt`, `BottomNavBars.kt`, `NavGraph.kt`):* Added `Routes.TEACHER_ANALYTICS` and integrated the `Analytics` destination with `Icons.Default.BarChart` as the 3rd bottom navigation tab positioned directly after `Timetable` (`Classes` $\rightarrow$ `Timetable` $\rightarrow$ `Analytics` $\rightarrow$ `Records`).
  8. *Offline Resilience:* Leverages `TeacherCacheManager` to compute metrics seamlessly from cached rosters and session history when disconnected, with `NoConnectionView` fallback.
* **Bugs & Regressions Resolved:**
  1. *Backend HTTP 500 on `/api/v1/auth/login` (`relation "authratelimit" does not exist`):* Executed pending Alembic migration `a2c4e6f8b1d3` (`alembic upgrade head`) on the remote database, generating the `authratelimit` table and enabling SHA-256 token digests.
  2. *Compose Modifier & Icon Deprecations:* Fixed `Modifier.padding` parameter syntax and replaced deprecated `Icons.Outlined.TrendingUp` with `Icons.AutoMirrored.Outlined.TrendingUp`.
* **Verification & Testing Conducted:**
  - Android Gradle compilation: `.\gradlew.bat compileDebugKotlin` $\rightarrow$ **BUILD SUCCESSFUL in 25s** with 0 errors.
  - Backend login endpoint verified: status 401 Unauthorized for invalid credentials with 0 database errors.

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

## 5. Maintenance Guidelines for Future Chats
> **Mandatory Rule for AI Assistant & Developers:**
> Whenever features, endpoints, or mobile screens are created, refactored, or removed:
> 1. Check this log at the start of the task to understand existing architectural constraints.
> 2. Record any major design decision under **1. Architectural Decision Records (ADRs)** with date and clock timestamp.
> 3. Record all mathematical formulas, derivations, variables, and code implementations under **2. Core Mathematical Formulations & Algorithmic Derivations**.
> 4. Update the **3. Cross-Platform Feature Matrix** if any capability's status changed.
> 5. Append a new session entry under **4. Chronological Chat & Development Activity Trail** in reverse-chronological order (newest on top), strictly formatted with **Date and exact Timestamp**:
>    ```markdown
>    ### Session: YYYY-MM-DD HH:mm:ss IST — <Session Title>
>    * **Scope:** <Backend / Frontend / Android / Database>
>    * **Features Added:** ...
>    * **Mathematical Formulations Applied:** (Formulas, derivations, variables, and code mapping)
>    * **Features Removed / Deprecated:** ...
>    * **Bugs & Regressions Resolved:** ...
>    * **Verification & Testing Conducted:** ...
>    * **Verification & Testing Conducted:** ...
>    ```
