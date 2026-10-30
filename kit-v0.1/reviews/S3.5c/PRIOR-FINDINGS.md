# Prior review rounds — findings and dispositions (head 6a4c8a0, then c1bfa98)

Two models ran the three lenses over package `fce6e7b8…43c8bf` (base 8d353f8, head 6a4c8a0), then again over `dc1058c5…77c9af` (base 8d353f8, head c1bfa98). This round reviews the full diff at the current head; verify these dispositions and report only what remains, regressed, or is newly introduced.

## Fixed in b0b998f / c1bfa98

- qwen RC-001 (blocker) — single-model v2 records kept plain ids. Fixed: ids are namespaced `<lens-id>@<model>` unconditionally in v2.
- qwen RC-002 / deepseek RC-001 (major/minor) — agreement column rendered a bare count. Fixed: renders `N/M` (agreement / panel size).
- qwen RC-003 / deepseek RA-001 (major) — the check-side panel-symmetry guard and the `--model`-with-inline-form refusal had no tests. Fixed: both now have tests, including a stored-record asymmetry tamper.
- deepseek RS-001 (blocker) — model attribution was self-asserted and one document could fill two slots. Fixed: duplicate finding-bearing documents are refused across slots; the Panel states attribution is dispatch-declared.
- deepseek RS-002 (major) — an empty panel passed as symmetric. Fixed: empty panel refused by name.
- deepseek RS-003 (minor) — location_key was truncated to 48 bits. Fixed: full sha256.
- deepseek RA-003 (minor) — location_key separation was unpinned. Fixed: test covers distinct artifacts and distinct dimensions.
- qwen RA-003 (major) — dual tests used identical dispositions for the twin pair. Fixed: a test now dispositions twins differently (rejected vs deferred).
- qwen RA-004 (major) — the one-model-only verdict case was untested. Fixed: test asserts a major from one model alone forces CHANGES REQUESTED and survives check.
- qwen RA-005 (minor) — symmetry counted an ingested `external` document. Fixed: symmetry covers internal lenses only, with a test.
- qwen RA-006 (minor) — inline-form guards untested. Fixed: tests for `--model` rejection and single-model namespacing.
- qwen RS-001 (major) — model labels differing only by whitespace bypassed the duplicate-slot refusal. Fixed: role/model strings are stripped before validation and use.
- qwen RS-002 (minor) — duplicate document across slots. Fixed as deepseek RS-001.
- qwen RA-001 (major) — the v1 path accepted two reviewers of the same role (v1 semantics refused it). Fixed: legacy path restores the duplicate-role refusal, and a v1-claiming record carrying namespaced ids or v2 fields is refused as a schema lie.

## Round 2 (head c1bfa98, package dc1058c5…77c9af) — fixed in 6453170 / 946bbae

- deepseek RC-001 / RS-001 (major) — a panel with no internal lens coverage (all-external, or models sharing one lens) passed the symmetry guard. Fixed: "incomplete panel: no internal lens coverage" on both paths, with a test.
- deepseek RC-002 / RS-004 / RA-002 / qwen RC-001 / RA-002 / RS-001 (major/minor) — the agreement denominator counted external documents (`2/3` on a dual panel). Fixed: agreement and the denominator count internal models only; external findings render `—`, with a test.
- deepseek RS-002 (major) — stored reviewer labels were not trimmed on the check path, so a padded model alias created a phantom slot. Fixed: labels normalized in check; padded-label test added.
- deepseek RA-001 (major) — the check-side duplicate-document guard had no test. Fixed: tampered-record test added.
- deepseek RS-003 (minor) — Panel model names rendered unescaped. Fixed: md_cell everywhere.
- deepseek RS-005 (minor) — finding-free documents are exempt from de-duplication. Rejected as a change, disclosed in the Panel attribution note: two models that both approve produce byte-identical documents, so refusing them would block clean panels.
- qwen RC-002 (minor) — no test placed two findings from ONE model at one location. Fixed: test pins agreement = distinct models.
- qwen RA-003 (minor) — the v1 schema-lie refusal was untested. Fixed: test added.

## Rejected with evidence

- qwen RC-004 / deepseek RC-002 (major/minor) — "kit-ci green" has no gate evidence artifact. This package is a PRE-PR review round: kit-ci has no run to cite until a PR exists. The record's gate evidence will name the CI run at the final head once the PR opens.
- deepseek RA-002 / qwen RA-002 (major) — a stored record rewritten consistently (drop a model's reviewers, findings, dispositions, relabel schema) can pass check. Out of the record's threat model: check verifies a record against the documents it cites, not against an actor who can rewrite both. The architecture doc now states this boundary; the committed diff is the exposure.

## Void

- deepseek `RC-deepseek.yaml` — schema-invalid document (artifact_reference `package:gate-evidence@unit` is not an allowed form). The run is void; the contract lens is re-run in this round.