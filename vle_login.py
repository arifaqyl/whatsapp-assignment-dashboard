from __future__ import annotations

import threading

import db as ops_db
from get_session import login_and_save

login_state = {
    "status": "idle",
    "code": None,
    "message": "",
    "error": "",
    "thread": None,
}


def _run_login() -> None:
    try:
        login_state["status"] = "running"
        login_state["code"] = None
        login_state["error"] = ""
        login_state["message"] = "Starting login flow"
        ops_db.record_system_health("vle_login", "running", "running: Starting login flow")
        login_and_save(login_state)
        login_state["status"] = "done"
        login_state["message"] = "VLE session refreshed"
    except Exception as exc:  # pragma: no cover - surfaced through bot logs
        login_state["status"] = "error"
        login_state["error"] = str(exc)
        login_state["message"] = str(exc)
        ops_db.record_system_health("vle_login", "error", f"error: {exc}")


def start_login_thread() -> bool:
    thread = login_state.get("thread")
    if thread and thread.is_alive():
        return False

    worker = threading.Thread(target=_run_login, daemon=True)
    login_state["thread"] = worker
    login_state["status"] = "starting"
    login_state["message"] = "Login thread starting"
    login_state["error"] = ""
    ops_db.record_system_health("vle_login", "starting", "starting: Login thread starting")
    worker.start()
    return True
