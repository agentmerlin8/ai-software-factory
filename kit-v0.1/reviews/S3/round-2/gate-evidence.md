# Deterministic gate evidence — S3 fix round at 52a7d81

All commands ran in the S3 worktree at head 52a7d81 (reviewed base 2bd431c, original base 152b205).

## Full control-plane suite

command: python3 -m unittest
exit: 0
result: Ran 1386 tests in 76.852s
status: OK

## Gate acceptance harness

command: python3 tools/gates/test_gate.py
exit: 0
result: Ran 66 tests in 0.831s
status: OK

## Workspace + environment templates

command: python3 tools/config_validator.py templates/workspace/workspace.toml templates/workspace/environments/local.toml templates/workspace/environments/github.toml
exit: 0
status: OK (3/3 templates)

## CI

CI runs on push for the branch head; the record commit is docs-only. Local evidence above is at the reviewed head.
