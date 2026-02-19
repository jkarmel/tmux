#!/usr/bin/env python3
"""Launch a todo in a tmux window with worktree-based execution."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_PATH_SEGMENTS = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
]
RUNNER_SCRIPT = Path.home() / ".tmux" / "scripts" / "codex-todo-runner.py"


def die(message: str, *, exit_code: int = 1) -> "None":
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


def ensure_reasonable_path() -> None:
    current = os.environ.get("PATH", "")
    parts = [part for part in current.split(":") if part]
    for segment in DEFAULT_PATH_SEGMENTS:
        if segment not in parts:
            parts.append(segment)
    os.environ["PATH"] = ":".join(parts)


def require_dependency(command: str) -> None:
    if shutil.which(command) is None:
        raise RuntimeError(f"'{command}' is required but not found in PATH")


def usage() -> str:
    return (
        "Usage: codex-todo.sh <todo-file>\n\n"
        "Runs Codex for the todo in an isolated git worktree and attempts strict-gate auto-merge.\n"
        "On merge-gate failure, writes merge_gate_failure.md and opens a merge helper Codex session."
    )


def run_capture(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, cwd=cwd)


def resolve_todo(path_arg: str) -> Path:
    todo_path = Path(path_arg).expanduser().resolve()
    if not todo_path.is_file():
        raise RuntimeError(f"Todo file not found: {path_arg}")
    return todo_path


def git_value(repo_root: Path, args: list[str]) -> str:
    result = run_capture(["git", "-C", str(repo_root), *args])
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {details}")

    value = result.stdout.strip()
    if not value:
        raise RuntimeError(f"git {' '.join(args)} returned an empty value")
    return value


def resolve_repo_root(start_dir: Path) -> Path:
    root_text = git_value(start_dir, ["rev-parse", "--show-toplevel"])
    root = Path(root_text).resolve()
    if not root.is_dir():
        raise RuntimeError(f"Resolved repo root is not a directory: {root}")
    return root


def tmux_value(format_string: str) -> str:
    result = run_capture(["tmux", "display-message", "-p", format_string])
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"tmux display-message failed: {details}")

    value = result.stdout.strip()
    if not value:
        raise RuntimeError(f"tmux returned an empty value for format: {format_string}")
    return value


def build_runner_command(
    repo_root: Path,
    launch_dir: Path,
    todo_path: Path,
    target_branch: str,
    target_revision: str,
    session_id: str,
) -> str:
    parts = [
        "python3",
        str(RUNNER_SCRIPT),
        "--repo-root",
        str(repo_root),
        "--launch-dir",
        str(launch_dir),
        "--todo",
        str(todo_path),
        "--target-branch",
        target_branch,
        "--target-revision",
        target_revision,
        "--session-id",
        session_id,
    ]
    return " ".join(shlex.quote(part) for part in parts)


def open_tmux_window(session_id: str, window_name: str, runner_command: str) -> str:
    tmux_command = f"bash -lc {shlex.quote(runner_command)}"
    result = run_capture(
        [
            "tmux",
            "new-window",
            "-P",
            "-F",
            "#{window_id}",
            "-t",
            session_id,
            "-n",
            window_name,
            tmux_command,
        ]
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"tmux new-window failed: {details}")

    window_id = result.stdout.strip()
    if not window_id:
        raise RuntimeError("tmux did not return a window id for the new window")
    return window_id


def display_tmux_message(message: str) -> None:
    run_capture(["tmux", "display-message", message])


def run(argv: list[str]) -> int:
    if len(argv) != 1:
        print(usage(), file=sys.stderr)
        return 2

    if not os.environ.get("TMUX"):
        raise RuntimeError("Run this command from inside a tmux session")

    ensure_reasonable_path()
    require_dependency("tmux")
    require_dependency("git")
    require_dependency("python3")

    if not RUNNER_SCRIPT.is_file():
        raise RuntimeError(f"Missing runner script: {RUNNER_SCRIPT}")

    todo_path = resolve_todo(argv[0])
    launch_dir = Path.cwd().resolve()
    repo_root = resolve_repo_root(launch_dir)
    target_branch = git_value(repo_root, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    target_revision = git_value(repo_root, ["rev-parse", "HEAD"])

    window_name = f"{todo_path.stem} (codex)"
    session_id = tmux_value("#{session_id}")
    runner_command = build_runner_command(
        repo_root,
        launch_dir,
        todo_path,
        target_branch,
        target_revision,
        session_id,
    )

    window_id = open_tmux_window(session_id, window_name, runner_command)
    display_tmux_message(
        f"Started {window_name} ({window_id}) in session {session_id} with todo: {todo_path}"
    )
    return 0


def main() -> int:
    try:
        return run(sys.argv[1:])
    except RuntimeError as exc:
        die(str(exc))
    except KeyboardInterrupt:
        die("Cancelled by user")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
