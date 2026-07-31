"""Focused unit tests for modular replay comparison components."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from .replay.allowed_diff import allowed_diff_for_path
from .replay.allowed_diff import MAX_ALLOWED_DIFF_RULES
from .replay.allowed_diff import validate_allowed_diff_rules
from .replay.comparator import diff_backend_snapshots
from .replay.normalizer import normalize_backend_snapshot
from .replay.report import REPORT_SCHEMA_VERSION
from .replay.report import build_acceptance_quality_metrics
from .replay.report import validate_report_payload
from .replay.report import write_diff_report
from .replay_models import AllowedDiffSpec
from .replay_models import BackendSnapshot
from .replay_models import ReplayCase
from .replay_models import SessionSnapshot
from .replay_models import SummarySnapshot


def _snapshot(*, backend_name: str = "inmemory") -> BackendSnapshot:
    summary = SummarySnapshot(
        session_id="session-1",
        summary_text=" stable summary ",
        original_event_count=4,
        compressed_event_count=2,
        summary_id="session-1:summary:v2",
        version=2,
        replaces="session-1:summary:v1",
        summary_timestamp=123.4567894,
    )
    active = SessionSnapshot(
        session_alias="default",
        app_name="app",
        user_id="user",
        session_id="session-1",
        session={
            "conversation_count": 1,
            "events": [{"author": "user", "text": " hi ", "function_calls": []}],
            "historical_events": [],
        },
        state={"meaningful_whitespace": " value "},
        summary=summary,
    )
    return BackendSnapshot(
        backend_name=backend_name,
        case_id="case-1",
        app_name="app",
        user_id="user",
        session_id="session-1",
        active_session_alias="default",
        session=active.session,
        state=active.state,
        memory={},
        summary=summary,
        sessions_by_alias={"default": active},
    )


def test_normalizer_preserves_business_string_whitespace_and_normalizes_summary() -> None:
    normalized = normalize_backend_snapshot(_snapshot())
    assert normalized["state"]["meaningful_whitespace"] == " value "
    assert normalized["session"]["events"][0]["text"] == " hi "
    assert normalized["summary"]["summary_text"] == "stable summary"
    assert normalized["summary"]["summary_timestamp"] == 123.456789


def test_comparator_reports_exact_path_and_summary_locator() -> None:
    left = _snapshot()
    right_summary = replace(left.summary, version=1)
    right = replace(left, backend_name="sqlite", summary=right_summary)
    diffs = diff_backend_snapshots(case=ReplayCase("case-1", "summary"), left=left, right=right)
    assert [diff.path for diff in diffs] == ["summary.version"]
    assert diffs[0].summary_id == "session-1:summary:v2"


def test_allowed_diff_supports_index_wildcard_and_backend_pair() -> None:
    case = ReplayCase(
        "case-1",
        "allowed",
        allowed_diff_rules=(
            AllowedDiffSpec(
                path="session.events[*].error_message",
                reason="SQLite driver text differs.",
                backend_pair=("inmemory", "sqlite"),
            ),
        ),
    )
    assert allowed_diff_for_path(
        case,
        "session.events[3].error_message",
        ("inmemory", "sqlite"),
    ) == (True, "SQLite driver text differs.")
    assert allowed_diff_for_path(
        case,
        "session.events[3].error_message",
        ("inmemory", "redis"),
    ) == (False, None)


def test_allowed_diff_governance_rejects_excess_ratio_and_summary_lineage() -> None:
    too_many = tuple(
        AllowedDiffSpec(path=f"state.field_{index}", reason="backend-specific")
        for index in range(MAX_ALLOWED_DIFF_RULES + 1)
    )
    with pytest.raises(ValueError, match="too many"):
        validate_allowed_diff_rules(ReplayCase("case-1", "too many", allowed_diff_rules=too_many))

    ratio_case = ReplayCase(
        "case-2",
        "ratio",
        allowed_diff_rules=(AllowedDiffSpec(path="state.value", reason="backend-specific"),),
    )
    with pytest.raises(ValueError, match="ratio"):
        validate_allowed_diff_rules(ratio_case, compared_field_count=5, used_allowed_count=1)

    lineage_case = ReplayCase(
        "case-3",
        "lineage",
        allowed_diff_rules=(AllowedDiffSpec(path="summary.version", reason="backend-specific"),),
    )
    with pytest.raises(ValueError, match="strict summary metadata"):
        validate_allowed_diff_rules(lineage_case)


def test_report_schema_version_and_contract(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    case_reports = [{
        "case_id": "case-1",
        "description": "example",
        "scenario_type": "extended",
        "comparisons": [],
    }]
    metadata = {
        "mode": "unit",
        "elapsed_seconds": 0.0,
        "backend_names": ["inmemory", "sqlite"],
        "baseline_backend": "inmemory",
        "comparison_mode": "baseline_vs_all",
        "acceptance_case_count": 0,
        "extra_case_count": 1,
        "quality_metrics": {},
    }
    write_diff_report(report_path, case_reports, metadata)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    validate_report_payload(payload)
    assert payload["schema_version"] == REPORT_SCHEMA_VERSION

    schema_path = Path(__file__).with_name("replay_report.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == REPORT_SCHEMA_VERSION
    assert set(schema["required"]) == {"schema_version", "meta", "cases"}


def test_quality_metrics_require_exact_injected_paths() -> None:
    reports = [{
        "case_id": "summary_binding_mismatch_injection",
        "normal_comparisons": [{"detected_diff_paths": []}],
        "injected_comparisons": [{
            "expected_diff_paths": ["summary.session_id"],
            "missing_expected_paths": [],
            "unexpected_diff_paths": [],
        }],
    }]
    metrics = build_acceptance_quality_metrics(reports)
    assert metrics["injection_detection_rate"] == 1.0
    assert metrics["normal_false_positive_rate"] == 0.0
    assert metrics["summary_fault_missed_case_ids"] == [
        "summary_lineage_corruption_injection",
        "summary_missing_injection",
    ]
