#!/usr/bin/env python3
"""Pick a todo file with fzf, then run codex-todo."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TODO_SUFFIXES = {".md", ".markdown", ".txt", ".todo"}
PREVIEW_CMD = (
    "if command -v bat >/dev/null 2>&1; then "
    "bat --style=numbers --color=always --line-range=:200 -- \"{}\"; "
    "else sed -n '1,200p' \"{}\"; fi"
)
DEFAULT_PATH_SEGMENTS = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
]


def pause_for_popup() -> None:
    try:
        input("Press Enter to close...")
    except EOFError:
        pass


def die(message: str, *, exit_code: int = 1) -> "None":
    print(f"ERROR: {message}", file=sys.stderr)
    pause_for_popup()
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


def run_command(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, cwd=cwd)


def get_start_dir() -> Path:
    result = run_command(["tmux", "display-message", "-p", "#{pane_current_path}"])
    if result.returncode == 0:
        text = result.stdout.strip()
        if text:
            path = Path(text)
            if path.is_dir():
                return path
    return Path.cwd()


def list_files_with_rg(start_dir: Path) -> list[str] | None:
    if shutil.which("rg") is None:
        return None
    rg = run_command(["rg", "--files"], cwd=start_dir)
    if rg.returncode != 0:
        details = (rg.stderr or rg.stdout).strip()
        raise RuntimeError(f"failed to enumerate files with rg: {details}")
    return [line.strip() for line in rg.stdout.splitlines() if line.strip()]


def list_files_with_find(start_dir: Path) -> list[str]:
    find_result = run_command(
        ["find", ".", "-path", "./.git", "-prune", "-o", "-type", "f", "-print"],
        cwd=start_dir,
    )
    if find_result.returncode != 0:
        details = (find_result.stderr or find_result.stdout).strip()
        raise RuntimeError(f"failed to enumerate files with find: {details}")

    files: list[str] = []
    for line in find_result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("./"):
            stripped = stripped[2:]
        if stripped:
            files.append(stripped)
    return files


def list_files(start_dir: Path) -> list[str]:
    rg_files = list_files_with_rg(start_dir)
    if rg_files is not None:
        return rg_files
    return list_files_with_find(start_dir)


def score_candidate(relative_path: str) -> tuple[int, str]:
    path_lower = relative_path.lower()
    name_lower = Path(relative_path).name.lower()

    if "/todos/" in f"/{path_lower}" or path_lower.startswith("todos/"):
        return (0, relative_path)
    if "todo" in name_lower:
        return (1, relative_path)
    if Path(relative_path).suffix.lower() in TODO_SUFFIXES:
        return (2, relative_path)
    return (3, relative_path)


def select_candidates(paths: list[str]) -> list[str]:
    if not paths:
        return []

    scored = sorted(paths, key=score_candidate)
    best_bucket = score_candidate(scored[0])[0]

    if best_bucket <= 1:
        return [path for path in scored if score_candidate(path)[0] <= 2]
    if best_bucket == 2:
        return [path for path in scored if score_candidate(path)[0] == 2]
    return scored


def fzf_pick(candidates: list[str], *, cwd: Path) -> str | None:
    candidates_path: Path | None = None
    output_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="codex-todo-candidates.", delete=False
        ) as candidates_file:
            candidates_file.write("\n".join(candidates))
            candidates_file.write("\n")
            candidates_path = Path(candidates_file.name)

        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="codex-todo-selected.", delete=False
        ) as output_file:
            output_path = Path(output_file.name)

        with candidates_path.open("r", encoding="utf-8") as stdin_file, output_path.open(
            "w", encoding="utf-8"
        ) as stdout_file:
            result = subprocess.run(
                [
                    "fzf",
                    "--prompt",
                    "todo> ",
                    "--height",
                    "100%",
                    "--layout",
                    "reverse",
                    "--preview-window",
                    "right:60%:wrap",
                    "--preview",
                    PREVIEW_CMD,
                ],
                stdin=stdin_file,
                stdout=stdout_file,
                check=False,
                cwd=cwd,
            )

        if result.returncode != 0:
            return None

        picked = output_path.read_text(encoding="utf-8").strip()
        return picked or None
    finally:
        if candidates_path is not None:
            candidates_path.unlink(missing_ok=True)
        if output_path is not None:
            output_path.unlink(missing_ok=True)


def run_codex_todo(todo_file: Path) -> int:
    runner = Path.home() / ".tmux" / "scripts" / "codex-todo.sh"
    if not runner.is_file():
        raise RuntimeError(f"Missing runner script: {runner}")
    result = subprocess.run([str(runner), str(todo_file)], check=False)
    return result.returncode


def main() -> int:
    try:
        ensure_reasonable_path()

        require_dependency("tmux")
        require_dependency("find")
        require_dependency("fzf")

        start_dir = get_start_dir()
        files = list_files(start_dir)
        candidates = select_candidates(files)
        if not candidates:
            die(f"No candidate files found under {start_dir}")

        picked = fzf_pick(candidates, cwd=start_dir)
        if picked is None:
            return 0

        todo_path = (start_dir / picked).resolve()
        if not todo_path.is_file():
            die(f"Selected file does not exist: {todo_path}")

        exit_code = run_codex_todo(todo_path)
        if exit_code != 0:
            pause_for_popup()
        return exit_code
    except RuntimeError as exc:
        die(str(exc))
    except KeyboardInterrupt:
        die("Cancelled by user")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
