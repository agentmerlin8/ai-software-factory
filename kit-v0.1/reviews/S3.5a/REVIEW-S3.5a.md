# Review record — S3.5a (review lane v1)

Dogfood run: the lane reviewed its own slice. Same discipline as the S3 pilot — mechanical gates first, three
blind lenses over scoped packages, structured findings, every finding dispositioned, zero undispositioned.

Reviewed head: `b3b2a6f8faa991cddf2c6f580accf49e36fb4473` · base: `152b205b48937a3a12020c02c8378022d088d86e`
Panels: `pkg-code` (`133698dd…`, tools/review modules + tests) and `pkg-content` (`27cbfea7…`, role packages, bench, docs).

## Round 1

Derived from the two committed records (`round-1/{code,content}/record/review.json`).

| area | findings | blocker | major | minor | fixed | rejected | deferred | verdict |
|---|---|---|---|---|---|---|---|---|
| code | 22 | 3 | 15 | 4 | 19 | 1 | 2 | CHANGES_REQUESTED |
| content | 14 | 1 | 10 | 3 | 9 | 2 | 3 | CHANGES_REQUESTED |
| **total** | **36** | **4** | **25** | **7** | **28** | **3** | **5** | |

Four blockers: three real defects in the package builder and record check (withheld files still in the diff,
`record.py check` not re-deriving, `package:diff@` touch exemption) — all fixed after the reviewed head
(`f2fc448`, `eaa65c4`); and one package-scope artifact (the scorer's import of `tools/review/findings.py`, which
ships in the code package) — rejected with evidence. Five deferred items are tracked in issues #26/#27.

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

## External review addendum (12 findings)

A platform-triggered Copilot review posted 12 findings on PR #28 (no review was requested). They are ingested as
`source=external` in `external/record/` with attributed evidence and dispositioned one by one: **all 12 fixed**
(`c70bb5e` literal pathspecs / NUL paths / stop-state / binary scope / UTF-8 refusal; `0939325` role-package stop
rules for omitted, truncated and binary evidence; `ecfab57` accurate summary totals).

The most serious were real: a stopped lens could yield a *checked approval*, `--include ""` could disclose the whole
diff, and per-file diffs used git pathspecs (a star-named file could embed another file's diff). Copilot remains
retired as a gate; these were treated as free signal and dispositioned like any external finding.
