"""Run sandboxed tests for the code review skill."""

from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """Build CLI arguments for the test simulation script."""

    parser = argparse.ArgumentParser(description="Simulate test execution for a diff file.")
    parser.add_argument("--diff-file", required=True, help="Path to the diff file.")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""

    return build_parser().parse_args(argv)


def extract_changed_test_files(diff_text: str) -> list[str]:
    """Extract test paths from well-formed or unusual git diff headers."""

    changed_test_files: list[str] = []
    for line in diff_text.splitlines():
        if not line.startswith("diff --git "):
            continue
        try:
            parts = shlex.split(line)
        except ValueError:
            continue
        if len(parts) < 4:
            continue

        for raw_path in parts[2:4]:
            normalized_path = raw_path.replace("\\", "/")
            if len(normalized_path) > 2 and normalized_path[1] == "/":
                normalized_path = normalized_path[2:]
            if not (
                normalized_path.startswith("tests/")
                or "/tests/" in normalized_path
            ):
                continue
            if normalized_path not in changed_test_files:
                changed_test_files.append(normalized_path)
    return changed_test_files


def main(argv: list[str] | None = None) -> int:
    """Emit a deterministic test summary based on changed paths."""

    args = parse_args(argv)
    diff_path = Path(args.diff_file).expanduser().resolve()
    diff_text = diff_path.read_text(encoding="utf-8")
    changed_test_files = extract_changed_test_files(diff_text)
    payload = {
        "diff_file": diff_path.name,
        "changed_test_files": changed_test_files,
        "test_update_present": bool(changed_test_files),
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
