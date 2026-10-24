# Standalone Factory — Build Plan

> Status: DRAFT — direction folded 2026-10-23 (C1 — config format — ratified; C2–C3 open for the owner). Companions: `prd.md`, `architecture.md`, `test-strategy.md`, `target-repo-readiness.md`, `grill-disposition.md`.
> This plan sequences the v0.1 program with the workspace model folded in. Everything here obeys the kit laws in `architecture.md`.

## Goal

A standalone, runtime-agnostic factory: the **kit** (this repo) is the distribution; **workspaces** are control homes that bind target repositories; **environments** resolve where and how work executes; **onboarding** installs it. Once standing, the factory builds its target products — the private reference product first, as a controlled migration.

## Ground rules

- Control plane: python3 (stdlib) + git + gh. The process lives in the repo; the dispatch package is the universal interface; one mechanical writer; prevention over reconciliation.
- Human gates unchanged: UI approvals, merge, escalations.
- This repo stays generic — **no private product names ever** (mechanical grep before every push).
- The private reference implementation is **read-only** during extraction; its live operation is never destabilized; its adoption is a later, controlled migration story (its own project).

## Slices (declarative-first — the approved build order)

### S1 — Declarative layer: workspace + environments + multi-repo ledger schema — FIRST INCREMENT

| # | Task | Deliverable | Acceptance |
|---|---|---|---|
| T1 | **Extraction (self-containment)** — transition ↔ evidence ↔ gate table, per-event-type schemas, role catalog, extracted genericized from the reference | new sections in `architecture.md` (+ schema module in `tools/`), genericized (private-identifier grep clean before push) | a fresh-context reader (agent or human with no reference access), given only the folded bundle, answers a fixed questionnaire — every transition's required evidence keys, every event type's required/null-allowed fields, the full role→tier matrix, the gate catalog with invocations — with zero clarification requests; each unanswered item is a named FAIL |
| T2 | **Workspace + environment config** — schemas, example templates, validator | `templates/workspace/workspace.toml`, `templates/workspace/environments/{local,github}.toml`, a `tools/` validator + tests, plus the frozen key-by-key schema table in `architecture.md` §Configuration (required/optional, types, defaults, validation rule per key) | validator PASSes both templates; every refusal class has a named, precise FAIL (unknown key, malformed binding, unresolvable pin, inline secret) |
| T3 | **Multi-repo ledger schema** — `repo@sha` pinning, per-repo sub-events, set-level transitions + completeness rules, corrections | architecture section + schema module + fixtures/tests | set-level transitions refuse on an incomplete set; per-repo sub-events fold correctly |
| T4 | **CI** — minimal workflow: `python3 -m unittest` + validator over the templates | workflow file | green on the PR; runs on every push |
| T5 | **Alignment pass** — layouts, readiness checks, command paths, cross-refs | doc diffs | cold-read: no contradictory paths left in the bundle |

**Explicitly NOT in S1:** runtime launch, model invocation, dispatch reservation, any change inside the reference implementation, board-schema changes, cloud spending.

### S2 — Control-plane port + workspace CLI (read path first)

Port the control-plane tools genericized (controller, ledger library, gates, prep tools, renders, card helpers) per the T1/T3 extractions, with their test suite wired into CI — the port lands **here, before its consumers** (S3's verify mode, S5's drills). Then the workspace CLI wraps the ported helpers: `factory workspace show` (bindings, factory pin, control-home mode, derived states) and `factory env show|resolve` (resolved models/limits per environment, refusing on missing credential references) — stable text/JSON output, nonzero refusal exits, read-only proof. No second state machine. Write-path candidates (`workspace init | add-repo`) as a follow-on increment.

Acceptance: ported control-plane suite green in CI; the read-path commands run read-only against a fixture workspace, wrapping the ported helpers.

### S3 — Onboarding

Deterministic scripts (preflight → inspect → propose → apply idempotent → verify fail-closed) + the guided-session brief (`ONBOARDING.md` consumed by any agent runtime) + the readiness registry (target + workspace checks) + the **fresh-repo test-out scripts** (the from-scratch test-out **run** gates v0.1.x hardening — per the vision and PRD FR-11). Acceptance: verify green on `benchmark-app`; precise FAILs on a deliberately non-compliant repo; the test-out scripts ship and run green against `benchmark-app` as a smoke.

### S4 — Runbook + adapter packages

`runbook.md` (idea → merged PR; human and orchestrator modes; contained-mode section) + `adapters/human` + `adapters/hermes` packages (`adapter.toml` + README). The hermes adapter's **live** leg: one real local dispatch through the standard dispatch mechanism (small token cost — no cloud), recorded as "live dispatch, non-sealed mechanism"; the sealed-mechanism re-run follows when the launch increment is authorized.

### S5 — Acceptance drills

D-1 dress rehearsal (both legs, per `test-strategy.md`) + D-2 handover drill (four variants). Records committed to this repo.

### Later (sketch)

v0.2 adapters (`claude-code`, `codex`, `api-direct`); reference-product adoption (controlled migration — a private project, run when the kit is proven); mirrors/rollups.

## Relationship to the execution seam (reference side)

The reference's bounded queue — **artifact validation** (contract item 7) next, then the gated launch increment — continues on its own track. The seam is the **per-environment dispatch mechanism** that environments drive; it no longer gates the kit's acceptance — the live hermes leg (C2, ratified) runs locally on the standard dispatch mechanism, with the sealed re-run following whenever the launch increment is authorized.

## Owner calls (C1–C3 ratified 2026-10-23 — all closed)

| # | Call | Recommendation | Why |
|---|---|---|---|
| C1 | Config format | **TOML — ✅ ratified 2026-10-23** (`tomllib`, Python ≥ 3.11) | the only format satisfying the stdlib law; YAML would need a non-stdlib dependency or a hand-rolled parser (forbidden) |
| C2 | Live-parity measurement | **✅ ratified 2026-10-23 (pragmatic path)** — both legs in D-1 + mechanical contract shape (required paths + sections); the live hermes leg runs locally through the standard dispatch mechanism once S4 ships ("live dispatch, non-sealed mechanism"); sealed-mechanism re-run when launch is authorized | makes FR-3's parity real, not tautological; decouples the kit's sign-off from execution-spending decisions |
| C3 | Acceptance targets | **✅ ratified 2026-10-23 (split kept)** — v0.1 criteria: `benchmark-app` dress rehearsal + handover drill + conformance (verify green / precise FAILs) + test-out scripts shipped (smoke on `benchmark-app`); the from-scratch fresh-repo test-out is the **v0.1.x hardening item #1** | the reference product's adoption stays a separate, later migration |

## How the build runs

- Work items: issues in this repo; **each slice lands as a reviewed PR** — bounded increments, independent fresh-context review before the merge gate, owner merges.
- Every increment states its own explicit exclusions (what it deliberately does not do) — the same discipline as the reference's bounded queue.
- Definition of done for the program: kit onboards a fresh repo (from-scratch test-out — the v0.1.x hardening gate, its first item); one story runs idea → merged PR via the runbook (**both adapter legs** — human + hermes, the hermes leg run locally per C2; the sealed-mechanism re-run is a later strengthening, not a completion gate); handover drill passes; every render/provenance check clean. Then the reference product is adopted (controlled), and the factory builds its products.