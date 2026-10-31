# Prior findings — S3.5b round 1

Round 1 ran at head `9c7b466` (package `f6eabd7a…55c8`) with the declared panel
(3 lenses × 2 models). Every finding is dispositioned below. Round 2 is scoped to
the current package diff plus this list: confirm each fix, re-check the rejected
items only if the new diff changes their premise, and look for defects introduced
by the fixes.

| id | severity | disposition | fix / reason |
|---|---|---|---|
| `RA-001@deepseek-v4-pro-0813` | major | rejected | rejected — Same boundary as RC-001: forge-free controller; the CI required check is the forge-anchored head binding. The new head_sha_resolves check closes the fabricated-head case. |
| `RA-001@qwen3.8-max` | major | fixed | fixed@01b04493f6d9 — head_rule_errors now requires `git merge-base --is-ancestor record_head pr_head` before the v2 record-tail diff, and a non-ancestor fixture asserts the refusal. |
| `RA-002@qwen3.8-max` | major | rejected | rejected — The controller is deliberately forge-free and resolves every commit in the control home's object DB (the same assumption as BUILD_GREEN commit_sha and MERGED merged_sha). The required CI check is the forge-anchored layer |
| `RC-001@deepseek-v4-pro-0813` | major | rejected | rejected — Same boundary as RA-002: the controller's evidence model is object-DB + human gate, not a forge client. 01b0449 adds head_sha_resolves/review_head_resolves so the evidenced head must exist in the repo; the required CI ch |
| `RC-001@qwen3.8-max` | major | fixed | fixed@01b04493f6d9 — T6 ruleset/drill evidence added as a record gate input: kit-v0.1/reviews/S3.5b/record/ruleset-drill.md records ruleset id 26277566 (active; kit-ci + review-record), the red run on  |
| `RC-002@deepseek-v4-pro-0813` | major | fixed | fixed@01b04493f6d9 — ruleset-drill.md is added as a record gate input (ruleset id, contexts, active state, red-run URL and message). |
| `RC-002@qwen3.8-max` | major | fixed | fixed@01b04493f6d9 — The retirement scan is now per-line, case-insensitive and order-independent (copilot AND review in the same line), so a Copilot-as-reviewer instruction cannot slip through by word  |
| `RS-001@deepseek-v4-pro-0813` | major | fixed | fixed@01b04493f6d9 — record_head is validated with SHA_RE before the prefix comparison. |
| `RS-001@qwen3.8-max` | major | fixed | fixed@01b04493f6d9 — record_head is validated with SHA_RE before the head comparison. |
| `RS-002@deepseek-v4-pro-0813` | major | rejected | rejected — The declared panel is repository policy, not a universal controller constant. The required CI check enforces it via ci_gate.panel_slot_errors against kit-v0.1/review-panel.toml; the generic controller enforces panel symm |
| `RA-002@deepseek-v4-pro-0813` | minor | fixed | fixed@01b04493f6d9 — open_blocker_majors now counts only fixed (with a non-empty fix_sha), rejected, or deferred entries; a value-less disposition leaves the finding OPEN. |
| `RA-003@deepseek-v4-pro-0813` | minor | fixed | fixed@01b04493f6d9 — ruleset-drill.md records the ruleset state transition and the red drill; the green half is the PR check run after the record commit. |
| `RA-003@qwen3.8-max` | minor | fixed | fixed@01b04493f6d9 — REVIEWING now requires SHA_RE.fullmatch(review_head_sha) before the prefix comparison; empty/non-hex evidence fails with a named detail. |
| `RA-004@deepseek-v4-pro-0813` | minor | fixed | fixed@01b04493f6d9 — A real request-path DRAFT_OPENED -> REVIEWING integration test now asserts the pr_number round-trips through the persisted row shape. |
| `RA-004@qwen3.8-max` | minor | fixed | fixed@fbceac56b3fb — ci_gate duplicate-slot guard covered by a new fixture asserting the 'duplicate reviewer slots' refusal. |
| `RA-005@qwen3.8-max` | minor | fixed | fixed@01b04493f6d9 — New ci_gate test runs the bare workflow invocation (default --records-root and --panel) and passes against the committed panel. |
| `RA-006@qwen3.8-max` | minor | fixed | fixed@01b04493f6d9 — New ci_gate fixture renders WITH --package-manifest, deletes the package file, and passes at the exact head and a record-tail head. |
| `RA-007@qwen3.8-max` | minor | fixed | fixed@fbceac56b3fb — manifest contract/gate specs are now repo-relative only (absolute or `..` refused at render and check); rebuild resolves them under the repo after a containment check. |
| `RC-003@deepseek-v4-pro-0813` | minor | rejected | rejected — The renderer already refuses an undispositioned finding: match_dispositions fails with `review record: undispositioned findings: ...` (record.py). The behaviour predates this slice's diff, so it is not visible in the pac |
| `RS-002@qwen3.8-max` | minor | fixed | fixed@01b04493f6d9 — Workflow passes PR_NUMBER and HEAD_SHA through the job env and quotes them in the shell; no ${{ }} expansion in the run block. |
| `RA-008@qwen3.8-max` | info | fixed | fixed@01b04493f6d9 — validate-doc now uses --repo to resolve a relative --package path; a foreign-cwd test pins the behaviour. |

## Fixed-artifact map (what to verify in the new diff)

- head-rule ancestry + hex validation: `tools/review/record_gate.py`
- disposition-value clearing: `record_gate.open_blocker_majors`
- head resolution + review_head_sha format: `tools/controller.py`
- manifest input containment + cwd-independent rebuild: `tools/review/package.py`, `tools/review/record.py`
- workflow env indirection: `.github/workflows/kit-ci.yml`
- docs-scan order independence: `tools/tests/test_docs_consistency.py`
- regression tests: `tools/review/tests/test_ci_gate.py`, `test_record.py`, `tools/tests/test_controller.py`, `test_review_gate.py`

