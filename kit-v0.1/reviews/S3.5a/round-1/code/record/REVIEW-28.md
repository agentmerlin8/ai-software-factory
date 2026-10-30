# Review record — PR 28 (S3.5a)

## Verdict

decision: CHANGES_REQUESTED
blockers: 3
majors: 15
minors: 4
infos: 0
undispositioned findings: 0
loop 1 of 2

## Package

path: kit-v0.1/reviews/S3.5a/round-1/pkg-code.md
sha256: 133698dd1f5fb12bddb68b8382b98659a0000ca0234e59d61765a765693baec0
range: 152b205b48937a3a12020c02c8378022d088d86e → b3b2a6f8faa991cddf2c6f580accf49e36fb4473

## Reviewers

| role | model | rubric | document |
| --- | --- | --- | --- |
| review-adversary | qwen3.8-max | review-adversary-v1 | kit-v0.1/reviews/S3.5a/round-1/code/findings-review-adversary.yaml |
| review-contract | qwen3.8-max | review-contract-v1 | kit-v0.1/reviews/S3.5a/round-1/code/findings-review-contract.yaml |
| review-security | qwen3.8-max | review-security-v1 | kit-v0.1/reviews/S3.5a/round-1/code/findings-review-security.yaml |

## Gate results

| gate | exit | evidence | sha256 |
| --- | --- | --- | --- |
| deterministic-gates | 0 | kit-v0.1/reviews/S3.5a/round-1/gate-evidence.md | 0d0c18608d880cb2cc0da61e992035e91dc308bc4cabf0b46c5c36606891bed1 |

## Findings

| id | lens | severity | dimension | artifact | disposition | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| RA-001 | review-adversary | blocker | behavior | package:diff@tools/review/package.py:310 | fixed | fixed: f2fc448 |
| RA-002 | review-adversary | major | behavior | package:diff@tools/review/record.py:781 | fixed | fixed: f2fc448 |
| RA-003 | review-adversary | major | behavior | package:diff@tools/review/record.py:745 | fixed | fixed: f2fc448 |
| RA-004 | review-adversary | major | behavior | package:diff@tools/review/record.py:808 | fixed | fixed: f2fc448 |
| RA-005 | review-adversary | major | behavior | package:diff@tools/review/record.py:426 | fixed | fixed: f2fc448 |
| RA-006 | review-adversary | major | test_quality | package:diff@tools/review/package.py:78 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/27 |
| RA-007 | review-adversary | minor | operational | package:diff@tools/review/record.py:726 | deferred | issue: https://github.com/agentmerlin8/ai-software-factory/issues/27 |
| RC-001 | review-contract | blocker | contract_fidelity | package:diff@tools/review/record.py:221 | fixed | fixed: eaa65c4 |
| RC-002 | review-contract | major | contract_fidelity | package:diff@tools/review/package.py:308 | fixed | fixed: f2fc448 |
| RC-003 | review-contract | major | contract_fidelity | package:diff@tools/review/record.py:408 | fixed | fixed: f2fc448 |
| RC-004 | review-contract | major | contract_fidelity | package:diff@tools/review/record.py:263 | fixed | fixed: eaa65c4 |
| RC-005 | review-contract | major | contract_fidelity | package:diff@tools/review/tests/test_score.py:500 | rejected | reason: The baseline is shipped and evidenced: tools/review/bench/baseline.json is in the content package (and committed), carries the bench package hash and models, and the README table matches it. The gate-evidence line names the command, not the output, which is the convention for deterministic gates. evidence: baseline.json: bench.package_sha256 20cbd558..., models qwen3.8-max, report.lenses/combined numbers matching README's frozen table. |
| RC-006 | review-contract | major | contract_fidelity | package:diff@tools/review/tests/test_score.py:23 | fixed | fixed: eaa65c4 |
| RC-007 | review-contract | minor | docs_process | package:diff@tools/review/record.py:517 | fixed | fixed: f2fc448 |
| RC-008 | review-contract | minor | docs_process | package:diff@tools/review/package.py:349 | fixed | fixed: eaa65c4 |
| RS-001 | review-security | major | security | package:diff@tools/review/package.py:237 | fixed | fixed: f2fc448 |
| RS-002 | review-security | major | security | package:diff@tools/review/package.py:351 | fixed | fixed: f2fc448 |
| RS-003 | review-security | major | security | package:diff@tools/review/record.py:169 | fixed | fixed: f2fc448 |
| RS-004 | review-security | major | operational | package:diff@tools/review/record.py:781 | fixed | fixed: f2fc448 |
| RS-005 | review-security | minor | security | package:diff@tools/review/record.py:517 | fixed | fixed: f2fc448 |
| RS-006 | review-security | blocker | security | package:diff@tools/review/package.py:310 | fixed | fixed: f2fc448 |
| RS-007 | review-security | major | security | package:diff@tools/review/record.py:430 | fixed | fixed: f2fc448 |

## Notes

Findings with source `external` (lens `external`, ids prefixed `EX-`) originate from legacy review sources outside the lane and are mapped onto the lane rubric by dimension; internal panel lenses carry id prefixes RC- (review-contract), RA- (review-adversary), and RS- (review-security).
