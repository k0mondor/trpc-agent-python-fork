# Replay consistency modules

The replay framework keeps backend lifecycle and persistence read-back in
`../replay_harness.py` and separates reusable comparison concerns here:

- `normalizer.py`: backend-neutral business projections;
- `allowed_diff.py`: exact/index-wildcard rules, reasons, backend pairs, and governance;
- `comparator.py`: declared snapshot mutations and recursive structured diffs;
- `report.py`: schema-versioned reports and acceptance quality metrics;
- `injectors.py`: out-of-band SQLite/Redis corruption used by end-to-end tests.

The public acceptance suite replays each clean trajectory once per backend,
then compares both the clean snapshots and an independently mutated candidate
view. Extended cases inject runtime faults during replay. Persistent adapters
close and reopen services before snapshot collection, so summary lineage,
multi-session state, and memory observations are read from storage rather than
from the original service objects.

Run the lightweight suite with:

```bash
python -m pytest tests/sessions/test_replay_modules.py \
  tests/sessions/test_replay_consistency.py -q
```

Set `TRPC_AGENT_REPLAY_REDIS_URL` to add Redis comparisons and raw Redis
corruption tests. The generated report follows
`../replay_report.schema.json`; it records normal/injected comparisons,
expected/missing/unexpected field paths, backend values, locators, runtime
context, and the three acceptance quality rates.

Production SDK fixes are intentionally outside this package. They should be
reviewed and committed independently from replay-framework changes.
