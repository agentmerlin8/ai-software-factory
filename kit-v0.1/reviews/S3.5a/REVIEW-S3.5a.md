# Review record — S3.5a (review lane v1)

Dogfood run: the lane reviewed its own slice. Same discipline as the S3 pilot — mechanical gates first, three
blind lenses over scoped packages, structured findings, every finding dispositioned, zero undispositioned.

Reviewed head: `b3b2a6f8faa991cddf2c6f580accf49e36fb4473` · base: `152b205b48937a3a12020c02c8378022d088d86e`
Panels: `pkg-code` (`133698dd…`, tools/review modules + tests) and `pkg-content` (`27cbfea7…`, role packages, bench, docs).

## Round 1

| area | findings | blocker | major | minor | fixed | rejected | deferred | verdict |
|---|---|---|---|---|---|---|---|---|
| code | 22 | 3 | 14 | 5 | 20 | 0 | 2 | CHANGES_REQUESTED |
| content | 14 | 0 | 8 | 6 | 11 | 2 | 1* | CHANGES_REQUESTED |
| **total** | **36** | **3** | **22** | **11** | **31** | **2** | **3** | |

\* content has one deferred; plus two deferred in code, all tracked in issue #27.

The three blockers were real and fixed after the reviewed head: withheld files still appeared in the package diff,
`record.py check` did not re-derive the record, and `fixed@` dispositions citing `package:diff@…` were exempt from
the touch rule. The fixes are committed on this branch (`f2fc448`, `eaa65c4`); the calibration baseline was
regenerated with the corrected metric definitions (`96f3ef1`).

## What this run proves (and what it does not)

- Proves: the lane's artifacts are reproducible and its refusals are exercised; a blind panel finds real defects in
  the lane's own code (36 in ~7.7k lines), including two blockers in the package builder.
- Does not prove: low noise (36 findings, 33 actionable), or coverage beyond the artifacts in these packages
  (fixtures, adapters, runbook are other slices).

## Reproduction

```bash
python3 tools/review/package.py build --repo . --base 152b205 --head b3b2a6f \
  --out kit-v0.1/reviews/S3.5a/round-1/pkg-code.md \
  --contract kit-v0.1/reviews/S3.5a/contract.md:contract \
  --gate kit-v0.1/reviews/S3.5a/round-1/gate-evidence.md:deterministic-gates \
  --include tools/review/__init__.py --include tools/review/yamlish.py \
  --include tools/review/findings.py --include tools/review/package.py \
  --include tools/review/record.py --include tools/review/tests
python3 tools/review/record.py check --record kit-v0.1/reviews/S3.5a/round-1/code/record --repo .
```
