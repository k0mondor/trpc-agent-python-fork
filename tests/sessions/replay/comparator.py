"""Snapshot mutation and exact recursive comparison."""

from __future__ import annotations

import re
from typing import Any, Optional

from ..replay_models import BackendSnapshot
from ..replay_models import DiffEntry
from ..replay_models import ReplayCase
from ..replay_models import SnapshotMutation
from ..replay_models import SnapshotMutationOperation
from .allowed_diff import allowed_diff_for_path
from .allowed_diff import validate_allowed_diff_rules
from .normalizer import normalize_backend_snapshot


_EVENT_INDEX_RE = re.compile(r"\[(\d+)\]")
_BASELINE_BACKEND_NAME = "inmemory"
_PERSISTENT_BACKEND_TARGETS = {"persistent", "secondary", "non_baseline"}
_BASELINE_BACKEND_TARGETS = {"baseline", "primary", "inmemory"}


def backend_target_matches(target_name: str, backend_name: str) -> bool:
    normalized_target = target_name.strip().lower()
    normalized_backend = backend_name.strip().lower()
    if normalized_target == normalized_backend:
        return True
    if normalized_target in _PERSISTENT_BACKEND_TARGETS:
        return normalized_backend != _BASELINE_BACKEND_NAME
    if normalized_target in _BASELINE_BACKEND_TARGETS:
        return normalized_backend == _BASELINE_BACKEND_NAME
    return False


def expected_diff_paths_for_backend_pair(
    case: ReplayCase,
    *,
    backend_a: str,
    backend_b: str,
) -> tuple[str, ...]:
    if not case.expected_diff_paths:
        return ()
    targets = [mutation.backend_name for mutation in case.snapshot_mutations]
    targets.extend(fault.backend_name for fault in case.runtime_faults)
    if any(
        backend_target_matches(target, backend_a) or backend_target_matches(target, backend_b)
        for target in targets
    ):
        return case.expected_diff_paths
    return ()


def parse_path_tokens(path: str) -> list[Any]:
    tokens: list[Any] = []
    for part in path.split("."):
        if not part:
            continue
        cursor = part
        while cursor:
            match = re.match(r"^([^\[]+)(\[(\d+)\])?(.*)$", cursor)
            if not match:
                raise ValueError(f"Invalid path segment: {cursor}")
            key, _, index, rest = match.groups()
            if key:
                tokens.append(key)
            if index is not None:
                tokens.append(int(index))
            cursor = rest
    return tokens


def _get_token(container: Any, token: Any) -> Any:
    if isinstance(token, int):
        return container[token]
    if isinstance(container, dict):
        return container[token]
    return getattr(container, token)


def _resolve_parent(root: Any, tokens: list[Any]) -> tuple[Any, Any]:
    target = root
    for token in tokens[:-1]:
        target = _get_token(target, token)
    return target, tokens[-1]


def _set_token(container: Any, token: Any, value: Any) -> None:
    if isinstance(token, int):
        container[token] = value
    elif isinstance(container, dict):
        container[token] = value
    else:
        setattr(container, token, value)


def _delete_token(container: Any, token: Any) -> None:
    if isinstance(token, int):
        del container[token]
    elif isinstance(container, dict):
        container.pop(token, None)
    else:
        delattr(container, token)


def set_path_value(root: Any, path: str, value: Any) -> None:
    tokens = parse_path_tokens(path)
    if not tokens:
        raise ValueError(f"Invalid mutation path: {path}")
    parent, last_token = _resolve_parent(root, tokens)
    _set_token(parent, last_token, value)


def _apply_snapshot_mutation(snapshot_view: dict[str, Any], mutation: SnapshotMutation) -> None:
    tokens = parse_path_tokens(mutation.path)
    if not tokens:
        raise ValueError(f"Invalid mutation path: {mutation.path}")
    parent, last_token = _resolve_parent(snapshot_view, tokens)
    if mutation.operation == SnapshotMutationOperation.SET:
        _set_token(parent, last_token, mutation.value)
        return
    if mutation.operation == SnapshotMutationOperation.DELETE:
        _delete_token(parent, last_token)
        return
    raise ValueError(f"Unsupported snapshot mutation operation: {mutation.operation}")


def _apply_snapshot_mutations(
    snapshot_view: dict[str, Any],
    mutations: tuple[SnapshotMutation, ...],
    backend_name: str,
) -> None:
    for mutation in mutations:
        if backend_target_matches(mutation.backend_name, backend_name):
            _apply_snapshot_mutation(snapshot_view, mutation)


def _extract_event_index(path: str) -> Optional[int]:
    match = _EVENT_INDEX_RE.search(path)
    return int(match.group(1)) if match else None


def _resolve_summary_id(left: Any, right: Any) -> Optional[str]:
    for value in (left, right):
        if isinstance(value, dict) and value.get("summary_id") is not None:
            return str(value["summary_id"])
    return None


def _resolve_session_id(left: dict[str, Any], right: dict[str, Any], fallback: str) -> str:
    if isinstance(left.get("session_id"), str) and left.get("session_id") == right.get("session_id"):
        return str(left["session_id"])
    return fallback


def _resolve_value_session_id(left: Any, right: Any, fallback: str) -> str:
    for value in (left, right):
        if isinstance(value, dict) and isinstance(value.get("session_id"), str):
            return str(value["session_id"])
    return fallback


def _make_diff(
    case: ReplayCase,
    backend_a: str,
    backend_b: str,
    scope: str,
    path: str,
    left: Any,
    right: Any,
    session_id: str,
    summary_id: Optional[str],
) -> DiffEntry:
    allowed, reason = allowed_diff_for_path(case, path, (backend_a, backend_b))
    return DiffEntry(
        case_id=case.case_id,
        backend_a=backend_a,
        backend_b=backend_b,
        scope=scope,
        path=path,
        left=left,
        right=right,
        allowed=allowed,
        session_id=session_id,
        event_index=_extract_event_index(path),
        summary_id=summary_id,
        reason=reason,
    )


def _diff_values(
    *,
    case: ReplayCase,
    backend_a: str,
    backend_b: str,
    scope: str,
    path: str,
    left: Any,
    right: Any,
    out: list[DiffEntry],
    session_id: str,
    summary_id: Optional[str],
) -> None:
    if type(left) is not type(right):
        out.append(_make_diff(case, backend_a, backend_b, scope, path, left, right, session_id, summary_id))
        return
    if isinstance(left, dict):
        for key in sorted(set(left) | set(right)):
            next_path = f"{path}.{key}"
            if key not in left or key not in right:
                out.append(
                    _make_diff(
                        case,
                        backend_a,
                        backend_b,
                        scope,
                        next_path,
                        left.get(key),
                        right.get(key),
                        session_id,
                        summary_id,
                    )
                )
            else:
                _diff_values(
                    case=case,
                    backend_a=backend_a,
                    backend_b=backend_b,
                    scope=scope,
                    path=next_path,
                    left=left[key],
                    right=right[key],
                    out=out,
                    session_id=session_id,
                    summary_id=summary_id,
                )
        return
    if isinstance(left, list):
        if len(left) != len(right):
            out.append(
                _make_diff(
                    case,
                    backend_a,
                    backend_b,
                    scope,
                    f"{path}.length",
                    len(left),
                    len(right),
                    session_id,
                    summary_id,
                )
            )
            return
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            _diff_values(
                case=case,
                backend_a=backend_a,
                backend_b=backend_b,
                scope=scope,
                path=f"{path}[{index}]",
                left=left_item,
                right=right_item,
                out=out,
                session_id=session_id,
                summary_id=summary_id,
            )
        return
    if left != right:
        out.append(_make_diff(case, backend_a, backend_b, scope, path, left, right, session_id, summary_id))


def _count_comparable_leaves(left: Any, right: Any) -> int:
    if isinstance(left, dict) and isinstance(right, dict):
        return sum(
            _count_comparable_leaves(left.get(key), right.get(key))
            for key in set(left) | set(right)
        )
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return 1
        return sum(_count_comparable_leaves(a, b) for a, b in zip(left, right))
    return 1


def diff_backend_snapshots(
    *,
    case: ReplayCase,
    left: BackendSnapshot,
    right: BackendSnapshot,
) -> list[DiffEntry]:
    """Normalize, apply declared mutation, recursively diff, and govern allows."""

    validate_allowed_diff_rules(case)
    left_view = normalize_backend_snapshot(left)
    right_view = normalize_backend_snapshot(right)
    _apply_snapshot_mutations(left_view, case.snapshot_mutations, left.backend_name)
    _apply_snapshot_mutations(right_view, case.snapshot_mutations, right.backend_name)
    diffs: list[DiffEntry] = []
    compared_field_count = 0

    for scope in ("session", "state", "memory", "summary"):
        compared_field_count += _count_comparable_leaves(left_view[scope], right_view[scope])
        _diff_values(
            case=case,
            backend_a=left.backend_name,
            backend_b=right.backend_name,
            scope=scope,
            path=scope,
            left=left_view[scope],
            right=right_view[scope],
            out=diffs,
            session_id=_resolve_session_id(left_view, right_view, case.session_id),
            summary_id=_resolve_summary_id(left_view.get(scope), right_view.get(scope)) if scope == "summary" else None,
        )

    left_aliases = left_view.get("sessions_by_alias", {})
    right_aliases = right_view.get("sessions_by_alias", {})
    for session_alias in sorted(set(left_aliases) | set(right_aliases)):
        left_alias = left_aliases.get(session_alias)
        right_alias = right_aliases.get(session_alias)
        compared_field_count += _count_comparable_leaves(left_alias, right_alias)
        _diff_values(
            case=case,
            backend_a=left.backend_name,
            backend_b=right.backend_name,
            scope="sessions_by_alias",
            path=f"sessions_by_alias.{session_alias}",
            left=left_alias,
            right=right_alias,
            out=diffs,
            session_id=_resolve_value_session_id(left_alias, right_alias, case.session_id),
            summary_id=_resolve_summary_id(
                left_alias.get("summary") if isinstance(left_alias, dict) else None,
                right_alias.get("summary") if isinstance(right_alias, dict) else None,
            ),
        )

    validate_allowed_diff_rules(
        case,
        compared_field_count=compared_field_count,
        used_allowed_count=sum(1 for diff in diffs if diff.allowed),
    )
    return diffs


def format_diffs(diffs: list[DiffEntry]) -> str:
    if not diffs:
        return "No differences detected."
    return "\n".join(
        f"{'ALLOWED' if diff.allowed else 'DIFF'} {diff.path}: {diff.left!r} != {diff.right!r}"
        for diff in diffs
    )
