"""Storage and report integration tests for the code review example."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import Mock

from examples.skills_code_review_agent.agent.agent import run_review_task
from examples.skills_code_review_agent.agent import agent as agent_module
from examples.skills_code_review_agent.agent.config import ReviewAgentConfig
from examples.skills_code_review_agent.agent import tools as agent_tools
from examples.skills_code_review_agent.src.storage.repository import ReviewRepository

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_run_review_task_writes_report_files_and_database(tmp_path: Path) -> None:
    """Running the pipeline should persist records and write both report artifacts."""

    output_dir = tmp_path / "outputs"
    db_path = tmp_path / "review.db"
    config = ReviewAgentConfig(
        fixture_path=str(FIXTURES_DIR / "security_issue.diff"),
        output_dir=output_dir,
        db_path=db_path,
        runtime="local",
        dry_run=True,
        fake_model=True,
    )

    task, report = run_review_task(config)

    json_path = output_dir / "review_report.json"
    markdown_path = output_dir / "review_report.md"
    assert json_path.exists()
    assert markdown_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["task_id"] == task.task_id
    assert payload["conclusion"] == "fail"
    assert payload["findings"]

    markdown = markdown_path.read_text(encoding="utf-8")
    assert "# Review Report" in markdown
    assert "## Findings" in markdown
    assert "## Monitoring" in markdown

    repository = ReviewRepository(db_path)
    bundle = repository.get_review_bundle(task.task_id)
    assert bundle["task"]["task_id"] == task.task_id
    assert bundle["input"]["changed_files_count"] == 2
    assert len(bundle["findings"]) >= 2
    assert bundle["report"]["final_verdict"] == report.conclusion.value
    assert json.loads(bundle["report"]["report_json"]) == payload
    assert bundle["report"]["report_markdown"] == markdown


def test_run_review_task_persists_human_review_state(tmp_path: Path) -> None:
    """Missing-test scenarios should persist a needs-human-review conclusion."""

    config = ReviewAgentConfig(
        fixture_path=str(FIXTURES_DIR / "missing_tests.diff"),
        output_dir=tmp_path / "outputs",
        db_path=tmp_path / "review.db",
        runtime="local",
        dry_run=True,
        fake_model=True,
    )

    task, report = run_review_task(config)

    repository = ReviewRepository(tmp_path / "review.db")
    bundle = repository.get_review_bundle(task.task_id)
    assert report.conclusion.value == "needs_human_review"
    assert bundle["report"]["final_verdict"] == "needs_human_review"
    assert bundle["task"]["status"] == "completed"


def test_secret_values_are_redacted_in_reports_and_database(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Secrets must be redacted before report generation and persistence."""

    temporary_input_dirs: list[Path] = []

    def tracked_temporary_directory(**kwargs):
        context = tempfile.TemporaryDirectory(dir=tmp_path, **kwargs)
        temporary_input_dirs.append(Path(context.name))
        return context

    monkeypatch.setattr(
        agent_module,
        "TemporaryDirectory",
        tracked_temporary_directory,
    )

    config = ReviewAgentConfig(
        fixture_path=str(FIXTURES_DIR / "secret_redaction.diff"),
        output_dir=tmp_path / "outputs",
        db_path=tmp_path / "review.db",
        runtime="local",
        dry_run=True,
        fake_model=True,
    )

    task, _report = run_review_task(config)

    json_payload = json.loads(
        (tmp_path / "outputs" / "review_report.json").read_text(encoding="utf-8")
    )
    markdown = (tmp_path / "outputs" / "review_report.md").read_text(encoding="utf-8")
    bundle = ReviewRepository(tmp_path / "review.db").get_review_bundle(task.task_id)

    forbidden_fragments = [
        "sk-test-1234567890abcdef",
        "Bearer super-secret-token-value",
        "super-secret-token-value",
    ]
    joined_db_text = json.dumps(bundle, ensure_ascii=False)
    expected_diff_sha256 = hashlib.sha256(
        (FIXTURES_DIR / "secret_redaction.diff")
        .read_text(encoding="utf-8")
        .encode("utf-8")
    ).hexdigest()
    for fragment in forbidden_fragments:
        assert fragment not in task.review_input.diff_text
        assert task.parsed_diff is not None
        assert fragment not in task.parsed_diff.raw_diff
        assert all(
            fragment not in line.raw_line
            for changed_file in task.parsed_diff.files
            for hunk in changed_file.hunks
            for line in hunk.lines
        )
        assert fragment not in json.dumps(json_payload, ensure_ascii=False)
        assert fragment not in markdown
        assert fragment not in joined_db_text
    assert temporary_input_dirs
    assert all(not path.exists() for path in temporary_input_dirs)
    assert not (tmp_path / "outputs" / "skill_inputs").exists()
    assert bundle["input"]["diff_sha256"] == expected_diff_sha256


def test_filter_denies_forbidden_paths_and_skips_sandbox(tmp_path: Path) -> None:
    """Forbidden paths should be denied before any sandbox script executes."""

    diff_path = tmp_path / "forbidden.diff"
    diff_path.write_text(
        """diff --git a/.env b/.env
--- a/.env
+++ b/.env
@@ -0,0 +1 @@
+API_KEY="sk-test-unsafe"
""",
        encoding="utf-8",
    )

    config = ReviewAgentConfig(
        diff_file=str(diff_path),
        output_dir=tmp_path / "outputs",
        db_path=tmp_path / "review.db",
        runtime="local",
        dry_run=True,
        fake_model=True,
    )

    task, _report = run_review_task(config)

    assert task.filter_decisions
    assert all(decision.decision.value == "deny" for decision in task.filter_decisions)
    assert task.sandbox_runs
    assert all(run.status.value == "blocked" for run in task.sandbox_runs)


def test_sandbox_failure_is_recorded_without_crashing_task(tmp_path: Path) -> None:
    """Sandbox failures should be recorded as findings while the review still completes."""

    config = ReviewAgentConfig(
        fixture_path=str(FIXTURES_DIR / "sandbox_failure.diff"),
        output_dir=tmp_path / "outputs",
        db_path=tmp_path / "review.db",
        runtime="local",
        dry_run=True,
        fake_model=True,
    )

    task, report = run_review_task(config)

    assert task.status.value == "completed"
    assert any(run.status.value == "failed" for run in task.sandbox_runs)
    assert any(finding.category.value == "sandbox" for finding in task.findings)
    assert report.monitoring_summary["sandbox_run_count"] >= 1


def test_container_runtime_dispatches_through_skill_run(monkeypatch, tmp_path: Path) -> None:
    """The orchestrator should delegate staging and execution to ``skill_run``."""

    manager = Mock()
    manager.cleanup = AsyncMock()
    runtime = Mock()
    runtime.manager.return_value = manager
    repository = Mock()
    repository.get_workspace_runtime.return_value = runtime

    run_tool = Mock()
    run_tool.name = "skill_run"
    run_tool.run_async = AsyncMock(
        return_value={
            "stdout": '{"warning_count": 1, "warnings": ["Security-sensitive call detected: eval"]}\n',
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 3,
            "warnings": [],
        }
    )
    tool_set = Mock()
    tool_set.get_tools = AsyncMock(return_value=[run_tool])

    monkeypatch.setattr(
        agent_tools,
        "create_skill_tool_set",
        lambda **_: (tool_set, repository),
    )

    config = ReviewAgentConfig(
        fixture_path=str(FIXTURES_DIR / "security_issue.diff"),
        output_dir=tmp_path / "outputs",
        db_path=tmp_path / "review.db",
        runtime="container",
        dry_run=True,
        fake_model=True,
    )

    task, report = run_review_task(config)

    assert task.status.value == "completed"
    assert task.filter_decisions
    assert all(decision.decision.value == "allow" for decision in task.filter_decisions)
    assert task.sandbox_runs
    assert all(run.status.value == "succeeded" for run in task.sandbox_runs)
    assert any(
        finding.source.value == "skill_script"
        and finding.title == "Use of eval introduces code execution risk"
        for finding in report.findings
    )
    assert run_tool.run_async.await_count == 3
    for call in run_tool.run_async.await_args_list:
        payload = call.kwargs["args"]
        assert payload["inputs"][0]["dst"] == "work/inputs/review.diff"
        assert payload["command"].startswith("python scripts/")
    manager.cleanup.assert_awaited()


def test_container_setup_failure_is_recorded(monkeypatch, tmp_path: Path) -> None:
    """An unavailable container executor must not crash the review task."""

    monkeypatch.setattr(
        agent_tools,
        "create_skill_tool_set",
        Mock(side_effect=RuntimeError("container runtime unavailable")),
    )
    config = ReviewAgentConfig(
        fixture_path=str(FIXTURES_DIR / "clean.diff"),
        output_dir=tmp_path / "outputs",
        db_path=tmp_path / "review.db",
        runtime="container",
        dry_run=True,
        fake_model=True,
    )

    task, report = run_review_task(config)

    assert task.status.value == "completed"
    assert all(run.status.value == "failed" for run in task.sandbox_runs)
    assert all(
        "container runtime unavailable" in run.stderr
        for run in task.sandbox_runs
    )
    assert any(finding.category.value == "sandbox" for finding in task.findings)
    assert report.conclusion.value == "needs_human_review"


def test_unconfigured_remote_runtime_is_not_executed(monkeypatch, tmp_path: Path) -> None:
    """Cube/E2B labels must not silently fall back to local host execution."""

    create_tool_set = Mock(side_effect=AssertionError("skill_run must not be called"))
    monkeypatch.setattr(agent_tools, "create_skill_tool_set", create_tool_set)

    config = ReviewAgentConfig(
        fixture_path=str(FIXTURES_DIR / "clean.diff"),
        output_dir=tmp_path / "outputs",
        db_path=tmp_path / "review.db",
        runtime="cube",
        dry_run=True,
        fake_model=True,
    )

    task, report = run_review_task(config)

    create_tool_set.assert_not_called()
    assert all(
        decision.reason_code == "runtime_not_configured"
        for decision in task.filter_decisions
    )
    assert all(run.status.value == "blocked" for run in task.sandbox_runs)
    assert report.conclusion.value == "needs_human_review"
