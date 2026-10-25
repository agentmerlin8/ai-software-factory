# Impl plan (seams extension) — STORY-102

The `seams` key is NOT part of the frozen v1 contract schema — it is a
project extension; dispatch_prep renders its section only when declared.

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
seams:
  - symbol: Parser.parse_data
    signature: "parse_data(self, raw) -> dict"
    file: src/app/parser.py
  - symbol: CacheLoader
    signature: "class CacheLoader"
    file: src/app/cache.py
invariants:
  - id: INV-1
    text: "parse errors never write state"
verify_commands:
  - "python3 -m unittest"
decisions:
  - decision: "seam-first plan"
```
