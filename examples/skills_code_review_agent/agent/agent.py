"""Agent factory and orchestration for the skills code review example."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from uuid import uuid4

from .config import ReviewAgentConfig
from .tools import build_blocked_run, build_skill_script_plan, execute_skill_scripts
from ..src.deduper import dedupe_and_classify_findings
from ..src.diff_parser import parse_unified_diff
from ..src.filter_policy import evaluate_invocations
from ..src.input_loader import load_review_input
from ..src.redactor import redact_report, redact_task
from ..src.report_writer import build_report_payload, render_markdown_report, write_report_files
from ..src.rule_engine import run_rule_engine, security_finding_spec
from ..src.review_types import (
    DiffLineType,
    FindingDisposition,
    FindingSource,
    FilterDecisionRecord,
    FilterDecisionType,
    ParsedDiff,
    ReviewCategory,
    ReviewConclusion,
    ReviewFinding,
    ReviewInput,
    ReviewInputKind,
    ReviewReport,
    ReviewSeverity,
    ReviewStatus,
    ReviewTask,
    SandboxRunStatus,
)
from ..src.storage.repository import ReviewRepository
from ..src.telemetry import build_monitoring_summary


@dataclass(slots=True)
class CodeReviewAgent:
    """Minimal orchestrator for the code review pipeline."""

    config: ReviewAgentConfig

    def run(self) -> tuple[ReviewTask, ReviewReport]:
        """Execute the minimal review pipeline and return task plus report."""

        return run_review_task(self.config)


def create_agent(config: ReviewAgentConfig) -> CodeReviewAgent:
    """Create the code review agent orchestrator."""

    return CodeReviewAgent(config=config)


def run_review_task(config: ReviewAgentConfig) -> tuple[ReviewTask, ReviewReport]:
    """Run the review pipeline and convert pipeline errors into audited tasks."""

    start_time = perf_counter()
    created_at = datetime.now(timezone.utc).isoformat()
    task = ReviewTask(
        task_id=str(uuid4()),
        status=ReviewStatus.RUNNING,
        review_input=_build_placeholder_review_input(config),
    )

    try:
        return _run_review_task_impl(
            config,
            task=task,
            start_time=start_time,
            created_at=created_at,
        )
    except Exception as exc:  # pylint: disable=broad-except
        return _finalize_failed_task(
            config,
            task=task,
            error=exc,
            start_time=start_time,
            created_at=created_at,
        )


def _run_review_task_impl(
    config: ReviewAgentConfig,
    *,
    task: ReviewTask,
    start_time: float,
    created_at: str,
) -> tuple[ReviewTask, ReviewReport]:
    """Execute the successful review path using an initialized task."""

    review_input = load_review_input(
        diff_file=config.diff_file,
        repo_path=config.repo_path,
        fixture_path=config.fixture_path,
    )
    parsed_diff = parse_unified_diff(review_input.diff_text)
    diff_sha256 = hashlib.sha256(review_input.diff_text.encode("utf-8")).hexdigest()
    task.review_input = review_input
    task.parsed_diff = parsed_diff

    all_findings = run_rule_engine(parsed_diff)
    with TemporaryDirectory(prefix="trpc-code-review-") as temporary_input_dir:
        diff_file_path = _materialize_diff_input(
            task=task,
            input_dir=Path(temporary_input_dir),
        )
        script_plan = build_skill_script_plan(
            diff_file=diff_file_path,
            project_root=Path(__file__).resolve().parents[3],
        )
        evaluated_invocations = evaluate_invocations(
            parsed_diff=parsed_diff,
            runtime=config.runtime,
            invocations=script_plan,
        )
        approved_invocations = [
            invocation
            for invocation, decision in evaluated_invocations
            if decision.decision == FilterDecisionType.ALLOW
        ]
        executed_runs = {
            run.name: run
            for run in execute_skill_scripts(
                approved_invocations,
                runtime=config.runtime,
                diff_file=diff_file_path,
                project_root=Path(__file__).resolve().parents[3],
            )
        }

    for invocation, decision in evaluated_invocations:
        task.add_filter_decision(decision)
        if decision.decision == FilterDecisionType.ALLOW:
            sandbox_run = executed_runs[invocation.name]
            task.add_sandbox_run(sandbox_run)
            all_findings.extend(_sandbox_run_findings(task, sandbox_run))
            all_findings.extend(_sandbox_output_findings(task, sandbox_run))
        else:
            task.add_sandbox_run(
                build_blocked_run(
                    invocation,
                    runtime=config.runtime,
                    reason=decision.reason,
                )
            )
            all_findings.extend(_filter_decision_findings(task, decision))

    processed_findings = dedupe_and_classify_findings(all_findings)
    for finding in processed_findings:
        task.add_finding(finding)
    task.status = ReviewStatus.COMPLETED

    summary = (
        "Loaded review input, parsed diff, completed deterministic rule review, "
        "and processed sandbox governance. "
        f"Changed files: {parsed_diff.changed_files_count}, "
        f"added lines: {parsed_diff.added_lines_count}, "
        f"deleted lines: {parsed_diff.deleted_lines_count}, "
        f"findings: {_count_by_disposition(processed_findings, FindingDisposition.FINDING)}, "
        f"human review: {_count_by_disposition(processed_findings, FindingDisposition.NEEDS_HUMAN_REVIEW)}, "
        f"warnings: {_count_by_disposition(processed_findings, FindingDisposition.WARNING)}, "
        f"filter decisions: {len(task.filter_decisions)}, "
        f"sandbox runs: {len(task.sandbox_runs)}."
    )
    conclusion = _decide_conclusion(processed_findings)
    total_duration_ms = int((perf_counter() - start_time) * 1000)
    monitoring_summary = build_monitoring_summary(
        task=task,
        parsed_diff=parsed_diff,
        total_duration_ms=total_duration_ms,
    )
    task = redact_task(task)
    report = ReviewReport.from_task(
        task=task,
        conclusion=conclusion,
        summary=summary,
        monitoring_summary=monitoring_summary,
    )
    report = redact_report(report)
    report_json = build_report_payload(report)
    report_markdown = render_markdown_report(report)
    write_report_files(
        report_json,
        report_markdown,
        output_dir=config.output_dir,
    )

    repository = ReviewRepository(config.db_path)
    repository.save_review(
        task=task,
        report=report,
        report_json=report_json,
        report_markdown=report_markdown,
        diff_sha256=diff_sha256,
        runtime_type=config.runtime,
        dry_run=config.dry_run,
        fake_model=config.fake_model,
        created_at=created_at,
        finished_at=datetime.now(timezone.utc).isoformat(),
        total_duration_ms=total_duration_ms,
    )
    return task, report


def _build_placeholder_review_input(config: ReviewAgentConfig) -> ReviewInput:
    """Build a non-I/O input record so even loader failures have a task."""

    if config.diff_file is not None:
        kind = ReviewInputKind.DIFF_FILE
        source = config.diff_file
        repo_path = None
    elif config.repo_path is not None:
        kind = ReviewInputKind.REPO_PATH
        source = config.repo_path
        repo_path = Path(config.repo_path).expanduser()
    else:
        kind = ReviewInputKind.FIXTURE
        source = config.fixture_path or "<missing-fixture>"
        repo_path = None
    return ReviewInput(
        kind=kind,
        source=str(source),
        diff_text="",
        repo_path=repo_path,
    )


def _finalize_failed_task(
    config: ReviewAgentConfig,
    *,
    task: ReviewTask,
    error: Exception,
    start_time: float,
    created_at: str,
) -> tuple[ReviewTask, ReviewReport]:
    """Persist and report a pipeline failure without leaking input content."""

    task.status = ReviewStatus.FAILED
    task.error_message = f"{type(error).__name__}: {error}"
    if task.parsed_diff is None:
        task.parsed_diff = ParsedDiff(raw_diff=task.review_input.diff_text)

    diff_sha256 = hashlib.sha256(
        task.review_input.diff_text.encode("utf-8")
    ).hexdigest()
    total_duration_ms = int((perf_counter() - start_time) * 1000)
    monitoring_summary = build_monitoring_summary(
        task=task,
        parsed_diff=task.parsed_diff,
        total_duration_ms=total_duration_ms,
    )
    summary = f"Review pipeline failed: {task.error_message}"
    task = redact_task(task)
    report = ReviewReport.from_task(
        task=task,
        conclusion=ReviewConclusion.ERROR,
        summary=summary,
        monitoring_summary=monitoring_summary,
    )
    report = redact_report(report)
    report_json = build_report_payload(report)
    report_markdown = render_markdown_report(report)
    write_report_files(
        report_json,
        report_markdown,
        output_dir=config.output_dir,
    )

    ReviewRepository(config.db_path).save_review(
        task=task,
        report=report,
        report_json=report_json,
        report_markdown=report_markdown,
        diff_sha256=diff_sha256,
        runtime_type=config.runtime,
        dry_run=config.dry_run,
        fake_model=config.fake_model,
        created_at=created_at,
        finished_at=datetime.now(timezone.utc).isoformat(),
        total_duration_ms=total_duration_ms,
    )
    return task, report


def _decide_conclusion(findings: list) -> ReviewConclusion:
    """Choose a final review verdict from classified findings."""

    if any(finding.disposition == FindingDisposition.FINDING for finding in findings):
        return ReviewConclusion.FAIL
    if any(
        finding.disposition == FindingDisposition.NEEDS_HUMAN_REVIEW
        for finding in findings
    ):
        return ReviewConclusion.NEEDS_HUMAN_REVIEW
    return ReviewConclusion.PASS


def _count_by_disposition(findings: list, disposition: FindingDisposition) -> int:
    """Count findings by presentation bucket."""

    return sum(1 for finding in findings if finding.disposition == disposition)


def _materialize_diff_input(*, task: ReviewTask, input_dir: Path) -> Path:
    """Write a short-lived raw diff for skill-script execution."""

    inputs_dir = input_dir.expanduser().resolve()
    inputs_dir.mkdir(parents=True, exist_ok=True)
    diff_file = inputs_dir / "review.diff"
    diff_file.write_text(task.review_input.diff_text, encoding="utf-8")
    return diff_file


def _sandbox_run_findings(task: ReviewTask, sandbox_run) -> list[ReviewFinding]:
    """Convert failed sandbox runs into structured findings without crashing the task."""

    if sandbox_run.status.value not in {"failed", "timed_out"}:
        return []

    severity = ReviewSeverity.MEDIUM
    confidence = 0.7
    if sandbox_run.status.value == "timed_out":
        severity = ReviewSeverity.HIGH
        confidence = 0.85

    return [
        ReviewFinding(
            severity=severity,
            category=ReviewCategory.SANDBOX,
            file=task.parsed_diff.changed_paths[0] if task.parsed_diff and task.parsed_diff.changed_paths else task.review_input.source,
            line=None,
            title=f"Sandbox script `{sandbox_run.name}` did not complete successfully",
            evidence=sandbox_run.stderr or sandbox_run.stdout or "Sandbox execution failed without output.",
            recommendation="Inspect the sandbox summary, fix the failing script or command, and rerun the review.",
            confidence=confidence,
            source=FindingSource.SANDBOX,
        )
    ]


def _sandbox_output_findings(task: ReviewTask, sandbox_run) -> list[ReviewFinding]:
    """Promote successful skill-script diagnostics into structured findings."""

    if sandbox_run.status != SandboxRunStatus.SUCCEEDED or not sandbox_run.stdout:
        return []

    try:
        payload = json.loads(sandbox_run.stdout)
    except json.JSONDecodeError:
        return []

    if sandbox_run.name != "run_linters":
        return []

    findings: list[ReviewFinding] = []
    for warning in payload.get("warnings", []):
        mapping = _linter_warning_to_finding(task, warning)
        if mapping is not None:
            findings.append(mapping)
    return findings


def _linter_warning_to_finding(task: ReviewTask, warning: str) -> ReviewFinding | None:
    """Map deterministic linter warnings onto the canonical finding schema."""

    base_spec = security_finding_spec(warning)
    if base_spec is None:
        return None

    file_path, line_number, evidence = _find_added_line_evidence(
        task,
        needle=base_spec.needle,
    )
    spec = security_finding_spec(
        warning,
        path=file_path,
        context=evidence,
    )
    assert spec is not None
    return ReviewFinding(
        severity=spec.severity,
        category=ReviewCategory.SECURITY,
        file=file_path,
        line=line_number,
        title=spec.title,
        evidence=evidence,
        recommendation=spec.recommendation,
        confidence=spec.confidence,
        source=FindingSource.SKILL_SCRIPT,
    )


def _filter_decision_findings(
    task: ReviewTask,
    decision: FilterDecisionRecord,
) -> list[ReviewFinding]:
    """Expose non-allow filter decisions in the final report."""

    if decision.decision == FilterDecisionType.ALLOW:
        return []

    severity = ReviewSeverity.MEDIUM
    title = "Sandbox invocation requires human review"
    recommendation = (
        "Use local runtime only for explicit development fallback, or implement a real "
        "isolated executor before allowing non-local runtimes."
    )
    if decision.decision == FilterDecisionType.DENY:
        severity = ReviewSeverity.HIGH
        title = "Sandbox invocation was denied by filter policy"
        recommendation = "Adjust the diff or invocation so it complies with the sandbox policy."

    file_path = (
        task.parsed_diff.changed_paths[0]
        if task.parsed_diff and task.parsed_diff.changed_paths
        else task.review_input.source
    )
    return [
        ReviewFinding(
            severity=severity,
            category=ReviewCategory.SANDBOX,
            file=file_path,
            line=None,
            title=title,
            evidence=f"{decision.target}: {decision.reason}",
            recommendation=recommendation,
            confidence=0.9 if decision.decision == FilterDecisionType.DENY else 0.75,
            source=FindingSource.FILTER,
            disposition=FindingDisposition.NEEDS_HUMAN_REVIEW,
        )
    ]


def _find_added_line_evidence(
    task: ReviewTask,
    *,
    needle: str,
) -> tuple[str, int | None, str]:
    """Locate the added line that triggered a sandbox warning."""

    if task.parsed_diff is None:
        return task.review_input.source, None, needle

    for changed_file in task.parsed_diff.files:
        for hunk in changed_file.hunks:
            for line in hunk.lines:
                if line.line_type != DiffLineType.ADD:
                    continue
                if needle not in line.text:
                    continue
                return changed_file.display_path, line.new_line_no, line.raw_line

    if task.parsed_diff.changed_paths:
        return task.parsed_diff.changed_paths[0], None, needle
    return task.review_input.source, None, needle
