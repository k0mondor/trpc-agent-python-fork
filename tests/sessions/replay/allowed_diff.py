"""Exact-path allowed-difference rules and governance."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional

from ..replay_models import ReplayCase


MAX_ALLOWED_DIFF_RULES = 8
MAX_ALLOWED_DIFF_RATIO = 0.10
_STRICT_SUMMARY_METADATA_FIELDS = {
    "session_id",
    "summary_id",
    "version",
    "replaces",
}
_PATH_TOKEN = re.compile(r"[\w:-]+|\[\d+\]|\[\*\]")


@dataclass(frozen=True)
class AllowedDiffRule:
    """One narrow, explained allowlist rule.

    ``[*]`` may wildcard a list index only. Key wildcards are deliberately not
    supported because rules such as ``*.id`` can hide business identifiers.
    """

    path: str
    reason: str
    backend_pair: Optional[tuple[str, str]] = None


def _tokenize_path(path: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    for chunk in _PATH_TOKEN.findall(path):
        if chunk.startswith("["):
            tokens.append(("index", chunk[1:-1]))
        else:
            tokens.append(("key", chunk))
    return tokens


def _matches(field_path: str, rule_path: str) -> bool:
    field_tokens = _tokenize_path(field_path)
    rule_tokens = _tokenize_path(rule_path)
    if len(field_tokens) != len(rule_tokens):
        return False
    for (field_kind, field_value), (rule_kind, rule_value) in zip(field_tokens, rule_tokens):
        if field_kind != rule_kind:
            return False
        if rule_kind == "index" and rule_value == "*":
            continue
        if field_value != rule_value:
            return False
    return True


def rules_for_case(case: ReplayCase) -> tuple[AllowedDiffRule, ...]:
    """Return explicit rules plus compatibility rules from legacy fields."""

    explicit = tuple(
        AllowedDiffRule(rule.path, rule.reason, rule.backend_pair)
        for rule in case.allowed_diff_rules
    )
    legacy = tuple(
        AllowedDiffRule(
            path=path,
            reason=case.allowed_diff_reasons.get(path, ""),
            backend_pair=case.allowed_diff_backend_pairs.get(path),
        )
        for path in case.allowed_diff_paths
    )
    return explicit + legacy


def allowed_diff_for_path(
    case: ReplayCase,
    path: str,
    backend_pair: tuple[str, str],
) -> tuple[bool, Optional[str]]:
    """Return whether ``path`` is allowed for the ordered backend pair."""

    for rule in rules_for_case(case):
        if rule.backend_pair is not None and tuple(rule.backend_pair) != tuple(backend_pair):
            continue
        if rule.reason.strip() and _matches(path, rule.path):
            return True, rule.reason
    return False, None


def validate_allowed_diff_rules(
    case: ReplayCase,
    *,
    compared_field_count: Optional[int] = None,
    used_allowed_count: Optional[int] = None,
) -> None:
    """Reject unexplained, broad, excessive, or lineage-masking rules."""

    rules = rules_for_case(case)
    if len(rules) > MAX_ALLOWED_DIFF_RULES:
        raise ValueError(
            f"Replay case '{case.case_id}' has too many allowed diffs: "
            f"{len(rules)} > {MAX_ALLOWED_DIFF_RULES}"
        )

    missing_reasons = sorted(rule.path for rule in rules if not rule.reason.strip())
    if missing_reasons:
        raise ValueError(
            f"Replay case '{case.case_id}' has allowed diffs without reasons: {missing_reasons}"
        )

    legacy_paths = set(case.allowed_diff_paths)
    orphan_reasons = sorted(set(case.allowed_diff_reasons) - legacy_paths)
    if orphan_reasons:
        raise ValueError(
            f"Replay case '{case.case_id}' has reasons for non-allowlisted paths: {orphan_reasons}"
        )
    orphan_pairs = sorted(set(case.allowed_diff_backend_pairs) - legacy_paths)
    if orphan_pairs:
        raise ValueError(
            f"Replay case '{case.case_id}' has backend pairs for non-allowlisted paths: {orphan_pairs}"
        )

    protected_paths: list[str] = []
    for rule in rules:
        tokens = _tokenize_path(rule.path)
        key_values = [value for kind, value in tokens if kind == "key"]
        if "summary" in key_values and key_values and key_values[-1] in _STRICT_SUMMARY_METADATA_FIELDS:
            protected_paths.append(rule.path)
    if protected_paths:
        raise ValueError(
            f"Replay case '{case.case_id}' cannot allow strict summary metadata diffs: "
            f"{sorted(protected_paths)}"
        )

    if compared_field_count is not None and used_allowed_count is not None and compared_field_count > 0:
        ratio = used_allowed_count / compared_field_count
        if ratio > MAX_ALLOWED_DIFF_RATIO:
            raise ValueError(
                f"Replay case '{case.case_id}' allowed-diff ratio is too high: "
                f"{used_allowed_count}/{compared_field_count} > {MAX_ALLOWED_DIFF_RATIO:.0%}"
            )
