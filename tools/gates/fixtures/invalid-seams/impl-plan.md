# Plan — widget throttle (invalid seed: unresolvable seam)

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
  - decision: "constant throttle"
    rejected: ["env var"]
seams:
  - symbol: extract_contract.NoSuchMemberAtBase
    file: tools/contract_lib.py
    signature: "def no_such_member()"
```
