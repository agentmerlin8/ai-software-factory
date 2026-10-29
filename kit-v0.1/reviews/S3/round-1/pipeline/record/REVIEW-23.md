# Review record — PR 23 (S3)

## Verdict

decision: CHANGES_REQUESTED
blockers: 0
majors: 11
minors: 7
infos: 0
undispositioned findings: 0
loop 1 of 2

## Package

path: kit-v0.1/reviews/S3/round-1/pkg-pipeline.md
sha256: 269be00da739de416d92686bf26baa6624c630a899ea102e693e720bebbcd3b1
range: 152b205b48937a3a12020c02c8378022d088d86e → 2bd431cd82e0938b42309ed3d1ff808d9e2260ad

## Reviewers

| role | rubric | document |
| --- | --- | --- |
| external | external-v1 | kit-v0.1/reviews/S3/round-1/pipeline/findings-external.yaml |
| review-adversary | review-adversary-v1 | kit-v0.1/reviews/S3/round-1/pipeline/findings-review-adversary.yaml |
| review-contract | review-contract-v1 | kit-v0.1/reviews/S3/round-1/pipeline/findings-review-contract.yaml |
| review-security | review-security-v1 | kit-v0.1/reviews/S3/round-1/pipeline/findings-review-security.yaml |

## Gate results

| gate | exit | evidence | sha256 |
| --- | --- | --- | --- |
| deterministic-gates | 0 | kit-v0.1/reviews/S3/round-1/gate-evidence.md | 5e3e920675d39d8117295fdede6908ce3fadfeab66999a52c63b9bfe0c2e67d1 |

## Findings

| id | lens | severity | dimension | artifact | disposition | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| EX-001 | external | major | behavior | onboarding/forge_probe.py:485 | fixed | fixed: 62867b7 |
| EX-002 | external | major | operational | onboarding/forge_probe.py:205 | fixed | fixed: 62867b7 |
| EX-003 | external | major | behavior | onboarding/forge_probe.py:491 | fixed | fixed: 62867b7 |
| RA-001 | review-adversary | major | test_quality | package:diff@onboarding/forge_probe.py:262 | fixed | fixed: a447cb2 |
| RA-002 | review-adversary | major | test_quality | package:diff@onboarding/tests/test_forge_probe.py:838 | fixed | fixed: 52a7d81 |
| RA-003 | review-adversary | major | test_quality | package:diff@onboarding/tests/test_onboard.py:484 | fixed | fixed: 52a7d81 |
| RA-004 | review-adversary | major | test_quality | package:diff@onboarding/tests/test_test_out.py:301 | fixed | fixed: 52a7d81 |
| RA-005 | review-adversary | minor | behavior | package:diff@onboarding/forge_probe.py:209 | fixed | fixed: a447cb2 |
| RA-006 | review-adversary | minor | behavior | package:diff@onboarding/onboard.py:535 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RA-007 | review-adversary | minor | operational | package:diff@onboarding/onboard.py:731 | fixed | fixed: a447cb2 |
| RA-008 | review-adversary | minor | operational | package:diff@onboarding/test_out.py:490 | fixed | fixed: a447cb2 |
| RA-009 | review-adversary | minor | behavior | package:diff@onboarding/onboard.py:433 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/26 |
| RC-001 | review-contract | major | contract_fidelity | onboarding/tests/test_test_out.py:22 | rejected | reason: Acceptance is a run, and it is green; the unit test's synthetic seed is a speed choice. evidence: The acceptance was executed at head 62867b7: `python3 onboarding/test_out.py --smoke` (default seed = the kit's benchmark-app/) ran 10/10 steps PASS, verify GREEN (18 pass / 0 fail), rc 0. test_out.py is unchanged since the reviewed head 2bd431c, so the run applies to the reviewed code. The unit test's synthetic seed is a suite-speed choice, not a deferral of the acceptance. |
| RS-001 | review-security | major | security | onboarding/forge_probe.py:303 | fixed | fixed: a447cb2 |
| RS-002 | review-security | major | security | onboarding/onboard.py:483 | fixed | fixed: a447cb2 |
| RS-003 | review-security | major | security | onboarding/onboard.py:416 | fixed | fixed: a447cb2 |
| RS-004 | review-security | minor | operational | onboarding/forge_probe.py:262 | fixed | fixed: a447cb2 |
| RS-005 | review-security | minor | operational | onboarding/forge_probe.py:209 | fixed | fixed: a447cb2 |

## Notes

Findings with source `external` (lens `external`, ids prefixed `EX-`) originate from legacy review sources outside the lane and are mapped onto the lane rubric by dimension; internal panel lenses carry id prefixes RC- (review-contract), RA- (review-adversary), and RS- (review-security).
