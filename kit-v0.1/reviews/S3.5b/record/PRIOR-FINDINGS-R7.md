# Prior findings — S3.5b round 7 (CI-parity fix re-review)

Round 6 reviewed the full change at `4c2ac8d` and returned **six of six
APPROVED** with zero findings (package `c1bad4bd…`). The record was rendered
and pushed at `7b3ca92`.

## Why round 7 exists

The required `review-record` check then failed in GitHub Actions at `7b3ca92`
(7s, job 128217792218) while passing locally:

```
review record gate: review record: record unreadable:
  .../pr/pr/kit-v0.1/reviews/S3.5b/record/review.json
```

Actions passes the PR checkout as a RELATIVE dir (`--repo pr`). The gate
joined that relative repo onto an already repo-joined record path, so the
authority looked for `pr/pr/...`. Local runs used `--repo .`, where the same
double join is harmless — a genuine environment delta, not a stale record.

Fix in `17500c4`: `ci_gate` resolves `--repo` once; `record_gate.evaluate`
resolves the record path before invoking the authority; a regression test
runs the gate from the parent directory with `--repo repo` and asserts exit 0
(it fails without the fix).

## Round-6 outcome (settled)

| id | count |
| --- | --- |
| findings | 0 |
| verdict | APPROVED (6/6 slots) |

## Earlier rounds

| round | head | verdict | findings | disposition |
| --- | --- | --- | --- | --- |
| 1 | `3884da3` | CHANGES_REQUESTED | 21 | 16 fixed, 5 rejected |
| 2 | `a02c2e5` | APPROVED | 3 minors | deferred to #33 |
| 3 | `9d1ba06` | APPROVED | 2 minors | deferred to #33 |
| 4 | `9b7bccd` | CHANGES_REQUESTED | 8 | all fixed in `4c4987d` |
| 5 | `4c4987d` | CHANGES_REQUESTED | 7 | all fixed in `f39de41` |
| 6 | `4c2ac8d` | APPROVED | 0 | — |

## This round

- Review the full change at `17500c4`. The only code delta since round 6 is
  the path-resolution fix; judge it (and the whole change it rides on) as it
  now stands.
- A clean review is a valid outcome. Do not invent findings, and do not
  re-raise an item recorded as settled above unless this package shows the
  fix is wrong or incomplete.