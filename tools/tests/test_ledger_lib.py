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


class TestUnreadableLedgerWrapping(unittest.TestCase):
    """R7-H2: load_events must wrap read failures (invalid UTF-8, a
    directory passed as the ledger path) as named LedgerErrors so the
    controller/render/CLI refusal handlers see them — raw decoding or
    filesystem exceptions never bypass the boundary."""

    def test_directory_path_raises_ledger_error(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(L.LedgerError) as cm:
                L.load_events(Path(td))
            self.assertIn("ledger unreadable:", str(cm.exception))

    def test_invalid_utf8_raises_ledger_error(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            p.write_bytes(b'{"event": "loop", "x": "\xff\xfe\x80"}\n')
            with self.assertRaises(L.LedgerError) as cm:
                L.load_events(p)
            self.assertIn("ledger unreadable:", str(cm.exception))

    def test_load_ledger_raw_behavior_unchanged(self):
        """load_ledger keeps its documented raw behavior: a directory
        path propagates the OSError, invalid UTF-8 the decode error."""
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(OSError):
                L.load_ledger(Path(td))
            p = Path(td) / "events.jsonl"
            p.write_bytes(b"\xff\xfe\x80\n")
            with self.assertRaises(UnicodeDecodeError):
                L.load_ledger(p)

    def test_normal_ledgers_unaffected(self):
        events = L.load_events(os.path.join(FIXTURES, "good_ledger.jsonl"))
        self.assertEqual(len(events), 4)
        with self.assertRaises(L.LedgerError) as cm:
            L.load_events(os.path.join(FIXTURES, "bad_json.jsonl"))
        self.assertIn("invalid JSON", str(cm.exception))


class TestValidateLedgerParseFailures(unittest.TestCase):
    """R7-H3: validate_ledger promises a non-raising list — parse
    failures (invalid JSON, invalid UTF-8) come back as findings; only
    filesystem errors (OSError) propagate."""

    def test_bad_json_returns_finding_list(self):
        errs = L.validate_ledger(os.path.join(FIXTURES, "bad_json.jsonl"))
        self.assertIsInstance(errs, list)
        self.assertEqual(len(errs), 1)
        self.assertIn("bad_json.jsonl:1: invalid JSON:", errs[0])

    def test_invalid_utf8_returns_finding_list(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            p.write_bytes(b"\xff\xfe\x80\n")
            errs = L.validate_ledger(p)
            self.assertEqual(len(errs), 1)
            self.assertIn("ledger unreadable:", errs[0])

    def test_directory_path_still_propagates_oserror(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(OSError):
                L.validate_ledger(Path(td))

    def test_good_ledger_still_valid(self):
        self.assertEqual(L.validate_ledger(
            os.path.join(FIXTURES, "good_ledger.jsonl")), [])


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

    def test_auto_event_id_skips_existing_ids(self):
        """R8-I1: an explicitly present id can occupy the row-count
        suffix (one row carrying `<type>-<story>-2`) — the old
        row-count allocation emitted that DUPLICATE and the append was
        refused by the candidate-ledger law. Auto ids now increment
        until the candidate is unused, never emitting a present id."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            write_jsonl(p, [transition(f"transition-{STORY}-2", "PLANNED")])
            evt = transition("", "CODING", "PLANNED")
            del evt["event_id"]
            L.append_event(p, evt)
            self.assertEqual(L.load_events(p)[-1]["event_id"],
                             f"transition-{STORY}-3")

    def test_auto_event_id_explicit_prefix_ids_no_collision(self):
        """The spec case: explicit `-1` and `-2` present → the next
        auto-allocation is `-3`, with no duplicate."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            write_jsonl(p, [transition(f"transition-{STORY}-1", "PLANNED"),
                            transition(f"transition-{STORY}-2", "CODING",
                                       "PLANNED")])
            evt = transition("", "BUILD_GREEN", "CODING")
            del evt["event_id"]
            L.append_event(p, evt)
            events = L.load_events(p)
            self.assertEqual(events[-1]["event_id"],
                             f"transition-{STORY}-3")

    def test_auto_event_id_skips_gap_and_overlap(self):
        """Explicit ids AHEAD of the row count (a gap `-5` with one row)
        still never collide: the allocation skips every used suffix."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            write_jsonl(p, [transition(f"transition-{STORY}-5", "PLANNED")])
            evt = transition("", "CODING", "PLANNED")
            del evt["event_id"]
            L.append_event(p, evt)
            # 1 row → base suffix 2; `-2` unused → allocated as-is
            self.assertEqual(L.load_events(p)[-1]["event_id"],
                             f"transition-{STORY}-2")
            # next auto: 2 rows → base 3, but `-5` is taken further on;
            # `-3` itself is free → allocated
            evt2 = transition("", "BUILD_GREEN", "CODING")
            del evt2["event_id"]
            L.append_event(p, evt2)
            self.assertEqual(L.load_events(p)[-1]["event_id"],
                             f"transition-{STORY}-3")

    def test_auto_event_id_other_story_ids_unaffected(self):
        """The skip-existing scan is per generated candidate — ids of
        OTHER stories/types never occupy this story's sequence beyond
        the exact-collision case."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            write_jsonl(p, [transition(f"transition-{STORY}-1", "PLANNED"),
                            transition("transition-OTHER-1", "PLANNED",
                                       story="OTHER")])
            evt = transition("", "CODING", "PLANNED")
            del evt["event_id"]
            L.append_event(p, evt)
            # 1 row for STORY → base suffix 2, `-2` unused despite the
            # other story's `-1`
            self.assertEqual(L.load_events(p)[-1]["event_id"],
                             f"transition-{STORY}-2")

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


class TestAppendCandidateLedger(unittest.TestCase):
    """F1: append_event validates the CANDIDATE ledger (existing rows +
    the new row) with the same machinery load uses — cross-row laws are
    enforced at append time, and a refusal never touches the file."""

    def test_duplicate_non_dispatch_id_refused_at_append(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            L.append_event(p, transition("t-1", "PLANNED"))
            before = p.read_bytes()
            with self.assertRaises(L.LedgerError) as cm:
                L.append_event(p, transition("t-1", "PLANNED"))
            self.assertIn("candidate ledger rejected:", str(cm.exception))
            self.assertIn("duplicate event_id 't-1'", str(cm.exception))
            self.assertEqual(p.read_bytes(), before)  # nothing written

    def test_second_dispatch_completion_refused_at_append(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            L.append_event(p, dispatch_pre())
            L.append_event(p, dispatch_done())
            before = p.read_bytes()
            with self.assertRaises(L.LedgerError) as cm:
                L.append_event(p, dispatch_done())
            self.assertIn("candidate ledger rejected:", str(cm.exception))
            self.assertIn("has 3 rows", str(cm.exception))
            self.assertEqual(p.read_bytes(), before)

    def test_orphan_live_completion_refused_at_append(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            with self.assertRaises(L.LedgerError) as cm:
                L.append_event(p, dispatch_done())  # live, no pre-spawn row
            self.assertIn("candidate ledger rejected:", str(cm.exception))
            self.assertIn("single completed row must be provenance=backfilled",
                          str(cm.exception))
            self.assertFalse(p.exists())

    def test_backfilled_single_completion_still_appends(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            L.append_event(p, dispatch_done(provenance="backfilled"))
            self.assertEqual(len(L.load_events(p)), 1)

    def test_happy_path_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            L.append_event(p, transition("t-1", "PLANNED"))
            L.append_event(p, transition("t-2", "CODING", "PLANNED"))
            L.append_event(p, dispatch_pre())
            L.append_event(p, dispatch_done())
            self.assertEqual(len(L.load_events(p)), 4)

    def test_stale_restart_keeps_specialized_message(self):
        """The pre-existing dispatch guard fires BEFORE the candidate
        check, so its specialized message is preserved."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            L.append_event(p, dispatch_pre())
            L.append_event(p, dispatch_done())
            with self.assertRaises(L.LedgerError) as cm:
                L.append_event(p, dispatch_pre())
            self.assertIn("stale notifications cannot re-start it",
                          str(cm.exception))


class TestAppendTrailingNewline(unittest.TestCase):
    """F2: a ledger file missing its trailing newline refuses appends
    (fail closed) — the next object would land on the same line."""

    def test_missing_terminator_refused_then_repaired(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            p.write_text(json.dumps(transition("t-1", "PLANNED")),
                         encoding="utf-8", newline="\n")  # no trailing \n
            before = p.read_bytes()
            with self.assertRaises(L.LedgerError) as cm:
                L.append_event(p, transition("t-2", "CODING", "PLANNED"))
            self.assertIn("missing its trailing newline", str(cm.exception))
            self.assertIn("repair the file before appending",
                          str(cm.exception))
            self.assertEqual(p.read_bytes(), before)  # file untouched
            # repair the terminator, then the append succeeds
            p.write_bytes(before + b"\n")
            L.append_event(p, transition("t-2", "CODING", "PLANNED"))
            events = L.load_events(p)
            self.assertEqual(len(events), 2)
            self.assertEqual(len(p.read_bytes().splitlines()), 2)

    def test_empty_file_not_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            p.write_bytes(b"")  # empty, no terminator — legal
            L.append_event(p, transition("t-1", "PLANNED"))
            self.assertEqual(len(L.load_events(p)), 1)


class TestUnknownFieldRejection(unittest.TestCase):
    """S2 field-set strictness (architecture.md §Ledger event schemas —
    extra-key rejection is controller-side): only allowed TOP-LEVEL keys
    survive; nested objects stay free-form."""

    def usage_row(self, **extra) -> dict:
        return norm({"event": "usage", "event_id": "u-1",
                     "recorded_at": "2026-10-25T00:00:00Z",
                     "provenance": "live",
                     "dispatch_id": "STORY-NNN-coder-1-1758800000",
                     "input_tokens": 10, "output_tokens": 5,
                     "cached_input_tokens": 0, "thinking_tokens": 0,
                     "token_semantics": "estimated",
                     "usage_source": "provider", **extra})

    def test_cost_field_on_usage_refused_file_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            L.append_event(p, self.usage_row())
            before = p.read_bytes()
            with self.assertRaises(L.LedgerError) as cm:
                L.append_event(p, self.usage_row(estimated_credit_low=1))
            self.assertIn("unknown field: usage.estimated_credit_low",
                          str(cm.exception))
            self.assertEqual(p.read_bytes(), before)  # refused BEFORE write

    def test_unknown_key_on_transition_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            evt = transition("t-1", "PLANNED")
            evt["cost_usd"] = 0.5
            with self.assertRaises(L.LedgerError) as cm:
                L.append_event(p, evt)
            self.assertIn("unknown field: transition.cost_usd",
                          str(cm.exception))
            self.assertFalse(p.exists())

    def test_sanctioned_annotations_pass(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            claim = norm({"event": "assignment", "story_id": STORY,
                          "op": "claim",
                          "recorded_at": "2026-10-25T00:00:00Z",
                          "provenance": "live", "owner": "worker-1",
                          "host": "h", "session": "s",
                          "claimed_at": "2026-10-25T00:00:00Z",
                          "start_sha": "a" * 40,
                          "requested_by": "orchestrator",
                          "override_used": {"scope": STORY, "by": "ops",
                                            "blob_sha": "b" * 40}})
            L.append_event(p, claim)
            evt = transition("t-1", "PLANNED")
            evt["override_used"] = {"scope": "ALL", "by": "ops",
                                    "blob_sha": "c" * 40}
            L.append_event(p, evt)
            self.assertEqual(len(L.load_events(p)), 2)

    def test_nested_check_results_stays_free_form(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            evt = transition("t-1", "PLANNED")
            evt["check_results"] = {"story_md": {"ok": True},
                                    "override_used": {"ok": False},
                                    "anything_else": [1, 2, 3]}
            L.append_event(p, evt)
            self.assertEqual(L.validate_ledger(p), [])

    def test_load_side_rejects_unknown_fields(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            bad = self.usage_row(estimated_credit_low=1)
            write_jsonl(p, [bad])
            errs = L.validate_ledger(p)
            self.assertIn("unknown field: usage.estimated_credit_low", errs)
            with self.assertRaises(L.LedgerError) as cm:
                L.load_events(p)
            self.assertIn("unknown field: usage.estimated_credit_low",
                          str(cm.exception))

    def test_allowed_fields_derived_from_schema(self):
        allowed = L.allowed_fields("dispatch")
        for f in set(S.REQUIRED["dispatch"]) | S.NULLABLE["dispatch"]:
            self.assertIn(f, allowed)
        for f in S.NULLABLE["dispatch"]:
            self.assertIn(f + "_reason", allowed)
        self.assertIn("event_id", allowed)
        self.assertIn("start_sha", L.allowed_fields("assignment"))
        self.assertIn("start_shas", L.allowed_fields("assignment"))
        for op_fields in S.ASSIGNMENT_OP_FIELDS.values():
            for f in op_fields:
                self.assertIn(f, L.allowed_fields("assignment"))
        self.assertEqual(L.allowed_fields("bogus"), frozenset())


class TestLFOnlySplitting(unittest.TestCase):
    """R3-D4: load_ledger must split on LF ONLY. append_event writes
    ensure_ascii=False, so a schema-valid string field can carry a
    literal U+2028/U+2029/U+0085 — splitlines() would tear that JSON
    record apart (unreadable ledger, later appends refused)."""

    def _roundtrip(self, td: str, marker: str) -> Path:
        p = Path(td) / "events.jsonl"
        evt = transition("t-1", "PLANNED")
        evt["check_results"] = {"story_md": {"ok": True, "note":
                                             f"before{marker}after"}}
        L.append_event(p, evt)
        # exactly ONE physical LF line: the record terminator
        self.assertEqual(p.read_bytes().count(b"\n"), 1)
        # the marker landed LITERALLY (ensure_ascii=False)
        self.assertIn(marker.encode("utf-8"), p.read_bytes())
        loaded = L.load_events(p)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(
            loaded[0]["check_results"]["story_md"]["note"],
            f"before{marker}after")
        # a subsequent append still works (the ledger stayed readable)
        L.append_event(p, transition("t-2", "CODING", "PLANNED"))
        self.assertEqual(len(L.load_events(p)), 2)
        self.assertEqual(p.read_bytes().count(b"\n"), 2)
        return p

    def test_u2028_line_separator_string_survives(self):
        with tempfile.TemporaryDirectory() as td:
            self._roundtrip(td, "\u2028")

    def test_u0085_next_line_string_survives(self):
        with tempfile.TemporaryDirectory() as td:
            self._roundtrip(td, "\u0085")

    def test_splitlines_would_have_torn_the_record(self):
        """The regression's mechanism, pinned: a record containing a
        literal U+2028 splits into 2 under splitlines() but 1 under
        split("\\n") — the latter is what load_ledger must use."""
        line = json.dumps({"note": "a\u2028b"}, ensure_ascii=False)
        self.assertEqual(len(line.splitlines()), 2)
        self.assertEqual(len(line.split("\n")), 1)


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


class TestAppendValidatesExistingLedgerFirst(unittest.TestCase):
    """R16-Q1: auto-allocation of an event id must validate the EXISTING
    ledger first — a malformed row used to raise TypeError inside
    _next_seq's set construction, bypassing the documented LedgerError
    refusal."""

    def test_malformed_existing_row_named_ledger_error(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            bad = transition("t-1", "PLANNED")
            bad["event_id"] = []  # unhashable — TypeError in a set build
            write_jsonl(p, [bad])
            before = p.read_bytes()
            evt = transition("", "CODING", "PLANNED")
            del evt["event_id"]
            with self.assertRaises(L.LedgerError) as cm:  # NOT TypeError
                L.append_event(p, evt)
            self.assertIn("ledger rejected:", str(cm.exception))
            self.assertEqual(p.read_bytes(), before)  # never modified

    def test_valid_ledger_auto_allocation_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            write_jsonl(p, [transition(f"transition-{STORY}-1", "PLANNED")])
            evt = transition("", "CODING", "PLANNED")
            del evt["event_id"]
            L.append_event(p, evt)
            self.assertEqual(L.load_events(p)[-1]["event_id"],
                             f"transition-{STORY}-2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
