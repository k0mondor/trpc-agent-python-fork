"""Backend-neutral business snapshot normalization."""

from __future__ import annotations

from typing import Any, Optional

from ..replay_models import BackendSnapshot
from ..replay_models import SessionSnapshot
from ..replay_models import SummarySnapshot


def normalize_scalar(value: Any) -> Any:
    """Sort structural containers without changing business string values."""

    if isinstance(value, dict):
        return {key: normalize_scalar(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [normalize_scalar(item) for item in value]
    if isinstance(value, tuple):
        return [normalize_scalar(item) for item in value]
    if isinstance(value, set):
        return sorted(normalize_scalar(item) for item in value)
    return value


def _normalize_timestamp(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), 6)


def _normalize_summary(summary: Optional[SummarySnapshot]) -> Optional[dict[str, Any]]:
    if summary is None:
        return None
    return {
        "session_id": summary.session_id,
        "summary_text": summary.summary_text.strip(),
        "original_event_count": summary.original_event_count,
        "compressed_event_count": summary.compressed_event_count,
        "summary_id": summary.summary_id,
        "version": summary.version,
        "replaces": summary.replaces,
        "summarized_event_count": summary.summarized_event_count,
        "summary_timestamp": _normalize_timestamp(summary.summary_timestamp),
        "metadata": normalize_scalar(summary.metadata),
    }


def _normalize_memory(memory_results: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for observation_key, observation in memory_results.items():
        if isinstance(observation, dict):
            entries = observation.get("entries", [])
            normalized_observation = {
                "query_name": observation.get("query_name"),
                "session_alias": observation.get("session_alias"),
                "app_name": observation.get("app_name"),
                "user_id": observation.get("user_id"),
                "session_id": observation.get("session_id"),
                "step_index": observation.get("step_index"),
            }
        else:
            entries = observation
            normalized_observation = {}
        normalized_entries = [
            {
                "author": entry.get("author"),
                "role": entry.get("role"),
                "text": (entry.get("text") or "").strip(),
            }
            for entry in entries
        ]
        normalized_observation["entries"] = sorted(
            normalized_entries,
            key=lambda item: (item["text"], item["author"] or "", item["role"] or ""),
        )
        normalized[observation_key] = normalize_scalar(normalized_observation)
    return normalized


def _normalize_session_snapshot(snapshot: SessionSnapshot) -> dict[str, Any]:
    return {
        "session_alias": snapshot.session_alias,
        "app_name": snapshot.app_name,
        "user_id": snapshot.user_id,
        "session_id": snapshot.session_id,
        "session": normalize_scalar(snapshot.session),
        "state": normalize_scalar(snapshot.state),
        "summary": _normalize_summary(snapshot.summary),
    }


def normalize_backend_snapshot(snapshot: BackendSnapshot) -> dict[str, Any]:
    """Project a backend snapshot into its deterministic comparison view."""

    return {
        "backend_name": snapshot.backend_name,
        "case_id": snapshot.case_id,
        "app_name": snapshot.app_name,
        "user_id": snapshot.user_id,
        "session_id": snapshot.session_id,
        "active_session_alias": snapshot.active_session_alias,
        "session": normalize_scalar(snapshot.session),
        "state": normalize_scalar(snapshot.state),
        "memory": _normalize_memory(snapshot.memory),
        "summary": _normalize_summary(snapshot.summary),
        "sessions_by_alias": {
            alias: _normalize_session_snapshot(session_snapshot)
            for alias, session_snapshot in sorted(snapshot.sessions_by_alias.items())
            if alias != snapshot.active_session_alias
        },
    }
