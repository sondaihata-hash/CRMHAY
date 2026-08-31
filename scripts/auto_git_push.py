import os
import subprocess
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
POLL_SECONDS = 5
COMMIT_MESSAGE = "chore: auto-commit on code change"


def run(cmd):
    return subprocess.run(cmd, cwd=str(REPO_DIR), capture_output=True, text=True)


def has_uncommitted_changes():
    result = run(["git", "status", "--porcelain"])
    return bool(result.stdout.strip())


def ensure_user_identity():
    user_name = run(["git", "config", "user.name"]).stdout.strip()
    user_email = run(["git", "config", "user.email"]).stdout.strip()
    if not user_name:
        run(["git", "config", "user.name", "Auto Commit Bot"])
    if not user_email:
        run(["git", "config", "user.email", "autocommit@local.dev"])


def commit_and_push():
    ensure_user_identity()
    add_result = run(["git", "add", "."])
    if add_result.returncode != 0:
        print("git add failed:", add_result.stderr)
        return

    status_result = run(["git", "status", "--porcelain"])
    if not status_result.stdout.strip():
        return

    commit_result = run(["git", "commit", "-m", COMMIT_MESSAGE])
    if commit_result.returncode != 0:
        print("git commit failed:", commit_result.stderr)
        return

    push_result = run(["git", "push", "origin", "HEAD"])
    if push_result.returncode != 0:
        print("git push failed:", push_result.stderr)
        return

    print("Auto push OK")


if __name__ == "__main__":
    print(f"Watching repo: {REPO_DIR}")
    while True:
        try:
            if has_uncommitted_changes():
                commit_and_push()
        except Exception as exc:
            print(f"Watcher error: {exc}")
        time.sleep(POLL_SECONDS)
