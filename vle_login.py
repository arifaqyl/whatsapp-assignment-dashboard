from __future__ import annotations

import threading

from get_session import login_and_save

login_state = {
    "status": "idle",
    "code": None,
    "thread": None,
}


def _run_login() -> None:
    try:
        login_state["status"] = "running"
        login_state["code"] = None
        login_and_save()
        login_state["status"] = "done"
    except Exception as exc:  # pragma: no cover - surfaced through bot logs
        login_state["status"] = "error"
        login_state["error"] = str(exc)


def start_login_thread() -> bool:
    thread = login_state.get("thread")
    if thread and thread.is_alive():
        return False

    worker = threading.Thread(target=_run_login, daemon=True)
    login_state["thread"] = worker
    login_state["status"] = "starting"
    worker.start()
    return True
