# Plan — widget lifecycle (invalid seed: coverage defects d1-d4 + e1)

```contract.yaml
contract_version: 1
files:
  - path: src/app/widget.py
    op: CREATE
tests:
  - ac: AC-2
    project: tests
    name: widget_accepts_valid
    asserts: "state written"
  - ac: AC-2
    project: tests
    name: widget_accepts_valid
    asserts: "duplicate name seed (d2)"
  - ac: AC-9
    project: tests
    name: GhostTests.NeverWritten
    asserts: "unknown AC seed (d3+d4)"
invariants:
  - id: INV-1
    text: "lifecycle hooks fire exactly once"
verify_commands:
  - "python3 -m unittest tests.test_widget"
decisions:
  - decision: "hooks in the lifecycle layer"
    rejected: ["repository triggers"]
failure_model:
  - risk: "hook silently skipped on refresh"
    discriminating_test: MissingTests.X
```
