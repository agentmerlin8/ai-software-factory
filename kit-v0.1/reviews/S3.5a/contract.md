# S3.5a contract — pinned review inputs

## Slice issue #24

**S3.5a — Review lane v1** (inserted before S4 per owner decision 2026-10-28; `kit-v0.1/build-plan.md` gains §S3.5 in this increment's PR)

**Why:** Copilot code review is retired as a gate by owner decision. S3 (PR #23) is held until it can be reviewed by our own lane. The lane must be owned, reproducible, blind (no author reasoning), and at least as strong on reasoning-class defects — field note 24 showed a checklist-only reviewer misses that class 5-for-5, so this is an adversarial panel, not another checklist.

**Deliverables**

- **Review package builder** — new package under the canonical tools layout (`tools/review/` in the kit; install mapping fixed in the impl plan per architecture §Canonical layouts). Inputs: pinned head SHA, base SHA, diff + `--stat`, spec/plan/handoff, deterministic gate evidence, allowed/withheld file list. Output: one package file + a content hash; byte-identical on re-run at the same SHA.
- **Three runtime-neutral review role packages** under `agents/`: `review-contract` (AC/plan fidelity, deviations, scope), `review-adversary` (behavioral: for each key assertion/guard, what production change makes it pass falsely; boundary/state/error paths), `review-security` (input validation, authorization, secrets, containment, failure modes). Each declares inputs, stay-out boundary, stop rules, and the structured output contract.
- **Structured findings** — YAML per `docs/reviewer-agent-evaluation-recommendations.md`: id, severity (blocker/major/minor/info), rubric dimension, artifact reference, evidence, required change, actionable. No free-form-only findings.
- **Disposition ledger + review record** — per finding: `fixed@<sha>`, `rejected` (reason + evidence), or `deferred` (issue link). Render `stories/<id>/review/review.json` + `REVIEW-<pr>.md` at the pinned SHA: reviewers (role + model), package hash, findings, dispositions, gate results, verdict. Verdict policy: any BLOCKER/MAJOR → CHANGES REQUESTED; 2-loop cap; re-review scoped to diff + prior findings.
- **Calibration seed** — `tools/review/bench/` seeded from our own history: field notes 18–25 and the five open #23 findings as ground truth. Scorer reports per-lens and combined precision, critical/major recall, unnecessary-revision rate, and approval escape rate.
- **Pilot on PR #23** — run the panel on S3, disposition all findings (including the five Copilot ones), commit the record to the branch; the S3 merge decision reads that record. No Copilot review requested.

**Acceptance**

- Package for a fixture PR is deterministic (byte-identical re-run at the same SHA).
- Pilot record for PR #23 exists at the head SHA with zero undispositioned findings; every disposition carries evidence (commit sha or issue link).
- Calibration baseline committed with per-lens and combined numbers.
- Zero Copilot review requests anywhere in the pilot.

**Explicitly not in S3.5a:** state-machine evidence changes, required CI check, ruleset changes (all S3.5b); the rest of the S4 runbook/process/agent/adapter packages; any model spend beyond local adapter dispatch; no change to the owner merge gate.

**Delivery:** bounded commits on a branch from `main@152b205` → PR → independent fresh-context review under the new lane → owner merges. Review standard for this PR is the lane itself (dogfood); record committed with the PR.

Refs: umbrella #16 · held slice #22 / PR #23 · field notes 23–25 · `docs/reviewer-agent-evaluation-recommendations.md`.

## Build plan §S3.5a

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
