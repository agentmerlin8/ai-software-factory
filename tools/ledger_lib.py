#!/usr/bin/env python3
"""tools/ledger_lib.py — event-ledger I/O: append/load + lifecycle laws.

The I/O half of the ledger port (architecture.md §Data model — the ledger,
§Ledger event schemas). The schema half ships in `tools/ledger_schema.py` —
the single validation authority. This module imports and calls it; it never
re-implements a schema rule.

Laws enforced at the I/O boundary:

  * The ledger is append-only JSON Lines: one JSON object per line, UTC
    timestamps (`YYYY-MM-DDTHH:MM:SSZ`), stable event ids, LF endings,
    trailing newline. Corrections are events, never edits.
  * `append_event` validates BEFORE writing (single-event schema via
    ledger_schema + the stale-restart check): a refused event never
    touches the file.
  * `load_events` parses and then validates the whole ledger — schema plus
    the cross-row laws ledger_schema enforces on a snapshot: the dispatch
    two-row lifecycle (pre-spawn then completion; reversed order, stale
    re-start, orphan completion refused) and non-dispatch event-id
    uniqueness. Refusals are named `LedgerError`s listing every violation.
  * Dispatch ids are pre-spawn: `<story>-<role>-<attempt>-<unix>`, supplied
    by the caller; a dispatch already recorded `ended` can never be
    re-started by a stale notification. Other event ids are auto-assigned
    when absent.
  * No default paths, no cwd assumptions: every function takes an explicit
    ledger path.

Determinism: same append order → same generated event ids
(`<type>-<story>-<seq>`). Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from . import ledger_schema as S
except ImportError:
    import ledger_schema as S

# Re-exports: ledger_schema is the validation authority — callers may use
# either import path, but there is exactly one implementation.
EVENT_TYPES = S.EVENT_TYPES
NULLABLE = S.NULLABLE
validate_event = S.validate_event
validate_events = S.validate_events
events_for = S.events_for
current_state = S.current_state
latest_findings = S.latest_findings


class LedgerError(ValueError):
    """Named refusal from the ledger I/O boundary (message lists every
    violation; nothing is written when append refuses)."""


def utcnow() -> str:
    """UTC timestamp `YYYY-MM-DDTHH:MM:SSZ` (delegates to ledger_schema)."""
    return S.utcnow()


# ------------------------------------------------------------------ load ----

def load_ledger(ledger_path: Path | str) -> list[dict[str, Any]]:
    """Parse the JSONL ledger WITHOUT validation (raw row access).

    A missing file is an empty ledger. Blank lines are skipped. Invalid
    JSON refuses with `<path>:<line>: invalid JSON: <detail>`.
    """
    p = Path(ledger_path)
    if not p.exists():
        return []
    events: list[dict[str, Any]] = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise LedgerError(f"{p}:{i}: invalid JSON: {e}") from e
    return events


def load_events(ledger_path: Path | str) -> list[dict[str, Any]]:
    """Parse + validate the ledger; return the event list.

    Validation is ledger_schema.validate_events: every single-event schema
    rule plus the cross-row laws (dispatch two-row lifecycle order —
    pre-spawn then completion; reversed order, stale re-start, orphan
    completion, and >2 rows refused; non-dispatch event-id uniqueness;
    review-attribution joins). Refuses with LedgerError listing every
    violation; use `validate_ledger` for a non-raising error list.
    """
    events = load_ledger(ledger_path)
    errs = S.validate_events(events)
    if errs:
        raise LedgerError("ledger rejected:\n  " + "\n  ".join(errs))
    return events


def validate_ledger(ledger_path: Path | str) -> list[str]:
    """Validate every row + the ledger-wide laws; [] = valid. Never raises
    on content (only on unreadable files)."""
    return S.validate_events(load_ledger(ledger_path))


# ---------------------------------------------------------------- append ----

def _next_seq(events: list[dict], etype: str, story_id: Any) -> int:
    n = sum(1 for e in events
            if isinstance(e, dict) and e.get("event") == etype
            and e.get("story_id") == story_id)
    return n + 1


def seen_ended(events: list[dict], evt: dict) -> str:
    """Return evt['event_id'] if an ENDED dispatch with that id already
    exists (a stale notification can never re-start it), else ''."""
    if evt.get("event") != "dispatch":
        return ""
    for e in events:
        if not isinstance(e, dict):
            continue
        if e.get("event") == "dispatch" and \
                e.get("event_id") == evt.get("event_id") and \
                e.get("ended_at") and not evt.get("ended_at"):
            return e["event_id"]
    return ""


def append_event(ledger_path: Path | str, event: dict[str, Any]) -> None:
    """Validate + append exactly one JSON line to the ledger.

    Validation is ledger_schema.validate_event plus the stale-restart check
    (`seen_ended`); on refusal nothing is written and LedgerError lists
    every violation. Convenience defaults applied before validation:
    a null-valued nullable field's missing `<field>_reason` sibling is NOT
    invented (the null+reason law stays the caller's job), but a
    value-carrying nullable field gets its reason sibling defaulted to
    null; absent `recorded_at` defaults to `utcnow()`, absent `provenance`
    to `live`. Non-dispatch events without an `event_id` get the
    deterministic `<type>-<story>-<seq>` id; dispatch events MUST carry
    their pre-spawn dispatch_id (`<story>-<role>-<attempt>-<unix>`).
    """
    if not isinstance(event, dict):
        raise LedgerError(f"event must be an object, got "
                          f"{type(event).__name__}")
    p = Path(ledger_path)
    events = load_ledger(p)
    etype = event.get("event")
    if etype not in S.EVENT_TYPES:
        raise LedgerError(f"unknown event type {etype!r}")
    evt = dict(event)
    # when a nullable field carries a value, its reason sibling must be null
    for f in S.NULLABLE.get(etype, frozenset()):
        if evt.get(f) is not None and f"{f}_reason" not in evt:
            evt[f"{f}_reason"] = None
    if etype == "dispatch" and not evt.get("event_id"):
        raise LedgerError("dispatch events must carry their pre-spawn "
                          "dispatch_id (<story>-<role>-<attempt>-<unix>)")
    if not evt.get("event_id"):
        sid = evt.get("story_id") or "global"
        evt["event_id"] = (f"{etype}-{sid}-"
                           f"{_next_seq(events, etype, evt.get('story_id'))}")
    evt.setdefault("recorded_at", utcnow())
    evt.setdefault("provenance", "live")
    ended_id = seen_ended(events, evt)
    if ended_id:
        raise LedgerError(f"event_id {ended_id} already recorded as ended — "
                          "stale notifications cannot re-start it")
    errs = S.validate_event(evt)
    if errs:
        raise LedgerError("event rejected:\n  " + "\n  ".join(errs))
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(evt, sort_keys=False, ensure_ascii=False) + "\n")
