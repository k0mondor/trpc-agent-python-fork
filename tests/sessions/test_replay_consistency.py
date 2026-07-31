"""Replay consistency smoke tests across session and memory backends."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from time import perf_counter
from typing import Callable
from typing import Type

import pytest

from .replay_cases import REPLAY_ACCEPTANCE_CASES
from .replay_cases import REPLAY_ALL_CASES
from .replay_cases import REPLAY_EXTRA_CASES
from .replay_cases import REPLAY_TARGETED_CASES
from .replay_harness import DEFAULT_REPORT_PATH
from .replay_harness import InMemoryReplayAdapter
from .replay_harness import RedisReplayAdapter
from .replay_harness import ReplayBackendAdapter
from .replay_harness import SqliteReplayAdapter
from .replay_harness import get_replay_clock_metadata
from .replay.allowed_diff import validate_allowed_diff_rules
from .replay.comparator import diff_backend_snapshots
from .replay.comparator import expected_diff_paths_for_backend_pair
from .replay.comparator import format_diffs
from .replay.injectors import inject_redis_event_author
from .replay.injectors import inject_redis_session_state
from .replay.injectors import inject_sqlite_event_author
from .replay.injectors import inject_sqlite_session_state
from .replay.injectors import RawStorageTarget
from .replay.report import build_acceptance_case_report
from .replay.report import build_acceptance_quality_metrics
from .replay.report import build_case_matrix_report
from .replay.report import build_comparison_report
from .replay.report import write_diff_report
from .replay_models import BackendSnapshot
from .replay_models import DiffEntry
from .replay_models import ReplayCase


ADAPTER_TYPES: tuple[Type[ReplayBackendAdapter], ...] = (
    InMemoryReplayAdapter,
    SqliteReplayAdapter,
)
REDIS_REPLAY_URL_ENV = "TRPC_AGENT_REPLAY_REDIS_URL"
AdapterFactory = Callable[[], ReplayBackendAdapter]

SUPPORTED_REPLAY_MODES = (
    "inmemory_only",
    "lightweight_inmemory_sqlite",
    "integration_inmemory_sqlite_redis",
)
REQUIRED_SCENARIO_COVERAGE = {
    "single_turn_dialogue": ["single_turn_event_author_injection"],
    "multi_turn_dialogue": ["multi_turn_event_text_injection"],
    "tool_call_dialogue": ["tool_call_name_injection"],
    "state_updates": ["state_value_injection", "runtime_state_corruption_fault"],
    "memory_write_and_read": ["memory_result_loss_injection", "cross_session_memory_aggregation"],
    "summary_generation_and_update": ["summary_version_injection", "summary_text_injection"],
    "summary_event_compaction": ["summary_text_injection", "restart_mid_replay_after_summary"],
    "exception_recovery": ["duplicate_event_runtime_fault", "partial_failure_event_loss_fault"],
}


def _build_backend_statuses(
    adapter_factories: tuple[AdapterFactory, ...],
    backend_report_metadata: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    statuses: list[dict[str, object]] = []
    enabled_names = {factory.name for factory in adapter_factories}
    for factory in adapter_factories:
        persistent = factory.name != InMemoryReplayAdapter.name
        statuses.append({
            "name": factory.name,
            "status": "enabled",
            "persistent": persistent,
            "restart_before_snapshot": persistent,
            "runtime": backend_report_metadata.get(factory.name, {}),
        })
    if RedisReplayAdapter.name not in enabled_names:
        statuses.append({
            "name": RedisReplayAdapter.name,
            "status": "skipped",
            "persistent": True,
            "restart_before_snapshot": True,
            "reason": f"Set {REDIS_REPLAY_URL_ENV} to enable Redis integration mode.",
        })
    return statuses


def _build_report_metadata(
    *,
    adapter_factories: tuple[AdapterFactory, ...],
    backend_report_metadata: dict[str, dict[str, object]],
    mode_name: str,
    elapsed_seconds: float,
    comparison_mode: str,
    acceptance_case_count: int,
    extra_case_count: int,
    quality_metrics: dict[str, object],
) -> dict[str, object]:
    return {
        "mode": mode_name,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "backend_names": [factory.name for factory in adapter_factories],
        "baseline_backend": adapter_factories[0].name,
        "comparison_mode": comparison_mode,
        "supported_modes": list(SUPPORTED_REPLAY_MODES),
        "backend_statuses": _build_backend_statuses(
            adapter_factories,
            backend_report_metadata,
        ),
        "required_scenario_coverage": REQUIRED_SCENARIO_COVERAGE,
        "clock_strategy": get_replay_clock_metadata(),
        "acceptance_case_count": acceptance_case_count,
        "extra_case_count": extra_case_count,
        "quality_metrics": quality_metrics,
    }


def _find_case(case_id: str) -> ReplayCase:
    for case in REPLAY_ALL_CASES + REPLAY_TARGETED_CASES:
        if case.case_id == case_id:
            return case
    raise KeyError(f"Unknown replay case: {case_id}")


async def _run_case_on_backend(
    adapter_factory: AdapterFactory,
    case: ReplayCase,
) -> tuple[BackendSnapshot, dict[str, object], dict[str, object]]:
    adapter = adapter_factory()
    await adapter.setup(case)
    try:
        snapshot = await adapter.run_case(case)
        return snapshot, adapter.get_runtime_metadata(), adapter.get_report_metadata()
    finally:
        await adapter.close()


async def _run_replay_cases(
    adapter_factories: tuple[AdapterFactory, ...],
    *,
    mode_name: str,
    cases: tuple[ReplayCase, ...] = REPLAY_ALL_CASES,
    write_report: bool = True,
) -> tuple[list[DiffEntry], list[dict[str, object]], float]:
    all_diffs: list[DiffEntry] = []
    case_reports: list[dict[str, object]] = []
    backend_report_metadata: dict[str, dict[str, object]] = {}
    start_time = perf_counter()

    for case in cases:
        backend_runs = [await _run_case_on_backend(adapter_factory, case) for adapter_factory in adapter_factories]
        snapshots = [run[0] for run in backend_runs]
        runtime_metadata = {
            snapshot.backend_name: runtime_info
            for snapshot, runtime_info, _ in backend_runs
        }
        for snapshot, _, report_metadata in backend_runs:
            backend_report_metadata.setdefault(snapshot.backend_name, report_metadata)
        baseline_snapshot = snapshots[0]
        comparisons: list[dict[str, object]] = []
        for other_snapshot in snapshots[1:]:
            diffs = diff_backend_snapshots(case=case, left=baseline_snapshot, right=other_snapshot)
            all_diffs.extend(diffs)
            comparisons.append(
                build_comparison_report(
                    case,
                    backend_a=baseline_snapshot.backend_name,
                    backend_b=other_snapshot.backend_name,
                    diffs=diffs,
                    runtime_context={
                        baseline_snapshot.backend_name: runtime_metadata[baseline_snapshot.backend_name],
                        other_snapshot.backend_name: runtime_metadata[other_snapshot.backend_name],
                    },
                ))
        case_reports.append(build_case_matrix_report(case, comparisons))

    elapsed_seconds = perf_counter() - start_time
    if write_report:
        write_diff_report(
            DEFAULT_REPORT_PATH,
            case_reports,
            metadata=_build_report_metadata(
                adapter_factories=adapter_factories,
                backend_report_metadata=backend_report_metadata,
                mode_name=mode_name,
                elapsed_seconds=elapsed_seconds,
                comparison_mode="baseline_vs_all",
                acceptance_case_count=len(REPLAY_ACCEPTANCE_CASES),
                extra_case_count=len(cases),
                quality_metrics={},
            ),
        )
    return all_diffs, case_reports, elapsed_seconds


def _without_injections(case: ReplayCase) -> ReplayCase:
    return replace(
        case,
        expected_diff_paths=(),
        snapshot_mutations=(),
        runtime_faults=(),
    )


async def _run_acceptance_cases(
    adapter_factories: tuple[AdapterFactory, ...],
) -> tuple[list[dict[str, object]], float, dict[str, dict[str, object]]]:
    """Replay each public trajectory once, then compare clean and injected views."""

    case_reports: list[dict[str, object]] = []
    backend_report_metadata: dict[str, dict[str, object]] = {}
    start_time = perf_counter()
    for injected_case in REPLAY_ACCEPTANCE_CASES:
        clean_case = _without_injections(injected_case)
        backend_runs = [
            await _run_case_on_backend(adapter_factory, clean_case)
            for adapter_factory in adapter_factories
        ]
        snapshots = [run[0] for run in backend_runs]
        runtime_metadata = {
            snapshot.backend_name: runtime_info
            for snapshot, runtime_info, _ in backend_runs
        }
        for snapshot, _, report_metadata in backend_runs:
            backend_report_metadata.setdefault(snapshot.backend_name, report_metadata)
        baseline_snapshot = snapshots[0]
        normal_comparisons: list[dict[str, object]] = []
        injected_comparisons: list[dict[str, object]] = []

        for other_snapshot in snapshots[1:]:
            runtime_context = {
                baseline_snapshot.backend_name: runtime_metadata[baseline_snapshot.backend_name],
                other_snapshot.backend_name: runtime_metadata[other_snapshot.backend_name],
            }
            normal_comparisons.append(
                build_comparison_report(
                    clean_case,
                    backend_a=baseline_snapshot.backend_name,
                    backend_b=other_snapshot.backend_name,
                    diffs=diff_backend_snapshots(
                        case=clean_case,
                        left=baseline_snapshot,
                        right=other_snapshot,
                    ),
                    runtime_context=runtime_context,
                ))
            injected_comparisons.append(
                build_comparison_report(
                    injected_case,
                    backend_a=baseline_snapshot.backend_name,
                    backend_b=other_snapshot.backend_name,
                    diffs=diff_backend_snapshots(
                        case=injected_case,
                        left=baseline_snapshot,
                        right=other_snapshot,
                    ),
                    runtime_context=runtime_context,
                ))

        case_reports.append(
            build_acceptance_case_report(
                injected_case,
                normal_comparisons=normal_comparisons,
                injected_comparisons=injected_comparisons,
            ))

    return case_reports, perf_counter() - start_time, backend_report_metadata


async def _run_full_suite(
    adapter_factories: tuple[AdapterFactory, ...],
    *,
    mode_name: str,
) -> tuple[
    dict[str, object],
    list[dict[str, object]],
    list[DiffEntry],
    list[dict[str, object]],
    float,
]:
    acceptance_reports, acceptance_elapsed, backend_report_metadata = await _run_acceptance_cases(
        adapter_factories
    )
    extra_diffs, extra_reports, extra_elapsed = await _run_replay_cases(
        adapter_factories,
        mode_name=mode_name,
        cases=REPLAY_EXTRA_CASES,
        write_report=False,
    )
    quality_metrics = build_acceptance_quality_metrics(acceptance_reports)
    elapsed_seconds = acceptance_elapsed + extra_elapsed
    write_diff_report(
        DEFAULT_REPORT_PATH,
        acceptance_reports + extra_reports,
        metadata=_build_report_metadata(
            adapter_factories=adapter_factories,
            backend_report_metadata=backend_report_metadata,
            mode_name=mode_name,
            elapsed_seconds=elapsed_seconds,
            comparison_mode="normal_then_injected_baseline_vs_all",
            acceptance_case_count=len(REPLAY_ACCEPTANCE_CASES),
            extra_case_count=len(REPLAY_EXTRA_CASES),
            quality_metrics=quality_metrics,
        ),
    )
    return quality_metrics, acceptance_reports, extra_diffs, extra_reports, elapsed_seconds


def _make_redis_adapter_factory() -> AdapterFactory:
    redis_url = os.getenv(REDIS_REPLAY_URL_ENV)
    if not redis_url:
        raise RuntimeError("Redis replay URL is not configured.")

    class _ConfiguredRedisReplayAdapter(RedisReplayAdapter):
        name = "redis"

        def __init__(self) -> None:
            super().__init__(redis_url=redis_url)

    return _ConfiguredRedisReplayAdapter


def _assert_case_expectations(
    all_diffs: list[DiffEntry],
    case_set: tuple[ReplayCase, ...],
    case_reports: list[dict[str, object]],
) -> None:
    case_map = {case.case_id: case for case in case_set}
    report_map = {str(report["case_id"]): report for report in case_reports}

    for case_id, case in case_map.items():
        case_report = report_map[case_id]
        comparisons = case_report.get("comparisons", [])
        for comparison in comparisons:
            backend_a = str(comparison["backend_a"])
            backend_b = str(comparison["backend_b"])
            expected_paths = set(expected_diff_paths_for_backend_pair(case, backend_a=backend_a, backend_b=backend_b))
            case_diffs = [
                diff
                for diff in all_diffs
                if diff.case_id == case_id
                and diff.backend_a == backend_a
                and diff.backend_b == backend_b
                and not diff.allowed
            ]
            detected_paths = {diff.path for diff in case_diffs}

            if expected_paths:
                missing_paths = sorted(expected_paths - detected_paths)
                unexpected_paths = sorted(detected_paths - expected_paths)
                assert not missing_paths, (
                    f"{case_id} missing expected diffs for {backend_a} vs {backend_b}: {missing_paths}"
                )
                assert not unexpected_paths, (
                    f"{case_id} produced unexpected diffs for {backend_a} vs {backend_b}: {unexpected_paths}\n"
                    f"{format_diffs(case_diffs)}"
                )
                continue

            assert not case_diffs, (
                f"{case_id} produced unexpected diffs for {backend_a} vs {backend_b}:\n"
                f"{format_diffs(case_diffs)}"
            )


def _assert_acceptance_report_locations(case_reports: list[dict[str, object]]) -> None:
    for case_report in case_reports:
        for comparison in case_report["injected_comparisons"]:
            for diff in comparison["diffs"]:
                assert diff["session_id"], f"{case_report['case_id']} diff has no session id"
                assert diff["left"] != diff["right"]
                if ".events[" in diff["path"]:
                    assert diff["event_index"] is not None
                if diff["path"].startswith("summary"):
                    assert diff["summary_id"], f"{case_report['case_id']} summary diff has no summary id"


def test_replay_consistency_smoke_cases() -> None:
    """Ensure acceptance and extended replay cases behave as expected."""

    quality_metrics, acceptance_reports, extra_diffs, extra_reports, elapsed_seconds = asyncio.run(
        _run_full_suite(ADAPTER_TYPES, mode_name="lightweight")
    )
    _assert_acceptance_report_locations(acceptance_reports)
    _assert_case_expectations(extra_diffs, REPLAY_EXTRA_CASES, extra_reports)

    assert quality_metrics["injection_detection_rate"] == 1.0
    assert quality_metrics["normal_false_positive_rate"] <= 0.05
    assert quality_metrics["summary_fault_detection_rate"] == 1.0
    assert elapsed_seconds <= 30.0, f"lightweight replay mode exceeded 30s: {elapsed_seconds:.3f}s"

    report = json.loads(DEFAULT_REPORT_PATH.read_text(encoding="utf-8"))
    assert report["summary"]["overall_status"] == "passed"
    assert report["summary"]["failed_case_count"] == 0
    assert report["summary"]["not_evaluated_case_count"] == 0
    assert [item["criterion_id"] for item in report["acceptance_criteria"]] == [
        "AC1",
        "AC2",
        "AC3",
        "AC4",
        "AC5",
        "AC6",
    ]
    assert all(item["status"] == "passed" for item in report["acceptance_criteria"])
    assert set(report["meta"]["required_scenario_coverage"]) == set(REQUIRED_SCENARIO_COVERAGE)


def test_replay_inmemory_only_lightweight_mode() -> None:
    """All public trajectories must also run without any external persistence service."""

    async def run() -> tuple[list[BackendSnapshot], float]:
        start_time = perf_counter()
        snapshots = [
            (
                await _run_case_on_backend(
                    InMemoryReplayAdapter,
                    _without_injections(case),
                )
            )[0]
            for case in REPLAY_ACCEPTANCE_CASES
        ]
        return snapshots, perf_counter() - start_time

    snapshots, elapsed_seconds = asyncio.run(run())
    assert len(snapshots) == 10
    assert all(snapshot.backend_name == "inmemory" for snapshot in snapshots)
    assert all(snapshot.sessions_by_alias for snapshot in snapshots)
    assert elapsed_seconds <= 30.0


def test_acceptance_case_count() -> None:
    """Keep the public acceptance suite fixed at 10 cases."""

    assert len(REPLAY_ACCEPTANCE_CASES) == 10
    assert len({case.case_id for case in REPLAY_ACCEPTANCE_CASES}) == 10
    assert all(case.expected_diff_paths for case in REPLAY_ACCEPTANCE_CASES)
    assert all(len(case.snapshot_mutations) == 1 for case in REPLAY_ACCEPTANCE_CASES)
    assert all(not case.runtime_faults for case in REPLAY_ACCEPTANCE_CASES)
    assert len(REPLAY_ALL_CASES) >= len(REPLAY_ACCEPTANCE_CASES)
    assert len(REPLAY_EXTRA_CASES) >= 1


def test_allowed_diff_requires_reason_and_protects_summary_lineage() -> None:
    """Allowed diffs must be explained and cannot mask summary ownership or lineage."""

    case = _without_injections(REPLAY_ACCEPTANCE_CASES[0])
    with pytest.raises(ValueError, match="without reasons"):
        validate_allowed_diff_rules(replace(case, allowed_diff_paths=("session.conversation_count",)))

    with pytest.raises(ValueError, match="strict summary metadata"):
        validate_allowed_diff_rules(
            replace(
                case,
                allowed_diff_paths=("summary.session_id",),
                allowed_diff_reasons={"summary.session_id": "Backend-specific ownership."},
            ))


def test_replay_harness_collects_all_session_alias_snapshots() -> None:
    """Non-active sessions should remain visible in the final snapshot."""

    snapshot, _, _ = asyncio.run(
        _run_case_on_backend(InMemoryReplayAdapter, _find_case("cross_session_memory_aggregation"))
    )

    assert snapshot.active_session_alias == "default"
    assert set(snapshot.sessions_by_alias) == {"source", "default"}
    assert snapshot.sessions_by_alias["source"].session_id == "replay-memory-source"
    assert (
        snapshot.sessions_by_alias["source"].session["events"][0]["text"]
        == "Please remember that I prefer oolong tea."
    )
    assert snapshot.sessions_by_alias["default"].session_id == "replay-memory-target"


def test_replay_snapshot_preserves_function_call_correlation_ids() -> None:
    """Tool call/response IDs are business linkage and must remain comparable."""

    case = _without_injections(REPLAY_ACCEPTANCE_CASES[2])
    snapshot, _, _ = asyncio.run(_run_case_on_backend(InMemoryReplayAdapter, case))
    call = snapshot.session["events"][1]["function_calls"][0]
    response = snapshot.session["events"][2]["function_responses"][0]
    assert call["call_id"] == "call-weather-1"
    assert response["call_id"] == call["call_id"]


def test_replay_harness_preserves_memory_query_observations_across_restart() -> None:
    """Repeated query names should preserve separate observations before and after restart."""

    snapshot, _, _ = asyncio.run(
        _run_case_on_backend(SqliteReplayAdapter, _find_case("memory_query_observation_survives_restart"))
    )

    observations = sorted(snapshot.memory.values(), key=lambda item: item["step_index"])
    assert [item["query_name"] for item in observations] == ["tea_preference", "tea_preference"]
    assert [item["session_alias"] for item in observations] == ["default", "default"]
    assert len(observations) == 2
    first_texts = {entry["text"] for entry in observations[0]["entries"]}
    second_texts = {entry["text"] for entry in observations[1]["entries"]}
    assert "Please remember that my favorite tea is oolong." in first_texts
    assert "I will remember your oolong preference." in first_texts
    assert "Also remember that I enjoy jasmine tea." in second_texts
    assert "I will remember the jasmine preference too." in second_texts
    assert "Also remember that I enjoy jasmine tea." not in first_texts


def test_replay_harness_keeps_duplicate_query_names_per_session_alias() -> None:
    """Query names may repeat across aliases without overwriting previous observations."""

    snapshot, _, _ = asyncio.run(
        _run_case_on_backend(SqliteReplayAdapter, _find_case("duplicate_memory_query_name_across_sessions"))
    )

    observations = sorted(snapshot.memory.values(), key=lambda item: item["step_index"])
    assert len(observations) == 2
    assert [item["query_name"] for item in observations] == ["shared_preference_search", "shared_preference_search"]
    assert [item["session_alias"] for item in observations] == ["source", "default"]
    assert observations[0]["step_index"] < observations[1]["step_index"]
    assert observations[0]["entries"] != observations[1]["entries"]
    first_texts = {entry["text"] for entry in observations[0]["entries"]}
    second_texts = {entry["text"] for entry in observations[1]["entries"]}
    assert any("dragon well" in text.lower() for text in first_texts)
    assert any("dragon well" in text.lower() for text in second_texts)


def test_raw_sqlite_corruption_is_detected_after_restart() -> None:
    """Out-of-band event and state corruption must survive restart and be located."""

    async def run() -> tuple[list[DiffEntry], list[DiffEntry]]:
        event_case = _without_injections(REPLAY_ACCEPTANCE_CASES[0])
        event_adapter = SqliteReplayAdapter()
        await event_adapter.setup(event_case)
        try:
            before_event = await event_adapter.run_case(event_case)
            event_identity = RawStorageTarget(**event_adapter.storage_identity())
            assert inject_sqlite_event_author(
                event_adapter.session_db_url,
                event_identity,
                event_id="replay-event-1",
            )
            after_event = await event_adapter.read_persisted_snapshot()
            event_diffs = diff_backend_snapshots(
                case=event_case,
                left=before_event,
                right=after_event,
            )
        finally:
            await event_adapter.close()

        state_case = _without_injections(REPLAY_ACCEPTANCE_CASES[3])
        state_adapter = SqliteReplayAdapter()
        await state_adapter.setup(state_case)
        try:
            before_state = await state_adapter.run_case(state_case)
            state_identity = RawStorageTarget(**state_adapter.storage_identity())
            assert inject_sqlite_session_state(
                state_adapter.session_db_url,
                state_identity,
                key="raw_corruption",
                value="sqlite",
            )
            after_state = await state_adapter.read_persisted_snapshot()
            state_diffs = diff_backend_snapshots(
                case=state_case,
                left=before_state,
                right=after_state,
            )
        finally:
            await state_adapter.close()
        return event_diffs, state_diffs

    event_diffs, state_diffs = asyncio.run(run())
    assert {diff.path for diff in event_diffs if not diff.allowed} == {"session.events[0].author"}
    assert {diff.path for diff in state_diffs if not diff.allowed} == {"state.raw_corruption"}


def test_replay_consistency_redis_integration_mode() -> None:
    """Run an optional Redis-backed integration comparison when configured."""

    redis_url = os.getenv(REDIS_REPLAY_URL_ENV)
    if not redis_url:
        pytest.skip(f"{REDIS_REPLAY_URL_ENV} is not set")

    redis_adapter_factory = _make_redis_adapter_factory()
    quality_metrics, acceptance_reports, extra_diffs, extra_reports, _ = asyncio.run(
        _run_full_suite(
            (InMemoryReplayAdapter, SqliteReplayAdapter, redis_adapter_factory),
            mode_name="integration",
        ))
    _assert_acceptance_report_locations(acceptance_reports)
    _assert_case_expectations(extra_diffs, REPLAY_EXTRA_CASES, extra_reports)
    assert quality_metrics["injection_detection_rate"] == 1.0
    assert quality_metrics["normal_false_positive_rate"] <= 0.05
    assert quality_metrics["summary_fault_detection_rate"] == 1.0


def test_raw_redis_corruption_is_detected_after_restart() -> None:
    """Run raw Redis event/state corruption checks when integration is enabled."""

    redis_url = os.getenv(REDIS_REPLAY_URL_ENV)
    if not redis_url:
        pytest.skip(f"{REDIS_REPLAY_URL_ENV} is not set")

    async def run() -> tuple[list[DiffEntry], list[DiffEntry]]:
        event_case = _without_injections(REPLAY_ACCEPTANCE_CASES[0])
        event_adapter = RedisReplayAdapter(redis_url=redis_url)
        await event_adapter.setup(event_case)
        try:
            before_event = await event_adapter.run_case(event_case)
            event_identity = RawStorageTarget(**event_adapter.storage_identity())
            assert inject_redis_event_author(redis_url, event_identity)
            after_event = await event_adapter.read_persisted_snapshot()
            event_diffs = diff_backend_snapshots(
                case=event_case,
                left=before_event,
                right=after_event,
            )
        finally:
            await event_adapter.close()

        state_case = _without_injections(REPLAY_ACCEPTANCE_CASES[3])
        state_adapter = RedisReplayAdapter(redis_url=redis_url)
        await state_adapter.setup(state_case)
        try:
            before_state = await state_adapter.run_case(state_case)
            state_identity = RawStorageTarget(**state_adapter.storage_identity())
            assert inject_redis_session_state(
                redis_url,
                state_identity,
                key="raw_corruption",
                value="redis",
            )
            after_state = await state_adapter.read_persisted_snapshot()
            state_diffs = diff_backend_snapshots(
                case=state_case,
                left=before_state,
                right=after_state,
            )
        finally:
            await state_adapter.close()
        return event_diffs, state_diffs

    event_diffs, state_diffs = asyncio.run(run())
    assert {diff.path for diff in event_diffs if not diff.allowed} == {"session.events[0].author"}
    assert {diff.path for diff in state_diffs if not diff.allowed} == {"state.raw_corruption"}
