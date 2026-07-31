# Skills Code Review Agent

This example implements a structured code-review agent prototype on top of
tRPC-Agent-Python. It combines deterministic rule detection, a formal
`code-review` skill package, filter-based governance, framework-native
`skill_run` execution, SQLite persistence, and dual-format reports.

## What This Example Covers

- Unified diff, repo path, and fixture inputs
- Deterministic review rules for:
  - security risks
  - async errors
  - resource leaks
  - missing tests
  - sensitive information leaks
  - database lifecycle issues
- Finding dedupe and confidence-based routing
- Filter decisions before sandbox execution
- Container execution delegated to `SkillToolSet` / `skill_run`
- Explicit development-local fallback with fixed argv, environment allowlist,
  timeout, and output truncation
- Secret redaction before reporting and persistence
- Failed-task reporting and SQLite persistence for pipeline exceptions
- SQLite storage for tasks, inputs, findings, sandbox runs, filter decisions, and reports
- `review_report.json` and `review_report.md` output generation
- Dry-run and fake-model audit labels for deterministic execution

## Directory Map

- `agent/`: orchestration, config, and runtime helpers
- repository `skills/code-review/`: canonical formal skill package and deterministic scripts
- example-local `examples/skills_code_review_agent/skills/code-review/`: teaching copy kept in sync for the example
- `src/`: parser, rules, filter policy, storage, report, telemetry, redaction
- `tests/`: fixtures and automated tests
- `DEVELOPMENT_PLAN.md`: phased implementation plan
- `DESIGN_NOTE.md`: short solution design summary
- `ACCEPTANCE_CHECKLIST.md`: acceptance-standard mapping

## Main Flow

```text
CLI / input
  -> normalized diff input
  -> structured diff parsing
  -> deterministic rule engine
  -> dedupe and confidence routing
  -> filter decisions
  -> governed skill scripts
  -> redaction
  -> JSON/Markdown reports
  -> SQLite persistence
```

## Running The Example

### 1. Run against a fixture

```bash
python examples/skills_code_review_agent/run_agent.py ^
  --fixture examples/skills_code_review_agent/tests/fixtures/security_issue.diff ^
  --output-dir examples/skills_code_review_agent/sample_outputs ^
  --db-path examples/skills_code_review_agent/sample_outputs/review.db ^
  --runtime local ^
  --dry-run ^
  --fake-model
```

### 2. Run against a diff file

```bash
python examples/skills_code_review_agent/run_agent.py ^
  --diff-file path/to/change.diff ^
  --output-dir examples/skills_code_review_agent/sample_outputs ^
  --db-path examples/skills_code_review_agent/sample_outputs/review.db ^
  --runtime local ^
  --dry-run ^
  --fake-model
```

### 3. Run against a repo path

```bash
python examples/skills_code_review_agent/run_agent.py ^
  --repo-path path/to/repo ^
  --output-dir examples/skills_code_review_agent/sample_outputs ^
  --db-path examples/skills_code_review_agent/sample_outputs/review.db ^
  --runtime local ^
  --dry-run ^
  --fake-model
```

`container` is the production path: the orchestrator submits structured
`skill_run` calls and lets the SDK own skill staging, workspace creation,
runtime execution, and input mapping. The diff path is passed through the
`inputs` field rather than interpolated into a shell command.

`local` is an explicit development fallback commonly used by dry-run and
fake-model tests, and is selected only through `--runtime local`.
It executes only the bundled, pre-resolved scripts with argv-based process
creation, a minimal environment that excludes host `PATH` and `PYTHONPATH`,
timeout, and output limits; it is not presented as sandbox isolation. `cube`
and `e2b` are rejected by the Filter
as `needs_human_review` until a framework runtime resolver is configured, so
they never silently fall back to host execution.

Before execution, the Filter inspects both the fixed argv and the executable
Skill script source for dangerous command and network tokens. It deliberately
does not reject matching text in the reviewed diff because the diff is staged
as data and is never interpreted as a command.

## Outputs

Each run produces:

- `review_report.json`
- `review_report.md`
- `review.db`

Raw diff inputs are staged only in a short-lived system temporary directory
while Skill scripts run. The directory is deleted before the task is returned,
so unredacted diff content is not retained with report artifacts.

The JSON report includes:

- final verdict
- findings
- warnings
- needs-human-review items
- filter decisions
- sandbox runs
- severity stats
- monitoring summary

## SQLite Query Surface

The repository layer persists these tables:

- `review_tasks`
- `review_inputs`
- `filter_decisions`
- `sandbox_runs`
- `findings`
- `review_reports`

You can fetch a full task bundle with `src/storage/repository.py`.

Key method:

- `get_review_bundle(task_id)`

## Formal Skill Package

The canonical reusable skill lives under:

- `skills/code-review/SKILL.md`
- `skills/code-review/USAGE.md`
- `skills/code-review/RULES.md`
- `skills/code-review/SCRIPT_CONTRACTS.md`

The example now resolves the repository-level `skills/code-review/` directory
first for repository indexing, skill-script planning, and future isolated runtime mounts.
The example-local copy remains as a fallback so the sample stays readable and
self-contained.

The main pipeline owns review policy, persistence, and reporting. It does not
manually create workspaces or run container commands: approved production
invocations are dispatched through the public `skill_run` tool. This MVP always
uses the deterministic reviewer and never creates an external model client.
`--dry-run` and `--fake-model` are therefore explicit audit labels persisted in
SQLite for acceptance tests and replay; they do not change execution behavior.
The `--runtime` option is what selects the Skill execution path.

## Test Coverage

Run the full example test suite with:

```bash
pytest examples/skills_code_review_agent/tests -q
```

Covered scenarios include:

- clean diff
- security issue
- async/resource leak
- database lifecycle issue
- missing tests
- duplicate finding behavior
- sandbox failure
- secret redaction

Phase 6 quality-gate evidence:

- full suite passes locally
- all 8 public fixtures generate both report artifacts
- a measured local dry-run remains within the acceptance time budget

## Related Docs

- `DEVELOPMENT_PLAN.md`
- `DESIGN_NOTE.md`
- `ACCEPTANCE_CHECKLIST.md`
- `PR_READINESS.md`
