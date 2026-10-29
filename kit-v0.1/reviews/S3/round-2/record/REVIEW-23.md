# Review record — PR 23 (S3)

## Verdict

decision: CHANGES_REQUESTED
blockers: 0
majors: 5
minors: 9
infos: 1
undispositioned findings: 0
loop 2 of 2

## Package

path: kit-v0.1/reviews/S3/round-2/package.md
sha256: 6014aa8b460055e2cda30d20da8b67c8af03a3a6d4033502afb8479706e559a2
range: 2bd431cd82e0938b42309ed3d1ff808d9e2260ad → 52a7d81344333b2337c7dc601b0a5b6bedcc3cd1

## Reviewers

| role | model | rubric | document |
| --- | --- | --- | --- |
| review-adversary | qwen3.8-max | review-adversary-v1 | kit-v0.1/reviews/S3/round-2/findings-review-adversary.yaml |
| review-contract | qwen3.8-max | review-contract-v1 | kit-v0.1/reviews/S3/round-2/findings-review-contract.yaml |
| review-security | qwen3.8-max | review-security-v1 | kit-v0.1/reviews/S3/round-2/findings-review-security.yaml |

## Gate results

| gate | exit | evidence | sha256 |
| --- | --- | --- | --- |
| deterministic-gates | 0 | kit-v0.1/reviews/S3/round-2/gate-evidence.md | 4f2c47b4317918abbadab14a218af3c21dfb69882442e6c35281557acead10a7 |

## Findings

| id | lens | severity | dimension | artifact | disposition | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| RA-001 | review-adversary | major | behavior | package:diff@onboarding/onboard.py:510 | fixed | fixed: 20044a75bdaceacffc54aeafbec924f010d52ef8 |
| RA-002 | review-adversary | major | test_quality | package:diff@onboarding/forge_probe.py:330 | fixed | fixed: 20044a75bdaceacffc54aeafbec924f010d52ef8 |
| RA-003 | review-adversary | minor | behavior | package:diff@onboarding/test_out.py:490 | fixed | fixed: 20044a75bdaceacffc54aeafbec924f010d52ef8 |
| RA-004 | review-adversary | minor | behavior | package:diff@onboarding/onboard.py:454 | fixed | fixed: 20044a75bdaceacffc54aeafbec924f010d52ef8 |
| RA-005 | review-adversary | minor | test_quality | package:diff@tools/check_genericization.py:161 | rejected | reason: No behavioral risk: the sweep is the union of tracked and walked files by design; walked_files already covers every tracked file, so the tracked enumeration is belt-and-braces, and a mutation removing it cannot hide a file. evidence: check_genericization.scan: files = sorted(set(tracked) \| set(walked_files(root))); walked_files walks the tree outside .git and applies the same exclusions. |
| RA-006 | review-adversary | minor | operational | package:diff@tools/gates/pre_review_gate.py:644 | fixed | fixed: 20044a75bdaceacffc54aeafbec924f010d52ef8 |
| RA-007 | review-adversary | minor | test_quality | package:diff@tools/check_genericization.py:169 | fixed | fixed: 52a7d81344333b2337c7dc601b0a5b6bedcc3cd1 |
| RA-008 | review-adversary | info | test_quality | package:diff@onboarding/onboard.py:336 | rejected | reason: Informational: defense-in-depth escaping behind a validated path; no change required and none made. evidence: cmd_propose refuses every --repo-url that normalize_repo_url rejects before render_workspace_draft is reached; actionable=false in the finding. |
| RC-001 | review-contract | major | contract_fidelity | onboarding/test_out.py:490 | rejected | reason: Acceptance executed at the reviewed head; the gate-evidence input is a deterministic-gate record, not the acceptance transcript. evidence: Acceptance evidence measured at the reviewed head 52a7d81344333b2337c7dc601b0a5b6bedcc3cd1: `python3 onboarding/test_out.py --smoke` (default seed = the kit's benchmark-app/) ran 10/10 steps PASS with verify GREEN (18 pass / 0 fail / 13 n-a), rc 0. The smoke is an execution acceptance, not a unit test; the record cites the run rather than embedding it in the package gate inputs. |
| RC-002 | review-contract | major | scope | tools/check_genericization.py:151 | rejected | reason: The scope extension is recorded, not hidden: the genericization tripwire is a carried S2 residual (T6) named in the PR body, and it enforces the build-plan ground rule that this repo stays generic. A second plan amendment for a carried residual is bookkeeping, not a scope change. evidence: PR #23 body, T6: 'Carried S2 residuals: ... F3 - genericization CI tripwire (tools/check_generi...'; build-plan ground rules: 'This repo stays generic - no private product names ever'. |
| RC-003 | review-contract | minor | docs_process | README.md:131 | fixed | fixed: 62867b7cadb7da31ef7314052ec652a71ec2f6f5 |
| RC-004 | review-contract | minor | docs_process | kit-v0.1/architecture.md:436 | fixed | fixed: 20044a75bdaceacffc54aeafbec924f010d52ef8 |
| RC-005 | review-contract | minor | docs_process | kit-v0.1/target-repo-readiness.md:11 | fixed | fixed: 20044a75bdaceacffc54aeafbec924f010d52ef8 |
| RS-001 | review-security | major | security | package:diff@onboarding/onboard.py:510 | fixed | fixed: 20044a75bdaceacffc54aeafbec924f010d52ef8 |
| RS-002 | review-security | minor | operational | package:diff@onboarding/forge_probe.py:330 | fixed | fixed: 20044a75bdaceacffc54aeafbec924f010d52ef8 |

## Prior rounds

| loop | decision | findings | dispositioned | path |
| --- | --- | --- | --- | --- |
| 1 | CHANGES_REQUESTED | 18 | 15 | kit-v0.1/reviews/S3/round-1/pipeline/record |
| 1 | CHANGES_REQUESTED | 21 | 9 | kit-v0.1/reviews/S3/round-1/readiness/record |
| 1 | CHANGES_REQUESTED | 18 | 5 | kit-v0.1/reviews/S3/round-1/tools-docs/record |

## Notes

Findings with source `external` (lens `external`, ids prefixed `EX-`) originate from legacy review sources outside the lane and are mapped onto the lane rubric by dimension; internal panel lenses carry id prefixes RC- (review-contract), RA- (review-adversary), and RS- (review-security).
