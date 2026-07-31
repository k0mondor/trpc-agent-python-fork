"""Replay comparison reports and acceptance quality metrics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ..replay_models import DiffEntry
from ..replay_models import ReplayCase
from .allowed_diff import rules_for_case
from .comparator import expected_diff_paths_for_backend_pair


REPORT_SCHEMA_VERSION = 4


def build_case_report(case: ReplayCase, diffs: list[DiffEntry]) -> dict[str, Any]:
    expected_paths = set(case.expected_diff_paths)
    allowed_paths = {rule.path for rule in rules_for_case(case)}
    detected_paths = {diff.path for diff in diffs if not diff.allowed}
    return {
        "case_id": case.case_id,
        "description": case.description,
        "expects_diffs": bool(expected_paths),
        "expected_diff_paths": sorted(expected_paths),
        "allowed_diff_paths": sorted(allowed_paths),
        "detected_diff_paths": sorted(detected_paths),
        "missing_expected_paths": sorted(expected_paths - detected_paths),
        "unexpected_diff_paths": sorted(detected_paths - expected_paths),
        "diffs": [diff.to_dict() for diff in diffs],
    }


def build_comparison_report(
    case: ReplayCase,
    *,
    backend_a: str,
    backend_b: str,
    diffs: list[DiffEntry],
    runtime_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    expected_paths = set(
        expected_diff_paths_for_backend_pair(case, backend_a=backend_a, backend_b=backend_b)
    )
    detected_paths = {diff.path for diff in diffs if not diff.allowed}
    return {
        "backend_a": backend_a,
        "backend_b": backend_b,
        "expected_diff_paths": sorted(expected_paths),
        "allowed_diff_paths": sorted(rule.path for rule in rules_for_case(case)),
        "detected_diff_paths": sorted(detected_paths),
        "missing_expected_paths": sorted(expected_paths - detected_paths),
        "unexpected_diff_paths": sorted(detected_paths - expected_paths),
        "runtime_context": runtime_context or {},
        "diffs": [diff.to_dict() for diff in diffs],
    }


def build_case_matrix_report(case: ReplayCase, comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    expected_paths = set(case.expected_diff_paths)
    detected_paths = {
        path
        for comparison in comparisons
        for path in comparison.get("detected_diff_paths", [])
    }
    return {
        "case_id": case.case_id,
        "description": case.description,
        "scenario_type": "extended",
        "expects_diffs": bool(expected_paths),
        "expected_diff_paths": sorted(expected_paths),
        "allowed_diff_paths": sorted(rule.path for rule in rules_for_case(case)),
        "detected_diff_paths": sorted(detected_paths),
        "missing_expected_paths": sorted(expected_paths - detected_paths),
        "unexpected_diff_paths": sorted(detected_paths - expected_paths),
        "comparison_count": len(comparisons),
        "comparisons": comparisons,
        "diffs": [diff for comparison in comparisons for diff in comparison.get("diffs", [])],
    }


def build_acceptance_quality_metrics(case_reports: list[dict[str, Any]]) -> dict[str, Any]:
    normal_failed_case_ids: list[str] = []
    injection_missed_case_ids: list[str] = []
    summary_fault_case_ids = {
        "summary_binding_mismatch_injection",
        "summary_missing_injection",
        "summary_lineage_corruption_injection",
    }
    reported_case_ids = {str(report["case_id"]) for report in case_reports}
    summary_fault_missed_case_ids = sorted(summary_fault_case_ids - reported_case_ids)

    for report in case_reports:
        case_id = str(report["case_id"])
        normal_comparisons = report.get("normal_comparisons", [])
        injected_comparisons = report.get("injected_comparisons", [])
        if any(comparison.get("detected_diff_paths") for comparison in normal_comparisons):
            normal_failed_case_ids.append(case_id)
        injection_detected = bool(injected_comparisons) and all(
            comparison.get("expected_diff_paths")
            and not comparison.get("missing_expected_paths")
            and not comparison.get("unexpected_diff_paths")
            for comparison in injected_comparisons
        )
        if not injection_detected:
            injection_missed_case_ids.append(case_id)
            if case_id in summary_fault_case_ids and case_id not in summary_fault_missed_case_ids:
                summary_fault_missed_case_ids.append(case_id)

    case_count = len(case_reports)
    detected_count = case_count - len(injection_missed_case_ids)
    normal_passed_count = case_count - len(normal_failed_case_ids)
    summary_fault_missed_case_ids.sort()
    summary_fault_count = len(summary_fault_case_ids)
    summary_fault_detected_count = summary_fault_count - len(summary_fault_missed_case_ids)
    return {
        "public_case_count": case_count,
        "injection_detected_count": detected_count,
        "injection_detection_rate": detected_count / case_count if case_count else 0.0,
        "injection_missed_case_ids": injection_missed_case_ids,
        "normal_passed_count": normal_passed_count,
        "normal_false_positive_count": len(normal_failed_case_ids),
        "normal_false_positive_rate": len(normal_failed_case_ids) / case_count if case_count else 0.0,
        "normal_false_positive_case_ids": normal_failed_case_ids,
        "summary_fault_case_count": summary_fault_count,
        "summary_fault_detected_count": summary_fault_detected_count,
        "summary_fault_detection_rate": (
            summary_fault_detected_count / summary_fault_count if summary_fault_count else 0.0
        ),
        "summary_fault_missed_case_ids": summary_fault_missed_case_ids,
    }


def validate_report_payload(payload: dict[str, Any]) -> None:
    """Validate the stable top-level contract mirrored by the JSON Schema file."""

    if payload.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ValueError(f"Unsupported replay report schema version: {payload.get('schema_version')}")
    if not isinstance(payload.get("meta"), dict):
        raise ValueError("Replay report 'meta' must be an object")
    if not isinstance(payload.get("cases"), list):
        raise ValueError("Replay report 'cases' must be an array")
    required_meta = {
        "mode",
        "elapsed_seconds",
        "backend_names",
        "baseline_backend",
        "comparison_mode",
        "acceptance_case_count",
        "extra_case_count",
        "quality_metrics",
    }
    missing_meta = sorted(required_meta - set(payload["meta"]))
    if missing_meta:
        raise ValueError(f"Replay report meta is missing required fields: {missing_meta}")
    for index, case_report in enumerate(payload["cases"]):
        if not isinstance(case_report, dict):
            raise ValueError(f"Replay report case {index} must be an object")
        if not isinstance(case_report.get("case_id"), str) or not case_report["case_id"]:
            raise ValueError(f"Replay report case {index} requires a non-empty case_id")
        if not isinstance(case_report.get("scenario_type"), str):
            raise ValueError(f"Replay report case {index} requires scenario_type")
        if case_report["scenario_type"] not in {"normal_and_injected", "extended"}:
            raise ValueError(
                f"Replay report case {index} has unsupported scenario_type: "
                f"{case_report['scenario_type']}"
            )


def write_diff_report(
    report_path: Path,
    case_reports: list[dict[str, Any]],
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "meta": metadata or {},
        "cases": case_reports,
    }
    validate_report_payload(payload)
    report_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
