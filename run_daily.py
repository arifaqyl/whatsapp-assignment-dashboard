#!/usr/bin/env python3
import os
import re
import sqlite3
import requests
from datetime import datetime, timedelta, date

import db as ops_db
from deadline_utils import is_active_due, parse_due_date
from gemini_dashboard import deduplicate_tasks
from config import BOT_TOKEN, CHAT_ID
from paths import DEADLINES_DB as DEADLINES_DB_PATH, MESSAGES_DB as MESSAGES_DB_PATH

DB_PATH = str(MESSAGES_DB_PATH)
DEADLINES_DB = str(DEADLINES_DB_PATH)
MYT_OFFSET = timedelta(hours=8)

COURSE_MAP = {
    "database": "DATABASE",
    "group project oop": "OOP",
    "coos": "COOS",
    "prob stat": "PROB STAT",
    "oosad": "OOSAD",
    "professional english": "PE",
    "project proposal pe group": "PE",
    "logistic pe": "PE",
}

STUDENT_TIMETABLE = {
    "DATABASE": {"days": {0, 4}, "groups": {"L01", "L01-B01"}},
    "OOP": {"days": {0, 4}, "groups": {"L01", "L01-B01"}},
    "COOS": {"days": {0, 3}, "groups": {"L02", "L02-B03"}},
    "PROB STAT": {"days": {0, 3}, "groups": {"L01", "L01-T01"}},
    "OOSAD": {"days": {1, 3}, "groups": {"L01", "L01-B01"}},
    "PE": {"days": {2}, "groups": {"L07"}},
}

WEEKDAY_INDEX = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
    "isnin": 0, "selasa": 1, "rabu": 2, "khamis": 3, "jumaat": 4, "sabtu": 5, "ahad": 6,
}


def utc_naive_to_myt(dt_iso):
    return datetime.fromisoformat(dt_iso) + MYT_OFFSET


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for chunk in [text[i:i + 4000] for i in range(0, len(text), 4000)]:
        r = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": chunk,
            "disable_web_page_preview": True,
        }, timeout=15)
        r.raise_for_status()


def get_active_deadlines():
    conn = sqlite3.connect(DEADLINES_DB)
    rows = conn.execute(
        "SELECT id, course, task, due, source FROM deadlines WHERE status != 'Done'"
    ).fetchall()
    conn.close()
    cleaned = []
    for id_str, course, task, due, source in deduplicate_tasks(rows):
        if not is_active_due(due):
            continue
        cleaned.append((id_str, course, task, due, source))
    cleaned.sort(key=lambda row: parse_due_date(row[3]))
    return cleaned


def get_recent_messages(hours=36):
    if not os.path.exists(DB_PATH):
        return []
    conn = sqlite3.connect(DB_PATH)
    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        "SELECT timestamp, group_name, sender, message FROM messages WHERE timestamp >= ? ORDER BY timestamp DESC",
        (since,)
    ).fetchall()
    conn.close()
    return rows


def infer_course(group_name):
    lower = (group_name or "").lower()
    for needle, course in COURSE_MAP.items():
        if needle in lower:
            return course
    return group_name


def resolve_target_date(message, msg_myt):
    lower = message.lower()
    base = msg_myt.date()

    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", lower)
    if m:
        day = int(m.group(1))
        month = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else base.year
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            pass

    if re.search(r"\btoday\b|\bhari ni\b|\bharini\b", lower):
        return base
    if re.search(r"\btomorrow\b|\besok\b|\besk\b", lower):
        return base + timedelta(days=1)

    for name, idx in WEEKDAY_INDEX.items():
        if re.search(rf"\b{name}\b", lower):
            delta = (idx - base.weekday()) % 7
            if delta == 0:
                delta = 7
            return base + timedelta(days=delta)

    return None


def relative_label(target, today):
    if not target:
        return None
    if target == today:
        return "today"
    if target == today + timedelta(days=1):
        return "tomorrow"
    return target.strftime("%a, %d %b %Y")


def extract_class_changes(messages, today):
    items = []
    seen = set()
    for ts, group, sender, message in messages:
        lower = message.lower()
        if not any(needle in lower for needle in ("no class", "online class", "conducted online", "on leave", "replacement class", "replace class", "reschedule", "postpone", "f2f")):
            continue

        msg_myt = utc_naive_to_myt(ts)
        target = resolve_target_date(message, msg_myt)
        label = relative_label(target, today)
        course = infer_course(group)
        if course not in STUDENT_TIMETABLE:
            continue
        if target and target.weekday() not in STUDENT_TIMETABLE[course]["days"]:
            continue

        expected_groups = STUDENT_TIMETABLE[course]["groups"]
        mentioned_groups = set(re.findall(r"\b(?:L\d{2}(?:-B\d{2}|-T\d{2})?|B\d{2}|T\d{2})\b", message.upper()))
        if mentioned_groups:
            if not any(
                mg in eg or eg in mg or mg == eg.split("-")[-1]
                for mg in mentioned_groups
                for eg in expected_groups
            ):
                continue

        if "no class" in lower or "on leave" in lower:
            summary = f"[{course}] No class {label}" if label else f"[{course}] No class"
        elif "online" in lower:
            summary = f"[{course}] Class is online {label}" if label else f"[{course}] Class is online"
        elif "f2f" in lower:
            summary = f"[{course}] Face-to-face session {label}" if label else f"[{course}] Face-to-face session"
        else:
            summary = f"[{course}] Class change {label}" if label else f"[{course}] Class change"

        summary = summary.rstrip()
        key = (course, target, summary)
        if key in seen:
            continue
        seen.add(key)
        items.append((target or date.max, summary, message.strip()))

    items.sort(key=lambda item: item[0])
    return [summary for _, summary, _ in items[:5]]


def extract_opportunities(messages, today):
    items = []
    seen = set()
    for ts, group, sender, message in messages:
        lower = message.lower()
        has_ecert = any(w in lower for w in ("e-cert", "ecert", "certificate", "sijil"))
        is_online = any(w in lower for w in ("online", "zoom", "google meet", "gmeet", "teams", "webinar"))
        is_free = any(w in lower for w in ("free", "percuma", "no fee"))
        if not (has_ecert and is_online and is_free):
            continue

        msg_myt = utc_naive_to_myt(ts)
        target = resolve_target_date(message, msg_myt)
        if target and target < today:
            continue
        course = infer_course(group)
        label = relative_label(target, today)
        title = re.sub(r"\s+", " ", message.splitlines()[0].strip())
        summary = f"[{course}] {title[:80]}"
        if label:
            summary += f" — {label}"
        if summary in seen:
            continue
        seen.add(summary)
        items.append((target or date.max, summary))

    items.sort(key=lambda item: item[0])
    return [summary for _, summary in items[:4]]


def build_digest(deadlines, messages):
    today = (datetime.utcnow() + MYT_OFFSET).date()
    urgent, this_week, upcoming = [], [], []

    for id_str, course, task, due, source in deadlines:
        due_date = parse_due_date(due)
        days = (due_date - today).days
        line = f"[{course}] {task} -> {due_date.strftime('%a, %d %B %Y')}"
        if days <= 2:
            urgent.append(line)
        elif days <= 7:
            this_week.append(line)
        else:
            upcoming.append(line)

    class_changes = extract_class_changes(messages, today)
    opportunities = extract_opportunities(messages, today)

    lines = [f"🗓️ {today.strftime('%A, %d %B %Y')}", ""]
    lines.append("URGENT")
    lines.extend(urgent or ["No items due today."])
    lines.append("")
    lines.append("THIS WEEK")
    lines.extend(this_week or ["Nothing else due this week."])
    lines.append("")
    lines.append("UPCOMING")
    lines.extend(upcoming or ["No upcoming dated items."])
    lines.append("")
    lines.append("CLASS CHANGES")
    lines.extend(class_changes or ["No class changes reported."])
    lines.append("")
    lines.append("OPPORTUNITIES")
    lines.extend(opportunities or ["None."])
    return "\n".join(lines)


def main():
    try:
        ops_db.init()
        deadlines = get_active_deadlines()
        messages = get_recent_messages()
        text = build_digest(deadlines, messages)
        send_telegram(text)
        ops_db.record_system_health(
            "daily_digest",
            "ok",
            f"deadlines={len(deadlines)}; messages={len(messages)}",
        )
        print(f"[{datetime.utcnow()}] Sent deterministic digest.", flush=True)
    except Exception as exc:
        ops_db.record_system_health("daily_digest", "error", str(exc))
        raise


if __name__ == "__main__":
    main()
