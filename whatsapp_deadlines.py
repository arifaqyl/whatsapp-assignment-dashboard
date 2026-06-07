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
    "lecture", "details", "schedule", "final", "midterm", "venue", "room",
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
    "become", "changed", "new date", "instead of", "turn into",
    "changed from", "change from", "replace with", "replaced with",
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

GENERIC_OPENERS = (
    "dear ",
    "assalamualaikum",
    "salam",
    "good morning",
    "good evening",
    "good afternoon",
    "hello",
    "hi ",
    "anyway",
)

STRONG_ACTION_HINTS = (
    "assignment", "project", "task", "submission", "submit", "deadline", "due",
    "quiz", "test", "exam", "presentation", "final assessment", "lab",
    "exercise", "proposal", "report", "interview", "assessment",
)


def _looks_like_exam_notice(group_name, message):
    lower = (message or "").lower()
    group_lower = (group_name or "").lower()
    if "exam" in lower or "examination" in lower:
        return True
    if "oop" not in group_lower and "oosad" not in group_lower:
        return False
    has_date = bool(_extract_explicit_dates(message, parse_message_timestamp("2026-06-07T00:00:00")))
    has_details_block = any(token in lower for token in ("time:", "duration:", "place:", "venue:", "format:", "details"))
    return has_date and has_details_block


def infer_course(group_name):
    lower = (group_name or "").lower()
    for needle, course in ACADEMIC_GROUP_TO_COURSE:
        if needle in lower:
            return course
    return None


def parse_message_timestamp(timestamp_iso):
    return datetime.fromisoformat(timestamp_iso).date()


def _parse_explicit_numeric_date(message, base_date):
    match = re.search(r"\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b", message, re.IGNORECASE)
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


def _parse_weekday_prefixed_date(message, base_date):
    match = re.search(
        r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"isnin|selasa|rabu|khamis|jumaat|sabtu|ahad)\s*,?\s*"
        r"(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\s+"
        r"(\d{1,2})(?:st|nd|rd|th)?(?:,\s*(\d{2,4}))?\b",
        message,
        re.IGNORECASE,
    )
    if not match:
        return None
    month = MONTH_MAP[match.group(1).lower()]
    day = int(match.group(2))
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

    for match in re.finditer(r"\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b", message, re.IGNORECASE):
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

    weekday_prefixed = _parse_weekday_prefixed_date(message, base_date)
    if weekday_prefixed and weekday_prefixed not in seen:
        seen.add(weekday_prefixed)
        dates.append(weekday_prefixed)

    for match in re.finditer(
        r"\b(\d{1,2}(?:\s*(?:&|,|and)\s*\d{1,2})+)\s+"
        r"(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)"
        r"(?:\s+(\d{2,4}))?\b",
        message,
        re.IGNORECASE,
    ):
        day_block = match.group(1)
        month = MONTH_MAP[match.group(2).lower()]
        year = match.group(3)
        year_val = base_date.year if year is None else int(year) + (2000 if len(year) == 2 else 0)
        for day_text in re.split(r"\s*(?:&|,|and)\s*", day_block):
            if not day_text.strip():
                continue
            day = int(day_text)
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


def _extract_date_range_start(message, base_date):
    match = re.search(
        r"\b(\d{1,2})\s*-\s*(\d{1,2})\s+"
        r"(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)"
        r"(?:\s+(\d{2,4}))?\b",
        message,
        re.IGNORECASE,
    )
    if not match:
        return None
    day = int(match.group(1))
    month = MONTH_MAP[match.group(3).lower()]
    year = match.group(4)
    year_val = base_date.year if year is None else int(year) + (2000 if len(year) == 2 else 0)
    try:
        return date(year_val, month, day)
    except ValueError:
        return None


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
        if (
            _is_reschedule_message(lower)
            or "instead of" in lower
            or "turn into" in lower
            or "changed from" in lower
            or "change from" in lower
            or "replace with" in lower
            or "replaced with" in lower
            or ("from" in lower and "to" in lower and len(explicit_dates) >= 2)
        ):
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
    if _looks_like_exam_notice(group_name, message):
        return "Exam"
    if "quiz" in lower:
        return "Quiz"
    if "sql exercise" in lower:
        return "SQL Exercise Attendance Submission"
    if "school visit" in lower or ("project proposal pe group" in group_lower and any(word in lower for word in ("event", "bus", "school", "sekolah"))):
        return "School visit activity"
    if "project brief" in lower and "project orion" in lower:
        return "Project Orion"

    lines = [re.sub(r"\s+", " ", line).strip(" -:\t") for line in message.strip().splitlines() if line.strip()]
    for line in lines:
        normalized = line.lower()
        if any(normalized.startswith(prefix) for prefix in GENERIC_OPENERS):
            continue
        return line[:90]
    first_line = re.sub(r"\s+", " ", message.strip().splitlines()[0])
    return first_line[:90]


def _normalize_message_for_matching(message):
    return (
        (message or "")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
    )


def _extract_structured_deadlines(group_name, message, timestamp_iso):
    base_date = parse_message_timestamp(timestamp_iso)
    normalized = _normalize_message_for_matching(message)
    entries = []
    seen = set()

    def _add_entry(task, due_date):
        if not task or not due_date:
            return
        key = (task, due_date.isoformat())
        if key in seen:
            return
        seen.add(key)
        entries.append((task, due_date))

    if re.search(r"project progress", normalized, re.IGNORECASE) and re.search(r"task\s*no\.?\s*7", normalized, re.IGNORECASE):
        match = re.search(
            r"submission\s+deadline\s*:\s*([^\n]+)",
            normalized,
            re.IGNORECASE,
        )
        if match:
            due = infer_due_date(match.group(1), timestamp_iso)
            _add_entry("Project Progress Submission - Task 7", due)

    if re.search(r"\bproject presentation\b", normalized, re.IGNORECASE):
        block = re.search(
            r"project presentation\s*(.*?)(?:\n\s*\n|final assessment|$)",
            normalized,
            re.IGNORECASE | re.DOTALL,
        )
        if block:
            range_start = _extract_date_range_start(block.group(1), base_date)
            if range_start:
                _add_entry("Project Presentation", range_start)
            else:
                dates = _extract_explicit_dates(block.group(1), base_date)
                if dates:
                    _add_entry("Project Presentation", min(dates))

    final_match = re.search(
        r"final assessment(?:\s*\(tentative\))?.{0,250}?date\s*:\s*([^\n]+)",
        normalized,
        re.IGNORECASE | re.DOTALL,
    )
    if final_match:
        due = infer_due_date(final_match.group(1), timestamp_iso)
        _add_entry("Final Assessment", due)

    if "school visit" in normalized.lower():
        due = infer_due_date(normalized, timestamp_iso)
        _add_entry("School visit activity", due)

    return entries


def should_create_deadline(group_name, message, due, reference_date=None):
    if reference_date is None:
        reference_date = date.today()
    if due is None or due < reference_date:
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
    if "class" in lower and "online" in lower and not any(word in lower for word in STRONG_ACTION_HINTS):
        return False
    if (
        "lecture" in lower
        and not any(word in lower for word in STRONG_ACTION_HINTS)
        and not any(word in lower for word in ("final", "midterm", "venue", "room", "details"))
        and "briefing" not in lower
    ):
        return False
    if any(lower.startswith(prefix) for prefix in GENERIC_OPENERS) and not any(word in lower for word in STRONG_ACTION_HINTS):
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
    base_date = parse_message_timestamp(timestamp_iso)
    explicit_dates = _extract_explicit_dates(message, base_date)
    if _is_cancel_only_message(lower, explicit_dates):
        task = infer_task_name(group_name, message)
        removed_ids = cancel(task, course, due=explicit_dates[0].strftime("%d %b %Y") if explicit_dates else None)
        return [(row_id, "canceled", course, task, "") for row_id in removed_ids]

    structured = _extract_structured_deadlines(group_name, message, timestamp_iso)
    if structured:
        created = []
        for task, due in structured:
            if not should_create_deadline(group_name, task, due, reference_date=base_date):
                continue
            row_id, status = add(task, course, due.strftime("%d %b %Y"), source="whatsapp")
            created.append((row_id, status, course, task, due.strftime("%d %b %Y")))
        if created:
            return created

    source = "whatsapp-reschedule" if _is_reschedule_message(lower) else "whatsapp"
    due = infer_due_date(message, timestamp_iso)
    if not should_create_deadline(group_name, message, due, reference_date=base_date):
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
