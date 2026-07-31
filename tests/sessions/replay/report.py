"""Replay comparison reports and acceptance quality metrics."""

from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path
from typing import Any, Optional

from ..replay_models import DiffEntry
from ..replay_models import ReplayCase
from .allowed_diff import rules_for_case
from .comparator import expected_diff_paths_for_backend_pair


REPORT_SCHEMA_VERSION = 5
COMPARISON_KEYS = ("normal_comparisons", "injected_comparisons", "comparisons")
SUMMARY_FAULT_CASE_IDS = {
    "summary_binding_mismatch_injection",
    "summary_missing_injection",
    "summary_lineage_corruption_injection",
}
REQUIRED_META_FIELDS = {
    "mode",
    "elapsed_seconds",
    "backend_names",
    "baseline_backend",
    "comparison_mode",
    "acceptance_case_count",
    "extra_case_count",
    "quality_metrics",
    "supported_modes",
    "backend_statuses",
    "required_scenario_coverage",
}


IMPLEMENTATION_PROFILE = {
    "adapter_lifecycle": "Backend setup, replay, restart, snapshot and close are isolated behind adapters.",
    "persistence_restart": "Persistent adapters close and reopen services before their final read-back.",
    "summary_lineage": "Summary content and session_id/summary_id/version/replaces are compared separately.",
    "multi_session_snapshot": "Every named session alias is retained in sessions_by_alias.",
    "memory_observations": "Memory reads retain query name, session alias and replay step index.",
    "allowed_diff_governance": "Rules require reasons, may scope backend pairs and cannot mask summary lineage.",
    "raw_storage_injection": "SQLite and optional Redis can be corrupted below the SDK boundary.",
    "exact_acceptance_metrics": "Expected, detected, missing and unexpected paths are reported independently.",
}


def _comparison_passed(comparison: dict[str, Any]) -> bool:
    return not comparison.get("missing_expected_paths") and not comparison.get(
        "unexpected_diff_paths"
    )


def _status_for_comparisons(comparisons: list[dict[str, Any]]) -> str:
    if not comparisons:
        return "not_evaluated"
    return "passed" if all(_comparison_passed(item) for item in comparisons) else "failed"


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
    comparison_report = {
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
    comparison_report["status"] = (
        "passed" if _comparison_passed(comparison_report) else "failed"
    )
    return comparison_report


def build_case_matrix_report(case: ReplayCase, comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "description": case.description,
        "scenario_type": "extended",
        "status": _status_for_comparisons(comparisons),
        "comparison_count": len(comparisons),
        "comparisons": comparisons,
    }


def build_acceptance_case_report(
    case: ReplayCase,
    *,
    normal_comparisons: list[dict[str, Any]],
    injected_comparisons: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build one public case result with separate clean and injected verdicts."""

    normal_status = _status_for_comparisons(normal_comparisons)
    injection_status = _status_for_comparisons(injected_comparisons)
    if "failed" in {normal_status, injection_status}:
        status = "failed"
    elif "not_evaluated" in {normal_status, injection_status}:
        status = "not_evaluated"
    else:
        status = "passed"
    return {
        "case_id": case.case_id,
        "description": case.description,
        "scenario_type": "normal_and_injected",
        "status": status,
        "normal_status": normal_status,
        "injection_status": injection_status,
        "normal_comparisons": normal_comparisons,
        "injected_comparisons": injected_comparisons,
    }


def build_acceptance_quality_metrics(case_reports: list[dict[str, Any]]) -> dict[str, Any]:
    normal_failed_case_ids: list[str] = []
    injection_missed_case_ids: list[str] = []
    reported_case_ids = {str(report["case_id"]) for report in case_reports}
    summary_fault_missed_case_ids = sorted(SUMMARY_FAULT_CASE_IDS - reported_case_ids)

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
            if case_id in SUMMARY_FAULT_CASE_IDS and case_id not in summary_fault_missed_case_ids:
                summary_fault_missed_case_ids.append(case_id)

    case_count = len(case_reports)
    detected_count = case_count - len(injection_missed_case_ids)
    normal_passed_count = case_count - len(normal_failed_case_ids)
    summary_fault_missed_case_ids.sort()
    summary_fault_count = len(SUMMARY_FAULT_CASE_IDS)
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


def _iter_comparisons(
    case_reports: list[dict[str, Any]],
) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    for report in case_reports:
        for key in COMPARISON_KEYS:
            for comparison in report.get(key, []):
                yield report, comparison


def _report_locator_metrics(case_reports: list[dict[str, Any]]) -> dict[str, Any]:
    checked_count = 0
    complete_count = 0
    incomplete: list[dict[str, str]] = []
    for report, comparison in _iter_comparisons(case_reports):
        for diff in comparison.get("diffs", []):
            if diff.get("allowed"):
                continue
            checked_count += 1
            path = str(diff.get("path", ""))
            complete = bool(
                diff.get("session_id")
                and path
                and "left" in diff
                and "right" in diff
                and diff["left"] != diff["right"]
            )
            if ".events[" in path:
                complete = complete and diff.get("event_index") is not None
            elif path.startswith("summary") or ".summary" in path:
                complete = complete and bool(diff.get("summary_id"))
            if complete:
                complete_count += 1
            else:
                incomplete.append({
                    "case_id": str(report.get("case_id", "")),
                    "path": path,
                })
    return {
        "checked_diff_count": checked_count,
        "complete_diff_count": complete_count,
        "completeness_rate": complete_count / checked_count if checked_count else 0.0,
        "incomplete_diffs": incomplete,
    }


def build_acceptance_criteria(
    metadata: dict[str, Any],
    case_reports: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Translate the six issue acceptance clauses into machine-readable verdicts."""

    metrics = metadata.get("quality_metrics", {})
    backend_statuses = metadata.get("backend_statuses", [])
    enabled_backends = [item for item in backend_statuses if item.get("status") == "enabled"]
    persistent_backends = [item for item in enabled_backends if item.get("persistent")]
    backend_passed = len(enabled_backends) >= 2 and bool(persistent_backends)
    locator_metrics = _report_locator_metrics(case_reports)
    locator_passed = (
        locator_metrics["checked_diff_count"] > 0
        and locator_metrics["completeness_rate"] == 1.0
    )
    lightweight_elapsed = metadata.get("elapsed_seconds")
    if metadata.get("mode") == "lightweight":
        performance_status = "passed" if lightweight_elapsed <= 30.0 else "failed"
        performance_actual: Any = lightweight_elapsed
    else:
        performance_status = "not_evaluated"
        performance_actual = "Only lightweight mode has a 30-second requirement."

    def criterion(
        criterion_id: str,
        requirement: str,
        status: str,
        actual: Any,
        threshold: str,
    ) -> dict[str, Any]:
        return {
            "criterion_id": criterion_id,
            "requirement": requirement,
            "status": status,
            "actual": actual,
            "threshold": threshold,
        }

    def verdict(passed: bool) -> str:
        return "passed" if passed else "failed"
    return [
        criterion(
            "AC1",
            "Compare InMemory with at least one persistent or simulated persistent backend.",
            verdict(backend_passed),
            {
                "enabled_backends": [item.get("name") for item in enabled_backends],
                "persistent_backends": [item.get("name") for item in persistent_backends],
            },
            "at least 2 enabled backends, including 1 persistent backend",
        ),
        criterion(
            "AC2",
            "Detect every injected inconsistency in the 10 public replay cases.",
            verdict(
                metrics.get("public_case_count") == 10
                and metrics.get("injection_detection_rate") == 1.0
            ),
            {
                "case_count": metrics.get("public_case_count", 0),
                "detection_rate": metrics.get("injection_detection_rate", 0.0),
                "missed_case_ids": metrics.get("injection_missed_case_ids", []),
            },
            "10 cases and 100% detection",
        ),
        criterion(
            "AC3",
            "Keep the false-positive rate for normal cases at or below 5%.",
            verdict(metrics.get("normal_false_positive_rate", 1.0) <= 0.05),
            metrics.get("normal_false_positive_rate", 1.0),
            "<= 0.05",
        ),
        criterion(
            "AC4",
            "Detect summary loss, overwrite-lineage errors and session ownership errors.",
            verdict(metrics.get("summary_fault_detection_rate") == 1.0),
            {
                "detection_rate": metrics.get("summary_fault_detection_rate", 0.0),
                "missed_case_ids": metrics.get("summary_fault_missed_case_ids", []),
            },
            "100% across all 3 summary fault classes",
        ),
        criterion(
            "AC5",
            "Locate every diff by session, field path, values, and event or summary identity.",
            verdict(locator_passed),
            locator_metrics,
            "100% locator completeness",
        ),
        criterion(
            "AC6",
            "Complete lightweight mode within 30 seconds; integrations may be skipped.",
            performance_status,
            performance_actual,
            "<= 30 seconds in lightweight mode",
        ),
    ]


def build_report_summary(
    case_reports: list[dict[str, Any]],
    acceptance_criteria: list[dict[str, Any]],
) -> dict[str, Any]:
    statuses = [str(report.get("status", "not_evaluated")) for report in case_reports]
    diff_records = [
        (diff, comparison)
        for _, comparison in _iter_comparisons(case_reports)
        for diff in comparison.get("diffs", [])
    ]
    failed_criteria = [
        str(item["criterion_id"])
        for item in acceptance_criteria
        if item.get("status") == "failed"
    ]
    return {
        "overall_status": "failed" if "failed" in statuses or failed_criteria else "passed",
        "case_count": len(case_reports),
        "passed_case_count": statuses.count("passed"),
        "failed_case_count": statuses.count("failed"),
        "not_evaluated_case_count": statuses.count("not_evaluated"),
        "diff_count": len(diff_records),
        "expected_diff_count": sum(
            not diff.get("allowed", False)
            and diff.get("path") in comparison.get("expected_diff_paths", [])
            for diff, comparison in diff_records
        ),
        "allowed_diff_count": sum(bool(diff.get("allowed")) for diff, _ in diff_records),
        "unexpected_diff_count": sum(
            not diff.get("allowed", False)
            and diff.get("path") in comparison.get("unexpected_diff_paths", [])
            for diff, comparison in diff_records
        ),
        "failed_criterion_ids": failed_criteria,
    }


def validate_report_payload(payload: dict[str, Any]) -> None:
    """Validate the stable top-level contract mirrored by the JSON Schema file."""

    if payload.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ValueError(f"Unsupported replay report schema version: {payload.get('schema_version')}")
    typed_fields = {
        "meta": dict,
        "summary": dict,
        "acceptance_criteria": list,
        "implementation_profile": dict,
        "cases": list,
    }
    for field, expected_type in typed_fields.items():
        if not isinstance(payload.get(field), expected_type):
            raise ValueError(f"Replay report '{field}' must be a {expected_type.__name__}")
    missing_meta = sorted(REQUIRED_META_FIELDS - set(payload["meta"]))
    if missing_meta:
        raise ValueError(f"Replay report meta is missing required fields: {missing_meta}")
    for index, case_report in enumerate(payload["cases"]):
        if not isinstance(case_report, dict):
            raise ValueError(f"Replay report case {index} must be an object")
        if not isinstance(case_report.get("case_id"), str) or not case_report["case_id"]:
            raise ValueError(f"Replay report case {index} requires a non-empty case_id")
        if not isinstance(case_report.get("scenario_type"), str):
            raise ValueError(f"Replay report case {index} requires scenario_type")
        if case_report.get("status") not in {"passed", "failed", "not_evaluated"}:
            raise ValueError(f"Replay report case {index} requires a valid status")
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
    report_metadata = metadata or {}
    acceptance_criteria = build_acceptance_criteria(report_metadata, case_reports)
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "summary": build_report_summary(case_reports, acceptance_criteria),
        "acceptance_criteria": acceptance_criteria,
        "implementation_profile": IMPLEMENTATION_PROFILE,
        "meta": report_metadata,
        "cases": case_reports,
    }
    validate_report_payload(payload)
    report_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
