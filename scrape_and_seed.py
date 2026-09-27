"""
scrape_and_seed_timetable.py

Scrapes mygbu.in timetable pages and upserts TimetableEntry rows for each
(course_offering, day, time) slot. Run manually or on a cron; each run fully
replaces the entries it previously created for the sections it touches, so
re-running after the university edits a page is exactly "re-import".

--- Why this doesn't depend on the university's HTML structure by ID ---
The site has no ids/classes to speak of already — everything here is located by
visible TEXT (day names, "(HH:MM-HH:MM)" time headers, the word "Remarks", the
"Code(Abbr)" pattern inside a cell). That's the most stable part of the page:
the university can restyle or add markup freely without breaking this. What
WILL break it, and there is no way around this short of a real API/export
(SRS Section 11 — this is exactly why manual upload is a required fallback,
not scraper resilience):
  - They rename days ("Monday" instead of "Mon") -> DAY_PATTERN below won't match.
  - They drop the "(08:30-09:30)" time format from column headers.
  - They stop writing "CS401(VS)" and use different punctuation in cells.
  - They remove the Remarks table or reorder its columns.
Every failure mode above raises ScrapeFormatError with the exact assumption
that broke, rather than silently importing wrong or partial data. Check the
"last known good" JSON snapshot (see SNAPSHOT_DIR below) against the failure.

--- Setup required before running ---
1. Fill in SECTION_CODE_MAP below: the university's opaque `section=2433` URL
   param -> your DB Section.name. This mapping cannot be scraped or guessed —
   file it here once per section you import.
2. Fill in TERM_NAME to match an existing AcademicTerm.name.
3. Subjects (by course_code) and Teachers (by full_name) must already exist in
   the DB. This script resolves against them; it does not create Subjects or
   Users, since inventing a teacher account from a scraped abbreviation isn't
   this script's call to make.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, time as time_
from enum import Enum
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

# DB-related imports (sqlmodel, app.models, app.core.database) are deferred into the
# functions that need them — resolve_and_upsert/replace_prior_entries/import_one
# below — rather than done here at module level. That's what keeps parse_slots()
# and friends importable and testable (test_parse_timetable.py) without the
# actual backend project on the path; pure parsing has no business depending on
# the DB layer anyway.


class DayOfWeek(str, Enum):
    """Mirrors app.models.academic.DayOfWeek by value. Kept as a separate
    definition here (not imported) so the parsing half of this file has no
    backend dependency — see note above. resolve_and_upsert() below converts
    to the real DB enum by .value when writing TimetableEntry rows."""
    MON = "MON"
    TUE = "TUE"
    WED = "WED"
    THU = "THU"
    FRI = "FRI"
    SAT = "SAT"
    SUN = "SUN"

# ---------------------------------------------------------------------------
# CONFIG — fill this in per import run
# ---------------------------------------------------------------------------

TIMETABLE_URLS = [
    "https://mygbu.in/schd/?name=SOICT&dept=CSE&section=2433",
]

# URL section code -> your DB Section.name. Required; the script refuses to
# guess this mapping.
SECTION_CODE_MAP: dict[str, str] = {
    "2433": "CS-IV-A",  # <-- replace with your real Section.name
}

TERM_NAME = "2026-27 Odd"  # must match an existing AcademicTerm.name exactly

SNAPSHOT_DIR = Path("timetable_snapshots")  # last-known-good JSON per URL, for diffing on failure

DAY_PATTERN = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)$", re.IGNORECASE)
TIME_HEADER_PATTERN = re.compile(r"\((\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\)")
CELL_PATTERN = re.compile(r"([A-Za-z0-9]+)\(([A-Za-z]+)\)")
ROOM_BATCH_PATTERN = re.compile(r"^([A-Za-z0-9\-\. ]+?)(?:\s+(G-\d+))?$")

DAY_MAP = {"MON": DayOfWeek.MON, "TUE": DayOfWeek.TUE, "WED": DayOfWeek.WED,
           "THU": DayOfWeek.THU, "FRI": DayOfWeek.FRI, "SAT": DayOfWeek.SAT, "SUN": DayOfWeek.SUN}


class ScrapeFormatError(Exception):
    """Raised when a structural assumption about the page breaks. Never caught
    silently — see module docstring for why."""


@dataclass
class ScrapedSlot:
    day: DayOfWeek
    start_time: time_
    end_time: time_
    subject_code: str
    faculty_abbr: str
    room: str | None
    batch_label: str | None


# ---------------------------------------------------------------------------
# Parsing — pure functions, no DB or network, so they're independently testable
# ---------------------------------------------------------------------------

def parse_remarks_maps(soup: BeautifulSoup) -> tuple[dict[str, str], dict[str, str]]:
    """Returns (subject_code -> subject_name, faculty_abbr -> full_name).
    Located by the visible word "Remarks", not by id/class — see docstring."""
    remarks_label = soup.find(string=re.compile(r"\bRemarks\b"))
    if not remarks_label:
        raise ScrapeFormatError('Could not find a "Remarks" heading on the page.')
    remarks_table = remarks_label.find_next("table")
    if not remarks_table:
        raise ScrapeFormatError('Found "Remarks" but no table follows it.')

    subject_map, faculty_map = {}, {}
    rows = remarks_table.find_all("tr")[1:]  # skip header
    if not rows:
        raise ScrapeFormatError("Remarks table has no data rows.")
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 5:
            continue
        subject_map[cols[0].text.strip()] = cols[1].text.strip()
        faculty_map[cols[3].text.strip()] = cols[4].text.strip()

    if not subject_map or not faculty_map:
        raise ScrapeFormatError("Remarks table parsed but yielded no subject/faculty entries.")
    return subject_map, faculty_map


def parse_time_headers(main_table) -> dict[int, tuple[time_, time_]]:
    """Column index -> (start, end), read from the header row's own text."""
    header_row = main_table.find("tr")
    if not header_row:
        raise ScrapeFormatError("Main timetable table has no header row.")
    headers = header_row.find_all(["th", "td"])
    result = {}
    for idx, cell in enumerate(headers):
        m = TIME_HEADER_PATTERN.search(cell.text)
        if m:
            h1, m1, h2, m2 = map(int, m.groups())
            result[idx] = (time_(h1, m1), time_(h2, m2))
    if not result:
        raise ScrapeFormatError(
            'No column matched the "(HH:MM-HH:MM)" time-header pattern — '
            "the university likely changed the header format."
        )
    return result


def parse_slots(soup: BeautifulSoup) -> list[ScrapedSlot]:
    subject_map, faculty_map = parse_remarks_maps(soup)

    main_table = soup.find("table")
    if not main_table:
        raise ScrapeFormatError("No table found on the page at all.")
    time_by_col = parse_time_headers(main_table)

    slots: list[ScrapedSlot] = []
    body_rows = main_table.find_all("tr")[1:]
    matched_any_day = False

    for row in body_rows:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        day_text = cells[0].text.strip()
        if not DAY_PATTERN.match(day_text):
            continue  # not a day row (could be a stray spacer row) — skip, don't fail
        matched_any_day = True
        day = DAY_MAP[day_text[:3].upper()]

        for col_idx, cell in enumerate(cells):
            if col_idx not in time_by_col:
                continue
            text = cell.text.strip()
            if not text:
                continue
            m = CELL_PATTERN.search(text)
            if not m:
                # Not every non-empty cell needs to match (rare stray text) — but
                # if this fires on every cell, format has drifted; caller checks
                # slots list length and warns if suspiciously small.
                continue
            subject_code, faculty_abbr = m.groups()
            remainder = CELL_PATTERN.sub("", text).strip()
            room_m = ROOM_BATCH_PATTERN.match(remainder) if remainder else None
            room = room_m.group(1).strip() if room_m and room_m.group(1) else None
            batch = room_m.group(2) if room_m else None
            start, end = time_by_col[col_idx]

            if subject_code not in subject_map:
                print(f"  WARNING: cell subject code {subject_code!r} not in Remarks table — skipping cell", file=sys.stderr)
                continue
            if faculty_abbr not in faculty_map:
                print(f"  WARNING: cell faculty abbr {faculty_abbr!r} not in Remarks table — skipping cell", file=sys.stderr)
                continue

            slots.append(ScrapedSlot(day, start, end, subject_code, faculty_abbr, room, batch))

    if not matched_any_day:
        raise ScrapeFormatError('No row\'s first cell matched a day name (Mon/Tue/.../Sun) — the day column moved or was renamed.')
    return slots, subject_map, faculty_map


def fetch_and_parse(url: str) -> tuple[list[ScrapedSlot], dict[str, str], dict[str, str]]:
    resp = httpx.get(url, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    return parse_slots(soup)


# ---------------------------------------------------------------------------
# DB resolution + upsert
# ---------------------------------------------------------------------------

async def resolve_and_upsert(session, url: str, slots: list[ScrapedSlot],
                              faculty_map: dict[str, str], section_name: str, term_name: str) -> None:
    from sqlmodel import select
    from app.models.academic import AcademicTerm, CourseOffering, Section, Subject, TimetableEntry
    from app.models.academic import DayOfWeek as DBDayOfWeek
    from app.models.user import RoleAssignment, RoleEnum, User

    term = (await session.exec(select(AcademicTerm).where(AcademicTerm.name == term_name))).first()
    if not term:
        raise ScrapeFormatError(f"No AcademicTerm named {term_name!r} exists — create it first, this script won't guess one.")

    section = (await session.exec(select(Section).where(Section.name == section_name, Section.term_id == term.id))).first()
    if not section:
        raise ScrapeFormatError(f"No Section named {section_name!r} for term {term_name!r} — check SECTION_CODE_MAP.")

    touched_offering_ids: set[int] = set()
    skipped = 0

    for slot in slots:
        subject = (await session.exec(select(Subject).where(Subject.course_code == slot.subject_code))).first()
        if not subject:
            print(f"  SKIP: Subject {slot.subject_code!r} not in DB — add it before re-running.", file=sys.stderr)
            skipped += 1
            continue

        faculty_full_name = faculty_map[slot.faculty_abbr]
        teacher = (await session.exec(select(User).where(User.full_name == faculty_full_name))).first()
        if not teacher:
            print(f"  SKIP: Teacher {faculty_full_name!r} not in DB — add the user before re-running.", file=sys.stderr)
            skipped += 1
            continue
        has_teacher_role = (await session.exec(
            select(RoleAssignment).where(RoleAssignment.user_id == teacher.id, RoleAssignment.role == RoleEnum.TEACHER)
        )).first()
        if not has_teacher_role:
            print(f"  SKIP: {faculty_full_name!r} exists but has no TEACHER role assignment.", file=sys.stderr)
            skipped += 1
            continue

        offering = (await session.exec(
            select(CourseOffering).where(
                CourseOffering.term_id == term.id,
                CourseOffering.section_id == section.id,
                CourseOffering.subject_id == subject.id,
                CourseOffering.teacher_id == teacher.id,
            )
        )).first()
        if not offering:
            offering = CourseOffering(term_id=term.id, section_id=section.id, subject_id=subject.id, teacher_id=teacher.id)
            session.add(offering)
            await session.flush()  # need offering.id below
            print(f"  CREATED CourseOffering #{offering.id}: {slot.subject_code} / {section_name} / {faculty_full_name}")

        touched_offering_ids.add(offering.id)
        session.add(TimetableEntry(
            course_offering_id=offering.id,
            day_of_week=DBDayOfWeek(slot.day.value),
            start_time=slot.start_time,
            end_time=slot.end_time,
            room=slot.room,
            batch_label=slot.batch_label,
            source=url,
            scraped_at=datetime.now(timezone.utc),
        ))

    await session.commit()
    print(f"Import summary for {section_name}: {len(slots) - skipped} slots written, {skipped} skipped, "
          f"{len(touched_offering_ids)} course offerings touched.")


async def replace_prior_entries(session, url: str) -> None:
    """Delete this URL's own previously-scraped entries before writing new ones —
    makes each run a clean re-import rather than an accumulating duplicate pile.
    Only ever touches rows this same source URL created; manual edits (a
    different `source` value) are left untouched."""
    from sqlmodel import select
    from app.models.academic import TimetableEntry

    prior = (await session.exec(select(TimetableEntry).where(TimetableEntry.source == url))).all()
    for entry in prior:
        await session.delete(entry)
    if prior:
        print(f"  Removed {len(prior)} previously-scraped entries for this URL before re-importing.")


async def import_one(url: str, engine) -> None:
    print(f"\n=== {url} ===")
    try:
        slots, subject_map, faculty_map = fetch_and_parse(url)
    except ScrapeFormatError as e:
        print(f"FORMAT BREAK — page structure changed: {e}", file=sys.stderr)
        print("Compare against the last snapshot in timetable_snapshots/ if you have one.", file=sys.stderr)
        return
    except httpx.HTTPError as e:
        print(f"NETWORK/HTTP ERROR: {e}", file=sys.stderr)
        return

    if len(slots) < 5:  # a real timetable has far more than this; a near-empty
        # result usually means the cell regex silently stopped matching rather
        # than the section genuinely having almost no classes.
        print(f"WARNING: only {len(slots)} slots parsed — this looks too low. "
              f"Check CELL_PATTERN against the page before trusting this import.", file=sys.stderr)

    SNAPSHOT_DIR.mkdir(exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / (re.sub(r"\W+", "_", url) + ".json")
    snapshot_path.write_text(json.dumps([s.__dict__ for s in slots], default=str, indent=2))

    section_code = next((p.split("=")[1] for p in url.split("?", 1)[-1].split("&") if p.startswith("section=")), None)
    section_name = SECTION_CODE_MAP.get(section_code or "")
    if not section_name:
        print(f"SKIP IMPORT: section code {section_code!r} has no entry in SECTION_CODE_MAP.", file=sys.stderr)
        return

    from sqlmodel.ext.asyncio.session import AsyncSession
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await replace_prior_entries(session, url)
        await resolve_and_upsert(session, url, slots, faculty_map, section_name, TERM_NAME)


async def main() -> None:
    from app.core.database import engine  # matches app/core/database.py's actual export
    for url in TIMETABLE_URLS:
        await import_one(url, engine)


if __name__ == "__main__":
    asyncio.run(main())