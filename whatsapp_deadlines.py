import re
import sqlite3
from datetime import date, datetime, timedelta

from deadlines import add, cancel
from paths import MESSAGES_DB as MESSAGES_DB_PATH


ACADEMIC_GROUP_TO_COURSE = (
    ("database", "DATABASE"),
    ("oop", "OOP"),
    ("coos", "COOS"),
    ("prob stat", "PROB STAT"),
    ("oosad", "OOSAD"),
    ("professional english", "PE"),
    ("project proposal pe group", "PE"),
    ("logistic pe", "PE"),
)

WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
    "isnin": 0,
    "selasa": 1,
    "rabu": 2,
    "khamis": 3,
    "jumaat": 4,
    "sabtu": 5,
    "ahad": 6,
}

MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

ACADEMIC_HINTS = (
    "submit", "submission", "assignment", "quiz", "test", "exam",
    "project", "proposal", "report", "presentation", "lab", "briefing",
    "progress", "resume", "cover letter", "mock job interview",
)

SKIP_HINTS = (
    "e-cert", "ecert", "certificate", "webinar", "competition", "workshop",
)

RELATIVE_DATE_CONTEXT_HINTS = (
    "due", "deadline", "submit", "submission", "test", "quiz", "exam",
    "class", "briefing", "meeting", "event", "visit", "presentation",
    "interview", "rescheduled", "postponed", "replacement",
)

RESCHEDULE_HINTS = (
    "reschedule", "rescheduled", "postpone", "postponed", "cancel",
    "replacement", "replace", "changed to", "move to", "moved to",
    "become", "changed", "new date",
)

CANCEL_ONLY_HINTS = (
    "cancelled", "canceled", "cancel", "call off", "called off"
)

NOISY_CHAT_PATTERNS = (
    r"\bi[' ]?m from\b",
    r"\bi got\b",
    r"\bsorry\b",
    r"\banyone\b",
    r"\bmadam\b",
    r"\beither\b",
    r"\bokay ke\b",
)


def infer_course(group_name):
    lower = (group_name or "").lower()
    for needle, course in ACADEMIC_GROUP_TO_COURSE:
        if needle in lower:
            return course
    return None


def parse_message_timestamp(timestamp_iso):
    return datetime.fromisoformat(timestamp_iso).date()


def _parse_explicit_numeric_date(message, base_date):
    match = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", message, re.IGNORECASE)
    if not match:
        return None
    day = int(match.group(1))
    month = int(match.group(2))
    year = match.group(3)
    if year is None:
        year_val = base_date.year
    else:
        year_val = int(year)
        if year_val < 100:
            year_val += 2000
    try:
        return date(year_val, month, day)
    except ValueError:
        return None


def _parse_explicit_text_date(message, base_date):
    match = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+"
        r"(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)"
        r"(?:\s+(\d{2,4}))?\b",
        message,
        re.IGNORECASE,
    )
    if not match:
        return None
    day = int(match.group(1))
    month = MONTH_MAP[match.group(2).lower()]
    year = match.group(3)
    year_val = base_date.year if year is None else int(year) + (2000 if len(year) == 2 else 0)
    try:
        return date(year_val, month, day)
    except ValueError:
        return None


def _parse_weekday(message, base_date):
    lower = message.lower()
    for name, idx in WEEKDAY_INDEX.items():
        if re.search(rf"\b{name}\b", lower):
            delta = (idx - base_date.weekday()) % 7
            if delta == 0:
                delta = 7
            return base_date + timedelta(days=delta)
    return None


def _extract_explicit_dates(message, base_date):
    dates = []
    seen = set()

    for match in re.finditer(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", message, re.IGNORECASE):
        day = int(match.group(1))
        month = int(match.group(2))
        year = match.group(3)
        if year is None:
            year_val = base_date.year
        else:
            year_val = int(year)
            if year_val < 100:
                year_val += 2000
        try:
            value = date(year_val, month, day)
        except ValueError:
            continue
        if value not in seen:
            seen.add(value)
            dates.append(value)

    for match in re.finditer(
        r"\b(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+"
        r"(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)"
        r"(?:\s+(\d{2,4}))?\b",
        message,
        re.IGNORECASE,
    ):
        day = int(match.group(1))
        month = MONTH_MAP[match.group(2).lower()]
        year = match.group(3)
        year_val = base_date.year if year is None else int(year) + (2000 if len(year) == 2 else 0)
        try:
            value = date(year_val, month, day)
        except ValueError:
            continue
        if value not in seen:
            seen.add(value)
            dates.append(value)

    return dates


def _is_reschedule_message(lower):
    return any(hint in lower for hint in RESCHEDULE_HINTS)


def _is_cancel_only_message(lower, explicit_dates):
    if not any(hint in lower for hint in CANCEL_ONLY_HINTS):
        return False
    if any(hint in lower for hint in ("reschedule", "rescheduled", "postpone", "postponed", "replacement", "replace", "changed to", "move to", "moved to", "new date", "become", "changed")):
        return False
    # If there are multiple dates, treat it as a reschedule, not a pure cancel.
    return len(explicit_dates) <= 1


def infer_due_date(message, timestamp_iso):
    base_date = parse_message_timestamp(timestamp_iso)
    lower = message.lower()

    explicit_dates = _extract_explicit_dates(message, base_date)
    if explicit_dates:
        if _is_reschedule_message(lower):
            return max(explicit_dates)
        return explicit_dates[0]

    if re.search(r"\btoday\b|\bhari ni\b|\bharini\b", lower) and any(hint in lower for hint in RELATIVE_DATE_CONTEXT_HINTS):
        return base_date
    if re.search(r"\btomorrow\b|\besok\b|\besk\b", lower) and any(hint in lower for hint in RELATIVE_DATE_CONTEXT_HINTS):
        return base_date + timedelta(days=1)

    weekday = _parse_weekday(message, base_date)
    if weekday and any(hint in lower for hint in RELATIVE_DATE_CONTEXT_HINTS):
        return weekday

    return None


def infer_task_name(group_name, message):
    lower = message.lower()
    group_lower = (group_name or "").lower()

    if "project proposal pe group" in group_lower and (
        re.search(r"\b\d{1,2}/\d{1,2}\b", lower)
        or "11th of june" in lower
        or any(word in lower for word in ("event", "bus", "school", "sekolah"))
    ):
        return "School visit activity"
    if "project progress" in lower and "task no. 7" in lower:
        return "Project Progress Submission — Task 7"
    if "assignment 4" in lower:
        return "Assignment 4: Resume, Cover Letter & Mock Job Interview"
    if "lab assignment" in lower:
        return "Lab Assignment"
    if "lab test" in lower:
        return "Lab Test"
    if "final test" in lower:
        return "Final Test"
    if "quiz" in lower:
        return "Quiz"
    if "sql exercise" in lower:
        return "SQL Exercise Attendance Submission"
    if "school visit" in lower or ("project proposal pe group" in group_lower and any(word in lower for word in ("event", "bus", "school", "sekolah"))):
        return "School visit activity"
    if "project brief" in lower and "project orion" in lower:
        return "Project Orion"

    first_line = re.sub(r"\s+", " ", message.strip().splitlines()[0])
    return first_line[:90]


def should_create_deadline(group_name, message, due):
    if due is None or due < date.today():
        return False
    lower = message.lower()
    group_lower = (group_name or "").lower()
    course = infer_course(group_name)
    if any(hint in lower for hint in SKIP_HINTS):
        return False
    if any(re.search(pattern, lower) for pattern in NOISY_CHAT_PATTERNS):
        if "project proposal pe group" not in group_lower:
            return False
    if "project orion" in lower and course != "COOS":
        return False
    if "submit" in lower and not any(word in lower for word in (
        "assignment", "project", "task", "report", "lab", "exercise",
        "proposal", "video", "progress", "resume", "cover letter", "interview",
    )):
        return False
    if not any(hint in lower for hint in ACADEMIC_HINTS):
        if not any(hint in (group_name or "").lower() for hint in ("project proposal pe group", "logistic pe")):
            return False
    if "no class" in lower and "assignment" not in lower and "test" not in lower:
        return False
    return True


def sync_message(group_name, message, timestamp_iso):
    course = infer_course(group_name)
    if not course:
        return []

    lower = message.lower()
    explicit_dates = _extract_explicit_dates(message, parse_message_timestamp(timestamp_iso))
    if _is_cancel_only_message(lower, explicit_dates):
        task = infer_task_name(group_name, message)
        removed_ids = cancel(task, course, due=explicit_dates[0].strftime("%d %b %Y") if explicit_dates else None)
        return [(row_id, "canceled", course, task, "") for row_id in removed_ids]

    source = "whatsapp-reschedule" if _is_reschedule_message(lower) else "whatsapp"
    due = infer_due_date(message, timestamp_iso)
    if not should_create_deadline(group_name, message, due):
        return []

    task = infer_task_name(group_name, message)
    row_id, status = add(task, course, due.strftime("%d %b %Y"), source=source)
    return [(row_id, status, course, task, due.strftime("%d %b %Y"))]


def sync_recent_messages(messages_db=str(MESSAGES_DB_PATH), since_days=14):
    cutoff = (datetime.now() - timedelta(days=since_days)).isoformat()
    conn = sqlite3.connect(messages_db)
    rows = conn.execute(
        "SELECT id, group_name, message, timestamp FROM messages WHERE done=0 AND timestamp >= ? ORDER BY id DESC",
        (cutoff,)
    ).fetchall()
    conn.close()

    created = []
    for _, group_name, message, timestamp_iso in rows:
        created.extend(sync_message(group_name, message, timestamp_iso))
    return created
