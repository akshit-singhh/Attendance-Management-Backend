# University Attendance System - AI Assistant Rules & Workflow Standards

This project maintains a central living documentation log at `D:\Attendance_management\Attendance_backend\PROJECT_EVOLUTION_LOG.md`.

## Mandatory Project Workflow Rules for Every Chat / Session:

1. **Context Awareness on Start:**
   - Always inspect `PROJECT_EVOLUTION_LOG.md` before initiating major modifications or answering architectural questions to understand active decisions and historical constraints.

2. **Automatic Activity Tracking on Completion (with Date & Timestamp):**
   - Whenever you complete a feature implementation, architectural refactoring, bug fix, or removal/deprecation of code across the Backend, Web Frontend, or Android App:
     - **Update `PROJECT_EVOLUTION_LOG.md` before concluding the turn.**
     - **Mandatory Date & Time Stamp Format:** Every new session entry under Section 4 MUST be prefixed with both the local calendar date and exact clock timestamp (e.g. `### Session: YYYY-MM-DD HH:mm:ss IST`).
     - If a major technology or protocol change occurred, add an **Architectural Decision Record (ADR)** explaining the context, problem, alternatives, technical decision, and exact timestamp.
     - If any mathematical formula or core business logic was created/used (e.g. attendance percentage, shortfall recovery, bunk limits, duration weighting, rate-limiting windows), document the exact formulation and derivation in **Section 2: Core Mathematical Formulations & Algorithmic Derivations**.
     - Update the **Cross-Platform Feature Matrix** if any capability's status changed.
     - Document the session entry under **Chronological Chat & Development Activity Trail** with:
       - Exact Date and Timestamp (e.g. `2026-11-04 03:07:00 IST`)
       - What was added / implemented
       - Mathematical formulas & derivations used
       - What was removed / deprecated
       - Why it was removed / changed (root cause / technical justification)
       - Test verification & sanity checks performed (commands executed, build/test outputs).

3. **Mandatory Mathematical Formulation & Business Logic Derivation Tracking:**
   - Whenever any algorithm, business logic, attendance calculation, statistical metric, or recovery logic uses a mathematical formula, derivation, or equation:
     - **Document the exact mathematical formulation, variables, step-by-step derivation, and LaTeX formula in `PROJECT_EVOLUTION_LOG.md`** under **Section 2: Core Mathematical Formulations & Algorithmic Derivations** and in the corresponding session activity entry.
     - Include:
       1. Formal problem statement & variable definitions (e.g., $A$ = attended classes, $H$ = held classes, $T$ = target threshold percentage).
       2. Step-by-step algebraic derivation leading to the final implemented formula.
       3. Code mapping: Which backend endpoint/SQL statement and mobile component implements the logic.
       4. Example numerical calculation with sample numbers formatted for project reports, viva presentations, defense, and documentation.

4. **Documentation Integrity:**
   - Never overwrite or erase previous history entries. Keep all past sessions preserved.
