# Plan — widget throttle (invalid seed: no invariants)

```contract.yaml
contract_version: 1
files:
  - path: src/app/widget.py
    op: CREATE
tests:
  - ac: AC-1
    project: tests
    name: widget_rejects_duplicate
    asserts: "refusal"
invariants: []
verify_commands:
  - "python3 -m unittest tests.test_widget"
decisions:
  - decision: "constant throttle"
    rejected: ["env var"]
```
