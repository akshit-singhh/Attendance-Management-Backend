# app/api/v1/web/timetable.py

import json
import time
import os
import urllib.request
from datetime import datetime
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, Query, HTTPException, status
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.config import settings
from app.core.database import get_session
from app.api.deps import require_coordinator
from app.models.academic import CourseOffering, Subject, Section, TimetableEntry
from app.schemas.academic import TimetableSlotResponse

router = APIRouter()

# ---------------------------------------------------------------------------
# PERIOD AND DAY DEFINITIONS (Matches GBU Official Samay Timetable System)
# ---------------------------------------------------------------------------

PERIOD_MAP = {
    1: "08:30-09:30",
    2: "09:30-10:30",
    3: "10:30-11:30",
    4: "11:30-12:30",
    5: "12:30-13:30",
    6: "13:30-14:30",
    7: "14:30-15:30",
    8: "15:30-16:30",
    9: "16:30-17:30",
    10: "17:30-18:30",
    11: "18:30-19:30",
}

DAY_MAP = {
    1: "Mon",
    2: "Tue",
    3: "Wed",
    4: "Thu",
    5: "Fri",
    6: "Sat",
    7: "Sun",
}

# ---------------------------------------------------------------------------
# IN-MEMORY CACHE FOR SAMAY API (1 Hour TTL + Offline Local Fallback)
# ---------------------------------------------------------------------------

_cache_lock = False
_samay_cache: Dict[str, Any] = {
    "raw_data": None,
    "last_fetched_epoch": 0,
    "last_synced_str": "Not synced yet",
    "metadata": None,
}

SAMAY_API_URL = settings.SAMAY_API_URL
_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
_DATA_DIR = os.path.join(_ROOT_DIR, "data")


def _resolve_timetable_backup() -> str:
    candidates = [
        os.path.join(_DATA_DIR, "timetableApi.txt"),
        os.path.join(_ROOT_DIR, "timetableApi.txt"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "timetableApi.txt"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]

LOCAL_BACKUP_PATH = _resolve_timetable_backup()


def _fetch_from_local_backup() -> List[Dict[str, Any]]:
    """Loads timetable data from local file as fallback if external site is unreachable."""
    path = _resolve_timetable_backup()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Samay Proxy] Warning: Failed reading local backup: {e}")
    return []


# Cache for section remarks (includes non-classroom courses like Minor Project and Industrial Training)
_remarks_cache: Dict[str, Any] = {}


def _resolve_remarks_backup() -> str:
    candidates = [
        os.path.join(_DATA_DIR, "section_remarks_backup.json"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "section_remarks_backup.json"),
        os.path.join(_ROOT_DIR, "section_remarks_backup.json"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    os.makedirs(_DATA_DIR, exist_ok=True)
    return candidates[0]

REMARKS_BACKUP_PATH = _resolve_remarks_backup()


def _load_remarks_backup():
    path = _resolve_remarks_backup()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                _remarks_cache.update(data)
        except Exception as e:
            print(f"[Samay Proxy] Warning: Could not read remarks backup: {e}")

_load_remarks_backup()


def _save_remarks_backup():
    try:
        path = _resolve_remarks_backup()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_remarks_cache, f, indent=2)
    except Exception as e:
        print(f"[Samay Proxy] Warning: Could not write remarks backup: {e}")

def fetch_section_remarks(school: str, dept: str, section_id: str) -> List[Dict[str, Any]]:
    """
    Fetches the official university Remarks table from the configured schedule endpoint,
    which includes classroom lectures/labs AND non-classroom courses like Minor Project (CS491)
    and Industrial Training (CS493).
    """
    cache_key = f"{school}_{dept}_{section_id}"
    if cache_key in _remarks_cache:
        return _remarks_cache[cache_key]
    if section_id in _remarks_cache:
        return _remarks_cache[section_id]

    base_schedule_url = settings.MYGBU_SCHEDULE_URL.rstrip("/")
    url = f"{base_schedule_url}/?name={school}&dept={dept}&section={section_id}"
    try:
        import ssl
        import re
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AttendanceApp/1.0",
                "Accept": "text/html,application/xhtml+xml",
            }
        )
        with urllib.request.urlopen(req, timeout=7, context=ctx) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            table_match = re.search(r'Remarks.*?<table[^>]*>(.*?)</table>', html, re.DOTALL | re.IGNORECASE)
            if not table_match:
                return _remarks_cache.get(section_id, [])

            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_match.group(1), re.DOTALL | re.IGNORECASE)
            parsed_rows = []
            for r in rows[1:]:  # skip header row
                cols = [
                    re.sub(r'<[^>]+>', '', c).strip()
                    for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', r, re.DOTALL | re.IGNORECASE)
                ]
                if len(cols) >= 5:
                    code = cols[0]
                    name = cols[1]
                    credits_val = cols[2]
                    abbr = cols[3]
                    assigned = cols[4]
                    raw_hours = cols[5] if len(cols) > 5 else ""

                    # Determine activity type
                    activity = "Lecture"
                    if "lab" in name.lower() or "lab" in code.lower():
                        activity = "Lab"
                    elif "project" in name.lower() or "project" in code.lower():
                        activity = "Project"
                    elif "training" in name.lower() or "training" in code.lower() or "internship" in name.lower():
                        activity = "Training"
                    elif "seminar" in name.lower():
                        activity = "Seminar"

                    parsed_rows.append({
                        "subject_code": code,
                        "subject_name": name,
                        "credits": credits_val,
                        "teacher_abbr": abbr,
                        "teacher_name": assigned,
                        "activity": activity,
                        "hours": raw_hours if raw_hours and raw_hours != ".0" else "",
                    })

            if parsed_rows:
                _remarks_cache[cache_key] = parsed_rows
                _remarks_cache[section_id] = parsed_rows
                _save_remarks_backup()
            return parsed_rows
    except Exception as e:
        print(f"[Samay Proxy] Warning: Failed to fetch section remarks for {section_id}: {e}")
        return _remarks_cache.get(section_id, [])


def _extract_metadata(raw_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Generates structured lookup indices for schools, departments, sections, and teachers."""
    schools_set = set()
    depts_map = {}   # school -> set of depts
    sections_map = {} # section_id -> dict
    teachers_map = {} # faculty_id or name -> dict

    for item in raw_items:
        school = (item.get("school") or "").strip()
        dept = (item.get("StudentDepartment") or item.get("dept") or "").strip()
        if not school:
            school = "SOICT"
        schools_set.add(school)

        if school not in depts_map:
            depts_map[school] = set()
        if dept:
            depts_map[school].add(dept)

        sec_id = str(item.get("Section_Id") or "").strip()
        sec_name = (item.get("SectionName") or "").strip()
        prog_code = (item.get("Code") or "").strip()
        prog_name = (item.get("program_name") or "").strip()

        if sec_id and sec_id not in sections_map:
            sections_map[sec_id] = {
                "section_id": sec_id,
                "section_name": sec_name,
                "school": school,
                "dept": dept,
                "program_code": prog_code,
                "program_name": prog_name,
            }

        t_name = (item.get("TeacherName") or "").strip()
        t_id = str(item.get("faculty_id") or "").strip()
        abbr = (item.get("abbr") or "").strip()
        t_school = (item.get("TeacherHomeSchool") or school).strip()

        if t_name and t_name not in teachers_map:
            teachers_map[t_name] = {
                "faculty_id": t_id,
                "teacher_name": t_name,
                "abbr": abbr,
                "dept": dept,
                "school": t_school,
            }

    sorted_schools = sorted(list(schools_set))
    serializable_depts = {k: sorted(list(v)) for k, v in depts_map.items()}
    sorted_sections = sorted(list(sections_map.values()), key=lambda x: (x["school"], x["dept"], x["section_name"]))
    sorted_teachers = sorted(list(teachers_map.values()), key=lambda x: x["teacher_name"])

    return {
        "schools": sorted_schools,
        "departments_by_school": serializable_depts,
        "sections": sorted_sections,
        "teachers": sorted_teachers,
        "total_slots": len(raw_items),
    }


def get_cached_samay_data(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Fetches and caches official Samay timetable data."""
    now = time.time()
    cache_age = now - _samay_cache["last_fetched_epoch"]

    # Return cached data if fresh (under 1 hour) and not forcing refresh
    if not force_refresh and _samay_cache["raw_data"] is not None and cache_age < 3600:
        return _samay_cache["raw_data"]

    # Attempt to fetch live from Samay
    fetched_data = None
    try:
        req = urllib.request.Request(
            SAMAY_API_URL,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AttendanceApp/1.0",
                "Accept": "application/json",
            }
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            if resp.status == 200:
                raw_bytes = resp.read()
                fetched_data = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        print(f"[Samay Proxy] Live fetch failed ({exc}), falling back...")

    # If live fetch failed, use existing cache or fallback file
    if not fetched_data:
        if _samay_cache["raw_data"] is not None:
            return _samay_cache["raw_data"]
        fetched_data = _fetch_from_local_backup()

    if not fetched_data:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="University Timetable service (Samay) is currently unreachable and no cached data exists."
        )

    # Update cache
    _samay_cache["raw_data"] = fetched_data
    _samay_cache["last_fetched_epoch"] = now
    _samay_cache["last_synced_str"] = datetime.now().strftime("%d %b %Y, %I:%M %p")
    _samay_cache["metadata"] = _extract_metadata(fetched_data)

    return fetched_data


# ---------------------------------------------------------------------------
# SAMAY TIMETABLE ENDPOINTS (Read-Only Live Viewer)
# ---------------------------------------------------------------------------

@router.get("/samay/metadata", tags=["Timetable - Official Samay Portal"])
async def get_samay_metadata(
    force_refresh: bool = Query(default=False),
    admin_id: int = Depends(require_coordinator),
):
    """Returns academic structure (Schools, Depts, Sections, Teachers) for filters."""
    get_cached_samay_data(force_refresh=force_refresh)
    metadata = _samay_cache.get("metadata") or {}
    return {
        "status": "success",
        "last_synced": _samay_cache["last_synced_str"],
        **metadata
    }


@router.get("/samay/slots", tags=["Timetable - Official Samay Portal"])
async def get_samay_slots(
    section_id: Optional[str] = Query(default=None),
    teacher_name: Optional[str] = Query(default=None),
    faculty_id: Optional[str] = Query(default=None),
    school: Optional[str] = Query(default=None),
    force_refresh: bool = Query(default=False),
    admin_id: int = Depends(require_coordinator),
):
    """
    Returns filtered timetable grid slots and remarks/teaching load breakdown.
    Can filter by section_id (Section View) or teacher_name/faculty_id (Teacher View).
    """
    raw_data = get_cached_samay_data(force_refresh=force_refresh)

    filtered_raw = raw_data

    # Clean parameter values
    s_id = section_id if isinstance(section_id, str) and section_id.strip() else None
    f_id = faculty_id if isinstance(faculty_id, str) and faculty_id.strip() else None
    t_name = teacher_name if isinstance(teacher_name, str) and teacher_name.strip() else None
    sch = school if isinstance(school, str) and school.strip() else None

    # Apply filters
    if s_id:
        target_sec = s_id.strip()
        filtered_raw = [x for x in filtered_raw if str(x.get("Section_Id", "")).strip() == target_sec]
    elif f_id:
        target_f_id = f_id.strip()
        filtered_raw = [x for x in filtered_raw if str(x.get("faculty_id", "")).strip() == target_f_id]
    elif t_name:
        target_t = t_name.strip().lower()
        filtered_raw = [x for x in filtered_raw if (x.get("TeacherName") or "").strip().lower() == target_t]
    elif sch:
        target_school = sch.strip().lower()
        filtered_raw = [x for x in filtered_raw if (x.get("school") or "").strip().lower() == target_school]

    # Transform slots to clean structure
    slots = []
    for item in filtered_raw:
        day_raw = item.get("TT_Day")
        period_raw = item.get("TT_Period")
        
        day = int(day_raw) if str(day_raw).isdigit() else 1
        period = int(period_raw) if str(period_raw).isdigit() else 1
        
        # Guard valid days 1-7 and periods 1-11
        if day not in DAY_MAP or period not in PERIOD_MAP:
            continue

        subj_code = (item.get("Subject_Code") or "").strip()
        subj_name = (item.get("subject_name") or "").strip()
        curr_t_name = (item.get("TeacherName") or "").strip()
        curr_t_abbr = (item.get("abbr") or "").strip()
        curr_f_id = str(item.get("faculty_id") or "").strip()
        curr_sec_name = (item.get("SectionName") or "").strip()
        curr_sec_id_val = str(item.get("Section_Id") or "").strip()
        prog_name = (item.get("program_name") or "").strip()
        room = (item.get("RoomName") or "").strip()
        building = (item.get("Building") or "").strip()
        activity = (item.get("ActivityTag") or "Lecture").strip()
        batch_id = str(item.get("Batch_Id") or "0").strip()

        slots.append({
            "day": day,
            "day_name": DAY_MAP[day],
            "period": period,
            "period_time": PERIOD_MAP[period],
            "subject_code": subj_code,
            "subject_name": subj_name,
            "teacher_name": curr_t_name,
            "teacher_abbr": curr_t_abbr,
            "faculty_id": curr_f_id,
            "section_name": curr_sec_name,
            "section_id": curr_sec_id_val,
            "program_name": prog_name,
            "room": room,
            "building": building,
            "activity": activity,
            "batch_id": batch_id,
        })

    # Sort slots by day then period
    slots.sort(key=lambda s: (s["day"], s["period"]))

    # Build summary breakdown table
    summary_rows = []
    title = "GBU University Timetable"
    subtitle = "Live view from official Samay portal"

    if s_id and filtered_raw:
        first_item = filtered_raw[0]
        sec_name = (first_item.get("SectionName") or "").strip()
        prog_name = (first_item.get("program_name") or "").strip()
        title = f"{prog_name} ({sec_name})" if prog_name else sec_name
        school_name = (first_item.get("school") or "SOICT").strip()
        dept_name = (first_item.get("StudentDepartment") or first_item.get("dept") or "CSE").strip()
        subtitle = f"Section ID: {s_id} • School: {school_name} • Dept: {dept_name}"

        # Fetch full official remarks table (includes Minor Project, Industrial Training, etc.)
        official_remarks = fetch_section_remarks(school_name, dept_name, s_id)

        # Count periods present in the timetable grid
        slot_hours_map = {}
        for s in slots:
            code = s["subject_code"]
            slot_hours_map[code] = slot_hours_map.get(code, 0) + 1

        if official_remarks:
            summary_rows = []
            for rem in official_remarks:
                code = rem["subject_code"]
                grid_hrs = slot_hours_map.get(code, 0)
                official_load = rem.get("hours", "")
                final_hours = official_load if (official_load and official_load != ".0") else (f"{grid_hrs}.0" if grid_hrs > 0 else (f"{rem['credits']}.0" if rem.get("credits") else ".0"))

                is_non_classroom = grid_hrs == 0

                summary_rows.append({
                    "subject_code": code,
                    "subject_name": rem["subject_name"],
                    "credits": rem["credits"],
                    "teacher_abbr": rem["teacher_abbr"],
                    "teacher_name": rem["teacher_name"],
                    "activity": rem["activity"],
                    "hours": final_hours,
                    "official_load": official_load if official_load else ".0",
                    "grid_hours": grid_hrs,
                    "is_non_classroom": is_non_classroom,
                })
        else:
            # Fallback to grouped slots if official remarks could not be fetched
            sub_map = {}
            for s in slots:
                key = (s["subject_code"], s["teacher_name"], s["activity"])
                if key not in sub_map:
                    sub_map[key] = {
                        "subject_code": s["subject_code"],
                        "subject_name": s["subject_name"],
                        "teacher_name": s["teacher_name"],
                        "teacher_abbr": s["teacher_abbr"],
                        "activity": s["activity"],
                        "credits": "-",
                        "hours": 0,
                        "is_non_classroom": False,
                    }
                sub_map[key]["hours"] += 1
            summary_rows = sorted(list(sub_map.values()), key=lambda r: r["subject_code"])

    elif (t_name or f_id) and filtered_raw:
        first_item = filtered_raw[0]
        disp_name = (first_item.get("TeacherName") or t_name or "Faculty").strip()
        disp_dept = (first_item.get("dept") or first_item.get("StudentDepartment") or "").strip()
        title = f"{disp_name} ({disp_dept})" if disp_dept else disp_name
        subtitle = f"Faculty Timetable • {first_item.get('TeacherHomeSchool', 'SOICT')}"

        # Group by Section + Subject + Activity
        sec_map = {}
        for s in slots:
            key = (s["section_name"], s["subject_code"], s["activity"])
            if key not in sec_map:
                sec_map[key] = {
                    "section_name": s["section_name"],
                    "subject_code": s["subject_code"],
                    "subject_name": s["subject_name"],
                    "activity": s["activity"],
                    "hours": 0
                }
            sec_map[key]["hours"] += 1
        summary_rows = sorted(list(sec_map.values()), key=lambda r: (r["section_name"], r["subject_code"]))

    return {
        "title": title,
        "subtitle": subtitle,
        "last_synced": _samay_cache["last_synced_str"],
        "total_slots": len(slots),
        "slots": slots,
        "summary": summary_rows,
        "non_classroom_courses": [r for r in summary_rows if r.get("is_non_classroom")],
        "period_definitions": PERIOD_MAP,
        "day_definitions": DAY_MAP,
    }


# ---------------------------------------------------------------------------
# LOCAL DATABASE TIMETABLE VIEWER (Fallback for custom database entries)
# ---------------------------------------------------------------------------

@router.get("/", response_model=List[TimetableSlotResponse], tags=["Timetable - Local Database"])
async def list_local_timetable(
    term_id: int,
    section_id: int,
    coordinator_id: int = Depends(require_coordinator),
    db: AsyncSession = Depends(get_session),
):
    """Read-only: checks timetable entries recorded in our local database."""
    statement = (
        select(TimetableEntry, CourseOffering, Subject, Section)
        .join(CourseOffering, TimetableEntry.course_offering_id == CourseOffering.id)
        .join(Subject, CourseOffering.subject_id == Subject.id)
        .join(Section, CourseOffering.section_id == Section.id)
        .where(CourseOffering.term_id == term_id, CourseOffering.section_id == section_id)
    )
    results = await db.exec(statement)
    return [
        TimetableSlotResponse(
            course_offering_id=offering.id,
            subject_code=subject.course_code,
            subject_name=subject.name,
            section_name=section.name,
            day_of_week=entry.day_of_week,
            start_time=entry.start_time,
            end_time=entry.end_time,
            room=entry.room,
            batch_label=entry.batch_label,
        )
        for entry, offering, subject, section in results
    ]