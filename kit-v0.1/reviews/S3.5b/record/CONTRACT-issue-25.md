# Contract — S3.5b (issue #25)

The frozen contract for this slice is the issue body plus the carried items and
owner decisions recorded on the issue. Both are reproduced here verbatim.

## Issue body

**S3.5b — Review gate cutover** (second increment of S3.5; depends on S3.5a's record format being proven on PR #23)

**Why:** A review lane that is not mechanically required is advisory. This increment makes the owned review record the gate and retires the Copilot-shaped review evidence from the state machine.

**Deliverables**

- **State machine cutover** — `DRAFT_OPENED.review_request_mechanism` no longer accepts Copilot mechanisms; its domain becomes `{review-lane}` (exact token fixed by the impl plan). `REVIEWING` evidence = review record path + record SHA + pinned head SHA. `READY_FOR_REVIEW` / `MERGE_ELIGIBLE` require: record exists, record head SHA == current head, no undispositioned BLOCKER/MAJOR, verdict-consistent. Historical events keep their old mechanism values (read path stays backward-compatible); new transitions refuse them.
- **Required CI check** — `review-record` job: fails closed on missing record, head-SHA mismatch, undispositioned finding, severity/verdict inconsistency, or a record not produced by the declared panel. Add to the branch ruleset as a required check (stage `disabled` → verify → `active` per the Pro ruleset lesson; never describe a disabled check as enforcement).
- **Docs/template retirement** — remove Copilot-request instructions from `runbook.md`, `process/`, templates, and the S3-shape onboarding docs where they are stated as the review mechanism; replace with the lane's invocation. Installed copies follow S4's canonical layout.
- **Fixture coverage** — gate-harness fixtures for: no record, wrong SHA, undispositioned blocker, verdict/severity mismatch, tampered record, historical old-mechanism event (must still fold).

**Acceptance**

- A throwaway PR with no review record is blocked by the required check; the same PR with a valid record passes.
- A record whose head SHA differs from the PR head fails.
- A record with one undispositioned MAJOR fails.
- `state_machine.py` refuses `review_request_mechanism=ready-push` on a new transition while still folding historical events that carry it.
- PR #23's S3.5a record satisfies `MERGE_ELIGIBLE` without editing the record.

**Explicitly not in S3.5b:** reviewer prompt/model tuning (calibration-gated, separate increments); any change to who may merge; S4's remaining runbook/process/agent/adapter content; execution-seam launch work.

**Delivery:** branch from the merged S3.5a head → PR → reviewed by the lane → owner merges.

## Carried items from S3.5a/S3.5c closeout

**O1 — head-match rule vs PR #23.** #25's acceptance says "PR #23's S3.5a record satisfies MERGE_ELIGIBLE without editing the record", but #23 sits at `669e398` while its round-2 record pins `52a7d81`. Under the new head-match rule that bullet cannot pass. Decided by the owner at recon: **grandfather pre-cutover `review.record.v1` records** — a v1 record may satisfy READY_FOR_REVIEW/MERGE_ELIGIBLE when its head is an ancestor of the PR head and every blocker/major is dispositioned; `review.record.v2` keeps the exact-head rule (plus record-directory-only tail commits). Do not weaken the rule to make the bullet pass.

**O2 — `check` needs a package that is never committed.** Every existing record failed `record.py check` on a fresh checkout because the package file is deliberately not committed. Reconciled in T1: the builder writes a deterministic manifest, the record stores it as `package.manifest`, and `check` rebuilds the package from the pinned base/head and compares the recorded `package_sha256`. Packages are never committed.

**O3 — lens documents are not validated at dispatch.** In S3.5c, 3 of 18 returned documents were schema-invalid and were only caught at record time. Folded into T4: `record.py validate-doc` validates schema + package hash + reviewer role/model before a document can reach record time.

## Owner decisions (2026-10-31)

- O1 = grandfather v1 records (ancestor head + all blocker/major dispositioned).
- Ruleset: create the `main` ruleset requiring `kit-ci` + `review-record`, stage `disabled`, verify on the live PR, then flip `active`.

## Slice boundary

Explicitly not in this slice: reviewer prompt/model tuning, new lenses, calibration-bench expansion, the rest of S4's runbook/process/agent/adapter packages, any change to the owner merge gate.