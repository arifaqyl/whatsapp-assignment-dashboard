#!/usr/bin/env python3
import os
from pathlib import Path
import sys

import paramiko


ROOT = Path(__file__).resolve().parent
REMOTE_ROOT = "/root/student-bot"
FILES_TO_SYNC = [
    "bot.py",
    "deadline_utils.py",
    "get_session.py",
    "ops_console/__init__.py",
    "ops_console/app.py",
    "ops_console/routes.py",
    "ops_console/services.py",
    "ops_console/templates/layout.html",
    "ops_console/templates/queue.html",
    "ops_console/templates/queue_item.html",
    "ops_console/templates/health.html",
    "db.py",
    "deadlines.py",
    "whatsapp_deadlines.py",
    "webhook_receiver.py",
    "vle_scraper.py",
    "run_daily.py",
    "run_ops_console.py",
    "ecosystem.config.js",
    "requirements.txt",
    "vle_login.py",
    "docs/ops-console-deploy.md",
]


def _env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def build_remote_commands():
    return [
        f"cd {REMOTE_ROOT} && python3 -m pip install --break-system-packages -r requirements.txt",
        f"cd {REMOTE_ROOT} && pm2 start ecosystem.config.js --only ops-console",
        f"cd {REMOTE_ROOT} && pm2 restart ops-console --update-env",
        f"cd {REMOTE_ROOT} && pm2 restart student-bot",
        f"cd {REMOTE_ROOT} && pm2 restart webhook-receiver",
        "pm2 save",
        "curl -f http://127.0.0.1:8091/api/ping",
        "pm2 status ops-console",
        "pm2 status student-bot",
        "pm2 status webhook-receiver",
    ]


def print_plan():
    print("Files to sync:")
    for rel in FILES_TO_SYNC:
        print(f" - {rel}")
    print("\nRemote commands:")
    for cmd in build_remote_commands():
        print(f" - {cmd}")


def upload_files(sftp):
    for rel in FILES_TO_SYNC:
        local_path = ROOT / rel
        remote_path = f"{REMOTE_ROOT}/{rel.replace(os.sep, '/')}"
        remote_dir = remote_path.rsplit("/", 1)[0]
        _mkdir_p(sftp, remote_dir)
        sftp.put(str(local_path), remote_path)


def _mkdir_p(sftp, remote_directory):
    parts = remote_directory.strip("/").split("/")
    path = ""
    for part in parts:
        path += "/" + part
        try:
            sftp.stat(path)
        except IOError:
            sftp.mkdir(path)


def run_remote(ssh):
    stdout_stream = getattr(sys.stdout, "buffer", None)
    stderr_stream = getattr(sys.stderr, "buffer", None)
    for cmd in build_remote_commands():
        stdin, stdout, stderr = ssh.exec_command(cmd)
        code = stdout.channel.recv_exit_status()
        out_bytes = stdout.read()
        err_bytes = stderr.read()
        out = out_bytes.decode("utf-8", errors="replace").strip()
        err = err_bytes.decode("utf-8", errors="replace").strip()
        print(f"$ {cmd}")
        if out:
            if stdout_stream:
                stdout_stream.write(out_bytes)
                stdout_stream.write(b"\n")
            else:
                print(out)
        if err:
            if stderr_stream:
                stderr_stream.write(err.encode("utf-8", errors="replace"))
                stderr_stream.write(b"\n")
            else:
                print(err)
        if code != 0:
            raise SystemExit(f"Remote command failed with exit code {code}: {cmd}")


def main():
    dry_run = os.getenv("OPS_DEPLOY_DRY_RUN", "1") != "0"
    host = _env("STUDENT_BOT_HOST", required=not dry_run)
    user = _env("STUDENT_BOT_USER", "root")
    password = _env("STUDENT_BOT_PASSWORD", required=not dry_run)

    if dry_run:
        print_plan()
        return

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=password)
    try:
        with ssh.open_sftp() as sftp:
            upload_files(sftp)
        run_remote(ssh)
    finally:
        ssh.close()


if __name__ == "__main__":
    main()
