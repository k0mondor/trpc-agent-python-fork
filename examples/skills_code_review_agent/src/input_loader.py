"""Input loading helpers for diffs, repo paths, and fixtures."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

from .review_types import ReviewInput, ReviewInputKind

GIT_DIFF_TIMEOUT_SECONDS = 30


def load_review_input(
    *,
    diff_file: str | Path | None = None,
    repo_path: str | Path | None = None,
    fixture_path: str | Path | None = None,
    changed_paths: Sequence[str] | None = None,
) -> ReviewInput:
    """Load a normalized review input from a single supported source."""

    provided_inputs = [
        value
        for value in (diff_file, repo_path, fixture_path)
        if value is not None
    ]
    if len(provided_inputs) != 1:
        raise ValueError("exactly one of diff_file, repo_path, or fixture_path must be set")

    if diff_file is not None:
        file_path = _ensure_existing_file(diff_file)
        return ReviewInput(
            kind=ReviewInputKind.DIFF_FILE,
            source=str(file_path),
            diff_text=file_path.read_text(encoding="utf-8"),
        )

    if fixture_path is not None:
        file_path = _ensure_existing_file(fixture_path)
        return ReviewInput(
            kind=ReviewInputKind.FIXTURE,
            source=str(file_path),
            diff_text=file_path.read_text(encoding="utf-8"),
        )

    repo_dir = _ensure_existing_path(repo_path)
    diff_text = load_git_workspace_diff(repo_dir, changed_paths=changed_paths)
    return ReviewInput(
        kind=ReviewInputKind.REPO_PATH,
        source=str(repo_dir),
        diff_text=diff_text,
        repo_path=repo_dir,
    )


def load_git_workspace_diff(
    repo_path: str | Path,
    changed_paths: Sequence[str] | None = None,
) -> str:
    """Load git diff text for a repo workspace.

    The loader prefers `git diff HEAD` because it captures both staged and
    unstaged tracked changes relative to the last commit. If the repository has
    no HEAD yet, it falls back to the working tree diff.
    """

    repo_dir = _ensure_existing_path(repo_path)
    pathspec = list(changed_paths or [])
    head_check = _run_git_command(
        [
            "git",
            "-C",
            str(repo_dir),
            "rev-parse",
            "--verify",
            "--quiet",
            "HEAD",
        ],
        operation="git rev-parse HEAD",
    )
    if head_check.returncode not in {0, 1}:
        raise RuntimeError(
            "failed to determine whether git HEAD exists: "
            f"{head_check.stderr.strip() or head_check.stdout.strip() or 'unknown error'}"
        )

    operation = "git diff HEAD" if head_check.returncode == 0 else "fallback git diff"
    command = ["git", "-C", str(repo_dir), "diff", "--no-ext-diff"]
    if head_check.returncode == 0:
        command.append("HEAD")
    command.extend(["--", *pathspec])

    completed = _run_git_command(command, operation=operation)
    if completed.returncode != 0:
        raise RuntimeError(
            f"failed to collect {operation}: "
            f"{completed.stderr.strip() or completed.stdout.strip() or 'unknown error'}"
        )
    return completed.stdout


def _run_git_command(
    command: list[str],
    *,
    operation: str,
) -> subprocess.CompletedProcess[str]:
    """Run a read-only git command with a bounded execution time."""

    try:
        return subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            timeout=GIT_DIFF_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"{operation} timed out after {GIT_DIFF_TIMEOUT_SECONDS} seconds"
        ) from exc


def _ensure_existing_path(path: str | Path | None) -> Path:
    """Validate a path exists and return a normalized `Path`."""

    if path is None:
        raise ValueError("path must not be None")

    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"path does not exist: {resolved}")
    return resolved


def _ensure_existing_file(path: str | Path) -> Path:
    """Validate a path exists and points to a file."""

    resolved = _ensure_existing_path(path)
    if not resolved.is_file():
        raise FileNotFoundError(f"expected a file path: {resolved}")
    return resolved
