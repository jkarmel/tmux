#!/usr/bin/env python3
"""Execute a todo inside a dedicated worktree, then strict-gate auto-merge."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_PATH_SEGMENTS = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
]
WORKTREES_DIR = Path.home() / ".tmux" / "worktrees"


@dataclass
class GateFailure:
    stage: str
    reason: str
    command: str | None = None
    stdout: str = ""
    stderr: str = ""


def pause_for_window() -> None:
    try:
        print("")
        input("Press Enter to close...")
    except EOFError:
        pass


def die(message: str, *, exit_code: int = 1) -> "None":
    print(f"ERROR: {message}", file=sys.stderr)
    pause_for_window()
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


def run_capture(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, cwd=cwd)


def run_checked(
    command: list[str], *, stage: str, reason: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    result = run_capture(command, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(
            GateFailure(
                stage=stage,
                reason=reason,
                command=" ".join(command),
                stdout=result.stdout,
                stderr=result.stderr,
            )
        )
    return result


def git_value(path: Path, args: list[str], *, stage: str, reason: str) -> str:
    result = run_capture(["git", "-C", str(path), *args])
    if result.returncode != 0:
        raise RuntimeError(
            GateFailure(
                stage=stage,
                reason=reason,
                command=f"git -C {path} {' '.join(args)}",
                stdout=result.stdout,
                stderr=result.stderr,
            )
        )

    value = result.stdout.strip()
    if not value:
        raise RuntimeError(
            GateFailure(
                stage=stage,
                reason=f"{reason} (empty output)",
                command=f"git -C {path} {' '.join(args)}",
                stdout=result.stdout,
                stderr=result.stderr,
            )
        )
    return value


def git_status_porcelain(path: Path) -> str:
    result = run_capture(["git", "-C", str(path), "status", "--porcelain"])
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"git status failed in {path}: {details}")
    return result.stdout


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--launch-dir", required=True)
    parser.add_argument("--todo", required=True)
    parser.add_argument("--target-branch", required=True)
    parser.add_argument("--target-revision", required=True)
    parser.add_argument("--session-id", required=True)
    return parser.parse_args(argv)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip())
    slug = slug.strip("-._")
    return slug or "todo"


def make_worktree_identity(todo_path: Path) -> tuple[str, str, Path]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = slugify(todo_path.stem)
    branch_name = f"todo/{slug}-{timestamp}"
    worktree_name = f"{slug}-{timestamp}"
    worktree_path = WORKTREES_DIR / worktree_name
    return branch_name, worktree_name, worktree_path


def resolve_codex_cwd(repo_root: Path, launch_dir: Path, worktree_path: Path) -> Path:
    try:
        relative = launch_dir.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return worktree_path

    candidate = worktree_path / relative
    if candidate.is_dir():
        return candidate
    return worktree_path


def create_worktree(repo_root: Path, target_revision: str, branch_name: str, worktree_path: Path) -> None:
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    result = run_capture(
        [
            "git",
            "-C",
            str(repo_root),
            "worktree",
            "add",
            "-b",
            branch_name,
            str(worktree_path),
            target_revision,
        ]
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"failed to create worktree: {details}")


def run_codex(todo_path: Path, codex_cwd: Path) -> int:
    print(f"Running Codex in worktree directory: {codex_cwd}")
    print(f"Todo file: {todo_path}")
    print("")

    with todo_path.open("r", encoding="utf-8") as todo_file:
        result = subprocess.run(
            ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox", "-"],
            cwd=codex_cwd,
            stdin=todo_file,
            check=False,
        )
    return result.returncode


def commit_worktree_changes(worktree_path: Path, commit_message: str) -> None:
    status_before = git_status_porcelain(worktree_path).strip()
    if not status_before:
        raise RuntimeError(
            GateFailure(
                stage="changes_present",
                reason="Codex exited successfully but produced no git changes in the worktree.",
            )
        )

    run_checked(
        ["git", "-C", str(worktree_path), "add", "-A"],
        stage="stage_changes",
        reason="Failed to stage worktree changes before merge.",
    )
    run_checked(
        ["git", "-C", str(worktree_path), "commit", "-m", commit_message],
        stage="commit_changes",
        reason="Failed to commit worktree changes before merge.",
    )


def enforce_merge_gates(repo_root: Path, target_branch: str, target_revision: str) -> None:
    current_branch = git_value(
        repo_root,
        ["symbolic-ref", "--quiet", "--short", "HEAD"],
        stage="target_branch_check",
        reason="Unable to read target branch in primary worktree.",
    )
    if current_branch != target_branch:
        raise RuntimeError(
            GateFailure(
                stage="target_branch_check",
                reason=(
                    "Primary worktree branch changed during todo execution. "
                    f"Expected '{target_branch}', found '{current_branch}'."
                ),
            )
        )

    current_revision = git_value(
        repo_root,
        ["rev-parse", "HEAD"],
        stage="target_revision_check",
        reason="Unable to read HEAD revision in primary worktree.",
    )
    if current_revision != target_revision:
        raise RuntimeError(
            GateFailure(
                stage="target_revision_check",
                reason=(
                    "Primary worktree HEAD changed during todo execution. "
                    f"Expected {target_revision}, found {current_revision}."
                ),
            )
        )

    status_text = git_status_porcelain(repo_root).strip()
    if status_text:
        raise RuntimeError(
            GateFailure(
                stage="primary_clean_check",
                reason="Primary worktree is not clean; strict auto-merge requires a clean tree.",
                stdout=status_text,
            )
        )


def merge_branch(repo_root: Path, branch_name: str) -> None:
    run_checked(
        ["git", "-C", str(repo_root), "merge", "--ff-only", branch_name],
        stage="ff_merge",
        reason="Fast-forward merge failed.",
    )


def find_todos_root(todo_path: Path) -> Path:
    for ancestor in todo_path.parents:
        if ancestor.name == "todos":
            return ancestor
    return todo_path.parent


def append_completion_footer(
    archived_todo_path: Path, commit_sha: str, branch_name: str, completion_timestamp: str
) -> None:
    existing = archived_todo_path.read_text(encoding="utf-8")
    separator = ""
    if existing:
        separator = "\n" if existing.endswith("\n") else "\n\n"

    footer = "\n".join(
        [
            f"{separator}---",
            "Completion:",
            f"- Commit SHA: `{commit_sha}`",
            f"- Branch: `{branch_name}`",
            f"- Timestamp: `{completion_timestamp}`",
            "",
        ]
    )
    with archived_todo_path.open("a", encoding="utf-8") as todo_file:
        todo_file.write(footer)


def archive_completed_todo(
    todo_path: Path,
    commit_sha: str,
    branch_name: str,
    *,
    completed_at: datetime | None = None,
) -> Path:
    completion_time = completed_at or datetime.now()
    completion_day = completion_time.strftime("%Y-%m-%d")
    completion_timestamp = completion_time.isoformat(timespec="seconds")

    done_dir = find_todos_root(todo_path) / "done" / completion_day
    done_dir.mkdir(parents=True, exist_ok=True)

    archived_todo_path = done_dir / todo_path.name
    if archived_todo_path.exists():
        raise RuntimeError(f"Archive destination already exists: {archived_todo_path}")

    shutil.move(str(todo_path), str(archived_todo_path))
    append_completion_footer(archived_todo_path, commit_sha, branch_name, completion_timestamp)
    return archived_todo_path


def cleanup_success(repo_root: Path, worktree_path: Path, branch_name: str) -> list[str]:
    warnings: list[str] = []

    remove_result = run_capture(["git", "-C", str(repo_root), "worktree", "remove", str(worktree_path)])
    if remove_result.returncode != 0:
        details = (remove_result.stderr or remove_result.stdout).strip()
        warnings.append(f"Could not remove worktree {worktree_path}: {details}")

    delete_result = run_capture(["git", "-C", str(repo_root), "branch", "-d", branch_name])
    if delete_result.returncode != 0:
        details = (delete_result.stderr or delete_result.stdout).strip()
        warnings.append(f"Could not delete branch {branch_name}: {details}")

    return warnings


def write_merge_gate_failure(
    repo_root: Path,
    todo_path: Path,
    target_branch: str,
    target_revision: str,
    branch_name: str,
    worktree_path: Path,
    failure: GateFailure,
) -> Path:
    output_path = repo_root / "merge_gate_failure.md"
    timestamp = datetime.now().isoformat(timespec="seconds")

    lines = [
        "# Merge Gate Failure",
        "",
        f"- Timestamp: `{timestamp}`",
        f"- Todo file: `{todo_path}`",
        f"- Repository: `{repo_root}`",
        f"- Target branch: `{target_branch}`",
        f"- Target start revision: `{target_revision}`",
        f"- Worktree branch: `{branch_name}`",
        f"- Worktree path: `{worktree_path}`",
        f"- Failed gate: `{failure.stage}`",
        "",
        "## Why It Failed",
        failure.reason,
        "",
        "## Suggested Next Steps",
        "1. Review this file and confirm the failure reason.",
        f"2. Inspect the worktree: `cd {worktree_path}` and check `git status`.",
        f"3. Resolve blockers, then merge manually into `{target_branch}`.",
        f"4. After a successful merge, move `{todo_path}` to `todos/done/<YYYY-MM-DD>/`.",
        "5. Delete this file after the merge is fully resolved.",
    ]

    if failure.command:
        lines.extend(["", "## Failed Command", "```bash", failure.command, "```"])

    if failure.stdout.strip():
        lines.extend(["", "## Captured Stdout", "```text", failure.stdout.strip(), "```"])

    if failure.stderr.strip():
        lines.extend(["", "## Captured Stderr", "```text", failure.stderr.strip(), "```"])

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def open_merge_helper_session(
    session_id: str,
    repo_root: Path,
    target_branch: str,
    branch_name: str,
    worktree_path: Path,
    todo_path: Path,
    failure_path: Path,
) -> None:
    task_name = todo_path.stem.strip() or "todo"
    if len(task_name) > 72:
        task_name = task_name[:72].rstrip()
    window_name = f"{task_name} :: merge-help (codex)"

    prompt = (
        "A strict merge gate failed for an automated todo run. "
        f"Read {failure_path.name} in the repo root first, then finish the merge safely. "
        f"Target branch: {target_branch}. Worktree branch: {branch_name}. "
        f"Worktree path: {worktree_path}. "
        f"After merge succeeds, archive the todo under todos/done/<YYYY-MM-DD>/ from {todo_path}. "
        "After that, delete merge_gate_failure.md."
    )

    inner_command = (
        f"cd {shlex.quote(str(repo_root))}; "
        f"codex --dangerously-bypass-approvals-and-sandbox {shlex.quote(prompt)}"
    )
    tmux_command = f"bash -lc {shlex.quote(inner_command)}"

    result = run_capture(
        [
            "tmux",
            "new-window",
            "-t",
            session_id,
            "-n",
            window_name,
            tmux_command,
        ]
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        print(f"WARNING: Failed to open merge helper session: {details}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    repo_root = Path(args.repo_root).expanduser().resolve()
    launch_dir = Path(args.launch_dir).expanduser().resolve()
    todo_path = Path(args.todo).expanduser().resolve()
    target_branch = args.target_branch
    target_revision = args.target_revision
    session_id = args.session_id

    if not repo_root.is_dir():
        die(f"Repo root does not exist: {repo_root}")
    if not launch_dir.is_dir():
        die(f"Launch directory does not exist: {launch_dir}")
    if not todo_path.is_file():
        die(f"Todo file does not exist: {todo_path}")

    ensure_reasonable_path()
    try:
        require_dependency("git")
        require_dependency("codex")
        require_dependency("tmux")

        branch_name, _worktree_name, worktree_path = make_worktree_identity(todo_path)
        print(f"Creating worktree: {worktree_path}")
        print(f"Worktree branch: {branch_name}")
        create_worktree(repo_root, target_revision, branch_name, worktree_path)

        codex_cwd = resolve_codex_cwd(repo_root, launch_dir, worktree_path)
        codex_exit = run_codex(todo_path, codex_cwd)
        if codex_exit != 0:
            print("")
            print(f"Codex exited with {codex_exit}. Keeping todo/worktree for follow-up.")
            pause_for_window()
            return codex_exit

        commit_message = f"todo: {todo_path.stem}"
        commit_worktree_changes(worktree_path, commit_message)
        enforce_merge_gates(repo_root, target_branch, target_revision)
        merge_branch(repo_root, branch_name)

        merged_commit_sha = git_value(
            repo_root,
            ["rev-parse", "HEAD"],
            stage="archive_metadata",
            reason="Unable to read merged commit SHA for todo archival.",
        )
        archived_todo_path = archive_completed_todo(
            todo_path,
            merged_commit_sha,
            branch_name,
        )
        warnings = cleanup_success(repo_root, worktree_path, branch_name)

        print("")
        print("Auto-merge complete.")
        print(f"Merged {branch_name} into {target_branch}.")
        print(f"Archived todo file: {archived_todo_path}")
        if warnings:
            print("")
            for warning in warnings:
                print(f"WARNING: {warning}")
        return 0
    except RuntimeError as exc:
        failure = exc.args[0] if exc.args else None
        if isinstance(failure, GateFailure):
            failure_path = write_merge_gate_failure(
                repo_root,
                todo_path,
                target_branch,
                target_revision,
                branch_name,
                worktree_path,
                failure,
            )
            print("")
            print("Strict merge gate failed. Wrote diagnostics to:")
            print(f"  {failure_path}")
            open_merge_helper_session(
                session_id,
                repo_root,
                target_branch,
                branch_name,
                worktree_path,
                todo_path,
                failure_path,
            )
            pause_for_window()
            return 1

        die(str(exc))
    except KeyboardInterrupt:
        die("Cancelled by user")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
