# Prior findings — S3.5b round 4 (full-change re-review)

This package is the **full PR change** (`bca959a..9b7bccd`, 62 files) because
the gate now proves full-change coverage: a record whose package covered only
a fix diff no longer certifies a PR. The review therefore re-reads code that
rounds 1–3 already judged; the table below is the settled record.

## Rounds 1–3 (owned lane)

| round | head | verdict | findings | disposition |
| --- | --- | --- | --- | --- |
| 1 | `3884da3` | CHANGES_REQUESTED | 21 | 16 fixed, 5 rejected with reasons |
| 2 | `a02c2e5` | APPROVED | 3 minors | deferred to issue #33 |
| 3 | `9d1ba06` | APPROVED | 2 minors | deferred to issue #33 |

Round 3 was a fix-only re-review after the required check rejected the round-2
record for a rebuild mismatch between clone origins. Round 4 exists because
the platform-triggered review (not a gate; dispositioned as external input)
found that a fix-only package could satisfy the record gate, plus eight other
defects. All nine are fixed in `9b7bccd`; this package contains those fixes
together with the whole slice.

## External findings dispositioned in this head

| id | severity | fixed by |
| --- | --- | --- |
| EX-01 required check runs verifier from PR head | P1 | `.github/workflows/kit-ci.yml` — base-revision verifier, PR as data |
| EX-02 record for another PR at the same head | P1 | `controller.py` — `review_record_pr` bound to DRAFT_OPENED |
| EX-03 `--include`/tail package satisfies coverage | P1 | `ci_gate.py` — full-change coverage from the manifest |
| EX-04 rename hides a deleted source file | P1 | `record_gate.py` — `--no-renames` on head-rule diffs |
| EX-05 `fixed` cites a commit off the PR history | P1 | `record_gate.py` — fix reachability from the PR head |
| EX-06 package committed from `stories/<id>/review/` | P1 | `record.py` — record-local `.gitignore`; runbook |
| EX-07 SSH origin rebuilds a different package | P1 | `package.py` — transport-independent identity |
| EX-08 absolute input spec breaks rebuild | P1 | `package.py` — repo-relative headings and specs |
| EX-09 `dispositions: null` raises TypeError | P2 | `record_gate.py`, `controller.py` — authority first |

## Instructions for this review

- Judge the code as it stands at `9b7bccd`. The nine items above are fixed and
  should not be re-raised absent new evidence in THIS package.
- Rounds 1–3 dispositions stand: do not re-raise a settled item without
  evidence that the fix in this package is wrong or incomplete.
- A clean review is a valid outcome. Do not invent findings to look thorough.
- New defects are in scope, especially in the coverage/trust-boundary code
  this round adds: a false pass is worse than a missed minor.