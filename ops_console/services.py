from collections import defaultdict
from datetime import datetime
import json
from urllib.parse import urlparse

import db
import deadlines
from whatsapp_deadlines import sync_message

HEALTH_THRESHOLDS_HOURS = {
    "ops_console": {"warn": 1, "critical": 6},
    "webhook_receiver": {"warn": 1, "critical": 2},
    "whatsapp_promotion": {"warn": 1, "critical": 6},
    "vle_login": {"warn": 1, "critical": 6},
    "vle_scraper": {"warn": 18, "critical": 48},
    "daily_digest": {"warn": 18, "critical": 36},
}


def _format_age(ts_value):
    if not ts_value:
        return "never"
    try:
        ts = datetime.fromisoformat(ts_value)
    except ValueError:
        return ts_value
    delta = datetime.now() - ts
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _parse_iso(ts_value):
    if not ts_value:
        return None
    try:
        return datetime.fromisoformat(ts_value)
    except ValueError:
        return None


def _health_severity(row):
    now = datetime.now()
    updated_at = _parse_iso(row.get("updated_at"))
    success_at = _parse_iso(row.get("last_success_at"))
    failure_at = _parse_iso(row.get("last_failure_at"))
    last_status = row.get("last_status")
    component = row.get("component")
    thresholds = HEALTH_THRESHOLDS_HOURS.get(component, {"warn": 12, "critical": 24})

    if last_status == "error":
        return "critical"
    if not updated_at:
        return "warn"

    updated_age_hours = (now - updated_at).total_seconds() / 3600
    success_age_hours = ((now - success_at).total_seconds() / 3600) if success_at else None
    failure_newer_than_success = bool(failure_at and (not success_at or failure_at >= success_at))

    if failure_newer_than_success and updated_age_hours <= thresholds["critical"]:
        return "critical"
    if updated_age_hours >= thresholds["critical"]:
        return "critical"
    if success_age_hours is not None and success_age_hours >= thresholds["critical"]:
        return "warn"
    if updated_age_hours >= thresholds["warn"]:
        return "warn"
    if success_age_hours is None or success_age_hours >= thresholds["warn"]:
        return "warn"
    return "ok"


def _pretty_json(raw_value):
    if not raw_value:
        return None
    if isinstance(raw_value, (dict, list)):
        return json.dumps(raw_value, indent=2, sort_keys=True)
    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError):
        return None
    return json.dumps(parsed, indent=2, sort_keys=True)


def _build_url_summary(url_value):
    if not url_value:
        return None
    parsed = urlparse(url_value)
    if not parsed.scheme or not parsed.netloc:
        return None
    query_keys = sorted(k for k in parsed.query.split("&") if k) if parsed.query else []
    return {
        "host": parsed.netloc,
        "path": parsed.path or "/",
        "query_keys": query_keys,
    }


def get_queue_view(status_filter="pending", source_type=None, reason_code=None, page=1, page_size=10):
    page = max(page, 1)
    page_size = max(min(page_size, 100), 1)
    status_filter = status_filter or "pending"
    if status_filter == "all":
        status_filter = None
    total_items = db.count_queue_items(status=status_filter, source_type=source_type, reason_code=reason_code)
    offset = (page - 1) * page_size
    items = db.get_queue_items(
        status=status_filter,
        source_type=source_type,
        reason_code=reason_code,
        limit=page_size,
        offset=offset,
    )
    counts = db.get_queue_counts()
    grouped_counts = defaultdict(int)
    status_totals = {"pending": 0, "resolved": 0, "dismissed": 0}
    for row in counts:
        if row["status"] in status_totals:
            status_totals[row["status"]] += row["count"]
        if row["status"] != "pending":
            continue
        grouped_counts[(row["source_type"], row["reason_code"])] += row["count"]
    for item in items:
        item["created_age"] = _format_age(item["created_at"])
        item["updated_age"] = _format_age(item["updated_at"])
    return {
        "items": items,
        "status_filter": status_filter or "all",
        "page": page,
        "page_size": page_size,
        "total_items": total_items,
        "total_pages": max((total_items + page_size - 1) // page_size, 1),
        "has_prev": page > 1,
        "has_next": offset + len(items) < total_items,
        "status_totals": {
            **status_totals,
            "all": sum(status_totals.values()),
        },
        "counts": sorted(
            (
                {"source_type": src, "reason_code": reason, "count": count}
                for (src, reason), count in grouped_counts.items()
            ),
            key=lambda row: (-row["count"], row["source_type"], row["reason_code"]),
        ),
    }


def get_queue_item_view(queue_item_id):
    item = db.get_queue_item(queue_item_id)
    if not item:
        return None
    item["created_age"] = _format_age(item["created_at"])
    item["updated_age"] = _format_age(item["updated_at"])
    source_row = None
    source_reference = None
    if item["source_type"] == "whatsapp" and item["source_row_id"]:
        source_row = db.get_message_row(item["source_row_id"])
        if source_row:
            source_row["raw_json_pretty"] = _pretty_json(source_row.get("raw_json"))
    elif item["source_type"] == "vle":
        source_reference = {
            "kind": "resource_url",
            "value": item["message"],
            "label": item["title"] or "VLE resource",
            "url_summary": _build_url_summary(item["message"]),
        }
    actions = db.get_operator_actions_for_item(queue_item_id)
    for action in actions:
        action["created_age"] = _format_age(action["created_at"])
        action["action_payload_pretty"] = _pretty_json(action.get("action_payload"))
    return {
        "item": item,
        "source_row": source_row,
        "source_reference": source_reference,
        "actions": actions,
    }


def _retry_vle_due_lookup(item):
    from vle_scraper import retry_due_lookup

    return retry_due_lookup(
        item.get("message"),
        task_name=item.get("title") or item.get("proposed_task") or "",
    )


def dismiss_queue_item(queue_item_id, actor="operator"):
    item = db.get_queue_item(queue_item_id)
    if not item:
        return False, "missing"
    db.update_queue_item(queue_item_id, status="dismissed", last_error=None)
    db.record_operator_action(
        queue_item_id,
        "dismiss",
        actor=actor,
        action_payload=json.dumps({"from_status": item["status"]}),
    )
    return True, "dismissed"


def approve_queue_item(queue_item_id, *, task, course, due, actor="operator"):
    item = db.get_queue_item(queue_item_id)
    if not item:
        return False, "missing"
    if not task or not course or not due:
        return False, "missing_fields"
    row_id, status = deadlines.add(task.strip(), course.strip(), due.strip(), source="operator")
    db.update_queue_item(
        queue_item_id,
        status="resolved",
        proposed_task=task.strip(),
        proposed_due=due.strip(),
        last_error=None,
    )
    db.record_operator_action(
        queue_item_id,
        "approve",
        actor=actor,
        action_payload=json.dumps(
            {
                "deadline_row_id": row_id,
                "deadline_status": status,
                "task": task.strip(),
                "course": course.strip(),
                "due": due.strip(),
            }
        ),
    )
    return True, status


def retry_queue_item(queue_item_id, actor="operator"):
    item = db.get_queue_item(queue_item_id)
    if not item:
        return False, "missing"

    if item["source_type"] == "whatsapp" and item["source_row_id"]:
        msg = db.get_message_row(item["source_row_id"])
        if not msg:
            db.update_queue_item(queue_item_id, last_error="missing message row for retry")
            return False, "missing_message"
        created = sync_message(msg["group_name"], msg["message"], msg["timestamp"])
        if created:
            best = created[0]
            db.update_queue_item(
                queue_item_id,
                status="resolved",
                proposed_task=best[3],
                proposed_due=best[4],
                last_error=None,
            )
            db.record_operator_action(
                queue_item_id,
                "retry_resolved",
                actor=actor,
                action_payload=json.dumps({"created": created}),
            )
            return True, "resolved"
        db.update_queue_item(queue_item_id, last_error="retry produced no deadline")
        db.record_operator_action(
            queue_item_id,
            "retry_no_change",
            actor=actor,
            action_payload=json.dumps({"source_type": item["source_type"]}),
        )
        return False, "no_change"

    if item["source_type"] == "vle" and item["message"]:
        due = _retry_vle_due_lookup(item)
        if due:
            task = (item["proposed_task"] or item["title"] or "").strip()
            course = (item["course"] or "").strip()
            if not task or not course:
                db.update_queue_item(queue_item_id, proposed_due=due, last_error="retry found due but item is missing task/course")
                db.record_operator_action(
                    queue_item_id,
                    "retry_incomplete",
                    actor=actor,
                    action_payload=json.dumps({"source_type": item["source_type"], "due": due}),
                )
                return False, "missing_fields"
            row_id, status = deadlines.add(task, course, due, source="vle-retry")
            db.update_queue_item(
                queue_item_id,
                status="resolved",
                proposed_task=task,
                proposed_due=due,
                last_error=None,
            )
            db.record_operator_action(
                queue_item_id,
                "retry_resolved",
                actor=actor,
                action_payload=json.dumps(
                    {
                        "source_type": item["source_type"],
                        "deadline_row_id": row_id,
                        "deadline_status": status,
                        "task": task,
                        "course": course,
                        "due": due,
                    }
                ),
            )
            return True, "resolved"
        db.update_queue_item(queue_item_id, last_error="retry could not recover a due date from the saved VLE reference")
        db.record_operator_action(
            queue_item_id,
            "retry_no_change",
            actor=actor,
            action_payload=json.dumps({"source_type": item["source_type"]}),
        )
        return False, "no_change"

    db.update_queue_item(queue_item_id, last_error="retry not supported for this source type")
    db.record_operator_action(
        queue_item_id,
        "retry_unsupported",
        actor=actor,
        action_payload=json.dumps({"source_type": item["source_type"]}),
    )
    return False, "unsupported"


def get_health_view():
    health_rows = db.get_system_health()
    unhealthy_components = []
    severity_totals = {"critical": 0, "warn": 0, "ok": 0}
    for row in health_rows:
        row["updated_age"] = _format_age(row["updated_at"])
        row["success_age"] = _format_age(row["last_success_at"])
        row["failure_age"] = _format_age(row["last_failure_at"])
        row["severity"] = _health_severity(row)
        severity_totals[row["severity"]] += 1
        if row["severity"] != "ok":
            unhealthy_components.append(row)

    deadlines_count = len(deadlines.get_all())
    recent_actions = db.get_recent_operator_actions(limit=20)
    for row in recent_actions:
        row["created_age"] = _format_age(row["created_at"])

    grouped_counts = defaultdict(int)
    for row in db.get_queue_counts():
        if row["status"] != "pending":
            continue
        grouped_counts[(row["source_type"], row["reason_code"])] += row["count"]
    attention_counts = sorted(
        (
            {"source_type": src, "reason_code": reason, "count": count}
            for (src, reason), count in grouped_counts.items()
        ),
        key=lambda row: (-row["count"], row["source_type"], row["reason_code"]),
    )[:6]

    return {
        "health_rows": health_rows,
        "unhealthy_components": unhealthy_components,
        "severity_totals": severity_totals,
        "attention_counts": attention_counts,
        "recent_actions": recent_actions,
        "deadlines_count": deadlines_count,
        "pending_messages": len(db.get_recent_pending()),
        "old_pending_messages": db.count_old_pending(),
        "pending_queue_items": len(db.get_queue_items(limit=500)),
    }
