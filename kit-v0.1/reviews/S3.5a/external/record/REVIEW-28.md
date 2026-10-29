# Review record — PR 28 (S3.5a)

## Verdict

decision: CHANGES_REQUESTED
blockers: 2
majors: 10
minors: 0
infos: 0
undispositioned findings: 0
loop 1 of 2

## Package

path: kit-v0.1/reviews/S3.5a/external/pkg.md
sha256: 6b486dd58025b36c4e4e895acba60063c0d1b8531f741a50c3e73c29725fecc5
range: 152b205b48937a3a12020c02c8378022d088d86e → c8f13b47b4f8e683abe0c010a87a19cee18a9be2

## Reviewers

| role | model | rubric | document |
| --- | --- | --- | --- |
| external | copilot-pull-request-reviewer | external-v1 | kit-v0.1/reviews/S3.5a/round-1/external-findings.yaml |

## Gate results

| gate | exit | evidence | sha256 |
| --- | --- | --- | --- |
| deterministic-gates | 0 | kit-v0.1/reviews/S3.5a/round-1/gate-evidence.md | 0d0c18608d880cb2cc0da61e992035e91dc308bc4cabf0b46c5c36606891bed1 |

## Findings

| id | lens | severity | dimension | artifact | disposition | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| EX-001 | external | major | operational | agents/review-adversary.agent.md:31 | fixed | fixed: 0939325 |
| EX-002 | external | major | operational | agents/review-contract.agent.md:31 | fixed | fixed: 0939325 |
| EX-003 | external | major | operational | agents/review-security.agent.md:31 | fixed | fixed: 0939325 |
| EX-004 | external | blocker | security | tools/review/findings.py:194 | fixed | fixed: c70bb5e |
| EX-005 | external | major | security | tools/review/package.py:212 | fixed | fixed: c70bb5e |
| EX-006 | external | major | operational | tools/review/package.py:264 | fixed | fixed: c70bb5e |
| EX-007 | external | major | operational | tools/review/package.py:289 | fixed | fixed: c70bb5e |
| EX-008 | external | blocker | security | tools/review/package.py:360 | fixed | fixed: c70bb5e |
| EX-009 | external | major | operational | tools/review/package.py:420 | fixed | fixed: c70bb5e |
| EX-010 | external | major | docs_process | kit-v0.1/reviews/S3.5a/REVIEW-S3.5a.md:17 | fixed | fixed: ecfab57 |
| EX-011 | external | major | operational | tools/review/findings.py:349 | fixed | fixed: c70bb5e |
| EX-012 | external | major | operational | tools/review/package.py:171 | fixed | fixed: c70bb5e |

## Notes

Findings with source `external` (lens `external`, ids prefixed `EX-`) originate from legacy review sources outside the lane and are mapped onto the lane rubric by dimension; internal panel lenses carry id prefixes RC- (review-contract), RA- (review-adversary), and RS- (review-security).
