# Deterministic gate evidence — S3 at 2bd431c

All commands ran in the S3 worktree at head 2bd431cd82e0938b42309ed3d1ff808d9e2260ad (base 152b205).

## Full control-plane suite

command: python3 -m unittest
exit: 0
result: Ran 1343 tests in 76.721s
status: OK

## Gate acceptance harness

command: python3 tools/gates/test_gate.py
exit: 0
result: Ran 63 tests in 0.566s
status: OK

## Workspace + environment templates

command: python3 tools/config_validator.py templates/workspace/workspace.toml templates/workspace/environments/local.toml templates/workspace/environments/github.toml
exit: 0
status: OK (3/3 templates)

## CI at the reviewed head

workflow: kit-ci
head_sha: 2bd431cd82e0938b42309ed3d1ff808d9e2260ad
runs: 40397315699, 40397218124, 40397210468 — all completed/success
