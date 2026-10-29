# Deterministic gate evidence — S3.5a at b3b2a6f

## Full control-plane suite

command: python3 -m unittest
exit: 0
result: Ran 1361 tests in 17.661s
status: OK

## Gate acceptance harness

command: python3 tools/gates/test_gate.py
exit: 0
result: Ran 58 tests in 0.308s
status: OK

## Templates

command: python3 tools/config_validator.py templates/workspace/workspace.toml templates/workspace/environments/local.toml templates/workspace/environments/github.toml
exit: 0
status: OK (3/3)

## Calibration baseline

command: python3 tools/review/bench/score.py --bench tools/review/bench --outputs ...
result: baseline frozen in tools/review/bench/baseline.json (17 cases; combined CM detection recall 1.0)
