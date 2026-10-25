# Plan — widget throttle (valid but unusual: must ACCEPT)

Prose deliberately contains poison strings for naive scanners: a bare
`pytest` invocation with no target, forbidden flags like `--filter`, and
an AC-1 mention that only the spec makes binding. PROSE IS NEVER SCANNED
for commands — only the contract's verify_commands are.

<!-- snippet quoted_from: tools/contract_lib.py@abc1234 -->
```python
CONTRACT_VERSION = 1  # quoted from working repo code
```

<!-- snippet compiled: true evidence: stories/STORY-910/compile-proof.md -->
```python
WIDGET_THROTTLE_SECONDS = 900  # compiled by the planner
```

```bash
python3 -m unittest
```

New file: tests/test_widget.py holds `widget_rejects_duplicate`.

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
