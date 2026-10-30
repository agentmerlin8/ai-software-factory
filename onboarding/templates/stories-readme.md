# stories/

One directory per story. Each contains:

- `STORY-{ID}.md` at the root of this folder (the story itself)
- `STORY-{ID}/test-plan.md`
- `STORY-{ID}/impl-plan.md`
- `STORY-{ID}/state.md` — the per-story audit trail (checklist + history)

Story state is ledger-derived: stage transitions append events to
`factory/ledger/events.jsonl`, and the factory renderers regenerate the
dashboard/state views from it — generated files are never hand-edited.
