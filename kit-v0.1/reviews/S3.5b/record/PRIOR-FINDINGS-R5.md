# Prior findings — S3.5b round 5 (fix re-review)

Round 4 reviewed the full change at `9b7bccd` (package `bab82dcf…`) and
returned **CHANGES_REQUESTED** from five of six slots: 1 blocker, 4 majors,
3 minors. All eight are fixed in `4c4987d`; this package is the full change at
that fix head. The round-4 record is preserved at `record/round-4/`.

## Round-4 findings and their fixes (all `fixed@4c4987d`)

| id | severity | defect | fix |
| --- | --- | --- | --- |
| `RA-001@qwen3.8-max` | blocker | job body ships with the PR ref; CODEOWNERS was advisory | code-owner review enabled on `main` (API readback in the T6 evidence), CODEOWNERS names both owner accounts, claim corrected |
| `RS-001@deepseek-v4-pro-0813` | major | same trust boundary, security lens | same fix; workflow comment names the mechanism and residual |
| `RC-001@qwen3.8-max` | major | architecture claimed protection no artifact recorded | T6 evidence now records the `require_code_owner_reviews: true` readback |
| `RA-002@qwen3.8-max` | major | reachability guard untested (deleting it kept the suite green) | regression test with a side-branch `fix_sha`; refusal asserted |
| `RA-003@qwen3.8-max` | major | rename guard untested (dropping `--no-renames` kept the suite green) | regression test moving a source file into the record dir |
| `RC-001@deepseek-v4-pro-0813` | minor | `review_head_match` claimed true when the authority rejected the record | fail-closed branch + controller test |
| `RA-001@deepseek-v4-pro-0813` | major | same reachability test gap, adversary lens | same regression test; merge-base extracted to `_is_ancestor` |
| `RA-002@deepseek-v4-pro-0813` | minor | withheld paths reported as "never packaged" | named "deliberately withheld" refusal + test |

## Earlier rounds

| round | head | verdict | findings | disposition |
| --- | --- | --- | --- | --- |
| 1 | `3884da3` | CHANGES_REQUESTED | 21 | 16 fixed, 5 rejected with reasons |
| 2 | `a02c2e5` | APPROVED | 3 minors | deferred to issue #33 |
| 3 | `9d1ba06` | APPROVED | 2 minors | deferred to issue #33 |
| 4 | `9b7bccd` | CHANGES_REQUESTED | 8 | all fixed in `4c4987d` |

## This round

- Review the full change at `4c4987d`. The round-4 defects above are fixed;
  do not re-raise them absent new evidence in THIS package.
- Rounds 1–3 dispositions stand; do not re-raise settled items.
- A clean review is a valid outcome. Do not invent findings to look thorough.
- The highest-value surface is the new coverage/trust-boundary code: a false
  pass is worse than a missed minor.