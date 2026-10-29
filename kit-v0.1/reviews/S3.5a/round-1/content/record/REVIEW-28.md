# Review record — PR 28 (S3.5a)

## Verdict

decision: CHANGES_REQUESTED
blockers: 1
majors: 10
minors: 3
infos: 0
undispositioned findings: 0
loop 1 of 2

## Package

path: kit-v0.1/reviews/S3.5a/round-1/pkg-content.md
sha256: 27cbfea787a79eb3215b6b432ce40ecf43e897984242ab0203872f1845328f60
range: 152b205b48937a3a12020c02c8378022d088d86e → b3b2a6f8faa991cddf2c6f580accf49e36fb4473

## Reviewers

| role | model | rubric | document |
| --- | --- | --- | --- |
| review-adversary | qwen3.8-max | review-adversary-v1 | kit-v0.1/reviews/S3.5a/round-1/content/findings-review-adversary.yaml |
| review-contract | qwen3.8-max | review-contract-v1 | kit-v0.1/reviews/S3.5a/round-1/content/findings-review-contract.yaml |
| review-security | qwen3.8-max | review-security-v1 | kit-v0.1/reviews/S3.5a/round-1/content/findings-review-security.yaml |

## Gate results

| gate | exit | evidence | sha256 |
| --- | --- | --- | --- |
| deterministic-gates | 0 | kit-v0.1/reviews/S3.5a/round-1/gate-evidence.md | 0d0c18608d880cb2cc0da61e992035e91dc308bc4cabf0b46c5c36606891bed1 |

## Findings

| id | lens | severity | dimension | artifact | disposition | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| RA-001 | review-adversary | major | test_quality | package:diff@tools/review/bench/score.py:323 | fixed | fixed: eaa65c4 |
| RA-002 | review-adversary | major | test_quality | package:diff@tools/review/bench/score.py:438 | fixed | fixed: eaa65c4 |
| RA-003 | review-adversary | minor | test_quality | package:diff@tools/review/bench/score.py:312 | fixed | fixed: eaa65c4 |
| RA-004 | review-adversary | major | test_quality | package:diff@tools/review/bench/score.py:320 | fixed | fixed: eaa65c4 |
| RA-005 | review-adversary | major | operational | package:diff@agents/review-adversary.agent.md:44 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/27 |
| RA-006 | review-adversary | major | test_quality | package:diff@tools/review/bench/README.md:158 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/27 |
| RA-007 | review-adversary | major | operational | package:diff@tools/review/bench/score.py:538 | fixed | fixed: eaa65c4 |
| RC-001 | review-contract | major | docs_process | package:diff@kit-v0.1/architecture.md:241 | fixed | fixed: eaa65c4 |
| RC-002 | review-contract | blocker | contract_fidelity | package:diff@tools/review/bench/score.py:47 | rejected | reason: Package-scope artifact: the code package (hash 133698dd...) ships tools/review/findings.py and the package.py/record.py modules; the content package was scoped to agents+bench+docs so each lens fits a context. The import resolves in the repository. evidence: tools/review/findings.py is present in pkg-code.md and in the branch at the reviewed head. |
| RC-003 | review-contract | minor | docs_process | package:diff@tools/review/bench/README.md:4 | fixed | fixed: eaa65c4 |
| RC-004 | review-contract | minor | docs_process | package:diff@agents/review-contract.agent.md:11 | fixed | fixed: eaa65c4 |
| RC-005 | review-contract | major | scope | package:diff@kit-v0.1/build-plan.md:55 | rejected | reason: The pilot record is the deliverable of a separate branch: PR #23's record lives at kit-v0.1/reviews/S3/ on the S3 branch, committed b2b2f4d, because it records that branch's head. It is referenced from the S3.5a PR body and cannot be inside the lane's own diff. evidence: S3 branch b2b2f4d: kit-v0.1/reviews/S3/REVIEW-23.md + four review.json records; issue #24 links it. |
| RS-001 | review-security | major | security | package:diff@tools/review/bench/score.py:202 | fixed | fixed: eaa65c4 |
| RS-002 | review-security | major | behavior | package:diff@tools/review/bench/README.md:22 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/27 |

## Notes

Findings with source `external` (lens `external`, ids prefixed `EX-`) originate from legacy review sources outside the lane and are mapped onto the lane rubric by dimension; internal panel lenses carry id prefixes RC- (review-contract), RA- (review-adversary), and RS- (review-security).
