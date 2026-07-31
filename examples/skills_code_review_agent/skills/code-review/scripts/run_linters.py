"""Run sandboxed lint or static checks for the code review skill."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """Build CLI arguments for the linter simulation script."""

    parser = argparse.ArgumentParser(description="Run deterministic lint checks on a diff file.")
    parser.add_argument("--diff-file", required=True, help="Path to the diff file.")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""

    return build_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run deterministic lint-style checks over the diff content."""

    args = parse_args(argv)
    diff_path = Path(args.diff_file).expanduser().resolve()
    diff_text = diff_path.read_text(encoding="utf-8")

    added_lines = [
        line[1:]
        for line in diff_text.splitlines()
        if line.startswith("+") and not line.startswith("+++ ")
    ]
    added_text = "\n".join(added_lines)
    executable_text = "\n".join(
        re.sub(r'''(["']).*?\1''', "", line).split("#", maxsplit=1)[0]
        for line in added_lines
    )
    if "TODO_FAIL_SANDBOX" in added_text:
        print("Simulated linter failure requested by fixture marker.", file=sys.stderr)
        return 2

    warnings: list[str] = []
    if re.search(r"(?<![\w.])eval\s*\(", executable_text):
        warnings.append("Security-sensitive call detected: eval")
    if re.search(r"shell\s*=\s*True", executable_text):
        warnings.append("Shell execution enabled in subprocess call")
    if re.search(r"verify\s*=\s*False", executable_text):
        warnings.append("TLS verification disabled")

    payload = {
        "diff_file": diff_path.name,
        "warning_count": len(warnings),
        "warnings": warnings,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
