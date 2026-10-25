# Plan — widget throttle (valid: seams resolve at base)

```contract.yaml
contract_version: 1
files:
  - path: src/app/widget.py
    op: CREATE
  - path: src/app/widget_maker.py
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
  - decision: "constant throttle"
    rejected: ["env var"]
seams:
  - symbol: WidgetCore.reset
    file: src/app/core.py
    signature: "def reset(self)"
  - symbol: WidgetMaker
    file: src/app/widget_maker.py
    signature: "class WidgetMaker"
```
