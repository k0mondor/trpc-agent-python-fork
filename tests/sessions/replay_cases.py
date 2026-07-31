"""Replay cases for acceptance and extended consistency tests."""

from __future__ import annotations

from dataclasses import replace

from .replay_models import EventSpec
from .replay_models import FunctionCallSpec
from .replay_models import FunctionResponseSpec
from .replay_models import ReplayCase
from .replay_models import ReplayStep
from .replay_models import RuntimeFault
from .replay_models import RuntimeFaultOperation
from .replay_models import SnapshotMutation
from .replay_models import SnapshotMutationOperation


_PERSISTENT_BACKEND = "persistent"


def _text_event(author: str, text: str) -> ReplayStep:
    """Build the common text-only replay step without repeating SDK model wiring."""

    role = "user" if author == "user" else "model"
    return ReplayStep.append_event(EventSpec(author=author, role=role, text=text))


_BASELINE_CASES: tuple[ReplayCase, ...] = (
    ReplayCase(
        case_id="single_turn_text",
        description="One user turn followed by one assistant text response.",
        session_id="replay-single-turn",
        steps=(
            ReplayStep.create_session(),
            _text_event("user", "Hello, what can you do?"),
            _text_event("assistant", "I can help answer questions."),
        ),
    ),
    ReplayCase(
        case_id="multi_turn_dialogue",
        description="Multiple user and assistant turns should preserve event ordering across backends.",
        session_id="replay-multi-turn",
        steps=(
            ReplayStep.create_session(),
            _text_event("user", "Hello assistant."),
            _text_event("assistant", "Hello, how can I help?"),
            _text_event("user", "Please remember my travel plan."),
            _text_event("assistant", "I will remember your travel plan."),
        ),
    ),
    ReplayCase(
        case_id="tool_call_and_response",
        description="Assistant tool call followed by tool response.",
        session_id="replay-tool-call",
        steps=(
            ReplayStep.create_session(),
            _text_event("user", "Check the Beijing weather."),
            ReplayStep.append_event(
                EventSpec(
                    author="assistant",
                    role="model",
                    function_calls=(
                        FunctionCallSpec(
                            name="get_weather",
                            args={"city": "Beijing"},
                            call_id="call-weather-1",
                        ),
                    ),
                ),
            ),
            ReplayStep.append_event(
                EventSpec(
                    author="assistant",
                    role="user",
                    function_responses=(
                        FunctionResponseSpec(
                            name="get_weather",
                            response={"temperature": "25C", "condition": "Sunny"},
                            call_id="call-weather-1",
                        ),
                    ),
                ),
            ),
        ),
    ),
    ReplayCase(
        case_id="state_and_memory_roundtrip",
        description="Repeated session state updates should preserve overwrite semantics before memory persistence.",
        session_id="replay-memory-state",
        steps=(
            ReplayStep.create_session(initial_state={"user_name": "alice"}),
            _text_event("user", "Please remember that I prefer tea."),
            ReplayStep.append_event(
                EventSpec(
                    author="assistant",
                    role="model",
                    text="Noted. You prefer tea over coffee.",
                    state_delta={"preference": "tea"},
                ),
            ),
            _text_event("user", "Actually update that to green tea."),
            ReplayStep.append_event(
                EventSpec(
                    author="assistant",
                    role="model",
                    text="Updated. You now prefer green tea.",
                    state_delta={"preference": "green tea", "drink_temperature": "hot"},
                ),
            ),
            ReplayStep.store_memory(),
            ReplayStep.search_memory(name="preference_search", query="green tea", limit=5),
        ),
    ),
    ReplayCase(
        case_id="summary_compaction_with_history",
        description="Deterministic summary compaction keeps recent events and stores historical events.",
        session_id="replay-summary-history",
        enable_summary=True,
        summary_keep_recent_count=2,
        store_historical_events=True,
        steps=(
            ReplayStep.create_session(),
            _text_event("user", "I am planning a weekend trip."),
            _text_event("assistant", "Great, where would you like to go?"),
            _text_event("user", "I want to visit Hangzhou."),
            _text_event("assistant", "Hangzhou is known for West Lake."),
            ReplayStep.create_summary(force=True),
        ),
    ),
    ReplayCase(
        case_id="summary_version_rolls_forward",
        description="A second summary should replace the first one with incremented lineage metadata.",
        session_id="replay-summary-version",
        enable_summary=True,
        summary_keep_recent_count=2,
        store_historical_events=True,
        steps=(
            ReplayStep.create_session(),
            _text_event("user", "Remember my project is called Atlas."),
            _text_event("assistant", "Got it, your project is Atlas."),
            _text_event("user", "It uses Redis and SQL backends."),
            _text_event("assistant", "Atlas uses Redis and SQL backends."),
            ReplayStep.create_summary(force=True),
            _text_event("user", "Also note that replay consistency is critical."),
            _text_event("assistant", "I will keep replay consistency in mind."),
            ReplayStep.create_summary(force=True),
        ),
    ),
)


_NEGATIVE_CASES: tuple[ReplayCase, ...] = (
    ReplayCase(
        case_id="summary_binding_mismatch_injection",
        description="Injected summary ownership mismatch must be reported at the exact summary field path.",
        session_id="replay-negative-summary-binding",
        enable_summary=True,
        summary_keep_recent_count=2,
        store_historical_events=True,
        expected_diff_paths=("summary.session_id",),
        snapshot_mutations=(
            SnapshotMutation(
                backend_name=_PERSISTENT_BACKEND,
                path="summary.session_id",
                value="wrong-session-id",
            ),
        ),
        steps=(
            ReplayStep.create_session(),
            _text_event("user", "Please summarize my travel plan."),
            _text_event("assistant", "Sure, tell me the route."),
            _text_event("user", "Shanghai to Hangzhou by train."),
            _text_event("assistant", "That route is short and convenient."),
            ReplayStep.create_summary(force=True),
        ),
    ),
    ReplayCase(
        case_id="summary_missing_injection",
        description="Injected summary loss must be detected as a summary-level mismatch.",
        session_id="replay-negative-summary-missing",
        enable_summary=True,
        summary_keep_recent_count=2,
        store_historical_events=True,
        expected_diff_paths=("summary",),
        snapshot_mutations=(
            SnapshotMutation(
                backend_name=_PERSISTENT_BACKEND,
                path="summary",
                value=None,
            ),
        ),
        steps=(
            ReplayStep.create_session(),
            _text_event("user", "Track my preferences for black coffee."),
            _text_event("assistant", "I noted your coffee preference."),
            _text_event("user", "Also remember I dislike sugary drinks."),
            _text_event("assistant", "I will avoid sugary drink suggestions."),
            ReplayStep.create_summary(force=True),
        ),
    ),
    ReplayCase(
        case_id="state_corruption_injection",
        description="Injected state corruption must surface at the exact state field path.",
        session_id="replay-negative-state-corruption",
        expected_diff_paths=("state.preference",),
        snapshot_mutations=(
            SnapshotMutation(
                backend_name=_PERSISTENT_BACKEND,
                path="state.preference",
                value="coffee",
            ),
        ),
        steps=(
            ReplayStep.create_session(initial_state={"user_name": "alice"}),
            _text_event("user", "Please remember that I prefer tea."),
            ReplayStep.append_event(
                EventSpec(
                    author="assistant",
                    role="model",
                    text="Noted. You prefer tea over coffee.",
                    state_delta={"preference": "tea"},
                ),
            ),
        ),
    ),
    ReplayCase(
        case_id="summary_lineage_corruption_injection",
        description="Injected summary lineage corruption must be detected via the replaces field.",
        session_id="replay-negative-summary-lineage",
        enable_summary=True,
        summary_keep_recent_count=2,
        store_historical_events=True,
        expected_diff_paths=("summary.replaces",),
        snapshot_mutations=(
            SnapshotMutation(
                backend_name=_PERSISTENT_BACKEND,
                path="summary.replaces",
                value="wrong-summary-id",
            ),
        ),
        steps=(
            ReplayStep.create_session(),
            _text_event("user", "Remember my project codename is Northstar."),
            _text_event("assistant", "The codename is Northstar."),
            _text_event("user", "It runs on both SQLite and Redis."),
            _text_event("assistant", "Northstar runs on SQLite and Redis."),
            ReplayStep.create_summary(force=True),
            _text_event("user", "Replay consistency matters a lot."),
            _text_event("assistant", "I will keep replay consistency as a priority."),
            ReplayStep.create_summary(force=True),
        ),
    ),
    ReplayCase(
        case_id="duplicate_event_runtime_fault",
        description="A duplicated event injected during replay must be detected as an event-count mismatch.",
        session_id="replay-negative-duplicate-event",
        expected_diff_paths=("session.events.length",),
        runtime_faults=(
            RuntimeFault(
                backend_name=_PERSISTENT_BACKEND,
                after_step=2,
                operation=RuntimeFaultOperation.DUPLICATE_LAST_EVENT,
            ),
        ),
        steps=(
            ReplayStep.create_session(),
            _text_event("user", "Please store this reminder."),
            _text_event("assistant", "I stored the reminder."),
        ),
    ),
    ReplayCase(
        case_id="runtime_state_corruption_fault",
        description="A runtime state corruption must be detected on the precise state path.",
        session_id="replay-negative-runtime-state",
        expected_diff_paths=("state.preference",),
        runtime_faults=(
            RuntimeFault(
                backend_name=_PERSISTENT_BACKEND,
                after_step=2,
                operation=RuntimeFaultOperation.SET_SESSION_VALUE,
                path="state.preference",
                value="coffee",
            ),
        ),
        steps=(
            ReplayStep.create_session(initial_state={"user_name": "alice"}),
            _text_event("user", "Please remember that I prefer tea."),
            ReplayStep.append_event(
                EventSpec(
                    author="assistant",
                    role="model",
                    text="Noted. You prefer tea over coffee.",
                    state_delta={"preference": "tea"},
                ),
            ),
        ),
    ),
    ReplayCase(
        case_id="runtime_summary_loss_fault",
        description="A runtime summary deletion must be detected as a missing summary.",
        session_id="replay-negative-runtime-summary-loss",
        enable_summary=True,
        summary_keep_recent_count=2,
        store_historical_events=True,
        expected_diff_paths=("summary",),
        runtime_faults=(
            RuntimeFault(
                backend_name=_PERSISTENT_BACKEND,
                after_step=5,
                operation=RuntimeFaultOperation.DELETE_SUMMARY,
            ),
        ),
        steps=(
            ReplayStep.create_session(),
            _text_event("user", "Please summarize my sprint notes."),
            _text_event("assistant", "Sure, continue."),
            _text_event("user", "We fixed replay ordering bugs."),
            _text_event("assistant", "The replay ordering bugs were fixed."),
            ReplayStep.create_summary(force=True),
        ),
    ),
    ReplayCase(
        case_id="runtime_summary_overwrite_fault",
        description="A runtime summary overwrite must be detected through lineage replacement fields.",
        session_id="replay-negative-runtime-summary-overwrite",
        enable_summary=True,
        summary_keep_recent_count=2,
        store_historical_events=True,
        expected_diff_paths=(
            "summary.replaces",
            "summary.metadata.replaces",
        ),
        runtime_faults=(
            RuntimeFault(
                backend_name=_PERSISTENT_BACKEND,
                after_step=8,
                operation=RuntimeFaultOperation.SET_SUMMARY_VALUE,
                path="metadata.replaces",
                value="wrong-summary-id",
            ),
        ),
        steps=(
            ReplayStep.create_session(),
            _text_event("user", "Remember the codename is Aurora."),
            _text_event("assistant", "The codename is Aurora."),
            _text_event("user", "Aurora runs on SQL and Redis."),
            _text_event("assistant", "Aurora runs on SQL and Redis."),
            ReplayStep.create_summary(force=True),
            _text_event("user", "Replay correctness matters a lot."),
            _text_event("assistant", "I will prioritize replay correctness."),
            ReplayStep.create_summary(force=True),
        ),
    ),
    ReplayCase(
        case_id="partial_failure_event_loss_fault",
        description=(
            "A partial failure that loses the final event but keeps state must be detected as an event-window mismatch."
        ),
        session_id="replay-negative-partial-failure",
        expected_diff_paths=("session.events.length",),
        runtime_faults=(
            RuntimeFault(
                backend_name=_PERSISTENT_BACKEND,
                after_step=2,
                operation=RuntimeFaultOperation.DROP_LAST_EVENT_KEEP_STATE,
            ),
        ),
        steps=(
            ReplayStep.create_session(initial_state={"user_name": "alice"}),
            _text_event("user", "Please remember that I prefer tea."),
            ReplayStep.append_event(
                EventSpec(
                    author="assistant",
                    role="model",
                    text="Noted. You prefer tea over coffee.",
                    state_delta={"preference": "tea"},
                ),
            ),
        ),
    ),
    ReplayCase(
        case_id="non_active_session_summary_loss_fault",
        description=(
            "A runtime summary deletion on a non-active session alias must still be detected through alias-scoped snapshots."
        ),
        session_id="replay-negative-target-session",
        enable_summary=True,
        summary_keep_recent_count=2,
        store_historical_events=True,
        expected_diff_paths=("sessions_by_alias.source.summary",),
        runtime_faults=(
            RuntimeFault(
                backend_name=_PERSISTENT_BACKEND,
                after_step=6,
                operation=RuntimeFaultOperation.DELETE_SUMMARY,
                session_alias="source",
            ),
        ),
        steps=(
            ReplayStep.create_session(session_alias="source", session_id="replay-negative-source-session"),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Please summarize my architecture notes."),
                session_alias="source",
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="Sure, continue with the details."),
                session_alias="source",
            ),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="We run SQLite for local replay and Redis in production."),
                session_alias="source",
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="I will summarize the backend setup."),
                session_alias="source",
            ),
            ReplayStep.create_summary(force=True, session_alias="source"),
            ReplayStep.create_session(session_alias="default", session_id="replay-negative-target-session"),
            _text_event("user", "Switch focus to the current session."),
        ),
    ),
)


_ROBUSTNESS_CASES: tuple[ReplayCase, ...] = (
    ReplayCase(
        case_id="cross_session_memory_aggregation",
        description=(
            "Memory written by one session should remain searchable from another session under the same app/user scope."
        ),
        session_id="replay-memory-target",
        steps=(
            ReplayStep.create_session(session_alias="source", session_id="replay-memory-source"),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Please remember that I prefer oolong tea."),
                session_alias="source",
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="Noted. You prefer oolong tea."),
                session_alias="source",
            ),
            ReplayStep.store_memory(session_alias="source"),
            ReplayStep.create_session(session_alias="default", session_id="replay-memory-target"),
            _text_event("user", "What drink did I say I prefer?"),
            ReplayStep.search_memory(
                name="cross_session_preference_search",
                query="oolong",
                limit=5,
            ),
        ),
    ),
    ReplayCase(
        case_id="restart_mid_replay_after_summary",
        description=(
            "Persistent backends should restore summary state correctly after a restart and continue replaying later turns."
        ),
        session_id="replay-restart-summary",
        enable_summary=True,
        summary_keep_recent_count=2,
        store_historical_events=True,
        steps=(
            ReplayStep.create_session(),
            _text_event("user", "I am planning a Hangzhou trip."),
            _text_event("assistant", "Great, what should I remember?"),
            _text_event("user", "Please remember I need a hotel near West Lake."),
            _text_event("assistant", "I will remember the hotel preference."),
            ReplayStep.create_summary(force=True),
            ReplayStep.restart_services(),
            _text_event("user", "Also note that I will arrive next Friday."),
            _text_event("assistant", "Arrival next Friday is recorded."),
            ReplayStep.restart_services(),
            _text_event("user", "I prefer morning check-in if available."),
            _text_event("assistant", "I will keep the morning check-in preference."),
            ReplayStep.create_summary(force=True),
        ),
    ),
    ReplayCase(
        case_id="state_namespace_roundtrip",
        description=(
            "App, user, session, and temp state should preserve their intended visibility across sessions and restarts."
        ),
        session_id="replay-state-namespace-b",
        steps=(
            ReplayStep.create_session(
                session_alias="writer",
                session_id="replay-state-namespace-a",
                initial_state={
                    "app:locale": "zh-CN",
                    "user:timezone": "Asia/Shanghai",
                    "temp:request_id": "req-1",
                    "draft": "first-session",
                },
            ),
            ReplayStep.append_event(
                EventSpec(
                    author="assistant",
                    role="model",
                    text="Updating shared and session state.",
                    state_delta={
                        "app:release": "2026.07",
                        "user:tone": "concise",
                        "temp:trace_id": "trace-1",
                        "draft": "writer-updated",
                    },
                ),
                session_alias="writer",
            ),
            ReplayStep.restart_services(),
            ReplayStep.create_session(
                session_alias="default",
                session_id="replay-state-namespace-b",
                initial_state={
                    "temp:request_id": "req-2",
                    "draft": "second-session",
                },
            ),
            _text_event("assistant", "Second session should inherit shared state only."),
        ),
    ),
    ReplayCase(
        case_id="duplicate_memory_query_name_across_sessions",
        description="Two sessions may reuse the same query name without overwriting earlier memory observations.",
        session_id="replay-duplicate-query-target",
        steps=(
            ReplayStep.create_session(session_alias="source", session_id="replay-duplicate-query-source"),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Please remember that I prefer dragon well tea."),
                session_alias="source",
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="Dragon well tea preference recorded."),
                session_alias="source",
            ),
            ReplayStep.store_memory(session_alias="source"),
            ReplayStep.search_memory(
                name="shared_preference_search",
                query="dragon well",
                limit=5,
                session_alias="source",
            ),
            ReplayStep.restart_services(),
            ReplayStep.create_session(session_alias="default", session_id="replay-duplicate-query-target"),
            ReplayStep.search_memory(
                name="shared_preference_search",
                query="dragon well",
                limit=5,
            ),
        ),
    ),
    ReplayCase(
        case_id="memory_query_observation_survives_restart",
        description=(
            "Memory query observations should retain their original step-time results across restarts and later writes."
        ),
        session_id="replay-memory-observation",
        steps=(
            ReplayStep.create_session(),
            _text_event("user", "Please remember that my favorite tea is oolong."),
            _text_event("assistant", "I will remember your oolong preference."),
            ReplayStep.store_memory(),
            ReplayStep.search_memory(name="tea_preference", query="oolong", limit=5),
            ReplayStep.restart_services(),
            _text_event("user", "Also remember that I enjoy jasmine tea."),
            _text_event("assistant", "I will remember the jasmine preference too."),
            ReplayStep.store_memory(),
            ReplayStep.search_memory(name="tea_preference", query="jasmine", limit=5),
        ),
    ),
    ReplayCase(
        case_id="cross_user_memory_isolation",
        description="Memory saved for one user must not become visible to another user in the same application.",
        session_id="replay-cross-user-target",
        steps=(
            ReplayStep.create_session(
                session_alias="source",
                session_id="replay-cross-user-source",
                user_id="replay-user-a",
            ),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Please remember that I prefer matcha desserts."),
                session_alias="source",
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="Matcha dessert preference recorded."),
                session_alias="source",
            ),
            ReplayStep.store_memory(session_alias="source"),
            ReplayStep.create_session(
                session_alias="default",
                session_id="replay-cross-user-target",
                user_id="replay-user-b",
            ),
            ReplayStep.search_memory(
                name="cross_user_matcha_search",
                query="matcha",
                limit=5,
            ),
        ),
    ),
)


def _with_snapshot_injection(
    case: ReplayCase,
    *,
    case_id: str,
    description: str,
    mutation_path: str,
    expected_path: str,
    value: object = None,
    operation: SnapshotMutationOperation = SnapshotMutationOperation.SET,
) -> ReplayCase:
    """Attach one independently declared comparator injection to a clean trajectory."""

    return replace(
        case,
        case_id=case_id,
        description=description,
        expected_diff_paths=(expected_path,),
        snapshot_mutations=(
            SnapshotMutation(
                backend_name=_PERSISTENT_BACKEND,
                path=mutation_path,
                operation=operation,
                value=value,
            ),
        ),
        runtime_faults=(),
    )


# The public acceptance suite contains 10 clean trajectories plus one explicit
# snapshot injection plan per trajectory. Tests first compare the unmodified
# snapshots (false-positive measurement), then apply the declared injection to
# the same snapshots (detection measurement), so no second backend replay is
# required.
REPLAY_ACCEPTANCE_CASES: tuple[ReplayCase, ...] = (
    _with_snapshot_injection(
        _BASELINE_CASES[0],
        case_id="single_turn_event_author_injection",
        description="Single-turn replay must detect an injected event author mismatch.",
        mutation_path="session.events[0].author",
        expected_path="session.events[0].author",
        value="corrupted-user",
    ),
    _with_snapshot_injection(
        _BASELINE_CASES[1],
        case_id="multi_turn_event_text_injection",
        description="Multi-turn replay must detect an injected event text mismatch at its exact index.",
        mutation_path="session.events[2].text",
        expected_path="session.events[2].text",
        value="corrupted travel plan",
    ),
    _with_snapshot_injection(
        _BASELINE_CASES[2],
        case_id="tool_call_name_injection",
        description="Tool replay must detect an injected function-call name mismatch.",
        mutation_path="session.events[1].function_calls[0].name",
        expected_path="session.events[1].function_calls[0].name",
        value="wrong_weather_tool",
    ),
    _with_snapshot_injection(
        _BASELINE_CASES[3],
        case_id="state_value_injection",
        description="State replay must detect an injected overwritten preference.",
        mutation_path="state.preference",
        expected_path="state.preference",
        value="coffee",
    ),
    _with_snapshot_injection(
        _BASELINE_CASES[3],
        case_id="memory_result_loss_injection",
        description="Memory replay must detect an injected loss of persisted search results.",
        mutation_path="memory.step_006:default:preference_search.entries",
        expected_path="memory.step_006:default:preference_search.entries.length",
        value=[],
    ),
    _with_snapshot_injection(
        _BASELINE_CASES[4],
        case_id="summary_text_injection",
        description="Summary replay must detect an injected semantic content mismatch.",
        mutation_path="summary.summary_text",
        expected_path="summary.summary_text",
        value="Unrelated corrupted summary.",
    ),
    _with_snapshot_injection(
        _BASELINE_CASES[5],
        case_id="summary_version_injection",
        description="Summary replay must detect an injected version rollback.",
        mutation_path="summary.version",
        expected_path="summary.version",
        value=1,
    ),
    _NEGATIVE_CASES[0],  # summary_binding_mismatch_injection
    _NEGATIVE_CASES[1],  # summary_missing_injection
    _NEGATIVE_CASES[3],  # summary_lineage_corruption_injection
)


# Extra cases extend coverage beyond the fixed acceptance set while reusing the
# same harness and reporting pipeline.
REPLAY_EXTRA_CASES: tuple[ReplayCase, ...] = (
    _NEGATIVE_CASES[4],  # duplicate_event_runtime_fault
    _NEGATIVE_CASES[5],  # runtime_state_corruption_fault
    _NEGATIVE_CASES[6],  # runtime_summary_loss_fault
    _NEGATIVE_CASES[7],  # runtime_summary_overwrite_fault
    _NEGATIVE_CASES[8],  # partial_failure_event_loss_fault
    _NEGATIVE_CASES[9],  # non_active_session_summary_loss_fault
    _ROBUSTNESS_CASES[0],  # cross_session_memory_aggregation
    _ROBUSTNESS_CASES[1],  # restart_mid_replay_after_summary
    _ROBUSTNESS_CASES[2],  # state_namespace_roundtrip
    _ROBUSTNESS_CASES[5],  # cross_user_memory_isolation
)


REPLAY_TARGETED_CASES: tuple[ReplayCase, ...] = (
    _ROBUSTNESS_CASES[3],  # duplicate_memory_query_name_across_sessions
    _ROBUSTNESS_CASES[4],  # memory_query_observation_survives_restart
)


REPLAY_ALL_CASES: tuple[ReplayCase, ...] = REPLAY_ACCEPTANCE_CASES + REPLAY_EXTRA_CASES
