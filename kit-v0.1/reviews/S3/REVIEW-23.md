# Review record — PR #23 (S3 slice)

Pilot run of the kit's review lane v1 (S3.5a) on the held S3 slice. Copilot code review is retired as a gate by owner decision; **zero review requests were issued** for this pilot.

Reviewed head: `2bd431cd82e0938b42309ed3d1ff808d9e2260ad` · base: `152b205b48937a3a12020c02c8378022d088d86e` · lane `review-lane-v1` v1

The slice diff was split into three scoped packages so each lens fits one context; the union covers every product file in the slice. Fixtures were exercised by the suites, not reviewed as product code.

## Round 1 — panel at the reviewed head

| area | package sha256 | findings | blocker | major | minor | fixed | rejected | deferred | verdict |
|---|---|---|---|---|---|---|---|---|---|
| readiness | `de145807307d…` | 21 | 1 | 5 | 15 | 9 | 1 | 11 | CHANGES_REQUESTED |
| pipeline | `269be00da739…` | 18 | 0 | 11 | 7 | 15 | 1 | 2 | CHANGES_REQUESTED |
| tools-docs | `bd0de80a5873…` | 18 | 1 | 5 | 12 | 5 | 5 | 8 | CHANGES_REQUESTED |

**Total: 57 findings** — 29 fixed, 7 rejected with evidence, 21 deferred to issue #26. Every finding carries exactly one disposition with evidence; zero undispositioned.

## Round 2 — scoped re-review at the fix head

Package `6014aa8b4600…` · reviewed `2bd431c` → `52a7d81` (fix diff + prior findings).

- findings: 15 (blocker 0, major 5, minor 9)
- dispositions: 11 fixed, 4 rejected, 0 deferred
- verdict at the reviewed head: **CHANGES_REQUESTED**

Round-2 findings were all closed after the reviewed head (fix commit) or dispositioned with evidence; the loop cap (2) was reached, so the remaining decision is the owner's merge gate.

## Legacy external findings

The five findings left open by the retired external reviewer were ingested as `source=external` with attributed evidence and dispositioned by this lane: EX-001…EX-003 in `pipeline`, EX-004…EX-005 in `tools-docs`. All five are `fixed`.

## How to reproduce

```bash
python3 tools/review/package.py build --repo <repo> --base 152b205 --head 2bd431c \
  --out kit-v0.1/reviews/S3/round-1/pkg-pipeline.md \
  --contract kit-v0.1/reviews/S3/contract.md:contract \
  --gate kit-v0.1/reviews/S3/round-1/gate-evidence.md:deterministic-gates \
  --include onboarding/onboard.py --include onboarding/forge_probe.py ...
python3 tools/review/record.py render --repo . --story S3 --pr 23 ...
python3 tools/review/record.py check --record kit-v0.1/reviews/S3/round-1/pipeline/record --repo .
```

Packages are byte-identical at the same SHAs; each record recomputes its package hash and re-verifies every disposition.
