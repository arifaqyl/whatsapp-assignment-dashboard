#!/usr/bin/env python3
"""
Deterministic academic dashboard.
Keeps the old module name so bot imports do not change.
"""
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from paths import DEADLINES_DB as DEADLINES_DB_PATH, MESSAGES_DB as MESSAGES_DB_PATH

from deadline_utils import (
    choose_better_source,
    choose_better_task,
    has_concrete_due,
    is_generic_due,
    parse_due_date,
    task_category,
    task_tokens,
    tasks_match,
)

DEADLINES_DB = str(DEADLINES_DB_PATH)
MESSAGES_DB = str(MESSAGES_DB_PATH)

COURSE_WA_KEYWORDS = {
    "DATABASE": ["database"],
    "OOP": ["oop", "esports roster manager"],
    "COOS": ["coos", "computer organization", "project orion"],
    "PROB STAT": ["prob stat", "probability", "statistics"],
    "OOSAD": ["oosad", "object oriented analysis"],
    "PE": ["professional english", "logistic pe", "pe group", "mock job interview"],
}

ACADEMIC_HINTS = (
    "deadline", "due", "submit", "submission", "assignment", "quiz", "test",
    "exam", "presentation", "proposal", "project", "lab", "report", "briefing",
    "postpone", "reschedule", "cancel", "replacement class", "replace class",
)

GENERIC_CLP_PREFIXES = (
    "test",
    "quiz",
    "assignment",
    "project",
    "proposal",
    "presentation",
    "practical",
    "lab",
    "project report",
    "project organisation",
    "mid term",
    "final test",
)


def get_tasks():
    try:
        conn = sqlite3.connect(DEADLINES_DB)
        rows = conn.execute(
            "SELECT id, course, task, due, source FROM deadlines WHERE status != 'Done'"
        ).fetchall()
        conn.close()
        return rows
    except sqlite3.OperationalError:
        return []


def get_wa_rows():
    cutoff = (datetime.now() - timedelta(days=21)).isoformat()
    try:
        conn = sqlite3.connect(MESSAGES_DB)
        rows = conn.execute(
            "SELECT id, group_name, sender, message, timestamp "
            "FROM messages WHERE done = 0 AND timestamp >= ? ORDER BY id DESC",
            (cutoff,)
        ).fetchall()
        conn.close()
        return rows
    except sqlite3.OperationalError:
        return []


def deduplicate_tasks(tasks):
    grouped = defaultdict(list)
    for row_id, course, task, due, *rest in tasks:
        source = rest[0] if rest else ""
        grouped[course].append({
            "ids": [str(row_id)],
            "course": course,
            "task": task,
            "due": due,
            "source": source,
            "source_set": {source},
        })

    merged_rows = []
    for course, items in grouped.items():
        used = [False] * len(items)
        for idx, item in enumerate(items):
            if used[idx]:
                continue
            used[idx] = True
            merged = item.copy()
            for jdx in range(idx + 1, len(items)):
                if used[jdx]:
                    continue
                other = items[jdx]
                if not tasks_match(merged["task"], other["task"]):
                    continue
                used[jdx] = True
                merged["ids"].extend(other["ids"])
                merged["task"] = choose_better_task(merged["task"], other["task"])
                if is_generic_due(merged["due"]) and not is_generic_due(other["due"]):
                    merged["due"] = other["due"]
                merged["source"] = choose_better_source(merged["source"], other["source"])
                merged["source_set"].add(other["source"])
            merged_rows.append(merged)

    cleaned = []
    by_course = defaultdict(list)
    for row in merged_rows:
        by_course[row["course"]].append(row)

    for course, rows in by_course.items():
        for row in rows:
            if not is_generic_due(row["due"]):
                cleaned.append(row)
                continue
            cat = task_category(row["task"])
            suppress = False
            for other in rows:
                if other is row:
                    continue
                if task_category(other["task"]) != cat:
                    continue
                if is_generic_due(other["due"]) and is_generic_due(row["due"]):
                    continue
                if tasks_match(row["task"], other["task"]):
                    suppress = True
                    break
            if not suppress:
                cleaned.append(row)

    cleaned.sort(key=lambda row: parse_due_date(row["due"]))
    final_rows = []
    for row in cleaned:
        final_rows.append((
            ",".join(row["ids"]),
            row["course"],
            row["task"],
            row["due"],
            row["source"],
        ))
    return final_rows


def _is_academic_message(message):
    lower = message.lower()
    return any(hint in lower for hint in ACADEMIC_HINTS)


def _course_for_message(group_name, message):
    text = f"{group_name} {message}".lower()
    for course, keywords in COURSE_WA_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return course
    return None


def _group_course_hint(group_name):
    lower = group_name.lower()
    for course, keywords in COURSE_WA_KEYWORDS.items():
        if any(keyword in lower for keyword in keywords):
            return course
    return None


def get_wa_by_course():
    grouped = defaultdict(list)
    for msg_id, group_name, sender, message, timestamp in get_wa_rows():
        if not _is_academic_message(message):
            continue
        course = _course_for_message(group_name, message)
        if not course:
            continue
        hinted_course = _group_course_hint(group_name)
        if hinted_course and hinted_course != course:
            continue
        grouped[course].append({
            "id": msg_id,
            "group": group_name,
            "sender": sender,
            "message": message.strip(),
            "timestamp": timestamp,
        })
    return grouped


def _task_matches_message(task, message):
    task_set = set(task_tokens(task))
    msg_set = set(task_tokens(message))
    if not task_set or not msg_set:
        return False
    task_cat = task_category(task)
    msg_cat = task_category(message)
    if task_cat != "other" and msg_cat != "other" and task_cat != msg_cat:
        return False
    overlap = len(task_set & msg_set)
    if overlap < max(2, min(4, len(task_set))):
        return False
    coverage = overlap / max(1, min(len(task_set), len(msg_set)))
    return coverage >= 0.5


def _best_wa_evidence(task, messages):
    for item in messages:
        if _task_matches_message(task, item["message"]):
            return item
    return None


def _format_due(due):
    parsed = parse_due_date(due)
    if parsed.year >= 9999:
        return due
    return parsed.strftime("%d %b %Y")


def _is_verification_task(task, due, source):
    lower = task.lower()
    if "check vle" in lower or "check vlep" in lower:
        return True
    if source == "manual" and "check" in lower:
        return True
    return False


def _is_low_signal_clp(task, due, source):
    if source != "vle-clp":
        return False
    if not is_generic_due(due):
        return False
    lower = task.lower()
    return lower.startswith(GENERIC_CLP_PREFIXES)


def _is_specific_dated_clp(task, due, source):
    if source != "vle-clp":
        return False
    if is_generic_due(due):
        return False
    lower = task.lower()
    if lower.startswith(GENERIC_CLP_PREFIXES) and task_category(task) in {"assignment", "project", "proposal", "presentation", "test", "quiz", "lab", "report"}:
        numbers = re.findall(r"\b\d+\b", task)
        if not numbers and "final examination week" not in lower and "final exam" not in lower:
            return False
    return True


def _dashboard_rank(row, wa_by_course):
    _, course, task, due, source = row
    due_date = parse_due_date(due)
    score = 0
    if source == "whatsapp":
        score += 5
    elif source == "manual":
        score += 1
    elif source == "vle-clp":
        score += 2 if _is_specific_dated_clp(task, due, source) else -3
    else:
        score += 4
    if not is_generic_due(due):
        score += 4
    if _best_wa_evidence(task, wa_by_course.get(course, [])):
        score += 1
    if "check vle" in task.lower():
        score -= 3
    if due_date.year < 9999:
        score += max(0, 20 - (due_date - date.today()).days)
    return score


def _filter_nodate_rows(nodate_rows, wa_by_course):
    return []


def _format_task_line(task_row, wa_item=None):
    id_str, course, task, due, source = task_row
    due_text = _format_due(due)
    if source == "whatsapp":
        source_tag = "WA"
    elif source == "vle-clp":
        source_tag = "CLP"
    elif source == "manual":
        source_tag = "MANUAL"
    else:
        source_tag = "VLE"
    line = f"• [{course}] {task} — {due_text} ({source_tag}) /check_{id_str.replace(',', '_')}"
    if wa_item:
        note = re.sub(r"\s+", " ", wa_item["message"]).strip()
        line += f" | WA: {note[:90]}"
    return line


def _compact_task_line(task_row, wa_item=None):
    id_str, course, task, due, source = task_row
    due_text = _format_due(due)
    if source == "whatsapp":
        source_tag = "WA"
    elif source == "vle-clp":
        source_tag = "CLP"
    elif source == "manual":
        source_tag = "MANUAL"
    else:
        source_tag = "VLE"
    text = f"[{course}] {task} — {due_text} ({source_tag}) /check_{id_str.replace(',', '_')}"
    if wa_item:
        note = re.sub(r"\s+", " ", wa_item["message"]).strip()
        text += f" | {note[:70]}"
    return text


def _agenda_bucket(due_date, today):
    days = (due_date - today).days
    if days < 0:
        return "OVERDUE"
    if days == 0:
        return "TODAY"
    if days <= 2:
        return "NEXT 48H"
    if days <= 7:
        return "THIS WEEK"
    return "LATER"


def _split_tasks(task_rows):
    today = date.today()
    urgent = []
    upcoming = []
    nodate = []
    for row in task_rows:
        if _is_verification_task(row[2], row[3], row[4]):
            nodate.append(row)
            continue
        if row[4] == "vle-clp" and not _is_specific_dated_clp(row[2], row[3], row[4]):
            nodate.append(row)
            continue
        due = parse_due_date(row[3])
        if not has_concrete_due(row[3]):
            continue
        days = (due - today).days
        if days < 0:
            continue
        if days <= 7:
            urgent.append(row)
        else:
            upcoming.append(row)
    return urgent, upcoming, nodate


def get_dashboard():
    today = date.today()
    wa_by_course = get_wa_by_course()
    tasks = deduplicate_tasks(get_tasks())
    urgent, upcoming, nodate = _split_tasks(tasks)
    nodate = _filter_nodate_rows(nodate, wa_by_course)
    urgent.sort(key=lambda row: _dashboard_rank(row, wa_by_course), reverse=True)
    upcoming.sort(key=lambda row: (parse_due_date(row[3]), -_dashboard_rank(row, wa_by_course)))
    all_rows = urgent + upcoming
    all_rows.sort(key=lambda row: (parse_due_date(row[3]), -_dashboard_rank(row, wa_by_course)))

    counts = {
        "today": sum(1 for row in all_rows if _agenda_bucket(parse_due_date(row[3]), today) == "TODAY"),
        "week": sum(1 for row in all_rows if _agenda_bucket(parse_due_date(row[3]), today) == "THIS WEEK"),
        "later": sum(1 for row in all_rows if _agenda_bucket(parse_due_date(row[3]), today) == "LATER"),
        "nodate": len(nodate),
    }

    lines = [f"📊 <b>Dashboard — {today.strftime('%d %b %Y')}</b>"]
    lines.append(
        f"At a glance: {counts['today']} today, {counts['week']} this week, {counts['later']} later"
        + (f", {counts['nodate']} need VLE check" if counts["nodate"] else "")
    )

    if all_rows:
        lines.append("")
        lines.append("🧭 <b>Next up</b>")
        grouped_rows = defaultdict(list)
        for row in all_rows:
            bucket = _agenda_bucket(parse_due_date(row[3]), today)
            grouped_rows[bucket].append(row)
        for bucket in ("TODAY", "NEXT 48H", "THIS WEEK", "LATER"):
            rows = grouped_rows.get(bucket, [])
            if not rows:
                continue
            lines.append(f"<b>{bucket}</b>")
            for row in rows[:3]:
                wa_item = _best_wa_evidence(row[2], wa_by_course.get(row[1], []))
                lines.append(_compact_task_line(row, wa_item))
            if len(rows) > 3:
                lines.append(f"• +{len(rows) - 3} more")
            lines.append("")
        if lines and lines[-1] == "":
            lines.pop()
    else:
        lines.append("")
        lines.append("• No dated tasks.")

    lines.append("")
    lines.append("⚡ <b>Focus</b>")
    concrete_priority = [row for row in urgent if row[4] != "manual"]
    if concrete_priority:
        lines.append(f"Prioritize {concrete_priority[0][1]}: {concrete_priority[0][2]}.")
    elif urgent:
        lines.append(f"Prioritize {urgent[0][1]}: {urgent[0][2]}.")
    elif upcoming:
        lines.append(f"Next dated item is {upcoming[0][1]}: {upcoming[0][2]}.")
    elif nodate:
        lines.append(f"Open VLE and verify {nodate[0][1]}: {nodate[0][2]}.")
    else:
        lines.append("Nothing pending right now.")

    matched_wa = []
    for bucket in (urgent, upcoming):
        for row in bucket:
            wa_item = _best_wa_evidence(row[2], wa_by_course.get(row[1], []))
            if wa_item:
                matched_wa.append((wa_item["timestamp"], row[1], wa_item))
    matched_wa.sort(reverse=True)
    unique_wa = []
    seen_pairs = set()
    for timestamp, course, item in matched_wa:
        key = (course, item["id"])
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        unique_wa.append((timestamp, course, item))

    if unique_wa:
        lines.append("")
        lines.append("🧾 <b>WA evidence</b>")
        for _, course, item in unique_wa[:2]:
            msg = re.sub(r"\s+", " ", item["message"]).strip()
            lines.append(f"• [{course}] {msg[:100]}")

    lines.append("")
    lines.append("<i>Grounded only in saved VLE rows and WhatsApp messages. Use /summary as the main view; /check and /done are the only action commands you really need.</i>")
    return "\n".join(lines)


if __name__ == "__main__":
    print(get_dashboard())
