# Target-Repo Readiness Checklist (First Pass)

> Status: DIRECTION-FOLDED 2026-10-23 (single-writer resolution, workspace-level checks, format pins; see `architecture.md`, `build-plan.md`). First pass drafted 2026-09-28.
> Purpose: everything a target repository needs for the factory to be **runtime-agnostic** — and the basis for the `onboard verify` mode (per-check PASS/FAIL, fail-closed, non-zero exit).

## How this list is used

1. **Before onboarding**: the operator (or the orchestrator) works the checklist top to bottom.
2. **Onboard verify** (to build): each item becomes a machine check with a PASS/FAIL line and a remedy printed on failure. Required items gate; advisories warn.
3. **Re-check on every significant change** (new board field, changed branch protection, new adapter) — verify is re-runnable and CI-gateable.

Check categories: **CORE** (required for any factory run) · **UI** (only when stories touch UI) · **ADAPTER** (required only when that adapter executes work) · **ADVISORY** (recommended).

Mode-aware applicability: control-home checks (kit install, ledger, config, kickoff) run **once** at the control home. In Mode 2, product-only target repos are checked against the binding subset (C-1, C-2, the C-3 target variant, C-13, and the story-branch conventions) — they never duplicate the control home. Target protection and a runnable test environment are per-binding requirements: each bound target repo keeps standard branch protection with **no factory bypass** (the bot-App bypass lives on the workspace repo only) and a runnable test environment, because `architecture.md` requires tests green in every touched repo and standard target protection. Installation checks stay at the control home.

## CORE — required in the control home (Mode 1: the target repo itself; Mode 2: the workspace repo)

| # | Check | Why | Verify method |
|---|---|---|---|
| C-1 | Git repository with a GitHub remote | Everything hangs off git + GitHub | `git remote -v`, `gh repo view` |
| C-2 | `gh` CLI authenticated with required scopes: Contents RW · Issues RW · Pull requests RW · Metadata R · **Projects v2 (classic `project` scope or org-level auth — not a fine-grained repo permission)** · **Administration: read** (for protection reads in C-3) | All forge operations | `gh auth status`; named API probes per check (registry) |
| C-3 | Default branch protected by a **ruleset** (PR + approval + required checks; no force-push/delete) with **bypass = the factory bot App only** (the single mechanical writer) — **Mode 2 target variant:** bound target repos keep standard protection (PR + approval + required checks; no force-push/delete) with **no factory bypass**; the bot-App bypass lives on the workspace repo only — where Evaluate mode is unavailable (GitHub Pro), stage `disabled` and flip `active` after the bot push path is verified | The merge gate must be real machinery; the bot stays the one writer | Ruleset read via API; bypass list inspected. **A repo is never described as protected until the `active` ruleset is read back via API post-flip; a staged-`disabled` or unreadable (plan-gated) ruleset renders `staged, not enforcing` in verify output** |
| C-4 | Labels for the lifecycle (idea/planning/…/done, needs-human) created | Kanban visibility, automation hooks | `gh label list` |
| C-5 | Idea front door: issue form + `idea` label | Standard intake path | Issue form in `.github/ISSUE_TEMPLATE/`, label exists |
| C-6 | GitHub Projects v2 board linked to the repo, with the `Stage` field (and derived group/status fields) | Board mirrors the lifecycle; stage writes are explicit | GraphQL probe of project fields |
| C-7 | Kit installed under `factory/` per the canonical installed layout (runbook, tools, gates, ledger seed, process, agents, adapters, templates, locks) | The process lives here | `factory/runbook.md` present; `factory/tools/` entry points run under python3 |
| C-8 | Empty, schema-valid ledger seeded (`factory/ledger/events.jsonl`) + renders pass | Source of truth exists and renders clean | `render_state --check`, `render_dashboard --check`, ledger validation |
| C-9 | Toolchain present: `python3` (stdlib), `git`, `gh` | Control plane baseline | `command -v` trio + versions |
| C-10 | Config present and parsed: `workspace.toml` (+ ≥1 resolvable `environments/*.toml`) with canonical paths, board/project id, default branch, identity (human-gate approver allowlist) | Tools are path/project-parameterized — no hardcoding | Config parsed by verify; schemas validated against the §Configuration frozen tables (`config_validator.py`) |
| C-11 | `factory/KICKOFF.md` (cold-start template) + `factory/PREFLIGHT.md` (verify output report; render-exempt) present | Cold-start discipline | Files exist; preflight re-runs |
| C-12 | Secrets posture documented: where creds live, what is never committed, how a fresh machine gets them | Locked-down portability | Secrets doc present; `.gitignore` sane |
| C-13 | Test environment runnable (project's own: docker compose, test DB, etc.) — or an explicit "manual" declaration; **Mode 2: per binding** — control home and every bound target repo | The coder's exit condition is build+tests green | Project-defined command, executed once by verify with the project's health check; may PASS via recorded manual attest or cached last-run (full execution is drill-only) |
| C-14 | Working agreement recorded (human touchpoints: Storybook approvals, merge gate, escalations; override file location) | The run's rules must be discoverable without tribal knowledge | Documented in runbook/config |

## WORKSPACE — control-home checks (Mode 2 only)

| # | Check | Why | Verify method |
|---|---|---|---|
| W-1 | Control home exists as a **dedicated workspace repo**, created from the kit workspace template (**Mode 2 only**; Mode 1 uses the in-repo layout — no separate repo) | Multi-repo default | Repo + template files present |
| W-2 | `workspace.toml` valid: factory pin resolvable, repo bindings listed with roles | Workspace identity | Schema-validate (`config_validator.py`; §Configuration frozen tables) + pin resolve — concrete pins must resolve |
| W-3 | `environments/` present; every declared environment file valid and resolvable (credential references named; no inline secrets) | Execution declared per environment | Schema-validate (§Configuration frozen tables — unknown keys and inline secrets refused); credential refs checked for presence only — values never read |
| W-4 | Every bound target repo reachable with required scopes; branch conventions available (`story/<id>-<slug>`) | Multi-repo mechanics depend on it | `gh repo view` per binding |
| W-5 | Ledger seeded at the control home (`factory/ledger/events.jsonl`); renders pass | Source of truth exists | Ledger validation + render `--check` |
| W-6 | Cross-repo contract-block template present for story specs (pinned interface owned by the provider repo) | Multi-repo story quality | Template exists |
| W-7 | Bot App installed on the control-home repo; ruleset bypass list contains the bot only | The single-writer mechanics need their home | Ruleset read via API; bypass list inspected |
| W-8 | Projects v2 board linked to the control home (the workspace repo in Mode 2); Stage options = state names | Board mirrors the lifecycle at the work's home | GraphQL probe of project fields |

## UI — when stories touch UI

| # | Check | Why | Verify method |
|---|---|---|---|
| U-1 | Storybook builds locally | Storybook-first gate has a harness | Project build command |
| U-2 | Visual review path defined (how a human sees a screen: story deployed, screenshot, local run) | Approvals need a surface | Documented + one dry-run |

## ADAPTER — required only when the adapter runs work

| # | Adapter | Prerequisites |
|---|---|---|
| A-1 | `human` | Editor + shell + git identity with push access to story branches; **any** AI chat (or human peers) for role work; the operator reads/writes artifacts directly |
| A-2 | `hermes` | Hermes runtime available to the operator; dispatch usable; adapter manifest + model table installed at the canonical installed path (`factory/adapters/hermes/adapter.toml`) |
| A-3 | `claude-code` (v0.2) | CLI installed + authenticated; non-interactive invocation documented |
| A-4 | `codex` (v0.2) | CLI installed + authenticated; non-interactive invocation documented |
| A-5 | `api-direct` (v0.2) | OpenAI-compatible endpoint + token; used for locked-down/Foundry targets |
| A-6 | (historical) `copilot` | "Coding agent assignable" style checks belong **here** — optional adapter, never core |

## ADVISORY — recommended

- A-7 | CI workflow that runs the control-plane test suite + gate scripts on PRs (the repo's own quality line).
- A-8 | Branch naming convention documented (`story/<id>-<slug>`) and enforced by the worktree convention (one story = one branch = one worktree).
- A-9 | Machines that work the repo have the workspace layout the runbook assumes (canonical path per machine; worktrees disposable).

## Notes

- **Adapter-awareness is the point**: the current onboarding flow in this repo treats a specific coding-agent product's assignability as a core prerequisite. In the kit model that becomes adapter check A-6. Core stays runtime-neutral.
- Every CORE check must be checkable **by script**; a check that cannot be automated gets a scripted **manual attest** (operator marks PASS with name/date recorded) so the list stays complete and honest — attest records are ledger `human_gate` events.
- Registry + kit version are recorded in the control home; a kit-version vs registry-version mismatch is a **FAIL** (drift visible); upgrade is an explicit path (v0.1.x).
- The list version is recorded in the target repo alongside the kit so drift is visible.