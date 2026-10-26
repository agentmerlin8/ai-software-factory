# Plan — widget throttle (invalid seed: bare runner commands)

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
invariants:
  - id: INV-1
    text: "no state on refusal"
verify_commands:
  - "pytest"
  - "pytest tests/does_not_exist"
decisions:
  - decision: "constant throttle"
    rejected: ["env var"]
```
