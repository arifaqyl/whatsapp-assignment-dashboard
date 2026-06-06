#!/usr/bin/env python3
import json
import os
import time
from datetime import datetime, timedelta

import run_daily
from paths import ROOT

STATE_FILE = str(ROOT / ".daily_digest_state.json")
MYT_OFFSET = timedelta(hours=8)
TARGET_HOUR = 8
TARGET_MINUTE = 0


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def should_run(now_myt, state):
    last_run = state.get("last_run_date")
    today_str = now_myt.strftime("%Y-%m-%d")
    if last_run == today_str:
        return False
    return (now_myt.hour, now_myt.minute) >= (TARGET_HOUR, TARGET_MINUTE)


def main():
    while True:
        now_myt = datetime.utcnow() + MYT_OFFSET
        state = load_state()
        if should_run(now_myt, state):
            run_daily.main()
            state["last_run_date"] = now_myt.strftime("%Y-%m-%d")
            save_state(state)
        time.sleep(30)


if __name__ == "__main__":
    main()
