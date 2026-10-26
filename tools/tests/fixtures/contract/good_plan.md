# Impl plan — STORY-NNN fixture

Some prose before the block.

```contract.yaml
contract_version: 1
files:
  - path: src/app/parser.py
    op: CREATE
  - path: src/app/api.py
    op: EXTEND
tests:
  - ac: AC-1
    name: rejects_malformed_input
    asserts: "refusal message + nothing written"
  - ac: AC-2
    project: tests/unit
    name: round_trip_preserves_order
invariants:
  - id: INV-1
    text: "parse errors never write state"
verify_commands:
  - "python3 -m unittest"
decisions:
  - decision: "hand-rolled YAML subset over a dependency"
    rejected: ["full YAML parser"]
```

Prose after the block.
