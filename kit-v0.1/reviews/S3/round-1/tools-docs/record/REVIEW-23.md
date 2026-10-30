# Review record — PR 23 (S3)

## Verdict

decision: CHANGES_REQUESTED
blockers: 1
majors: 5
minors: 12
infos: 0
undispositioned findings: 0
loop 1 of 2

## Package

path: kit-v0.1/reviews/S3/round-1/pkg-tools-docs.md
sha256: bd0de80a5873fd8fa69dc5d701b01fd84c873f1d914ef6f04f94d4d9fd40ec33
range: 152b205b48937a3a12020c02c8378022d088d86e → 2bd431cd82e0938b42309ed3d1ff808d9e2260ad

## Reviewers

| role | model | rubric | document |
| --- | --- | --- | --- |
| external | qwen3.8-max | external-v1 | kit-v0.1/reviews/S3/round-1/tools-docs/findings-external.yaml |
| review-adversary | qwen3.8-max | review-adversary-v1 | kit-v0.1/reviews/S3/round-1/tools-docs/findings-review-adversary.yaml |
| review-contract | qwen3.8-max | review-contract-v1 | kit-v0.1/reviews/S3/round-1/tools-docs/findings-review-contract.yaml |
| review-security | qwen3.8-max | review-security-v1 | kit-v0.1/reviews/S3/round-1/tools-docs/findings-review-security.yaml |

## Gate results

| gate | exit | evidence | sha256 |
| --- | --- | --- | --- |
| deterministic-gates | 0 | kit-v0.1/reviews/S3/round-1/gate-evidence.md | 5e3e920675d39d8117295fdede6908ce3fadfeab66999a52c63b9bfe0c2e67d1 |

## Findings

| id | lens | severity | dimension | artifact | disposition | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| EX-004 | external | major | operational | tools/gates/pre_review_gate.py:639 | fixed | fixed: 62867b7 |
| EX-005 | external | minor | docs_process | README.md:131 | fixed | fixed: 62867b7 |
| RA-001 | review-adversary | major | test_quality | package:diff@tools/check_genericization.py:144 | fixed | fixed: 52a7d81 |
| RA-002 | review-adversary | minor | test_quality | package:diff@tools/gates/mechanical_checks.py:201 | rejected | reason: Not dead code: the bare-invocation check is scoped to declared runners; contract-shape validation lives in the contract parser. evidence: mechanical_checks.check_bare_runners returns early when no --runner names are declared (nothing to check), and the added verify_commands type check runs whenever the check is active. |
| RA-003 | review-adversary | minor | test_quality | package:diff@tools/gates/pre_review_gate.py:172 | rejected | reason: Documented normalization, not a defect. evidence: The loop rewrites non-list contract fields to [] with the comment explaining that a list-typed field failing the contract parser is normalized so the gate reports the parser's named findings rather than crashing. |
| RA-004 | review-adversary | minor | test_quality | package:diff@tools/gates/pre_review_gate.py:366 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RA-005 | review-adversary | minor | operational | package:diff@tools/gates/pre_review_gate.py:502 | fixed | fixed: 62867b7 |
| RA-006 | review-adversary | minor | operational | package:diff@onboarding/templates/gitignore:6 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RA-007 | review-adversary | minor | behavior | package:diff@tools/check_genericization.py:132 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RC-001 | review-contract | blocker | contract_fidelity | kit-v0.1/target-repo-readiness.md:9 | rejected | reason: Package-scope artifact: the docs describe artifacts shipped in the same PR but outside this package. evidence: onboarding/readiness.py (1291 lines), onboard.py, forge_probe.py and their tests are in the same diff (pkg-readiness / pkg-pipeline, hashes de145807 / 269be00d). The tools-docs package was scoped to tools+docs so each lens fit a context. |
| RC-002 | review-contract | minor | docs_process | README.md:131 | fixed | fixed: 62867b7 |
| RC-003 | review-contract | major | scope | tools/check_genericization.py:1 | rejected | reason: The tool is a carried S2 residual, disclosed in the PR body. evidence: PR #23 body, T6: 'Carried S2 residuals: F3 - genericization CI tripwire (tools/check_generi...'. It enforces the build-plan ground rule 'This repo stays generic - no private product names ever'. |
| RC-004 | review-contract | minor | docs_process | kit-v0.1/architecture.md:436 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RC-005 | review-contract | minor | docs_process | kit-v0.1/target-repo-readiness.md:11 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RS-001 | review-security | major | security | package:diff@onboarding/templates/gitignore:7 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RS-002 | review-security | major | security | package:diff@ONBOARDING.md:173 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RS-003 | review-security | minor | security | package:diff@.github/workflows/kit-ci.yml:28 | rejected | reason: No elevated token: the workflow declares no permissions block and no secrets; fetch-depth only affects history. evidence: .github/workflows/kit-ci.yml: actions/checkout with fetch-depth 0 and no token input, no permissions: key, no secret references; GITHUB_TOKEN is not passed to any script. |
| RS-004 | review-security | minor | security | package:diff@tools/check_genericization.py:153 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |

## Notes

Findings with source `external` (lens `external`, ids prefixed `EX-`) originate from legacy review sources outside the lane and are mapped onto the lane rubric by dimension; internal panel lenses carry id prefixes RC- (review-contract), RA- (review-adversary), and RS- (review-security).
