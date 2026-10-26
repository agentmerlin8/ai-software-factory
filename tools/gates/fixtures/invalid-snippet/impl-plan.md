# Plan — widget throttle (invalid seed: unannotated snippet)

The planner "pinned" this snippet without compiling it or quoting it:

```python
total = count * PRICE  # never compiled — field-defect class
```

Command fences and the contract fence are exempt; only CODE snippet
languages require annotation.

```bash
python3 -m unittest
```

```contract.yaml
contract_version: 1
files:
  - path: src/app/widget.py
    op: CREATE
  - path: tests/test_widget.py
    op: CREATE
tests:
  - ac: AC-1
    project: tests
    name: widget_rejects_duplicate
    asserts: "refusal + no state written"
invariants:
  - id: INV-1
    text: "duplicate input never mutates state"
verify_commands:
  - "python3 -m unittest tests.test_widget"
decisions:
  - decision: "contract constant WIDGET_THROTTLE_SECONDS"
    rejected: ["env var", "server header"]
```
