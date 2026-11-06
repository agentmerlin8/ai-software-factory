# Prior findings — S3.5b round 6 (final fix re-review)

Round 5 reviewed the full change at `4c4987d` (package `903fe89e…`) and
returned **CHANGES_REQUESTED** from four of six slots: 3 majors, 3 minors and
one scope finding. The code findings are fixed in `f39de41`; the scope
finding is resolved by the owner's recorded decision in `4c2ac8d`. This
package is the full change at that head.

## Round-5 findings and their resolutions

| id | severity | defect | resolution |
| --- | --- | --- | --- |
| `RC-001@deepseek-v4-pro-0813` | major (scope) | code-owner review changed merge protection beyond the frozen decision set | owner scope exception recorded in `OWNER-DECISION-codeowner-review.md` (2026-11-06), cited from CODEOWNERS and architecture, T6 claim reconciled |
| `RA-001@deepseek-v4-pro-0813` | major | PR binding untested at READY_FOR_REVIEW / MERGE_ELIGIBLE | tests: sibling-PR record refused at both transitions + historical no-pr row fails closed |
| `RS-001@deepseek-v4-pro-0813` | major | whole records root excluded from coverage — a source file there could be certified unreviewed | exclusion narrowed to evidence-shaped artifacts; any other path under a records root is required and named by the withheld refusal; test commits `review/evil.py` and asserts refusal |
| `RA-001@qwen3.8-max` | major | "cannot diff" coverage branch untested | test: well-formed unresolvable `--base` exits 1 with `coverage: cannot diff` |
| `RA-002@qwen3.8-max` | minor | missing-manifest guard untested | unit test asserts `coverage_errors` refuses |
| `RA-003@qwen3.8-max` | minor | historical no-pr row tested only through REVIEWING | MERGE_ELIGIBLE test added |
| `RA-002@deepseek-v4-pro-0813` | minor | withheld paths misnamed as never-packaged | named refusal + test (round 4), retained |

## Earlier rounds

| round | head | verdict | findings | disposition |
| --- | --- | --- | --- | --- |
| 1 | `3884da3` | CHANGES_REQUESTED | 21 | 16 fixed, 5 rejected with reasons |
| 2 | `a02c2e5` | APPROVED | 3 minors | deferred to issue #33 |
| 3 | `9d1ba06` | APPROVED | 2 minors | deferred to issue #33 |
| 4 | `9b7bccd` | CHANGES_REQUESTED | 8 | all fixed in `4c4987d` |
| 5 | `4c4987d` | CHANGES_REQUESTED | 7 | all fixed in `f39de41` |

## This round

- Review the full change at `4c2ac8d`. The round-5 defects are fixed; do not
  re-raise them absent new evidence in THIS package.
- The trust-boundary question is settled by the owner decision recorded in
  `OWNER-DECISION-codeowner-review.md`; it is a scope call, not a defect.
- A clean review is a valid outcome. Do not invent findings to look thorough;
  a false pass is worse than a missed minor.