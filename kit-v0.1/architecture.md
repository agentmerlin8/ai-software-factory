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
2. `main` is protected by a **ruleset** (require PR + approval + required checks; block force-push/delete). The bypass list contains **only the factory bot App** (actor type `Integration`, mode `always`). Humans are never in the bypass list — the human merge gate stays mechanically real. In Mode 2 the ruleset and bot-App bypass live on the workspace repo (the control home); target repos keep standard protection with no factory bypass.
3. Claims and handovers **push on write** through the controller (bot identity). Races: first-push-wins; the loser is refused with the handover path. A non-fast-forward push → fetch → re-validate claim uniqueness → retry, so a duplicate claim never lands. If one ever does, it is adjudicated with a `correction` event — never an edit.
4. Workers and agents never hold writer authority: every state mutation flows through the permissioned controller path. Story branches + PRs are the only artifacts workers write directly.
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

## State machine

Pipeline states, **ported as-is from the reference** (the reference map has extended since this draft — currently 19 pipeline states + BLOCKED; the extraction task ports the current set):

```
BACKLOG → GRILLED → TEST_PLAN → SPEC_FROZEN → PLANNED → GATE_PASSED
        → PLAN_APPROVED → STORYBOOK_APPROVAL → CODING → BUILD_GREEN
        → DRAFT_OPENED → REVIEWING → FIXING → FULL_TESTS_GREEN
        → READY_FOR_REVIEW → MERGE_ELIGIBLE → MERGED → LIVE_PROVEN → CLOSED
(any) → BLOCKED → resumes at originating state
```

- `STORYBOOK_APPROVAL` applies to UI stories; lane-skip rules ride with the extraction task.
- Every arrow = a controller transition with evidence + checks.
- `READY_FOR_REVIEW` (forge flag) ≠ `MERGE_ELIGIBLE` (factory state). Never conflated.
- Loop caps: 2 per stage; escalation beyond.
- BLOCKED entry/exit evidence rules land with the extraction task (transition table).

## Data model — the ledger

`factory/ledger/events.jsonl` — append-only, one JSON object per event, UTC timestamps, stable IDs.

Event types (v0.1): `transition`, `dispatch`, `usage`, `stage`, `finding`, `loop`, `correction`, `assignment` (claim lifecycle), `handover`, `human_gate`, `recovery`, `outcome`. (`override_used` is an annotation payload on transition events — not an event type; see conventions below.)

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
| claim | `owner`, `host`, `session`, `start_sha`, `claimed_at` | Required before the first active transition after BACKLOG (planning counts as working the story; the extraction task pins the exact first gated transition against the current map). Refused if an open claim exists (unless override) |
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

Role→tier matrix by difficulty (S/M/L) stays static and complete (any unrouted role = process defect, counted). Tier→model is **per-environment** adapter config. Routing records to the ledger pre-spawn (`requested_model`), adapters report `reported_model`; mismatch → `model_unverified: true`.

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

All 2026-09-28 grill findings are dispositioned in `grill-disposition.md`. Owner calls and the first build slice are tracked in `build-plan.md` (config format — TOML — and the live-parity path — pragmatic — ratified 2026-10-23; the test-out split remains open).