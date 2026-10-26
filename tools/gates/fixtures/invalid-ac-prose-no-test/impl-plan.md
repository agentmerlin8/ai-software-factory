# Plan — widget throttle (invalid seed: AC prose, no named test)

AC-2: the audit row is written on every refusal. AC-2: an audit row is
written. AC-2 is handled by the refusal path — AC-2, AC-2, AC-2.

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
  - "python3 -m unittest tests.test_widget"
decisions:
  - decision: "audit inside the refusal path"
    rejected: ["post-hoc sweep"]
```
