# Impl plan — STORY-101

Prose before the block (the plan prose is NEVER embedded in role
packages — only the contract slice is).

```contract.yaml
contract_version: 1
files:
  - path: src/app/cache.py
    op: CREATE
  - path: src/app/parser.py
    op: EXTEND
tests:
  - ac: AC-1
    name: test_rejects_malformed_input
    project: tests
    asserts: "refusal message + nothing written"
  - ac: AC-2
    name: test_round_trip_preserves_order
    project: tests
invariants:
  - id: INV-1
    text: "parse errors never write state"
verify_commands:
  - "python3 -m unittest"
decisions:
  - decision: "hand-rolled cache over a dependency"
    rejected: ["external cache library"]
```

Prose after the block.
