import re
from datetime import date, datetime

GENERIC_DUE_MARKERS = (
    "VLE",
    "CLP",
    "SEE",
    "TBD",
    "N/A",
    "WEEK",
    "TIME REMAINING",
    "OVERDUE",
    "SUBMITTED",
)

SOURCE_RANK = {
    "manual": 5,
    "whatsapp-reschedule": 5,
    "whatsapp": 4,
    "vle": 3,
    "vle-pe": 3,
    "vle-database": 3,
    "vle-oop": 3,
    "vle-coos": 3,
    "vle-prob stat": 3,
    "vle-oosad": 3,
    "vle-clp": 1,
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "at", "by",
    "with", "week", "check", "vle", "clp", "see", "due", "submission", "submit",
}


def parse_due_date(due_str):
    if not due_str:
        return date(9999, 12, 31)

    s = due_str.strip()
    for pattern in (
        r"((?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4})",
        r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})",
        r"([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4})",
        r"(\d{1,2}\.\d{1,2}\.\d{2,4})",
        r"(\d{1,2}-\d{1,2}-\d{2,4})",
        r"(\d{1,2}/\d{1,2}/\d{2,4})",
        r"(\d{4}-\d{2}-\d{2})",
    ):
        match = re.search(pattern, s)
        if match:
            s = match.group(1)
            break

    for fmt in (
        "%A, %B %d, %Y",
        "%A %B %d, %Y",
        "%A, %B %d %Y",
        "%A %B %d %Y",
        "%d %b %Y",
        "%d %B %Y",
        "%d %b %y",
        "%d %B %y",
        "%b %d, %Y",
        "%B %d, %Y",
        "%b %d %Y",
        "%B %d %Y",
        "%d.%m.%y",
        "%d.%m.%Y",
        "%d-%m-%y",
        "%d-%m-%Y",
        "%d/%m/%y",
        "%d/%m/%Y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass

    if is_generic_due(due_str):
        return date(9999, 12, 31)
    return date(9999, 12, 30)


def is_generic_due(due_str):
    if not due_str:
        return True
    upper = due_str.upper()
    return any(marker in upper for marker in GENERIC_DUE_MARKERS)


def has_concrete_due(due_str):
    parsed = parse_due_date(due_str)
    return parsed.year < 9999


def is_active_due(due_str, today=None):
    if today is None:
        today = date.today()
    parsed = parse_due_date(due_str)
    return parsed.year < 9999 and parsed >= today


def normalize_task_name(task):
    text = (task or "").strip()
    text = re.sub(r"^\[PDF/Resource\]\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^[•\-\*·]+\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,.;:-")


def task_tokens(task):
    text = normalize_task_name(task).lower()
    text = re.sub(r"\([^)]*%[^)]*\)", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return [tok for tok in text.split() if tok and tok not in STOPWORDS]


def task_numbers(task):
    text = normalize_task_name(task)
    text = re.sub(r"\(\s*\d+\s*%\s*\)", " ", text)
    text = re.sub(r"\b\d+\s*%\b", " ", text)
    return re.findall(r"\b\d+\b", text)


def task_category(task):
    lower = normalize_task_name(task).lower()
    for label, needles in (
        ("exam", ("exam", "examination")),
        ("final_exam", ("final exam", "final examination")),
        ("midterm", ("mid term", "midterm")),
        ("quiz", ("quiz",)),
        ("assignment", ("assignment",)),
        ("project", ("project",)),
        ("proposal", ("proposal",)),
        ("presentation", ("presentation", "demo")),
        ("test", ("test",)),
        ("lab", ("lab", "practical")),
        ("report", ("report",)),
    ):
        if any(needle in lower for needle in needles):
            return label
    return "other"


def tasks_match(task_a, task_b):
    a = normalize_task_name(task_a)
    b = normalize_task_name(task_b)
    if not a or not b:
        return False
    if a.lower() == b.lower():
        return True

    cat_a = task_category(a)
    cat_b = task_category(b)
    if cat_a != cat_b and "other" not in (cat_a, cat_b):
        return False

    nums_a = task_numbers(a)
    nums_b = task_numbers(b)
    if nums_a and nums_b and nums_a != nums_b:
        return False

    toks_a = set(task_tokens(a))
    toks_b = set(task_tokens(b))
    if not toks_a or not toks_b:
        return False

    overlap = len(toks_a & toks_b) / min(len(toks_a), len(toks_b))
    return overlap >= 0.6


def choose_better_task(existing_task, new_task):
    existing = normalize_task_name(existing_task)
    new = normalize_task_name(new_task)
    if not existing:
        return new
    if not new:
        return existing

    def _score(text):
        score = len(task_tokens(text)) + len(text)
        lower = text.lower()
        if "check vle" in lower:
            score -= 15
        if "week" in lower and "exam" in lower:
            score -= 8
        return score

    existing_score = _score(existing)
    new_score = _score(new)
    return new if new_score > existing_score else existing


def should_replace_due(existing_due, new_due, existing_source=None, new_source=None):
    if not new_due:
        return False
    if not existing_due:
        return True
    if new_source == "whatsapp-reschedule" and existing_source in {"whatsapp", "whatsapp-reschedule", "manual"}:
        return True
    if not has_concrete_due(existing_due) and has_concrete_due(new_due):
        return True
    if is_generic_due(existing_due) and not is_generic_due(new_due):
        return True
    return False


def choose_better_source(existing_source, new_source):
    old_rank = SOURCE_RANK.get(existing_source or "", 0)
    new_rank = SOURCE_RANK.get(new_source or "", 0)
    return new_source if new_rank >= old_rank else existing_source
