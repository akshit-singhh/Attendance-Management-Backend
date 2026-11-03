# University Attendance System - AI Assistant Rules & Workflow Standards

This project maintains a central living documentation log at `D:\Attendance_management\Attendance_backend\PROJECT_EVOLUTION_LOG.md`.

## Mandatory Project Workflow Rules for Every Chat / Session:

1. **Context Awareness on Start:**
   - Always inspect `PROJECT_EVOLUTION_LOG.md` before initiating major modifications or answering architectural questions to understand active decisions and historical constraints.

2. **Automatic Activity Tracking on Completion (with Date & Timestamp):**
   - Whenever you complete a feature implementation, architectural refactoring, bug fix, or removal/deprecation of code across the Backend, Web Frontend, or Android App:
     - **Update `PROJECT_EVOLUTION_LOG.md` before concluding the turn.**
     - **Mandatory Date & Time Stamp Format:** Every new session entry under Section 3 MUST be prefixed with both the local calendar date and exact clock timestamp (e.g. `### Session: YYYY-MM-DD HH:mm:ss IST`).
     - If a major technology or protocol change occurred, add an **Architectural Decision Record (ADR)** explaining the context, problem, alternatives, technical decision, and exact timestamp.
     - Update the **Cross-Platform Feature Matrix** if any capability's status changed.
     - Document the session entry under **Chronological Chat & Development Activity Trail** with:
       - Exact Date and Timestamp (e.g. `2026-11-04 03:07:00 IST`)
       - What was added / implemented
       - What was removed / deprecated
       - Why it was removed / changed (root cause / technical justification)
       - Test verification & sanity checks performed (commands executed, build/test outputs).

3. **Documentation Integrity:**
   - Never overwrite or erase previous history entries. Keep all past sessions preserved.
