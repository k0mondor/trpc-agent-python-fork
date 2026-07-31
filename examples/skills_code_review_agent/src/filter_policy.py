"""Filter policy helpers for sandbox governance decisions."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from .review_types import FilterDecisionRecord, FilterDecisionType, ParsedDiff

_FORBIDDEN_PATH_PARTS = (".git/", ".env", "secrets/", "id_rsa", ".pem")
_NETWORK_TOOLS = ("curl", "wget", "invoke-webrequest")
_DANGEROUS_TEXT = ("rm -rf", "del /f", "format ", "shutdown ", "mkfs")
_NETWORK_CALLS = (
    "requests.", "httpx.", "urllib.request.urlopen", "socket.socket",
    "socket.create_connection", "aiohttp.ClientSession",
)
_DANGEROUS_CALLS = (
    "eval", "exec", "os.system", "os.popen", "os.remove", "os.unlink",
    "os.rmdir", "shutil.rmtree", "subprocess.run", "subprocess.Popen",
    "subprocess.call", "subprocess.check_call", "subprocess.check_output",
)
_IMPLEMENTED_RUNTIMES = frozenset({"local", "container"})


@dataclass(slots=True, frozen=True)
class SkillScriptInvocation:
    name: str
    script_path: Path
    command: list[str]
    target: str


def evaluate_invocations(
    *,
    parsed_diff: ParsedDiff,
    runtime: str,
    invocations: list[SkillScriptInvocation],
    max_changed_files: int = 50,
    max_added_lines: int = 2000,
) -> list[tuple[SkillScriptInvocation, FilterDecisionRecord]]:
    """Evaluate sandbox invocations and return one auditable decision per script."""

    decisions = []
    over_budget = (
        parsed_diff.changed_files_count > max_changed_files
        or parsed_diff.added_lines_count > max_added_lines
    )
    for invocation in invocations:
        violations, error = _inspect_script(invocation.script_path)
        decision = _decision(FilterDecisionType.ALLOW, invocation, "allow", "Invocation allowed by default policy.")
        if runtime not in _IMPLEMENTED_RUNTIMES:
            decision = _decision(
                FilterDecisionType.NEEDS_HUMAN_REVIEW,
                invocation,
                "runtime_not_configured",
                f"Runtime `{runtime}` is not configured by this example.",
                human_review=True,
            )
        elif _contains_forbidden_path(parsed_diff):
            decision = _decision(
                FilterDecisionType.DENY,
                invocation,
                "forbidden_path",
                "Diff touches a forbidden path and cannot enter sandbox execution.",
            )
        elif error:
            decision = _decision(
                FilterDecisionType.DENY,
                invocation,
                error[0],
                f"Skill script could not be inspected: {error[1]}",
            )
        elif "dangerous_command" in violations or _contains_any(invocation.command, _DANGEROUS_TEXT):
            decision = _decision(
                FilterDecisionType.DENY, invocation, "dangerous_command",
                "Invocation contains a dangerous command pattern.",
            )
        elif "network_not_allowed" in violations or _contains_any(invocation.command, _NETWORK_TOOLS):
            decision = _decision(
                FilterDecisionType.DENY, invocation, "network_not_allowed",
                "Network access is not permitted for sandbox scripts by default.",
            )
        elif over_budget:
            decision = _decision(
                FilterDecisionType.NEEDS_HUMAN_REVIEW,
                invocation,
                "over_budget",
                "Diff size exceeds sandbox budget and requires manual approval.",
                human_review=True,
            )
        decisions.append((invocation, decision))
    return decisions


def _decision(
    decision: FilterDecisionType,
    invocation: SkillScriptInvocation,
    code: str,
    reason: str,
    *,
    human_review: bool = False,
) -> FilterDecisionRecord:
    return FilterDecisionRecord(
        decision=decision,
        target=invocation.target,
        reason_code=code,
        reason=reason,
        requires_human_review=human_review,
    )


def _contains_forbidden_path(parsed_diff: ParsedDiff) -> bool:
    return any(
        part in path.replace("\\", "/")
        for path in parsed_diff.changed_paths
        for part in _FORBIDDEN_PATH_PARTS
    )


def _inspect_script(script_path: Path) -> tuple[set[str], tuple[str, str] | None]:
    """Inspect executable AST calls, so comments and quoted examples are ignored."""

    try:
        tree = ast.parse(script_path.read_text(encoding="utf-8"), filename=str(script_path))
    except (OSError, UnicodeError) as exc:
        return set(), ("script_unreadable", str(exc))
    except SyntaxError as exc:
        return set(), ("script_invalid", str(exc))

    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".")[0]] = item.name if item.asname else item.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                aliases[item.asname or item.name] = f"{node.module}.{item.name}"

    violations: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func, aliases)
        if name.startswith(_NETWORK_CALLS):
            violations.add("network_not_allowed")
        if name in _DANGEROUS_CALLS:
            violations.add("dangerous_command")
    return violations, None


def _call_name(node: ast.expr, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _contains_any(parts: list[str], tokens: tuple[str, ...]) -> bool:
    text = " ".join(parts).lower()
    return any(token in text for token in tokens)
