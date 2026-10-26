# Plan — widget list UI (invalid seed: rendered cardinality wrong)

```contract.yaml
contract_version: 1
files:
  - path: web/stories/widgets.stories.js
    op: CREATE
  - path: tests/test_widget_ui.py
    op: CREATE
tests:
  - ac: AC-1
    project: tests
    name: widget_rows_render
    asserts: "two rows, one detail region"
invariants:
  - id: INV-1
    text: "row/detail association preserved"
verify_commands:
  - "python3 -m unittest tests.test_widget_ui"
decisions:
  - decision: "cardinality asserted mechanically"
    rejected: ["eyeball review"]
ui_assertions:
  - file: web/stories/widgets.stories.js
    export: WidgetList
    assertions:
      - selector: ".widget-row + .detail-region"
        count: 1
      - selector: ".widget-row"
        min_count: 2
```
