import re

BASE_KEYWORDS = (
    "submit", "submission", "due", "deadline", "hantar", "serah",
    "assignment", "quiz", "test", "exam", "presentation", "report",
    "project", "brief", "briefing", "resit", "interview", "meeting",
    "activity", "visit", "school", "resume", "cover letter", "mock interview",
    "lecture", "details", "schedule", "final", "midterm", "venue", "room",
    "esok", "esk", "tomorrow", "hari ni", "harini", "today",
    "malam ni", "tonight", "minggu ni", "this week", "by ",
    "kena", "must", "wajib", "compulsory", "jangan lupa", "urgent", "penting",
    "cancel", "reschedule", "postpone", "tangguh", "online", "zoom", "teams", "replace",
    "e-cert", "ecert", "certificate", "free", "webinar", "workshop", "competition",
    "fill in", "fill up", "isi", "form", "register", "daftar", "vote", "sign",
    "reply", "balas", "confirm", "attendance", "hadir", "sila", "tolong",
)

PE_GROUP_HINTS = (
    "project proposal pe group",
    "professional english",
    "logistic pe",
)

PE_CONTEXT_HINTS = (
    "event", "activity", "school", "sekolah", "visit", "bus", "proposal",
    "meeting", "video", "minute meeting", "report", "vendor", "driver",
    "programme", "program", "cert", "certificate", "ecert", "free",
    "resume", "cover letter", "mock interview", "interview",
)

DATE_RE = re.compile(
    r"\b(?:"
    r"\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?|"
    r"\d{1,2}/\d{1,2}(?:/\d{2,4})?|"
    r"\d{1,2}\s*(?:jan|feb|mar|apr|may|jun|june|jul|aug|sep|oct|nov|dec)[a-z]*\s*\d{0,4}|"
    r"(?:jan|feb|mar|apr|may|jun|june|jul|aug|sep|oct|nov|dec)[a-z]*\s*\d{1,2}(?:,\s*\d{4})?|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"isnin|selasa|rabu|khamis|jumaat|sabtu|ahad"
    r")\b",
    re.IGNORECASE,
)


def is_relevant_message(group_name, text):
    lower_text = (text or "").lower()
    lower_group = (group_name or "").lower()

    if any(keyword in lower_text for keyword in BASE_KEYWORDS):
        return True

    has_date = bool(DATE_RE.search(lower_text))
    pe_group = any(hint in lower_group for hint in PE_GROUP_HINTS)
    pe_context = any(hint in lower_text for hint in PE_CONTEXT_HINTS)

    if pe_group and (has_date or pe_context):
        return True

    if has_date and pe_context:
        return True

    return False
