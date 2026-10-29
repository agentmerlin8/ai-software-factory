# Review record — PR 23 (S3)

## Verdict

decision: CHANGES_REQUESTED
blockers: 1
majors: 5
minors: 15
infos: 0
undispositioned findings: 0
loop 1 of 2

## Package

path: kit-v0.1/reviews/S3/round-1/pkg-readiness.md
sha256: de145807307d37da509d48552638156a2669ba07b9ff3cc40cc0a6eeae87dd06
range: 152b205b48937a3a12020c02c8378022d088d86e → 2bd431cd82e0938b42309ed3d1ff808d9e2260ad

## Reviewers

| role | rubric | document |
| --- | --- | --- |
| review-adversary | review-adversary-v1 | kit-v0.1/reviews/S3/round-1/readiness/findings-review-adversary.yaml |
| review-contract | review-contract-v1 | kit-v0.1/reviews/S3/round-1/readiness/findings-review-contract.yaml |
| review-security | review-security-v1 | kit-v0.1/reviews/S3/round-1/readiness/findings-review-security.yaml |

## Gate results

| gate | exit | evidence | sha256 |
| --- | --- | --- | --- |
| deterministic-gates | 0 | kit-v0.1/reviews/S3/round-1/gate-evidence.md | 5e3e920675d39d8117295fdede6908ce3fadfeab66999a52c63b9bfe0c2e67d1 |

## Findings

| id | lens | severity | dimension | artifact | disposition | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| RA-001 | review-adversary | major | test_quality | onboarding/readiness.py:872 | fixed | fixed: 52a7d81 |
| RA-002 | review-adversary | blocker | behavior | onboarding/readiness.py:773 | fixed | fixed: a447cb2 |
| RA-003 | review-adversary | minor | behavior | onboarding/readiness.py:440 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RA-004 | review-adversary | minor | behavior | onboarding/readiness.py:1230 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RA-005 | review-adversary | minor | behavior | onboarding/readiness.py:1173 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RA-006 | review-adversary | minor | operational | onboarding/readiness.py:384 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RA-007 | review-adversary | minor | test_quality | onboarding/tests/test_readiness.py:306 | fixed | fixed: 52a7d81 |
| RA-008 | review-adversary | minor | behavior | onboarding/readiness.py:348 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RA-009 | review-adversary | minor | behavior | onboarding/readiness.py:546 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RA-010 | review-adversary | minor | test_quality | onboarding/readiness.py:1048 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RC-001 | review-contract | minor | docs_process | onboarding/readiness.py:1250 | fixed | fixed: 52a7d81 |
| RC-002 | review-contract | major | contract_fidelity | onboarding/tests/test_readiness.py:330 | rejected | reason: Same acceptance evidence as pipeline RC-001. evidence: The acceptance was executed at head 62867b7: `python3 onboarding/test_out.py --smoke` (default seed = the kit's benchmark-app/) ran 10/10 steps PASS, verify GREEN (18 pass / 0 fail), rc 0. test_out.py is unchanged since the reviewed head 2bd431c, so the run applies to the reviewed code. The unit test's synthetic seed is a suite-speed choice, not a deferral of the acceptance. |
| RC-003 | review-contract | minor | docs_process | onboarding/tests/test_readiness.py:6 | fixed | fixed: 52a7d81 |
| RC-004 | review-contract | minor | docs_process | onboarding/tests/test_docs_consistency.py:5 | fixed | fixed: 52a7d81 |
| RS-001 | review-security | major | security | package:diff@onboarding/readiness.py:1166 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RS-002 | review-security | major | security | package:diff@onboarding/readiness.py:1043 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RS-003 | review-security | major | behavior | package:diff@onboarding/readiness.py:773 | fixed | fixed: a447cb2 |
| RS-004 | review-security | minor | security | package:diff@onboarding/readiness.py:553 | fixed | fixed: a447cb2 |
| RS-005 | review-security | minor | operational | package:diff@onboarding/readiness.py:348 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RS-006 | review-security | minor | security | package:diff@onboarding/readiness.py:379 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RS-007 | review-security | minor | security | package:diff@onboarding/readiness.py:1135 | fixed | fixed: a447cb2 |

## Notes

Findings with source `external` (lens `external`, ids prefixed `EX-`) originate from legacy review sources outside the lane and are mapped onto the lane rubric by dimension; internal panel lenses carry id prefixes RC- (review-contract), RA- (review-adversary), and RS- (review-security).
