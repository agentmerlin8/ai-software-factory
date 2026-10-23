# PRD: Factory Kit v0.1

> Status: DRAFT — direction folded 2026-10-23 (workspace model, environments, single-writer; see `architecture.md`, `build-plan.md`, `grill-disposition.md`). Drafted 2026-09-28.
> Scope of this release: v0.1 ("the dress rehearsal build"), per the project vision.

## Executive Summary

The Factory Kit makes the software factory's process, agents, and control plane fully portable. They live **in the target repository (single-repo) or in the project's workspace control home (multi-repo)**, run on nothing but `git` + `gh` + `python3` (plus the project's own toolchain), and can be executed by a **human operator or any AI orchestrator**.

v0.1 ships: the kit skeleton, the universal **dispatch-package** interface, the deterministic **control plane** (ledger, controller, gates, prep, renders), two execution **adapters** (`human`, `hermes`), the complete **idea → completed PR runbook**, and a **dress rehearsal** proving the whole path end-to-end on a real target repo, plus a **two-machine handover drill**.

The kit is the product. The runbook is its contract.

## Background & Problem

The factory process has matured into a deterministic, auditable pipeline — ledger-backed state machine, evidence-checked transitions, gate scripts, fresh-context reviews, provenance renders — in a private reference implementation. But parts of it remain bound to a single agent runtime:

1. The orchestrator presupposes a session of one specific agent product.
2. Coder/fixer dispatch hardwires that product's subagent mechanism.
3. Token accounting reads that product's local runtime database.
4. Process rules live in that product's skill system — invisible to any other executor.
5. Run kickoff pins models through that product's config.

Consequences: the process cannot run in locked-down client environments; it cannot be handed to another developer, another machine, or another agent runtime intact; and it cannot be adopted by a fresh repository without that runtime present.

The fix is structural, not a feature: move the process into the repo, define a runtime-neutral execution interface, and keep every runtime behind an adapter.

## Goals

- **G1** — The factory's agents, process, and control plane live in the kit repo and in every onboarded target repo. No runtime is required to *hold* the process.
- **G2** — A human with an editor, a shell, and git push access can take an idea to a completed PR by following the runbook — dispatching role work to any AI chat (or executing review steps themselves).
- **G3** — Any AI orchestrator (any product) can execute the same process by following the same runbook and consuming the same dispatch packages.
- **G4** — The kit proves itself: end-to-end dress rehearsal on a real target repo, plus a two-machine handover drill.

## Personas

| ID | Persona | Cares about |
|---|---|---|
| P1 | **Developer-operator** (human driving) | Checklists, copy-pasteable commands, refusals that name the exact problem |
| P2 | **Agent-orchestrator** (any runtime) | Same runbook, packages it can consume, zero runtime-specific magic |
| P3 | **Pickup developer** (mid-sprint, another machine/person/agent) | Zero-question resume: state, history, decisions, next legal move |
| P4 | **Auditor** (org/client) | Verifiable claims, provenance, override/adjudication visibility |

## Functional Requirements

> Acceptance format: Given/When/Then scenarios are provided for behavioral requirements. Mechanical requirements carry checklist acceptance (each item independently verifiable).

### FR-1 — Kit structure and single-source process

The kit ships as: `agents/` (role packages), `process/` (process rules), `tools/` (control plane), `adapters/` (execution bindings), `runbook.md`, `onboarding/`. Process rules live **in the repo**, versioned with the code.

- [ ] A fresh clone of a kit-onboarded repo contains every rule needed to run the process, with no reference to any external skill system as a requirement.
- [ ] `process/` documents are the single source; no process rule exists only in an agent runtime.

### FR-2 — The runbook completes idea → PR in both modes

`runbook.md` specifies every step from idea intake to merged-and-proven PR: the action, the command, the artifact produced, the gate that must pass, and who may perform it (human / agent / either). No step requires an agent runtime; no step assumes a specific AI provider.

- [ ] **Given** a fresh clone of an onboarded repo, **when** a developer-operator follows the runbook top to bottom with any AI chat available, **then** they can reach a merged PR without asking project-specific questions.
- [ ] **Given** an agent-orchestrator of any runtime, **when** it follows the same runbook, **then** it performs the same steps with the same artifacts and the same gates.

### FR-3 — Dispatch packages are the universal interface

The kit generates, for **every role dispatch**, one self-contained markdown package with six sections: header, role instruction, pinned inputs (at a recorded SHA), expected outputs, stop rules, and the resume preamble. One assembler owns the final package file; prep tools produce its inputs. The same package is consumed by an adapter (fed to an agent) or pasted by a human into any AI chat.

- [ ] **Given** an approved story at PLANNED, **when** the operator runs the package generator, **then** a single file is produced that includes the role instruction, every input pinned by SHA, the expected output paths, and explicit stop rules.
- [ ] **Given** the same package, **when** it is executed by the `human` adapter (paste into a chat) and by the `hermes` adapter (subagent dispatch), **then** both produce artifacts at the same paths with the same contract shape (parity check).

### FR-4 — Deterministic control plane

The control plane (controller, ledger library, gates, prep scripts, renderers, board sync) is plain `python3` + `git` + `gh`. **No AI is required for any control-plane step.** Invalid transitions and missing evidence are refused with a named failed check, exit non-zero, and are never worked around.

- [ ] **Given** a transition request that skips a required state or lacks required evidence, **when** the controller is invoked, **then** it refuses, names the failed check, and exits non-zero.
- [ ] **Given** the full test suite of the control plane, **when** run on a machine with only python3, git, gh, **then** it is green.

### FR-5 — Event ledger and state machine

`factory/ledger/events.jsonl` is the single source of truth: append-only, UTC-timestamped, schema-validated (missing values are `null` + reason — never invented zeros; corrections are events, never edits). The 16-state story machine is enforced against the ledger + git + GitHub. `state.md`, `DASHBOARD.md`, and `PROVENANCE.md` are **generated** renders.

- [ ] **Given** a ledger and a story, **when** the renderers run, **then** generated files match the ledger byte-for-byte (check mode passes).
- [ ] **Given** any event with a missing field, **when** validation runs, **then** it is rejected unless a sibling `<field>_reason` is present and non-empty.

### FR-6 — Ownership lock

One story = at most one open claim. Claim is required **before the first active transition after BACKLOG**. Transfers happen only via a `handover` event; an abrupt takeover by the incoming owner is allowed when the old owner is unresponsive, and is flagged in the record. Claims and handovers **push on write**; races are resolved first-push-wins, and the loser receives a refusal plus the handover path.

- [ ] **Given** a story owned by session A, **when** session B requests a transition without holding a claim, **then** the controller refuses and names the lock.
- [ ] **Given** an open claim by an unresponsive owner, **when** the incoming owner records an abrupt takeover, **then** the ledger records the handover (from, to, reason, flagged) and subsequent transitions by the new owner pass.
- [ ] **Given** two machines claiming in the same window, **when** both push, **then** exactly one claim exists in the ledger and the other pusher receives the handover-path refusal.

### FR-7 — Lock override (human escape hatch)

A versioned file `factory/locks/OVERRIDE` (template `OVERRIDE.example`) with `scope` (story ID or ALL), `by`, `reason`, `date`. Present + valid + committed = claim-lock checks pass for the scope. **Fail-closed**: missing, malformed, uncommitted, or placeholder-filled is NOT an override. **Narrow**: bypasses the claim lock only. **Audited**: transitions under override are annotated (incl. the override blob SHA); renders show active overrides. Delete to restore.

- [ ] **Given** a stuck lock and a human who fills and commits the override, **when** a scoped transition runs, **then** it passes and the ledger annotates the override (scope, by, reason, blob SHA).
- [ ] **Given** an override file with a placeholder reason, **when** a locked transition runs, **then** it still refuses, naming the invalid field.

### FR-8 — Execution adapters

An adapter declares its contract in `adapters/<name>/adapter.toml` (machine-readable: prerequisites, tier→model table, usage reporting, identity convention) plus a `README.md`. v0.1 ships `human` (operator pastes package into any AI chat, commits artifacts) and `hermes` (dispatch with per-role model application). Interfaces are defined so `claude-code`, `codex`, and `api-direct` (OpenAI-compatible — the locked-down path) drop in at v0.2.

- [ ] **Given** the adapters directory, **when** a new adapter is authored, **then** it can be added without changing control-plane code (interface constraint test).

### FR-9 — Tier-based routing

The role→tier matrix (S/M/L difficulty) is unchanged in spirit; **tier→concrete-model mapping is per-adapter configuration**. No control-plane tool writes runtime configuration.

- [ ] **Given** a dispatch for role R at difficulty M, **when** routing resolves, **then** the ledger records the tier and the adapter-resolved model, and no runtime config is modified by the tool.

### FR-10 — Usage accounting

Usage is **executor-reported**: adapters that can harvest tokens do so; executors that cannot write `null` + reason. Metrics report (`metrics_report.py`) reconciles per-story usage from the ledger, with ambiguity reported, never guessed.

- [ ] **Given** dispatches with mixed usage reporting, **when** the metrics report runs, **then** every dispatch row closes and unreported usage renders as null + reason.

### FR-11 — Onboarding and target-repo readiness

Onboarding is deterministic scripts (python3, stdlib) plus a guided session: `onboard <repo>` preflight → inspect → propose → apply (idempotent, never overwrites) → verify. v0.1 ships the **Target-Repo Readiness Checklist** (target + workspace-level checks) and an **onboard verify** mode: per-check PASS/FAIL, fail-closed, non-zero exit, CI-gateable. Checks are **adapter-aware** (checks like "coding agent assignable" belong to one optional adapter, not to the core). v0.1.x hardens this with a fresh-repo test-out.

- [ ] **Given** a target repo missing a prerequisite (e.g. no board or no labels), **when** verify mode runs, **then** it reports the specific missing prerequisite as FAIL and exits non-zero.
- [ ] **Given** a compliant fresh repo, **when** onboard + verify run, **then** all checks PASS and the repo can immediately start the runbook.

### FR-12 — Human gates only

The only human touchpoints in a run: Storybook approvals (for UI stories), the merge gate, and escalations (2 failed loops). "Completed PR" = MERGE_ELIGIBLE with evidence → human merges → live-proven → closeout with provenance rendered. Any other question to a human during a run is a process defect and is logged as such.

- [ ] **Given** a story reaching MERGE_ELIGIBLE, **when** the operator requests merge eligibility, **then** the controller verifies tested SHA == head SHA, findings dispositioned, threads resolved, and a recorded human merge decision.

## Non-Functional Requirements

- **NFR-1 No services** — control plane runs with `python3` (stdlib), `git`, `gh`. No databases, no daemons, no second remote.
- **NFR-2 Determinism** — same inputs → same outputs for every control-plane command and generator; renders are regenerable.
- **NFR-3 Portability** — Linux and macOS; no root required; works inside devcontainers and locked-down networks where only the repo travels (the process definition travels in git; execution still needs GitHub — the fully offline path is the v0.2 `api-direct` adapter).
- **NFR-4 Fail-closed** — every check refuses on ambiguity and names the problem.
- **NFR-5 Honest records** — null + reason; corrections are events; no invented zeros; override and adjudication always visible.
- **NFR-6 Token economy** — dispatch packages inline their inputs (bundle pattern); every dispatch lifecycle closes with a usage row.

## Out of Scope (v0.1)

- Adapters beyond `human` and `hermes` (deferred to v0.2).
- Concurrent execution of multiple stories is allowed but uncoordinated in v0.1; per-story serialization is enforced by the claim lock (revisit per reference policy).
- Autonomous merge (human gate stays; opt-in policy only, if ever).
- Hash-chained tamper-evidence (v0.2 trigger-based).
- Central warehouse / cross-project rollups (derived-only, later).
- Non-GitHub forges.
- Adopting the kit in the private reference implementation (later, controlled migration).

## Success Criteria

- [ ] **Dress rehearsal**: one small idea → merged PR on the benchmark-app target, operated via kit + runbook only; result recorded as a provenance trail.
- [ ] **Handover drill** (two machines, fresh clone): (a) transition refused without claim, (b) cooperative handover passes, (c) abrupt takeover flagged and passes, (d) override path exercised and annotated — all four recorded.
- [ ] Control-plane test suite ported and green (python3 + git + gh only).
- [ ] Runbook completes with no unanswered steps — validated by a cold read from a fresh clone.
- [ ] Zero imports of any agent-runtime SDK in `tools/`; dispatch parity between `human` and `hermes` adapters on the dress-rehearsal story.
- [ ] Onboard verify runs green against the benchmark-app and reports precise failures on a deliberately non-compliant repo.

## Assumptions

- `gh` is authenticated with required scopes; a GitHub Projects v2 board exists for the target.
- The private reference implementation is available as a read-only extraction source.
- The benchmark-app repo (this repo's `benchmark-app/`) is an acceptable dress-rehearsal target.
- Storybook approval gates apply only to stories with UI surfaces.

## Dependencies

- `git`, `gh` CLI (auth scopes per onboarding), `python3` (stdlib only).
- GitHub: repo, labels, issue forms, Projects v2 board.
- For the `hermes` adapter: a Hermes runtime with `delegate_task`; for the `human` adapter: any AI chat and a git identity.

## Open Items (for grill)

All 2026-09-28 grill findings are dispositioned in `grill-disposition.md`; the owner calls awaiting ratification and the first build slice are in `build-plan.md`.