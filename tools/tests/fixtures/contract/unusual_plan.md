# Impl plan — valid but unusual

```contract.yaml
contract_version: 1
files:
  - path: tools/gates/digest_gate.py
    op: CREATE
tests:
  - ac: AC-1
    project: tools/tests
    name: digest_gate_refuses_mismatch
    asserts: "named refusal"
invariants:
  - id: INV-1
    text: "digests are computed, never cached across runs"
verify_commands:
  - "python3 -m unittest discover -s tools/tests"
  - "python3 tools/contract_lib.py stories/STORY-NNN/impl-plan.md"
decisions:
  - decision: "fail closed on any missing input"
    rejected: ["best-effort render"]
failure_model:
  - risk: "stale digest accepted"
    discriminating_test: digest_gate_refuses_mismatch
api_operations:
  - method: POST
    route: /items
    source: src/app/api.py
    action: create_item
    responses:
      - status: 201
        type: ItemCreated
      - status: 409
        type: Conflict
  - method: GET
    route: /items/{id}
    source: src/app/api.py
    action: get_item
    removed: true
```
