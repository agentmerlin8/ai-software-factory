# Review calibration bench

A stand-alone fixture for calibrating the three review lenses:
17 ground-truth cases, each a self-contained review artifact that
*contains* a planted defect, plus the findings a correct review of that
artifact would produce.  The companion scorer (`score.py`) compares what
each lens actually produced against that ground truth.

The artifacts are blind-review fixtures: they hold the defect only —
never the lesson, never the note the case came from.

## Layout

- `cases/<id>.json` — case metadata (id, title, provenance, artifact)
  plus the **answer key** (`expected.findings`). This file is scoring
  data, not review input.
- `cases/artifacts/<id>/<file>` — the review input. One directory per
  case id, so the path component right after `artifacts/` in any
  artifact_reference names the case.
- `score.py` — the scorer.

Packaging note: package `cases/artifacts/**` for the lenses; never ship
`cases/*.json` with the review input (it is the answer key).

## Provenance (one line per case)

| case     | provenance |
|----------|------------|
| fn18     | field note 18 — UI slice manifest EXTEND review note |
| fn19     | field note 19 — confirmation dialog review note |
| fn20     | field note 20 — gate script collision scan review note |
| fn21     | field note 21 — contract types refresh review note |
| fn22     | field note 22 — test plan verification review note |
| fn23     | field note 23 — review lane process note |
| fn24a    | field note 24 — contract config presence test note (a) |
| fn24b    | field note 24 — path containment guard note (b) |
| fn24c    | field note 24 — queue drain helper note (c) |
| fn25     | field note 25 — twin request-path guards note |
| pr23-01  | onboarding PR review note — forge probe ruleset selector |
| pr23-02  | onboarding PR review note — forge probe ruleset listing |
| pr23-03  | onboarding PR review note — forge probe ruleset selection |
| pr23-04  | onboarding PR review note — pre-review gate UI-results reader |
| pr23-05  | onboarding PR review note — quickstart document |
| clean-01 | control — authored clean fixture (guard change) |
| clean-02 | control — authored clean fixture (docs-only change) |

## Adjudication rules

- **Severity mapping:** `blocker` counts as critical for every metric
  bucket. The `critical_major_*` metrics cover severities
  `{blocker, major}`.
- **Defective case:** any expected finding with severity blocker or
  major. An **approval escape** is a defective case for which no
  produced finding has severity blocker or major (the per-case decision
  stays APPROVED even though changes are required).
- **Unnecessary revision:** a clean case with at least one produced
  blocker/major finding (the lens would have requested changes on a case
  that needs none).
- **Match rule:** a produced finding's case id is the path component
  immediately after `artifacts/` in its artifact_reference; it matches
  an expected finding when case id, artifact file (the tail of the
  path), and rubric_dimension all agree.
- **Per-case decision (combined):** derived from the findings for that
  case — `CHANGES_REQUESTED` if any lens produced blocker/major for it,
  otherwise `APPROVED`. A case escapes only if **no** lens raised
  blocker/major for it.

## Matching rule limits

- Matching is path-level: line numbers, severities, and evidence text do
  not participate. A produced finding on line 3 of the right file still
  matches the expected finding cited at line 21.
- One expected finding per (case, artifact file, rubric_dimension); the
  loader refuses a case that needs two indistinguishable findings.
- A produced reference without an `artifacts/` component
  (`package:diff@...` or any repo-root path) is unattributable: it adds
  to `produced` and lowers precision but can never match.
- Duplicates dent precision only: the same expected finding matched
  twice counts once, and the extra produced finding is still counted in
  `produced`.
- Severity still gates protection: a produced finding that matches an
  expected major with severity `info` counts as matched but does not
  protect the case from an approval escape.

## Metrics (per lens and combined)

| metric | meaning |
|--------|---------|
| `produced` | findings the lens emitted (all, including unattributable ones) |
| `matched` | produced findings that matched an expected finding |
| `precision` | matched / produced (0 when nothing produced) |
| `recall` | matched / all expected findings across cases |
| `critical_major_expected` | expected findings with severity blocker or major |
| `critical_major_matched` | of those, how many were matched |
| `critical_major_recall` | critical_major_matched / critical_major_expected |
| `clean_cases` | cases with an empty expected finding list |
| `unnecessary_revisions` | clean cases where the lens produced blocker/major |
| `unnecessary_revision_rate` | unnecessary_revisions / clean_cases |
| `defective_cases` | cases with any expected blocker/major finding |
| `approval_escapes` | defective cases with no produced blocker/major |
| `approval_escape_rate` | approval_escapes / defective_cases |

Rates are rounded to 4 decimals in both output modes. The combined block
is the union of lenses for matching and critical coverage; a case
escapes only when no lens raised blocker/major for it.

## How to run

1. Package the bench once per lens (each lens reviews the same packaged
   `cases/artifacts/**` input and writes one findings document).
2. Score:

```sh
python3 tools/review/bench/score.py \
  --bench tools/review/bench \
  --outputs review-contract=contract.json \
  --outputs review-adversary=adversary.json \
  --outputs review-security=security.json
```

`--bench` may be any absolute path (the module also runs as a plain file
path from any working directory). Add `--json` for a single
`review.bench.v1` object. Exit codes: `0` inputs parsed and metrics
printed (metrics are data — zero matches is still exit 0), `1`
unreadable/invalid input, `2` usage errors. A findings document whose
author (`review.reviewer`) disagrees with the `ROLE=` label is refused.

## Refreshing the baseline

`baseline.json` is not written by the scorer — the orchestrator stores
it after a lens run. To refresh: re-run every lens against the current
artifact set, capture `--json`, and diff against the stored baseline.
When a case is re-authored, update its answer key and re-count the
`artifact_reference` line numbers: the loader refuses out-of-range
lines, and citations should resolve to the line that actually carries
the defect.