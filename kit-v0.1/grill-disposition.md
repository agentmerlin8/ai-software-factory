# Grill Disposition — Kit v0.1 (round 1, 2026-09-28)

> Purpose: the round-1 grill (`grill-record-2026-09-28.md`) returned **REVISIONS REQUIRED** with 34 findings and 12 decisions needed. This document records how **every** item is now dispositioned — written into the bundle, turned into build work, or explicitly deferred. Nothing is silently dropped. The round-1 verdict stands historically; a fresh adversarial review of this folded bundle is the next quality gate (see `build-plan.md`).

**Legend:** **FOLDED** — written into the bundle in this fold · **RESOLVED** — decision pinned (owner call) · **TASK** — becomes build work (slice/task id) · **DEFERRED** — explicitly later · **NOTED** — clarification recorded, no action owed.

## Critical / important / minor findings

| # | Finding (short) | Disposition | Where it now lives |
|---|---|---|---|
| 1 | Ledger branch & push model contradicted branch protection | FOLDED | `architecture.md` §Ledger durability and the single writer (single writer; ruleset bypass = bot App only; push-on-write; no-merge rule) — bot identity + push path proven live in the reference; enforcing ruleset staged pending activation |
| 2 | Bundle not self-contained (evidence/role catalogs, per-type schemas) | TASK T1 | extraction → `architecture.md` (+ `tools/` schema module); acceptance: controller implementable from the bundle alone |
| 3 | Config format vs stdlib (YAML needs PyYAML) | RESOLVED + FOLDED | TOML pinned (`architecture.md` §Configuration; PRD C-10; readiness C-10); validator = TASK T2 |
| 4 | Kit/target layout disagreed across four docs | FOLDED | `architecture.md` §Canonical layouts (kit-repo vs installed control home); C-7, test-strategy paths aligned |
| 5 | Adapter interface prose-only; FR-8 untestable | FOLDED | `adapter.toml` machine-readable manifest (`architecture.md` §Adapter interface; PRD FR-8; readiness A-2 path fixed) |
| 6 | Dispatch parity test tautological; live parity undefined | RESOLVED (C2) + FOLDED | `test-strategy.md` §5 (contract test vs live) + D-1 both legs; contract shape defined |
| 7 | "16 states + BLOCKED" wording / counts | FOLDED | `architecture.md` §State machine — ported as-is; the extraction ports the current set (19 + BLOCKED); BLOCKED evidence rides TASK T1 |
| 8 | Metrics baseline impossible for fresh repo | FOLDED | `architecture.md` control-plane table — baseline optional in v0.1 (null + reason); unrouted census tied to FR-9 |
| 9 | gh scope list wrong/insufficient (Projects, Administration) | FOLDED | readiness C-2 (scope table incl. Projects v2 + Administration: read); named probes ride the registry (S3) |
| 10 | Override "committed" semantics / placeholder detection | FOLDED | `architecture.md` §Override file (HEAD-blob read; fail-closed uncommitted; placeholder patterns enumerated) |
| 11 | `override_used` event vs annotation; `stage` vs `transition` | FOLDED | `architecture.md` §Data model conventions |
| 12 | Dispatch sections 5-vs-6; no assembler owner | FOLDED | PRD FR-3 + `architecture.md` §Dispatch package (six sections; one assembler; prep tools feed it) |
| 13 | Story artifact layout undefined | FOLDED | `architecture.md` §Canonical layouts (story-directory row) |
| 14 | Gate catalog naming/undefined terms | RESOLVED + TASK T1 | descriptive slugs pinned; catalog published with extraction |
| 15 | KICKOFF/PREFLIGHT orphan requirements | FOLDED | `architecture.md` §Onboarding architecture + readiness C-11 (PREFLIGHT = verify report, render-exempt; KICKOFF = template) |
| 16 | Board Stage mechanics undefined | FOLDED | `architecture.md` §Integrations (Stage options = state names; fail-closed refusal); C-6 registry work in S3 |
| 17 | Merge-gate TOCTOU; LIVE_PROVEN undefined | TASK T1 | exact re-check + evidence keys land in the transition table; LIVE_PROVEN keeps a target-configurable attested path (v0.1) |
| 18 | macOS vs bash-4 onboard script | FOLDED | onboarding scripts = python3 (stdlib), macOS-safe (`architecture.md` §Onboarding) |
| 19 | No upgrade/drift story for installed kit | FOLDED | readiness Notes — kit-version vs registry mismatch = FAIL; `--upgrade` path (v0.1.x) |
| 20 | Judgment-call acceptance criteria | FOLDED | `test-strategy.md` D-1 operationalized (fresh-context reader; zero clarification requests; defect-flagged records) |
| 21 | Render determinism has no byte contract | FOLDED | `architecture.md` §Data model — render byte contract (ledger+git only, ISO-8601 UTC, sorted keys, LF, no wall-clock) |
| 22 | Undefined prep-tool terms | FOLDED | `architecture.md` control-plane table defines both terms inline |
| 23 | Claim-gating boundary ambiguous | FOLDED + TASK T1 | claim lifecycle rule pinned; the exact first gated transition rides the extraction's transition table |
| 24 | Convention deviations (template) | NOTED | the bundle keeps `target-repo-readiness.md` as its fourth doc deliberately (no UI surfaces); posture-detection update rides S3 |
| 25 | Dispatch ID: attempt authority / unix units | FOLDED | `architecture.md` §Data model (controller allocates at pre-spawn; unix = seconds, UTC) |
| 26 | Pyramid share percentages untestable | FOLDED | `test-strategy.md` — labeled indicative |
| 27 | D-1 "human adapter driving control plane" garbled | FOLDED | `test-strategy.md` D-1 reworded |
| 28 | Pointer direction (skills) | FOLDED | `architecture.md` kit-structure note — pointers remain conveniences in a runtime's skill system, never requirements |
| 29 | Concurrency wording conflated lock vs scheduling | FOLDED | PRD out-of-scope reworded |
| 30 | Locked-down network wording vs GitHub requirement | FOLDED | PRD NFR-3 clarification (process travels; execution needs GitHub; offline path = v0.2 `api-direct`) |
| 31 | Labels/issue-form set not frozen | TASK S3 | exact label list + form filename published with the onboarding registry |
| 32 | C-13 heavy verify execution in CI | FOLDED | readiness C-13 — manual attest / cached last-run allowed; full execution drill-only |
| 33 | Identity consumption; branch slug rules | FOLDED (partial) + TASK | identity conventions in `adapter.toml` + claim fields; exact slug rules ride T1/T2 docs |
| 34 | Fixture tree ambiguity | NOTED | single fixture roots documented during the port (T2 tests); no cross-tree scanning |

## Decisions needed from the product owner (D1–D12)

| # | Decision | Disposition |
|---|---|---|
| D1 | Ledger push model | RESOLVED — single-writer + App bypass ruleset (bot identity + push path proven live; ruleset staged pending activation; folded into `architecture.md` §Ledger durability) |
| D2 | Config format & schema | RESOLVED — TOML (`tomllib`, Python ≥ 3.11); schema freeze = TASK T2 (C1 ratified 2026-10-23) |
| D3 | Self-containment extraction | TASK T1 — extraction is build work, from the reference, genericized |
| D4 | State machine port-as-is | RESOLVED — port as-is; "16 states + BLOCKED" wording superseded by the reference's current 19 + BLOCKED; extraction ports the current set |
| D5 | Gate catalog naming | RESOLVED — descriptive slugs (numeric ids violate the genericization rule) |
| D6 | `record_usage` manual entry path | RESOLVED — kept for human mode (manual entry / null + reason) |
| D7 | Dispatch-package sections / assembler | RESOLVED — six sections; one assembler owns the file |
| D8 | Adapter manifest format | RESOLVED — `adapter.toml` |
| D9 | Verify registry format | RESOLVED — JSON registry files; attest records = ledger `human_gate` events |
| D10 | KICKOFF.md keep/drop | RESOLVED — keep both; PREFLIGHT = verify report (render-exempt); KICKOFF = installed template |
| D11 | Render byte contract | RESOLVED — ledger+git only; ISO-8601 UTC; sorted keys; LF; no wall-clock |
| D12 | Live parity in v0.1 | RESOLVED (C2, ratified 2026-10-23) — both legs in D-1 + mechanical contract shape; the live hermes leg runs locally (non-sealed, labeled as such); sealed re-run when launch is authorized |

## Owner ratification items (C1–C3 ✅ — all ratified 2026-10-23)

- **C1** — config format = TOML — **✅ ratified 2026-10-23.**
- **C2** — live-parity measurement (pragmatic path: one real local dispatch, labeled non-sealed; sealed re-run when authorized) — **✅ ratified 2026-10-23.**
- **C3** — acceptance targets (test-out split kept: scripts in v0.1; from-scratch run = v0.1.x item #1) — **✅ ratified 2026-10-23.**
- **Ruleset-flip evidence standard** — applied as recommended (never "protected" until the `active` ruleset reads back; staged/unreadable renders "staged, not enforcing") — see readiness C-3 and `architecture.md` §Ledger durability (item 5).

See `build-plan.md` §Owner calls.