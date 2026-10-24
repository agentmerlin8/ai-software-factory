# Architecture: Factory Kit v0.1

> Status: DRAFT — direction folded 2026-10-23 (workspace model, environments, single-writer durability; format pins). Drafted 2026-09-28.
> Companion docs: `prd.md`, `test-strategy.md`, `target-repo-readiness.md`, `grill-record-2026-09-28.md` (round 1), `grill-disposition.md` (every finding's disposition), `build-plan.md` (slice plan).

## Overview

The kit puts the process in the repo and keeps every runtime behind an adapter. The control plane is deterministic software; every AI sits behind a **dispatch package**.

```
                 ┌────────────────────────────────────────────────┐
                 │              CONTROL HOME (Mode 1)             │
                 │                                                │
  idea ──▶ issue │  factory/          runbook.md                  │
                 │   ├── tools/       (control plane, python3)    │
                 │   ├── gates/       (mechanical checks)         │
                 │   ├── ledger/      (events.jsonl = truth)      │
                 │   ├── templates/   (dispatch pkg, state, etc.) │
                 │   ├── locks/       (OVERRIDE escape hatch)     │
                 │   ├── process/     (process rules, versioned)  │
                 │   ├── agents/      (role packages)             │
                 │   └── adapters/    (human · hermes · …)        │
                 │  stories/          (specs, plans, evidence)    │
                 └───────────┬───────────────────┬────────────────┘
                             │                   │
              operator (human) │         orchestrator (any runtime)
                             │                   │
                             └──── follows runbook.md ────┐
                                                          ▼
                              dispatch package (one per role, pinned @ SHA)
                                    │                     │
                             human adapter          hermes adapter
                          (paste into any chat)   (subagent dispatch)
                                    │                     │
                                    └──── artifacts + usage ────┘
                                                          │
                                              controller enforces transitions
                                              gates run; renders regenerate;
                                              board syncs; provenance follows
```

Substrate: **git + gh + python3 (stdlib)**. No services, no daemons, no second remote (NFR-1).

## Workspace model — control homes, workspaces, environments

Since this draft, the consumption model has been refined further (owner-accepted 2026-10-23; it generalizes the preview and adoption decisions rather than replacing them):

```
factory repo (this repo)   the distribution: CLI · core agents · skills · process rules · templates · adapters
   └─ workspace            the control home for a project: repo bindings + config + ledger + story records + renders
        └─ target repos    product: code · story branches · PRs · CI   (evidence lives here as pinned SHAs)
```

- **Workspace = the control home.** It carries `workspace.toml` (identity, repo bindings, factory pin), `environments/`, `ledger/`, `stories/`, dispatch packages, `locks/`, renders. Same skeleton as the single-repo `factory/` install — what changes is *who hosts it*:
  - **Mode 1 — in-repo** (single-repo projects): control home inside the target repo. The original kit shape; **no migration needed**.
  - **Mode 2 — dedicated workspace repo** (multi-repo default): control home in its own repo, created from this kit's workspace template; `workspace.toml` lists the target repos. Idea front door, board, and story records live at the workspace; the target repos stay product.
  - **Mode 3 — primary-repo control home** (special cases only): control home inside a designated primary target repo — permitted by the accepted model; out of scope for v0.1 tooling.
- **The "ledger lives with the product" law generalizes:** with N repos the product = the workspace's repo set, and the **control home is the set's authoritative git home**. Every event still pins exact SHAs; validation resolves `repo@sha` against *that repo's* object database; provenance joins across the set.

### `workspace.toml` (pinned format: TOML)

```toml
# workspace.toml (illustrative)
workspace = "example-workspace"
control_home = "dedicated"          # dedicated | in-repo
[factory]
pin = "<kit tag/sha>"               # the kit version this workspace runs
[[repos]]
id = "app"                          # target repositories
role = "primary"                    # primary | member
url = "github.com/ORG/app"
[[repos]]
id = "web"
role = "member"
url = "github.com/ORG/web"
[policy]
gates = "default"
```

Board/project id, default branch and identity conventions also resolve from workspace config; tools never hardcode paths.

### `environments/` — how, never what

An environment resolves *how* work executes; it never changes the process. Per-environment file (TOML):

```toml
# environments/local.toml (illustrative)
runtime = "hermes"                  # hermes | copilot-cli | api-direct | human | …
dispatch = { mechanism = "delegate_task", isolation = "per-invocation" }
state = { agent_home = "~/.hermes", transcripts = "<path>", usage = "harvest" }
models = { T1 = "…", T2 = "…", T3 = "…" }
credentials = { refs = ["…"] }      # references only — never inline values
limits = { wall = "…", runs = "…", budget = "…" }
```

- Fits the execution seam: routing resolved and sealed **before spawn**; configuration isolated per invocation; shared runtime config is never mutated; refuse — never fall back.
- Readiness stays **adapter-aware** (kit law 8): each environment declares which adapters are in play and their prerequisites.

### Multi-repo stories — mechanics

- **Identity**: workspace-scoped `STORY-NNN`. The spec declares `repos: [...]`, each repo's role (provider / consumer / both), plus a **cross-repo contract block** — the pinned interface (API shapes, events, schemas), owned by the provider repo.
- **Branches**: the story's branch exists in **every touched repo**; one worktree per repo per story.
- **Plans & dispatches**: impl/test plans carry per-repo manifests; seam recon runs per repo; dispatch packages pin inputs as `repo@sha:path` (bundle pattern; parity rule unchanged).
- **Ownership**: **one claim per story**, workspace level (`start_shas` per repo). Handover transfers the whole story.
- **Gates**: build/tests green in **each** touched repo; the mechanical plan gate runs once at the workspace; the review package carries all repo diffs + the contract block; findings tagged by repo; fix loops target specific repos.
- **State**: story-level states unchanged; per-repo detail rides as sub-events (`BUILD_GREEN{repo}`, `DRAFT_OPENED{repo}`, `MERGED{repo}`). Set-level transitions refuse until the set is complete: `MERGE_ELIGIBLE` = all PRs review-closed + green; `MERGED` = all merges done; `LIVE_PROVEN` = cross-repo end-to-end proof.
- **Merge order**: expand-first — the contract owner merges first, consumers follow; reverts run in reverse. Recorded in the plan.
- **Closeout**: workspace-level PROVENANCE joins idea → decisions → dispatches (per repo) → evidence → PR set → merge set → proof. Each PR body references the workspace story.
- **Local resolution**: each environment records where bound repos are checked out locally (workspace-relative sibling paths by default, plus an optional `repos[].path` override key); the exact convention freezes with T2/T3.

### CLI surface (candidate additions)

Shipped in the reference and ported genericized: `status` · `doctor` · `prepare` (sealed, non-launching) · `validate`, plus the run-phase record path, surfaced in the kit as `run-stage`. Workspace layer (candidates): `factory workspace init|add-repo|show` · `factory env show|resolve` · `render` · `claim` / `transition` (wraps the existing controller — no second state machine; read path first).

## Ledger durability and the single writer

The "ledger lives with the product" law is binding, and the write path is a **single mechanical writer** — the bot-App identity and its push path are proven live in the reference implementation; the enforcing ruleset there is staged (`disabled`) pending activation verification (see item 5), and the same staging discipline applies to every onboarded repo:

1. The ledger lives at the control home, on the default branch (`main`).
2. `main` is protected by a **ruleset** (require PR + approval + required checks; block force-push/delete). The bypass list contains **only the factory bot App** (actor type `Integration`, mode `always`). Humans are never in the bypass list — the human merge gate stays mechanically real. In Mode 2 the ruleset and bot-App bypass live on the workspace repo (the control home); target repos keep standard protection with no factory bypass. A ruleset bypass is identity-scoped, never path-scoped: the App's exemption covers whatever the App pushes, so the bot's write surface is bounded by governance, not by the ruleset — its token is minted only inside permissioned controller jobs, its pushes are auditable (App commit identity + ruleset insights), and the watchdog alarms on unexpected pushes. The bot never merges product PRs; merging stays a human-gated action. The port's acceptance checks assert both boundaries: the bypass identity never merges, and App pushes are controller-only.
3. Claims and handovers **push on write** through the controller (bot identity). Races: first-push-wins; the loser is refused with the handover path. A non-fast-forward push → fetch → **rebuild the pending append(s) on the fetched tip** → re-validate claim uniqueness → retry — never re-push the stale tip as-is, and never merge independently appended ledger histories: appends serialize on the remote tip (first-push-wins per claim; appends for different stories stack). If one ever does, it is adjudicated with a `correction` event — never an edit.
4. Workers and agents never hold writer authority: every state mutation flows through the permissioned controller path. Story branches + PRs are the only artifacts workers write directly. Story/product PRs never modify control files (`factory/ledger/**`, `factory/locks/**`, generated renders): the port includes a control-file guard in the gate set (a PR diff check) so an approved story PR cannot carry ledger or lock changes past the controller — trusted initialization (the onboarding ledger seed) is the one sanctioned, recorded exception.
5. **Staging:** where a forge plan lacks ruleset *Evaluate* mode (GitHub Pro — Evaluate is Enterprise-only), stage the ruleset `disabled` (non-enforcing) and flip to `active` after the bot push path is verified live. Never describe a disabled or merely documented ruleset as active protection; where even ruleset API reads are plan-gated (observed live in the reference — HTTP 403), enforcement is recorded as unverified and never claimed as protection.

## Components

### Kit structure (source of truth, this repo)

| Path | Contents | Ported from |
|---|---|---|
| `agents/` | Role packages: instruction + inputs + outputs + stop rules, runtime-neutral | `agents/*.agent.md` (genericized) |
| `process/` | Process rules moved out of any agent runtime (planning, adversarial reviews, grill, recovery, UI-approval gate). Pointers may remain in a runtime's skill system as conveniences — never as requirements | process skills (content → docs, pointers left behind) |
| `tools/` | Control plane (see below) | private reference implementation, genericized |
| `adapters/` | `human/`, `hermes/`, (+ v0.2: `claude-code/`, `codex/`, `api-direct/`) — each with `adapter.toml` manifest + README | new |
| `templates/` | Workspace + story + config templates (workspace.toml, environments, story skeleton) | new |
| `runbook.md` | idea → completed PR, both modes | private runbook, genericized |
| `onboarding/` | install scripts + verify mode + readiness checks registry | `onboarding/`, extended |

Installed layout differs — see §Canonical layouts below.

### Canonical layouts

Kit repo (this repo — the distribution): `agents/` · `process/` · `tools/` (incl. `tools/gates/`) · `adapters/` · `templates/` · `onboarding/` · `runbook.md` · `docs/` · `kit-v0.1/`.

Installed control home (Mode 1 target repo, or Mode 2 workspace repo):

| Path | Contents |
|---|---|
| `workspace.toml` | Workspace identity, repo bindings, factory pin |
| `environments/` | Per-environment TOML (how work executes) |
| `stories/` | Story records: `spec.md`, refinement/decisions, `test-plan.md`, `impl-plan.md`, `handoff.md`, `state.md`, `PROVENANCE.md`, `evidence/`, `dispatch/`, `review/` |
| `factory/runbook.md` | Installed runbook |
| `factory/tools/` | Control plane |
| `factory/gates/` | Mechanical check scripts + fixtures |
| `factory/ledger/` | `events.jsonl` (the truth) + schema notes |
| `factory/templates/` | Dispatch package + story templates |
| `factory/locks/` | `OVERRIDE.example` escape hatch |
| `factory/process/` · `factory/agents/` · `factory/adapters/` | Installed process rules, role packages, adapter bindings |
| `factory/PREFLIGHT.md` | Verify-mode output report (render-exempt) |
| `factory/KICKOFF.md` | Installed cold-start template |
| `factory/DASHBOARD.md` + `stories/<id>/state.md`, `stories/<id>/PROVENANCE.md` | Generated renders (never hand-edited) |

Work artifacts (code) live on the story branch in each touched repo; the control home holds the records.

### Control plane (`tools/`)

| Tool | Responsibility | Notes |
|---|---|---|
| `controller.py` | Transition engine: validates every state change against ledger + git + gh; refuses with named checks; claim-lock enforcement; override consultation; claim/handover/override commands | Judgment-free |
| `ledger_lib.py` | Event schema, validation (null+reason), append/read helpers | Schema is the contract |
| `gates/` | Mechanical pre-checks (plan gate, pre-review gate, digest gate) with fixtures | Run before any LLM review |
| `make_context_bundle.py` | Deterministic context assembly: inline pinned inputs at base SHA into one bundle | Feeds dispatch packages |
| `dispatch_prep.py` | Seam recon: contract slice, named-test exists/missing inventory, scope boundary, generated hard-read gate block | Pre-coder/fixer |
| `plan_prep.py` | Planner verify-pack (paste-ready block) | Pre-planner |
| `build_handoff.py` | Story handoff rendering (acceptance, files CREATE/EXTEND, verify commands, resume preamble) | |
| `model_router.py` | Role→tier matrix resolution; **per-environment model table**; records pre-spawn dispatch event. Never writes runtime config | |
| `record_usage.py` | Usage events from executor reports (harvest when available; null+reason otherwise); manual entry path kept for human mode | No runtime DB dependency |
| `metrics_report.py` | Per-story token/loop reconciliation (baseline optional in v0.1: null+reason when absent); unrouted-dispatch census | |
| `render_state.py`, `render_dashboard.py`, `render_provenance.py` | Generated renders (never hand-edited); `--check` modes | |
| `sync_board.py` | GitHub Projects v2 Stage writes (explicit, never inferred) | Project id from config |

**Port rules:** keep behavior; genericize names (no product-specific component/file/test names); drop runtime-specific code paths (runtime-config writes, runtime-DB harvests → replaced per FR-9/FR-10); stdlib only.

Gate scripts live under `tools/gates/` in the kit repo; on install they land at `factory/gates/` (a sibling of `factory/tools/`, per §Canonical layouts).

Since the draft, the reference shipped a read-only CLI (`status`, `doctor`) and a sealed, non-launching preparation/validation path (the **execution seam**). The kit ports those genericized; the launch surface remains gated in the reference (no cloud runner, no spend).

## Gate catalog (extracted)

Four mechanical gates, extracted from the reference with descriptive slugs (no numeric ids):

| Gate | What it checks | Script | Invocation | When |
|---|---|---|---|---|
| `digest-gate` | Context digest completeness + anchors at a pinned base SHA | `check_digest.py` | `python3 factory/tools/check_digest.py <STORY-ID> [BASE_SHA]` | After context acquisition, before the planner dispatch. Exit 0 = planner may dispatch; non-zero = re-dispatch context acquisition, never hand-patch the digest |
| `plan-gate` | Plan-lane mechanical checks: contract block schema, AC coverage, cross-story collisions, anchor resolution, seam verification, coverage set-diffs, snippet annotations | `pre_review_gate.py` (plan mode) | `python3 factory/gates/pre_review_gate.py plan <story-dir> [--spec <path>] [--inflight auto\|none]` | PLANNED → GATE_PASSED (run live by the controller) and before any LLM plan review. Exit 0 = pass |
| `build-gate` | Build-lane mechanical checks: manifest↔diff, AC↔test discovery, pinned command execution, api-shape, UI DOM assertions, fidelity checks | `pre_review_gate.py` (build mode) | `python3 factory/gates/pre_review_gate.py build <story-dir> [--base main] [--diff-file <name-status file>] [--discovery <project>=<list-tests file>]... [--run-discovery] [--run-verify]` | At BUILD_GREEN, before any LLM code review. Exit 0 = pass |
| `gate-selftest` | Gate acceptance harness: seeded-invalid fixtures rejected, valid-but-unusual accepted | `test_gate.py` | `python3 factory/gates/test_gate.py` | CI and before any gate change deploys. Non-zero if any fixture behaves wrongly |

Notes:

- `plan-gate` and `build-gate` are **modes of one script** (the pre-review gate).
- Invocations are the **installed** control-home form (`factory/...`); in this kit repo the scripts live under `tools/` (`python3 tools/gates/pre_review_gate.py ...`) — see §Canonical layouts.
- **Mechanical gates run BEFORE any LLM review** (mechanical-before-judgment law).
- Fixtures policy: a gate change is not deployed until the acceptance harness rejects every seeded-invalid fixture and accepts every valid-but-unusual one.
- Machine-readable form: `tools/gate_catalog.py` (`GATES`).

## State machine

Pipeline states, **ported as-is from the reference** (the reference map has extended since this draft — currently 19 pipeline states + BLOCKED; the current set is extracted below):

```
BACKLOG → GRILLED → TEST_PLAN → SPEC_FROZEN → PLANNED → GATE_PASSED
        → PLAN_APPROVED → STORYBOOK_APPROVAL → CODING → BUILD_GREEN
        → DRAFT_OPENED → REVIEWING → FIXING → FULL_TESTS_GREEN
        → READY_FOR_REVIEW → MERGE_ELIGIBLE → MERGED → LIVE_PROVEN → CLOSED
(any) → BLOCKED → resumes at originating state
```

- `STORYBOOK_APPROVAL` applies to UI stories (see the UI-skip rule below).
- Every arrow = a controller transition with evidence + checks.
- `READY_FOR_REVIEW` (forge flag) ≠ `MERGE_ELIGIBLE` (factory state). Never conflated.
- Loop caps: 2 per stage; escalation beyond.
- BLOCKED entry/exit evidence rules: see the transition table below (`reason` required; resumes at the originating state).

### Transition table (extracted — current set)

Required evidence keys are passed `k=v` on a transition request and persisted into the transition event's `check_results`; the controller refuses without them — it never invents values. Hard checks are controller-enforced; gate/artifact names refer to §Gate catalog and §Canonical layouts. Machine-readable form: `tools/state_machine.py` (`EVIDENCE_KEYS`, `CHECK_SUMMARY`).

| To state | Required evidence keys | Hard checks (controller-enforced) | Gate / artifacts |
|---|---|---|---|
| GRILLED | — | `story.md` exists; Given/When/Then acceptance criteria; ≤6 top-level ACs; one structural signal | `stories/<id>/story.md` |
| TEST_PLAN | — | `story.md` re-checked; DEV and QA grill records exist, each signed by its persona owner; ≥80% correct; zero major misunderstandings; signer/author overlap logged (never blocks) | `grill-dev.md`, `grill-qa.md` |
| SPEC_FROZEN | `spec_path`, `spec_freeze_sha` | `test-plan.md` committed at `spec_freeze_sha`; canonical story id (next-in-sequence unless `sequence_reason`); spec file exists; `spec_freeze_sha` resolves in git; branch convention `story/<id>-<slug>` when supplied; spec-freeze human approval (answered `human_gate gate=spec_freeze`, or an allowlisted human via `approved_by`) | `test-plan.md`, spec file, git object DB |
| PLANNED | — | SPEC_FROZEN reached; impl-plan contract block present and validates; planner lane closed dispatch, or a lane-skip deviation with human approval | `impl-plan.md` contract block |
| GATE_PASSED | — | plan-gate exit 0 (run live) | `plan-gate` (§Gate catalog) |
| PLAN_APPROVED | `verdict` | plan-review verdict recorded; verdict ∈ {PASS, PASS-WITH-NITS}; plan loops ≤2 | `stage` event (`stage=plan_review`) or evidence |
| STORYBOOK_APPROVAL | — | answered `human_gate gate=visual` signed by an allowlisted human | UI stories only |
| CODING | — | PLAN_APPROVED reached; claim (assignment) exists — the first gated transition; non-UI skip requires the declared non-UI line | `assignment` claim event (§Claim lifecycle) |
| BUILD_GREEN | `commit_sha`, `build_result`, `named_tests_result` | `commit_sha` resolves; build green; named tests green; coder lane closed dispatch or lane-skip deviation | git object DB; the project's test runner |
| DRAFT_OPENED | `pr_number`, `review_request_mechanism` | `pr_number` recorded; mechanism ∈ {ready-push, draft-mention-opportunistic} (ONE authenticated request); no duplicate DRAFT_OPENED | PR |
| REVIEWING | — | DRAFT_OPENED reached | external review request |
| FIXING | — | REVIEWING reached (or post-review reentry) | `finding` + `loop` events |
| FULL_TESTS_GREEN | `projects`, `tests_passed`, `tests_failed`, `tests_skipped`, `exclusions`, `exit_code` | exact suite membership (projects, skip/exclusion reasons, exit code); `exit_code=0`; `tests_failed=0` | full-suite `stage` event |
| READY_FOR_REVIEW | `internal_verdict`, `threads_state`, `fix_pushes`, `r1_arrival` | internal verdict ∈ {PASS, PASS-WITH-NITS}; threads all resolved OR an external-review-unavailable watchdog exception logged (absence is never recorded as PASS); ≤1 fix push where both first-round sets arrived pre-edit (target, not safety constraint) | review threads |
| MERGE_ELIGIBLE | `tested_sha`, `head_sha`, `unresolved_threads` | tested SHA == current head (re-checked at the gate); verdict eligible; no logic-touched nits; external findings dispositioned; external-unique findings attributed when instrumentation present; `unresolved_threads=0`; human merge approval recorded (`human_gate gate=merge`, answered) | `finding`/`review_attribution` events; merge gate |
| MERGED | `merged_sha`, `merged_at`, `pr_number` | merge evidence complete | merge commit |
| LIVE_PROVEN | `live_proof_evidence` | evidence is an existing path or http(s) URL | live-proof artifact |
| CLOSED | `doc_sync`, `closeout_checklist` | doc-sync obligations checked; closeout checklist complete; outcome recorded (`outcome` event with `merged_sha`) | `outcome` event; closeout checklist |
| BLOCKED | `reason` | from any state; reason required; resumes at the originating state; nothing else allowed while blocked | `transition` event (flag, not a column) |

Evidence value formats: sha keys are 7–40 hex resolving in the repo object DB; `tested_sha` must prefix-match `head_sha`; `pr_number`/`fix_pushes` are digits; verdict keys ∈ {PASS, PASS-WITH-NITS}; `build_result`/`named_tests_result` = `green`; `exit_code`/`tests_failed`/`unresolved_threads` = `0`; `threads_state` ∈ {all-resolved, watchdog-exception}; `r1_arrival` ∈ {both-pre-edit, other}; `live_proof_evidence` = existing path or http(s) URL.

Rules:

- **Next-in-sequence** is the only default move; SPEC_FROZEN accepts a recorded `sequence_reason` escape for the story-id sequence rule.
- **FIXING reentry** is legal only from {FULL_TESTS_GREEN, READY_FOR_REVIEW, MERGE_ELIGIBLE} (a FAIL verdict routes back) — never from pre-review states.
- **UI skip**: PLAN_APPROVED → CODING without STORYBOOK_APPROVAL is legal only when the story declares non-UI; a missing or ambiguous declaration is treated as UI (fail closed).
- **BLOCKED**: from any state, `reason` required, resumes at the originating state, and no other transition is allowed while blocked.
- **Claim gate**: CODING is the first gated transition — an assignment/claim must exist (§Claim lifecycle).
- **Lane-skip deviations** for the planner/coder lanes require human approval (`deviation` event, `lane-skip:<role>`).
- **Loop caps**: 2 per stage, then escalation (BLOCKED with `gate=loop_cap` human gate).

## Data model — the ledger

`factory/ledger/events.jsonl` — append-only, one JSON object per event, UTC timestamps, stable IDs.

Event types (v0.1, ported set): `transition`, `dispatch`, `usage`, `stage`, `finding`, `loop`, `recovery`, `correction`, `outcome`, `human_gate`, `deviation`, `review_context`, `review_attribution`, and `assignment` (claim lifecycle: claim · handover · release — ONE event type with per-op fields). (`override_used` is an annotation payload on transition events — not an event type; see conventions below.) Full per-type schemas: §Ledger event schemas.

Conventions (unchanged from the reference):

- Missing value = `null` + sibling `<field>_reason` (non-empty), mechanically validated per type.
- Corrections are events, never edits.
- `override_used` is an annotation payload embedded in the transition it permitted — **not** a separate event; `stage` records forge/board sync — distinct from `transition`.
- Dispatch IDs are pre-spawn: `<story>-<role>-<attempt>-<unix>` (unix = seconds, UTC; attempt numbers are allocated by the controller at pre-spawn).
- Every dispatch lifecycle closes (outcome + usage or null+reason).
- In multi-repo mode, pinning fields carry `repo@sha` (see §Workspace model).
- **Render byte contract:** renders derive from **ledger + git only** (no live forge reads): UTC ISO-8601 (`YYYY-MM-DDTHH:MM:SSZ`), sorted keys, LF endings, no wall-clock in output — determinism is a property, not a hope.

### Claim lifecycle (assignment event, extended)

| Op | Fields | Rule |
|---|---|---|
| claim | `owner`, `host`, `session`, `start_sha`, `claimed_at` | Required before the first active transition after BACKLOG (planning counts as working the story; pinned: the controller-enforced gate is CODING — see §Transition table). Refused if an open claim exists (unless override) |
| handover | `from`, `to`, `reason`, `flagged` (bool) | The only sanctioned transfer; abrupt takeover = flagged handover |
| release | implicit at CLOSED; record persists | The durable ownership record |

### Override file (`factory/locks/OVERRIDE`)

```
scope: STORY-012   # or ALL
by: <human name>
reason: <why — required, no placeholders>
date: <UTC>
```

- Read semantics: the override is the blob at `HEAD` of the current branch; a working-tree file that differs from `HEAD` is fail-closed **uncommitted**. Not an override: absent, malformed, placeholder-filled (angle-bracket template values, empty values, literal `TODO`/`CHANGEME`), or uncommitted — the refusal names the bad field.
- Valid + committed + scoped → claim-lock checks pass for the scope; every permitted transition carries an `override_used` annotation incl. the file's blob SHA at use time.
- Narrow: never bypasses evidence, gate, or order checks. Renders show active overrides.

## Ledger event schemas (extracted)

Machine-readable form: `tools/ledger_schema.py` (pure validation, no I/O; the append/load ports connect at S2). This section and the module are the same contract — keep them consistent.

**Envelope** — required on EVERY event: `event_id`, `recorded_at` (UTC `...Z`), `provenance` ∈ {`live`, `backfilled`}, plus `event` (the type name).

**The null+reason law (both directions).** A null-allowed field carries a sibling `<field>_reason`: a non-empty string when the field is null, and null whenever the field has a value — never invented zeros. A `<field>_reason` for a NON-nullable required field is itself a violation. Exempt value fields (the field IS the reason, no sibling): `blocked_reason`, `fallback_reason`, `root_cause_one_line`.

| Type | Required fields | Null-allowed fields (sibling `<field>_reason`) | Enums / type rules |
|---|---|---|---|
| `assignment` | `story_id`, `op` + per-op: claim → `owner`, `host`, `session`, `start_sha`, `claimed_at`; handover → `from`, `to`, `reason`, `flagged`; release → `owner` | — | `op` ∈ {claim, handover, release}; handover `flagged` is an explicit bool. "One open claim per story" + refusal semantics are CONTROLLER rules (S2), not schema rules |
| `dispatch` | `story_id`, `role`, `requested_model`, `reported_model`, `thinking_setting`, `session_id`, `parent_session_id`, `attempt`, `started_at`, `ended_at`, `outcome`, `prompt_version`, `worktree`, `model_unverified` | `reported_model`, `thinking_setting`, `session_id`, `parent_session_id`, `ended_at`, `outcome`, `worktree`, `requested_model` | `event_id` matches `<story>-<role>-<attempt>-<unix>`; `reported_model` null requires `model_unverified=true` |
| `usage` | `dispatch_id`, `input_tokens`, `output_tokens`, `cached_input_tokens`, `thinking_tokens`, `token_semantics`, `usage_source` | `cached_input_tokens`, `thinking_tokens`, `dispatch_id` | tokens-only — no cost fields (kit law) |
| `stage` | `story_id`, `stage`, `started_at`, `ended_at`, `commit_sha`, `tests_passed`, `tests_failed`, `tests_skipped`, `tests_excluded`, `evidence_path` | `started_at`, `ended_at`, `commit_sha`, `tests_passed`, `tests_failed`, `tests_skipped`, `tests_excluded`, `evidence_path` | |
| `finding` | `finding_id`, `story_id`, `source`, `reviewed_sha`, `introduced_sha`, `class`, `severity`, `disposition`, `logic_touched`, `overlap_id`, `fixer`, `fix_sha`, `validation`, `evidence_path` | `reviewed_sha`, `introduced_sha`, `overlap_id`, `fixer`, `fix_sha`, `validation`, `evidence_path`, `severity` | `source` ∈ {internal, external, human, orchestrator, script_gate}; `class` ∈ {mechanical, reasoning, contract, nit, design-invalidating}; `disposition` ∈ {accepted, dismissed, deferred, fixed, fix_in_revision, record_only}; `logic_touched` bool; nit law below |
| `loop` | `story_id`, `stage`, `loop_n`, `class`, `root_cause_one_line` | — | `root_cause_one_line` non-empty string — tagged at the moment it happens, never backfilled |
| `recovery` | `story_id`, `original_assignment`, `fallback_reason`, `actual_model`, `remaining_scope`, `consumed_loop_count`, `additional_cost`, `human_intervention` | `actual_model`, `remaining_scope`, `additional_cost`, `original_assignment` | |
| `transition` | `story_id`, `from_state`, `to_state`, `requested_by`, `checks_run`, `check_results`, `approved_by` | `approved_by` | `checks_run` is an array of strings; `check_results` is an object |
| `human_gate` | `story_id`, `gate`, `question`, `asked_at`, `answered_at` | `answered_at`, `story_id` | `gate` ∈ {visual, merge, loop_cap, scope, safety, meta, avoidable, spec_freeze} |
| `outcome` | `story_id`, `merged_sha`, `merged_at`, `live_proof_status`, `observation_end`, `escaped_defects`, `totals`, `recommendation` | `merged_sha`, `merged_at`, `live_proof_status`, `observation_end`, `escaped_defects`, `totals`, `recommendation` | |
| `correction` | `story_id`, `contradicted_claim`, `corrected_value`, `source_citation`, `corrected_artifact` | `story_id` | |
| `deviation` | `story_id`, `contract_field`, `planned`, `actual`, `justification`, `approved_by` | `approved_by` | lane-skip deviations carry `contract_field = lane-skip:<role>` + human `approved_by` |
| `review_context` | `story_id`, `review_role`, `reviewed_sha`, `base_sha`, `context_manifest_path`, `context_manifest_digest`, `prompt_version`, `dispatch_id`, `delivery_surface`, `requested_at` | `dispatch_id` | `review_role` ∈ {behavior_reviewer, contract_reviewer, external_review}; `delivery_surface` ∈ {dispatch-package, pr-full-diff}; SHAs full 40-hex; digest full SHA-256; path confined under `stories/<story_id>/` (no `..`) |
| `review_attribution` | `story_id`, `finding_id`, `internal_context_event_id`, `external_context_event_id`, `classification`, `classified_by`, `evidence_path` | — | `classification` ∈ {MISSED_WITH_CONTEXT, CONTEXT_ABSENT, INTRODUCED_AFTER_INTERNAL}; joins below |

Rules:

- **Dispatch IDs are pre-spawn**: `<story>-<role>-<attempt>-<unix>`. Lifecycle: up to TWO rows share one event_id — pre-spawn (`ended_at` null) then completion (`ended_at` set), in that order; a backfilled dispatch may be a single ended row. More rows, a second completion (stale re-start), an orphan completion, or reversed order are errors. Non-dispatch event_ids are strictly unique.
- **Nit law**: a fix that changes logic is NEVER class `nit` — nits require `logic_touched=false` AND verifying evidence (`fix_sha` or `validation`); `logic_touched=true` + `class=nit` is forbidden at schema level.
- **Review context is immutable**: full 40-character base/reviewed SHAs, a SHA-256 manifest digest, and a manifest path confined under `stories/<id>/`.
- **Review-attribution joins**: `internal_context_event_id` must reference a `behavior_reviewer` review_context, `external_context_event_id` an `external_review` review_context, both of the same story; classifications other than INTRODUCED_AFTER_INTERNAL require identical `reviewed_sha` on both contexts; `finding_id` must reference an external-source finding of the same story.
- **Corrections are events, never edits** — a contradicted claim gets a `correction` row citing its source; the ledger is append-only.

## Dispatch package (universal interface)

One generated markdown per dispatch. Schema (six sections):

| Section | Content |
|---|---|
| Header | story, role, attempt, tier, producer, timestamp |
| Role instruction | the role's task definition (from `agents/`) |
| Pinned inputs | every input inlined or referenced **at recorded SHA** (bundle pattern) |
| Expected outputs | exact artifact paths (+ contract shape where applicable) |
| Stop rules | when to stop and report rather than improvise |
| Resume preamble | check git status/log/artifacts first; READ-ONLY priors; budget; first edit within N calls |

Generation: **one assembler owns the final package file**; the prep tools (`plan_prep`, `dispatch_prep`, `build_handoff`, `make_context_bundle`) produce its inputs. Consumption: **any** executor — adapter or human. Parity rule: same package through any adapter → same artifact paths + same contract shape (contract shape = required artifact paths + required sections/front-matter keys per role output).

## Adapter interface

Each adapter directory declares:

| Part | Content |
|---|---|
| `adapter.toml` | machine-readable manifest: name, prerequisite checks, tier (T1/T2/T3) → concrete model table, usage-report command, identity convention (read by controller/router/verify) |
| `README.md` | human prose: prerequisites, identity, step-by-step execution of a dispatch package |
| usage | how execution reports usage back (or null+reason) — declared in the manifest |

v0.1 adapters: **`human`** (operator pastes package into any AI chat; commits artifacts; usage usually null+reason) and **`hermes`** (dispatch with per-role model applied at spawn; usage harvested where available). Constraint (FR-8): adding an adapter touches no control-plane code; the constraint test validates the manifest schema + presence.

## Routing

Role→tier matrix by difficulty (S/M/L) stays static and complete (any unrouted role = process defect, counted). Tier→model is **per-environment** adapter config. Routing records to the ledger pre-spawn (`requested_model`), adapters report `reported_model`; mismatch → `model_unverified: true`. The full extracted matrix: §Role catalog.

## Role catalog (extracted)

Twelve canonical dispatch roles (machine-readable form: `tools/role_catalog.py`). Canonical spelling is snake_case; aliases canonicalize hyphen variants and historical spellings (e.g. `implementation-planner` / `impl-planner` → `planner`, `story-decomposer` → `decomposer`, `test-plan-generator` → `test_plan_generator`, `behavior-reviewer` → `behavior_reviewer`). An unknown role raises with the known list — an unrouted role is a process defect, counted (unrouted-dispatch census).

| Role | Purpose | S | M | L |
|---|---|---|---|---|
| `context_acquisition` | Assemble the pinned, digest-validated context bundle for a story | T1 | T1 | T1 |
| `decomposer` | Break an approved idea into sequenced, independently mergeable stories | T1 | T1 | T2 |
| `test_plan_generator` | Author the story's test plan and grill records (DEV/QA) | T1 | T1 | T2 |
| `planner` | Author the implementation plan (the implementation-plan author) | T2 | T2 | T3 |
| `plan_reviewer` | Adversarially review the implementation plan against the spec | T2 | T2 | T3 |
| `plan_reviewer_a` | Plan review lane A (independent reviewer, two-reviewer split) | T2 | T2 | T3 |
| `plan_reviewer_b` | Plan review lane B (independent reviewer, two-reviewer split) | T2 | T2 | T3 |
| `coder` | Implement the story on its branch against the frozen plan | T1 | T2 | T2 |
| `fixer` | Post-review combined fix pass against enumerated findings | T1 | T2 | T2 |
| `code_reviewer` | Internal code review of the story diff before external review | T2 | T2 | T2 |
| `behavior_reviewer` | Internal behavior review against acceptance criteria | T2 | T2 | T2 |
| `contract_reviewer` | Internal contract review: plan contract block vs shipped diff | T2 | T2 | T2 |

Notes:

- The matrix is **static and complete** — every role resolves for every difficulty; a role absent from a resolution is a process defect, counted.
- **Tier → concrete model is per-environment** adapter configuration (`environments/*.toml`, §Configuration) — never in the catalog.
- The orchestrator runs as a **lane**, not a routed dispatch role.

## Onboarding architecture

- `onboard <repo>` — deterministic scripts (**python3**, stdlib; no bash-4 dependency, macOS-safe) plus a **guided session** (an adapter: any agent runtime reads `ONBOARDING.md` and walks the operator through the choices — bind repos, environments, providers, budgets — invoking the same commands and recording decisions). Scripts: preflight → inspect → propose config (workspace/env drafts) → apply (idempotent, never overwrites) → verify → print the next action. The guide never installs on its own and never invents steps.
- `onboard verify` — runs the **readiness check registry**: each check = id, category (CORE/UI/WORKSPACE/ADAPTER/ADVISORY), verify method (script), remedy text, `attest_allowed`. Checks that cannot automate get a scripted **manual attest** recorded as a ledger `human_gate` event. Registry files are JSON (machine-authored); adapter checks live under `adapters/*/checks/`.
- Registry + kit version recorded in the control home; re-runnable and CI-gateable. A kit-version vs registry-version mismatch is a **FAIL** (drift must be visible); upgrade is an explicit `--upgrade` path (v0.1.x).
- `PREFLIGHT.md` = verify-mode output report (render-exempt); `KICKOFF.md` = installed cold-start template.

## Integrations

- **git**: branches `story/<id>-<slug>`; one story = one branch = one worktree per repo; push-per-gate (closing gates), claims/handovers push-on-write; preflight fetch + staleness check stops the run on divergence (see §Ledger durability).
- **GitHub via gh**: issues (idea front door), PRs, labels, Projects v2 board (Stage writes explicit via `sync_board.py`; Stage options = state names — a state with no matching option is a fail-closed refusal naming the missing option).
- **Forge as the bus (contained mode)**: forge events (labels, comments, forms, dispatch) are the mechanical trigger bus; tasks move via **requests** (comment commands / labels / structured JSON) applied by one permissioned controller — "movers request, the controller disposes"; a Story Card comment shows state + next legal moves. Proven live in the reference; ported genericized. Environments resolve where/how execution happens; triggers stay forge-native.
- **CI (advisory)**: control-plane test suite + gate scripts on PRs.

## Configuration

Two declarative layers, both pinned to **TOML** (stdlib `tomllib`; Python ≥ 3.11 — no YAML: it would break the stdlib-only law, and hand-rolling a parser is forbidden. **Owner-ratified 2026-10-23**):

| File | Where | Carries |
|---|---|---|
| `workspace.toml` | control home root | workspace identity, `factory` pin, `control_home` mode, repo bindings, policy, identity |
| `environments/<name>.toml` | control home | runtime, dispatch mechanism/isolation, state paths, tier→model table, credential **references**, limits |

Machine-written artifacts stay JSON/JSONL (ledger, registries, move requests). No hardcoded paths in tools — all resolved from workspace config + control-home root.

## Out of scope for this architecture doc

Docker Compose (kit has no services — target projects define their own), UI design doc (kit's surfaces are generated markdown + GitHub; no app UI), ADO integration (separate future question).

## Open items

All 2026-09-28 grill findings are dispositioned in `grill-disposition.md`. Owner calls and the first build slice are tracked in `build-plan.md` — all owner calls ratified 2026-10-23 (config format: TOML; live parity: pragmatic local-dispatch path; acceptance split: test-out scripts in v0.1, from-scratch run as v0.1.x item #1).