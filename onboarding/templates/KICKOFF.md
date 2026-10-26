# KICKOFF — cold start for a factory run

Start every factory run in a FRESH agent session. Do not continue a session
that carries process or meta discussion — **the repo is the state**; the
session is disposable. Everything a new session needs lives in the control
home: the declarations (`workspace.toml`, `environments/*.toml`), the ledger
(`factory/ledger/events.jsonl`), the generated renders, and `stories/`.

## Kickoff message (paste into a fresh session, fill the bracket)

```text
Factory run.

Read, in order, and follow them exactly:
1. factory/KICKOFF.md (this cold-start contract)
2. workspace.toml + environments/*.toml (declared runtimes, models, limits)
3. factory/PREFLIGHT.md (the latest verify result)
4. stories/README.md, then the scoped story directory
5. the docs/ context bundle (prd, architecture, ui-design, test-strategy)

Scope for this run: [<story id> / the next N stories / your proposal].

Rules you must not drift on:
- the ledger is the state: append events and regenerate the renders;
  NEVER hand-edit a generated file
- one story = one branch = one worktree; branch naming: story/<id>-<slug>
- every gate is mechanical: a refusal names the failed check — fix the
  fact, don't work around it
- stop and ask a human only at the gates listed below; anything else
  asked is a process defect
```

## Cold-start checklist (first moves in a fresh session)

- [ ] `git status` + `git log` — confirm a clean tree and where the last run stopped
- [ ] renders `--check` — generated views match the ledger; if not, re-render and commit before anything else
- [ ] ledger valid — the ledger schema check reports clean
- [ ] no pending human gates — an unanswered `human_gate` row is surfaced with its status, never re-asked
- [ ] Storybook builds clean — when the run scope includes UI
- [ ] test environment sane — the project's test command runs green before any test-heavy story

## Human gates

Stop and ask a human ONLY for:

- **Storybook approvals** — for UI stories, no feature code wires components until the story is human-approved
- **the merge gate** — PR merges are human-approved
- **escalations** — two failed revision loops at any stage

Anything else a session asks a human is a process defect — record it as an
avoidable human-gate event; the target is zero. Every gate decision is
recorded as a `human_gate` ledger row (question, answer, decider, time).
