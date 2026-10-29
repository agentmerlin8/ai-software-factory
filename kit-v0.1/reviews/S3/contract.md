# S3 contract — pinned review inputs

## Slice issue #22

**S3 — Onboarding** (per `kit-v0.1/build-plan.md` §S3; S2 landed as PR #21, merged `152b205`)

Deterministic onboarding scripts — preflight → inspect → propose → apply (idempotent) → verify (fail-closed) — plus the guided-session brief (`ONBOARDING.md`, consumable by any agent runtime), the readiness registry (target + workspace checks), and the fresh-repo test-out scripts.

**Acceptance (S3 gate, fixture-scoped):**

- `verify` green on a fixture control-home repo — every check the S3 registry implements.
- Precise, named FAILs on a deliberately non-compliant repo.
- The test-out scripts ship and run green against `benchmark-app` as a smoke.

The full `benchmark-app` conformance smoke (verify green on a real onboarding) requires C-7's installed runbook + adapter packages and is explicitly sequenced **after S4**; the from-scratch fresh-repo test-out **run** stays the v0.1.x hardening item #1 (per C3).

**Explicitly not in S3:** runtime launch, model invocation, dispatch reservation, reference changes, board-schema changes, cloud spend; the runbook + process + agent/adapter packages (S4); acceptance drills (S5).

**Delivery:** bounded commits on a branch → reviewed PR → owner merges. Review standard per #19/#21: iterate the Copilot review to a clean verdict + independent fresh-context adversarial review before the merge gate.

Refs: umbrella #16 · prior slice #20.

## Build plan §S3

### S3 — Onboarding

Deterministic scripts (preflight → inspect → propose → apply idempotent → verify fail-closed) + the guided-session brief (`ONBOARDING.md` consumed by any agent runtime) + the readiness registry (target + workspace checks) + the **fresh-repo test-out scripts** (the from-scratch test-out **run** gates v0.1.x hardening — per the vision and PRD FR-11). Acceptance (S3 gate, fixture-scoped): verify green on a fixture control-home repo (every check the S3 registry implements); precise FAILs on a deliberately non-compliant repo; the test-out scripts ship and run green against `benchmark-app` as a smoke. The full `benchmark-app` conformance smoke — verify green on a real onboarding, which requires C-7's installed runbook and adapter packages — is explicitly scheduled **after S4**, where those prerequisites ship; it stays a v0.1 criterion per C3, sequenced after its prerequisites.

### S3.5a — Review lane v1 (owner-inserted 2026-10-28, before S4)

The review gate the kit owns: a deterministic review package, three runtime-neutral adversarial lenses, structured findings, and a dispositioned record at the pinned head. Copilot code review is retired as a gate (owner decision) — the lane must be reproducible and blind rather than another checklist.

| # | Task | Deliverable | Acceptance |
|---|---|---|---|
| T1 | Findings schema + strict YAML-subset reader | `tools/review/findings.py` + `tools/review/yamlish.py` + tests | valid document → canonical form; every refusal class named; JSON accepted |
| T2 | Review package builder | `tools/review/package.py` + tests | byte-identical rebuild at the same SHAs; allowed/withheld honored; deterministic truncation; named refusals |
| T3 | Record renderer + disposition ledger | `tools/review/record.py` + tests | any blocker/major → CHANGES REQUESTED; undispositioned findings refuse render; `fixed` requires a commit touching the artifact; `deferred` requires an issue link |
| T4 | Calibration bench + scorer | `tools/review/bench/` (field notes 18–25, the held slice's external findings, clean controls) + `tools/review/score.py` | per-lens and combined precision, critical/major recall, unnecessary-revision rate, approval-escape rate; baseline committed |
| T5 | Three runtime-neutral role packages | `agents/review-contract\|review-adversary\|review-security.agent.md` | each declares inputs, stay-out boundary, stop rules, structured output contract; no runtime-specific frontmatter |
| T6 | Docs + install mapping | this section + `architecture.md` §Review lane | command-accuracy read; no contradictory paths |
| T7 | Lane self-review (dogfood) + PR | record under `kit-v0.1/reviews/S3.5a/` | zero undispositioned findings; package hash pinned |

**Pilot:** run the panel on the held S3 slice (PR #23) at its head SHA; disposition the five external findings and every panel finding; the S3 merge decision reads that record. No external review is requested.

**Explicitly NOT in S3.5a:** state-machine evidence changes, the required CI check, ruleset changes (all S3.5b); the rest of S4's runbook/process/agent/adapter packages; any model spend beyond local adapter dispatch; any change to the owner merge gate.

## Review standard at review time

Copilot code review is retired as a gate (owner decision 2026-10-28). This record is the review gate for the slice;
the owner still makes the merge decision.
