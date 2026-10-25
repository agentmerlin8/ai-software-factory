#!/usr/bin/env python3
"""Tests for tools/ledger_lib.py — append/load I/O over events.jsonl,
delegating validation to tools/ledger_schema.py: round-trip, refusals
leave the file untouched, dispatch two-row lifecycle, stale-restart block,
non-dispatch duplicate ids, deterministic auto event ids, UTC format."""
from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from tools import ledger_lib as L
from tools import ledger_schema as S

STORY = "STORY-NNN"
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "ledger_lib")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def norm(evt: dict) -> dict:
    """Fill the null+reason siblings the schema requires (test helper)."""
    for f in sorted(S.NULLABLE.get(evt["event"], frozenset())):
        if f not in evt:
            evt[f] = None
        if evt[f] is None and not evt.get(f + "_reason"):
            evt[f + "_reason"] = "test: unavailable"
        elif evt[f] is not None:
            evt[f + "_reason"] = None
    return evt


def transition(eid: str, to_state: str, frm: str = "BACKLOG",
               story: str = STORY) -> dict:
    return norm({"event": "transition", "event_id": eid,
                 "recorded_at": "2026-10-25T00:00:00Z", "provenance": "live",
                 "story_id": story, "from_state": frm, "to_state": to_state,
                 "requested_by": "orchestrator", "checks_run": ["story_md"],
                 "check_results": {"story_md": {"ok": True}}})


def dispatch_pre(eid: str = "STORY-NNN-coder-1-1758800000") -> dict:
    return norm({"event": "dispatch", "event_id": eid,
                 "recorded_at": "2026-10-25T00:00:00Z", "provenance": "live",
                 "story_id": STORY, "role": "coder",
                 "requested_model": "model-a", "reported_model": None,
                 "reported_model_reason": "not yet reported (pre-spawn)",
                 "model_unverified": True, "attempt": 1,
                 "started_at": "2026-10-25T00:00:00Z",
                 "prompt_version": "coder-v1"})


def dispatch_done(eid: str = "STORY-NNN-coder-1-1758800000",
                  provenance: str = "live") -> dict:
    return norm({"event": "dispatch", "event_id": eid,
                 "recorded_at": "2026-10-25T01:00:00Z",
                 "provenance": provenance,
                 "story_id": STORY, "role": "coder",
                 "requested_model": "model-a", "reported_model": "model-a",
                 "model_unverified": False, "attempt": 1,
                 "started_at": "2026-10-25T00:00:00Z",
                 "ended_at": "2026-10-25T01:00:00Z", "outcome": "success",
                 "prompt_version": "coder-v1"})


def write_jsonl(path: Path, events: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


class TestUtcNow(unittest.TestCase):
    def test_utc_z_format(self):
        self.assertRegex(L.utcnow(), UTC_RE)


class TestLoad(unittest.TestCase):
    def test_missing_file_is_empty_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(L.load_ledger(Path(td) / "nope.jsonl"), [])
            self.assertEqual(L.load_events(Path(td) / "nope.jsonl"), [])

    def test_bad_json_refuses_with_path_and_line(self):
        with self.assertRaises(L.LedgerError) as cm:
            L.load_ledger(os.path.join(FIXTURES, "bad_json.jsonl"))
        self.assertIn("bad_json.jsonl:1: invalid JSON:", str(cm.exception))

    def test_good_fixture_loads(self):
        events = L.load_events(os.path.join(FIXTURES, "good_ledger.jsonl"))
        self.assertEqual(len(events), 4)
        self.assertEqual(L.validate_ledger(
            os.path.join(FIXTURES, "good_ledger.jsonl")), [])

    def test_duplicate_non_dispatch_id_refused_at_load(self):
        with self.assertRaises(L.LedgerError) as cm:
            L.load_events(os.path.join(FIXTURES, "duplicate_ids.jsonl"))
        self.assertIn("duplicate event_id 'transition-STORY-NNN-1'",
                      str(cm.exception))

    def test_blank_lines_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            p.write_text(json.dumps(transition("t-1", "PLANNED"))
                         + "\n\n" + json.dumps(
                             transition("t-2", "CODING", "PLANNED")) + "\n",
                         encoding="utf-8")
            self.assertEqual(len(L.load_events(p)), 2)


class TestLoadLifecycleLaws(unittest.TestCase):
    """Cross-row dispatch laws enforced at load (schema authority:
    ledger_schema.validate_events — I/O must surface them)."""

    def test_prespawn_then_completion_ok(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            write_jsonl(p, [dispatch_pre(), dispatch_done()])
            self.assertEqual(len(L.load_events(p)), 2)

    def test_reversed_order_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            write_jsonl(p, [dispatch_done(), dispatch_pre()])
            with self.assertRaises(L.LedgerError) as cm:
                L.load_events(p)
            # completion-first means the second row is the pre-spawn row —
            # "a pre-spawn row may only be followed by its completion"
            self.assertIn("second row lacks ended_at", str(cm.exception))

    def test_stale_restart_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            write_jsonl(p, [dispatch_done(), dispatch_done()])
            with self.assertRaises(L.LedgerError) as cm:
                L.load_events(p)
            self.assertIn("re-started after end", str(cm.exception))

    def test_orphan_live_completion_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            write_jsonl(p, [dispatch_done()])
            with self.assertRaises(L.LedgerError) as cm:
                L.load_events(p)
            self.assertIn("single completed row must be provenance=backfilled",
                          str(cm.exception))

    def test_backfilled_single_completion_ok(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            write_jsonl(p, [dispatch_done(provenance="backfilled")])
            self.assertEqual(len(L.load_events(p)), 1)

    def test_three_rows_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            write_jsonl(p, [dispatch_pre(), dispatch_done(), dispatch_done()])
            with self.assertRaises(L.LedgerError) as cm:
                L.load_events(p)
            self.assertIn("has 3 rows", str(cm.exception))


class TestAppend(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            L.append_event(p, transition("t-1", "PLANNED"))
            events = L.load_events(p)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_id"], "t-1")
            # exactly one JSON line, LF, trailing newline
            raw = p.read_bytes()
            self.assertTrue(raw.endswith(b"\n"))
            self.assertNotIn(b"\r", raw)
            self.assertEqual(len(raw.splitlines()), 1)

    def test_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ledger" / "deep" / "events.jsonl"
            L.append_event(p, transition("t-1", "PLANNED"))
            self.assertTrue(p.exists())

    def test_validation_refusal_leaves_file_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            L.append_event(p, transition("t-1", "PLANNED"))
            before = p.read_bytes()
            bad = transition("t-2", "CODING", "PLANNED")
            del bad["to_state"]
            with self.assertRaises(L.LedgerError) as cm:
                L.append_event(p, bad)
            self.assertIn("missing required field `to_state`",
                          str(cm.exception))
            self.assertEqual(p.read_bytes(), before)
            # refusal before the file exists creates nothing
            q = Path(td) / "never" / "events.jsonl"
            with self.assertRaises(L.LedgerError):
                L.append_event(q, {"event": "bogus"})
            self.assertFalse(q.exists())

    def test_unknown_event_type_refused(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(L.LedgerError) as cm:
                L.append_event(Path(td) / "e.jsonl", {"event": "bogus"})
            self.assertEqual("unknown event type 'bogus'", str(cm.exception))

    def test_non_dict_refused(self):
        with tempfile.TemporaryDirectory() as td:
            not_a_dict = ["not", "a", "dict"]
            with self.assertRaises(L.LedgerError):
                L.append_event(Path(td) / "e.jsonl", not_a_dict)  # type: ignore[arg-type]

    def test_auto_event_id_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            evt = transition("", "PLANNED")
            del evt["event_id"]
            L.append_event(p, evt)
            events = L.load_ledger(p)
            self.assertEqual(events[0]["event_id"],
                             f"transition-{STORY}-1")

    def test_defaults_recorded_at_utc_and_provenance_live(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            evt = transition("t-1", "PLANNED")
            del evt["recorded_at"]
            del evt["provenance"]
            L.append_event(p, evt)
            stored = L.load_ledger(p)[0]
            self.assertRegex(stored["recorded_at"], UTC_RE)
            self.assertEqual(stored["provenance"], "live")

    def test_dispatch_requires_prespawn_id(self):
        with tempfile.TemporaryDirectory() as td:
            evt = dispatch_pre()
            del evt["event_id"]
            with self.assertRaises(L.LedgerError) as cm:
                L.append_event(Path(td) / "e.jsonl", evt)
            self.assertIn("pre-spawn dispatch_id", str(cm.exception))

    def test_dispatch_lifecycle_append_then_complete(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            L.append_event(p, dispatch_pre())
            L.append_event(p, dispatch_done())
            self.assertEqual(len(L.load_events(p)), 2)

    def test_stale_restart_blocked_at_append(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            L.append_event(p, dispatch_pre())
            L.append_event(p, dispatch_done())
            stale = dispatch_pre()  # ended dispatch, pre-spawn row again
            with self.assertRaises(L.LedgerError) as cm:
                L.append_event(p, stale)
            self.assertIn("stale notifications cannot re-start it",
                          str(cm.exception))
            self.assertEqual(len(L.load_ledger(p)), 2)

    def test_seen_ended_helper(self):
        ended = dispatch_done()
        self.assertEqual(L.seen_ended([ended], dispatch_pre()), ended["event_id"])
        self.assertEqual(L.seen_ended([dispatch_pre()], dispatch_done()), "")
        self.assertEqual(L.seen_ended([ended], transition("t", "PLANNED")), "")


class TestReexports(unittest.TestCase):
    def test_schema_is_the_authority(self):
        self.assertIs(L.validate_event, S.validate_event)
        self.assertIs(L.validate_events, S.validate_events)
        self.assertEqual(L.EVENT_TYPES, S.EVENT_TYPES)

    def test_helper_queries_over_loaded_ledger(self):
        events = L.load_events(os.path.join(FIXTURES, "good_ledger.jsonl"))
        self.assertEqual(L.current_state(events, STORY), "CODING")
        self.assertEqual(len(L.events_for(events, STORY, "transition")), 2)
        self.assertEqual(L.latest_findings(events), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
