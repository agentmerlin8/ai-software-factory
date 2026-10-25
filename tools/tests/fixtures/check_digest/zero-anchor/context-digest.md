# Context digest — STORY-905

## 1. File manifest

| path | role | facts extracted |
|---|---|---|
| `src/app.py` | subject | compute_total signature |

## 2. Story contract

Acceptance criteria: exactly 2. Widget totals must be exact; negative
counts refuse.

## 3. Frozen decisions

- Integer arithmetic only (rejected: float accumulation).

## 4. Code anchors

- `src/app.py:0` — def compute_total

## 5. Conventions

- stdlib only; tests live under tests/.

## 6. Open questions

- none.

## 7. Risk list

- rounding drift on large inputs — M

## 8. Verification block

BASE_SHA: HEAD
