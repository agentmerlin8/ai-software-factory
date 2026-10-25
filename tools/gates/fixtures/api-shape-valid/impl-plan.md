# Plan — items API (valid: verify_unchanged declared)

```contract.yaml
contract_version: 1
files:
  - path: src/app/core.py
    op: EXTEND
tests:
  - ac: AC-1
    project: tests
    name: api_items_listed
    asserts: "200 + ItemList"
invariants:
  - id: INV-1
    text: "route shape frozen"
verify_commands:
  - "python3 -m unittest tests.test_api"
decisions:
  - decision: "single items route"
    rejected: ["per-tenant routes"]
api_operations:
  - method: GET
    route: /items
    source: src/app/api.py
    action: list_items
    responses:
      - status: 200
        type: ItemList
    verify_unchanged: true
```
