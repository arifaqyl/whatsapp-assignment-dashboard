#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path

import paramiko

from paths import SESSION_FILE


REMOTE_PATH = "/root/student-bot/storageState.json"


def _env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def main():
    host = _env("STUDENT_BOT_HOST", required=True)
    user = _env("STUDENT_BOT_USER", "root")
    password = _env("STUDENT_BOT_PASSWORD", required=True)

    local_path = Path(SESSION_FILE)
    local_path.parent.mkdir(parents=True, exist_ok=True)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=password)
    try:
        with ssh.open_sftp() as sftp:
            sftp.get(REMOTE_PATH, str(local_path))
    finally:
        ssh.close()

    print(f"Synced {REMOTE_PATH} -> {local_path}")


if __name__ == "__main__":
    main()
