#!/usr/bin/env python3
"""Tests for tools/metrics_report.py — deterministic token/efficiency
report: totals + per-story + per-lane + per-role tables (tokens only — no
cost, no API-call column), the unrouted-dispatch census (null
requested_model = unrouted, never guessed), default output path
factory/metrics/TOKEN-METRICS.md, explicit --snapshot (never wall-clock
auto-named), the baseline loop (--freeze / --diff incl. the reasoning-loop
guard), determinism (byte-identical for the same ledger), and CLI
refusals.

Fixtures: tools/tests/fixtures/render/sample_events.json via
ledger_lib.append_event. No network, no writes outside tempdirs.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools import ledger_lib as L
from tools import metrics_report as MT
from tools import render_state as RS
from tools.tests.test_render_state import build_ledger


class MetricsReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        (cls.root / "workspace.toml").write_text(
            'workspace = "metrics-ws"\n', encoding="utf-8")
        cls.ledger = build_ledger(cls.root)
        cls.events = L.load_events(cls.ledger)
        cls.header = RS.generated_header("factory/ledger/events.jsonl")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    # ---- collect / totals
    def test_collect_totals_tokens_only(self):
        c = MT.collect(self.events)
        t = c["totals"]
        self.assertEqual(t["dispatches"], 2)
        self.assertEqual(t["usage_rows"], 1)
        self.assertEqual(t["input_tokens"], 1000)
        self.assertEqual(t["output_tokens"], 200)
        self.assertEqual(t["cached_input_tokens"], 50)
        self.assertEqual(t["loops"], 2)
        # no cost/call fields anywhere in the totals
        for banned in ("cost", "credit", "rate", "calls", "api_calls"):
            self.assertNotIn(banned, t)

    def test_two_row_dispatch_lifecycle_counts_once(self):
        c = MT.collect(self.events)
        self.assertEqual(c["per_story"]["STORY-201"]["dispatches"], 1)
        self.assertEqual(c["per_story"]["STORY-201"]["routed"], 1)
        self.assertEqual(c["per_story"]["STORY-201"]["served_model_known"], 1)

    def test_unrouted_census(self):
        c = MT.collect(self.events)
        st = c["per_story"]["STORY-202"]
        self.assertEqual(st["unrouted"], 1)
        self.assertEqual(st["routed"], 0)
        self.assertEqual(st["unrouted_ids"], ["STORY-202-planner-1-1789001000"])
        text = MT.render_md(c, "t", self.header, len(self.events))
        self.assertIn("## Unrouted-dispatch census", text)
        self.assertIn("requested_model was not recorded", text)
        self.assertIn("- STORY-202: STORY-202-planner-1-1789001000", text)

    def test_render_md_title_and_tables(self):
        c = MT.collect(self.events)
        text = MT.render_md(
            c, f"{RS.workspace_name(self.root)} — Token & Efficiency Metrics",
            self.header, len(self.events))
        self.assertTrue(text.startswith(self.header))
        self.assertIn("# metrics-ws — Token & Efficiency Metrics", text)
        self.assertIn("## Totals", text)
        self.assertIn("## Per story", text)
        self.assertIn("## Per story × lane (input tokens)", text)
        self.assertIn("## Per role", text)
        self.assertIn("| STORY-201 | 1 | 1,000 | 200 | 1/1 | 1/1 | 1/1 |",
                      text)
        self.assertIn("reasoning:1", text)
        # lane grouping
        self.assertIn("| build |", text.replace(" |", " |"))
        self.assertIn("plan", text)
        self.assertNotIn("Calls", text)  # no API-call column
        self.assertNotIn("cost", text.lower())

    def test_determinism_no_wall_clock(self):
        c = MT.collect(self.events)
        a = MT.render_md(c, "t", self.header, 21)
        b = MT.render_md(MT.collect(self.events), "t", self.header, 21)
        self.assertEqual(a, b)
        sa = MT.snapshot_bytes(MT.to_jsonable(c))
        sb = MT.snapshot_bytes(MT.to_jsonable(MT.collect(self.events)))
        self.assertEqual(sa, sb)
        self.assertNotIn(b"generated_at", sa)
        self.assertTrue(sa.endswith(b"\n"))
        # sorted keys
        doc = json.loads(sa)
        self.assertEqual(list(doc.keys()),
                         ["per_role", "per_story", "totals"])

    # ---- CLI
    def test_cli_default_output_and_explicit_snapshot(self):
        rc = MT.main(["--root", str(self.root)])
        self.assertEqual(rc, 0)
        md = self.root / "factory" / "metrics" / "TOKEN-METRICS.md"
        self.assertTrue(md.exists())
        text = md.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(self.header))
        self.assertIn("metrics-ws — Token & Efficiency Metrics", text)
        self.assertTrue(text.endswith("\n"))
        # no auto-dated snapshot: metrics dir holds ONLY the md
        self.assertEqual(sorted(p.name for p in md.parent.iterdir()),
                         ["TOKEN-METRICS.md"])
        # explicit snapshot path only
        with tempfile.TemporaryDirectory() as td:
            sp = Path(td) / "snap" / "custom.json"
            rc = MT.main(["--root", str(self.root), "--snapshot", str(sp)])
            self.assertEqual(rc, 0)
            self.assertTrue(sp.exists())
            doc = json.loads(sp.read_text(encoding="utf-8"))
            self.assertIn("totals", doc)

    def test_freeze_then_diff_loop(self):
        metrics = self.root / "factory" / "metrics"
        self.assertEqual(MT.main(["--root", str(self.root),
                                  "--freeze", "baseline-a"]), 0)
        base = metrics / "baseline-a.json"
        self.assertTrue(base.exists())
        # diff against the frozen baseline: zero deltas, guard OK
        out_path = Path(tempfile.mkdtemp()) / "diff.txt"
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = MT.main(["--root", str(self.root),
                          "--diff", str(base)])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("# Delta vs baseline", out)
        self.assertIn("| TOTAL input_tokens | 1,000 | 1,000 | +0 (+0%) |",
                      out)
        self.assertIn("Reasoning-loop guard: baseline 1 → current 1 — OK",
                      out)
        # per-story scope
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = MT.main(["--root", str(self.root), "--diff", str(base),
                          "--story", "STORY-201"])
        self.assertEqual(rc, 0)
        self.assertIn("| STORY-201 input_tokens | 1,000 | 1,000 | +0 (+0%) |",
                      buf.getvalue())

    def test_diff_regression_verdict(self):
        base = {"totals": {"input_tokens": 100, "output_tokens": 10,
                           "dispatches": 1},
                "per_story": {"S": {"loops": {"reasoning": 0}}}}
        cur = {"totals": {"input_tokens": 50, "output_tokens": 5,
                          "dispatches": 1},
               "per_story": {"S": {"loops": {"reasoning": 3}}}}
        out = MT.diff(base, cur, "S")
        self.assertIn("REGRESSION — reasoning loops rose", out)

    def test_diff_unreadable_baseline_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "nope.json"
            rc = MT.main(["--root", str(self.root), "--diff", str(missing)])
            self.assertEqual(rc, 1)

    # ---- null dispatch_id usage rows never collapse (B9)
    def test_null_dispatch_id_usage_rows_all_counted(self):
        def usage(eid, did, it, ot):
            return {"event": "usage", "event_id": eid, "dispatch_id": did,
                    "input_tokens": it, "output_tokens": ot,
                    "cached_input_tokens": None,
                    "cached_input_tokens_reason": "n/a",
                    "thinking_tokens": None, "thinking_tokens_reason": "n/a",
                    "token_semantics": "tokens",
                    "usage_source": "executor-report",
                    "recorded_at": "2026-09-01T00:00:00Z",
                    "provenance": "live"}
        events = [usage("usage-1", None, 100, 10),
                  usage("usage-2", None, 200, 20)]
        c = MT.collect(events)
        self.assertEqual(c["totals"]["usage_rows"], 2)
        self.assertEqual(c["totals"]["input_tokens"], 300)
        self.assertEqual(c["totals"]["output_tokens"], 30)
        # and the by-id join dict stays free of a "None" key
        snap = MT.to_jsonable(c)
        self.assertEqual(snap["totals"]["usage_rows"], 2)

    # ---- requested model is not a served model (B10)
    def test_requested_model_never_counted_as_served(self):
        dispatch = {
            "event": "dispatch", "event_id": "STORY-230-coder-1-1789003000",
            "story_id": "STORY-230", "role": "coder",
            "requested_model": "model-y", "reported_model": None,
            "reported_model_reason": "executor report carried no model id",
            "thinking_setting": None, "thinking_setting_reason": "n/a",
            "session_id": None, "session_id_reason": "pre-spawn row",
            "parent_session_id": None, "parent_session_id_reason": "top",
            "attempt": 1, "started_at": "2026-09-01T01:00:00Z",
            "ended_at": None, "ended_at_reason": "pre-spawn row",
            "outcome": None, "outcome_reason": "pre-spawn row",
            "prompt_version": "coder-v0.1.0", "worktree": "/tmp/wt",
            "model_unverified": True,
            "recorded_at": "2026-09-01T01:00:00Z", "provenance": "live"}
        c = MT.collect([dispatch])
        self.assertEqual(dict(c["per_role"]["coder"]["models"]),
                         {"(unreported)": 1})
        text = MT.render_md(c, "t", self.header, 1)
        self.assertIn("(unreported)×1", text)
        self.assertNotIn("model-y×", text)

    # ---- reasoning-loop guard scoping under --story (B11)
    def test_guard_scopes_per_story_never_all(self):
        base = {"totals": {}, "per_role": {},
                "per_story": {"A": {"loops": {"reasoning": 5}},
                              "B": {"loops": {"reasoning": 1}}}}
        cur = {"totals": {}, "per_role": {},
               "per_story": {"A": {"loops": {"reasoning": 5}}}}
        # B absent from current → B contributes 0, never the all-story 5
        out = MT.diff(base, cur, "B")
        self.assertIn("Reasoning-loop guard: baseline 1 → current 0 — OK",
                      out)

    # ---- malformed baseline = documented exit-1 refusal (B12)
    def _diff_rc(self, baseline_doc) -> tuple[int, str]:
        import contextlib, io
        with tempfile.TemporaryDirectory() as td:
            bp = Path(td) / "base.json"
            bp.write_text(json.dumps(baseline_doc), encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = MT.main(["--root", str(self.root),
                              "--diff", str(bp)])
        return rc, err.getvalue()

    def test_malformed_baseline_refusals(self):
        rc, err = self._diff_rc([])
        self.assertEqual(rc, 1)
        self.assertIn("error: malformed baseline snapshot: top level must "
                      "be an object, got list", err)
        rc, err = self._diff_rc({"totals": {"input_tokens": "invalid"},
                                 "per_story": {}, "per_role": {}})
        self.assertEqual(rc, 1)
        # token totals may be null (unknown) but never a non-numeric string
        self.assertIn("error: malformed baseline snapshot: "
                      "`totals.input_tokens` must be numeric or null, "
                      "got 'invalid'", err)
        rc, err = self._diff_rc({"per_story": {}, "per_role": {}})
        self.assertEqual(rc, 1)
        self.assertIn("error: malformed baseline snapshot: missing "
                      "`totals` section", err)

    def test_valid_baseline_still_renders_delta(self):
        import contextlib, io
        with tempfile.TemporaryDirectory() as td:
            bp = Path(td) / "base.json"
            bp.write_bytes(MT.snapshot_bytes(MT.to_jsonable(
                MT.collect(self.events))))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = MT.main(["--root", str(self.root), "--diff", str(bp)])
        self.assertEqual(rc, 0)
        self.assertIn("# Delta vs baseline", buf.getvalue())
        self.assertIn("| TOTAL input_tokens | 1,000 | 1,000 | +0 (+0%) |",
                      buf.getvalue())

    def test_bad_ledger_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.jsonl"
            bad.write_text("{not json}\n", encoding="utf-8")
            self.assertEqual(
                MT.main(["--root", str(self.root), "--ledger", str(bad)]), 2)

    def test_title_generic_without_workspace_toml(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            root.joinpath("factory", "ledger").mkdir(parents=True)
            (root / "factory" / "ledger" / "events.jsonl").write_bytes(
                self.ledger.read_bytes())
            self.assertEqual(MT.main(["--root", str(root)]), 0)
            text = (root / "factory" / "metrics" /
                    "TOKEN-METRICS.md").read_text(encoding="utf-8")
            self.assertIn("# factory — Token & Efficiency Metrics", text)

    # ---- round-20 U1: multi-row dispatches aggregate identically at
    # every scope (sum everywhere — never last-row-wins per dispatch)
    _DISPATCH_U1 = {
        "event": "dispatch", "event_id": "STORY-U1-coder-1-1789004000",
        "story_id": "STORY-U1", "role": "coder",
        "requested_model": "model-x", "reported_model": "model-x",
        "model_unverified": False}

    @staticmethod
    def _usage_u(eid, it, ot, it_reason=None, ot_reason=None):
        return {"event": "usage", "event_id": eid,
                "dispatch_id": "STORY-U1-coder-1-1789004000",
                "input_tokens": it, "input_tokens_reason": it_reason,
                "output_tokens": ot, "output_tokens_reason": ot_reason,
                "cached_input_tokens": None,
                "cached_input_tokens_reason": "not itemized"}

    def test_multi_usage_rows_sum_at_every_scope(self):
        c = MT.collect([self._DISPATCH_U1,
                        self._usage_u("u1", 100, 10),
                        self._usage_u("u2", 200, 20)])
        self.assertEqual(c["totals"]["input_tokens"], 300)
        st = c["per_story"]["STORY-U1"]
        self.assertEqual(st["input_tokens"], 300)
        self.assertEqual(st["output_tokens"], 30)
        self.assertEqual(st["per_lane"]["build"]["input_tokens"], 300)
        self.assertEqual(c["per_role"]["coder"]["input_tokens"], 300)
        self.assertEqual(c["per_role"]["coder"]["output_tokens"], 30)
        # a SINGLE usage row still lands unchanged (no fixture drift)
        c1 = MT.collect([self._DISPATCH_U1, self._usage_u("u1", 100, 10)])
        self.assertEqual(c1["totals"]["input_tokens"], 100)
        self.assertEqual(c1["per_story"]["STORY-U1"]["input_tokens"], 100)
        self.assertEqual(c1["per_role"]["coder"]["input_tokens"], 100)
        # dispatches counted once regardless of usage-row count
        self.assertEqual(c["totals"]["dispatches"], 1)
        self.assertEqual(st["dispatches"], 1)
        self.assertEqual(c["totals"]["usage_rows"], 2)

    # ---- round-20 U2: unknown token counts preserved end-to-end
    def test_null_counts_stay_unknown_never_zero(self):
        c = MT.collect([self._DISPATCH_U1,
                        self._usage_u("u1", None, 5, "provider withheld")])
        self.assertIsNone(c["totals"]["input_tokens"])
        self.assertEqual(c["totals"]["input_tokens_unknown"], 1)
        self.assertEqual(c["totals"]["input_tokens_reasons"],
                         {"provider withheld": 1})
        self.assertIsNone(c["per_story"]["STORY-U1"]["input_tokens"])
        self.assertIsNone(c["per_role"]["coder"]["input_tokens"])
        # a MEASURED zero stays a numeric zero — not unknown
        c0 = MT.collect([self._DISPATCH_U1, self._usage_u("u0", 0, 0)])
        self.assertEqual(c0["totals"]["input_tokens"], 0)
        self.assertNotIn("input_tokens_unknown", c0["totals"])

    def test_partial_totals_sum_known_and_mark_unknown_rows(self):
        c = MT.collect([self._DISPATCH_U1,
                        self._usage_u("u1", 100, 10),
                        self._usage_u("u2", None, 5, "executor truncated")])
        t = c["totals"]
        self.assertEqual(t["input_tokens"], 100)
        self.assertEqual(t["input_tokens_unknown"], 1)
        md = MT.render_md(c, "t", self.header, 3)
        self.assertIn("| input_tokens | 100 partial (1 unknown rows) |", md)

    def test_report_renders_unknown_with_reason(self):
        c = MT.collect([self._DISPATCH_U1,
                        self._usage_u("u1", None, 5, "provider withheld")])
        md = MT.render_md(c, "t", self.header, 2)
        self.assertIn("| input_tokens | unknown (provider withheld) |", md)
        self.assertIn("| STORY-U1 | 1 | unknown (provider withheld) | 5 |",
                      md)
        self.assertIn("| coder | 1 | unknown (provider withheld) | 5 |", md)
        # never a bare numeric zero for the unknown field
        self.assertNotIn("| input_tokens | 0 |", md)

    def test_snapshot_round_trip_keeps_null(self):
        c = MT.collect([self._DISPATCH_U1,
                        self._usage_u("u1", None, 5, "provider withheld")])
        snap = MT.to_jsonable(c)
        payload = MT.snapshot_bytes(snap)
        doc = json.loads(payload)
        self.assertIsNone(doc["totals"]["input_tokens"])
        self.assertEqual(doc["totals"]["input_tokens_reasons"],
                         {"provider withheld": 1})
        self.assertIsNone(doc["per_story"]["STORY-U1"]["input_tokens"])
        # the snapshot survives baseline validation and re-parses to the
        # same bytes (deterministic null serialization)
        self.assertIsNone(MT.validate_baseline(doc))
        self.assertEqual(MT.snapshot_bytes(doc), payload)

    def test_diff_never_claims_delta_against_unknown(self):
        known = {"totals": {"input_tokens": 500, "output_tokens": 10,
                            "dispatches": 1},
                 "per_story": {}, "per_role": {}}
        c_unknown = MT.collect([self._DISPATCH_U1,
                                self._usage_u("u1", None, 5,
                                              "provider withheld"),
                                self._usage_u("u2", None, 5,
                                              "provider withheld")])
        cur = {"totals": c_unknown["totals"], "per_story": {},
               "per_role": {}}
        out = MT.diff(known, cur, None)
        self.assertIn("| TOTAL input_tokens | 500 | unknown (provider "
                      "withheld ×2) | unknown |", out)
        self.assertNotIn("-500", out)
        # partial vs known → partial, still no numeric delta
        c_partial = MT.collect([self._DISPATCH_U1,
                                self._usage_u("u1", 100, 10),
                                self._usage_u("u2", None, 5, "truncated")])
        out2 = MT.diff(known, {"totals": c_partial["totals"],
                               "per_story": {}, "per_role": {}}, None)
        self.assertIn("| TOTAL input_tokens | 500 | 100 partial "
                      "(1 unknown rows) | partial |", out2)
        self.assertNotIn("-400", out2)
        # both sides known → numeric delta unchanged
        out3 = MT.diff(known, {"totals": {"input_tokens": 400,
                                          "output_tokens": 10,
                                          "dispatches": 1},
                               "per_story": {}, "per_role": {}}, None)
        self.assertIn("| TOTAL input_tokens | 500 | 400 | -100 (-20%) |",
                      out3)


class MetricsCellSweepTests(unittest.TestCase):
    """Round 32 S2: ledger-sourced free-text cells (story ids, roles,
    models, loop classes, token reasons) are escaped before insertion
    into every metrics table — a pipe/newline must not add columns or
    split rows."""

    def test_cell_helper_identical_to_render_state(self):
        for v in ("a|b", "a\r\nb", "a\rb", "a\nb", "plain"):
            self.assertEqual(MT._cell(v), RS._cell(v), v)

    def test_token_reason_pipe_escaped_in_totals_row(self):
        usage = {
            "event": "usage", "event_id": "usage-STORY-219-coder-1",
            "dispatch_id": "STORY-219-coder-1-1789009000",
            "input_tokens": None,
            "input_tokens_reason": "executor said a|b\nc",
            "output_tokens": None,
            "output_tokens_reason": "executor said a|b\nc",
            "cached_input_tokens": None,
            "cached_input_tokens_reason": "n/a",
            "thinking_tokens": None, "thinking_tokens_reason": "n/a",
            "token_semantics": "tokens", "usage_source": "executor-report",
            "recorded_at": "2026-09-01T02:00:00Z", "provenance": "live"}
        dispatch = {
            "event": "dispatch", "event_id": "STORY-219-coder-1-1789009000",
            "story_id": "STORY-219", "role": "co|der",
            "requested_model": "mod|el-a\nv1", "reported_model": "mod|el-a\nv1",
            "thinking_setting": "on", "session_id": "s",
            "parent_session_id": "p", "attempt": 1,
            "started_at": "2026-09-01T01:00:00Z",
            "ended_at": "2026-09-01T02:00:00Z", "outcome": "done",
            "prompt_version": "v1", "worktree": "w",
            "model_unverified": False,
            "recorded_at": "2026-09-01T02:00:00Z", "provenance": "live"}
        c = MT.collect([dispatch, usage])
        md = MT.render_md(c, "t", "H", 2)
        trow = next(l for l in md.splitlines()
                    if l.startswith("| input_tokens |"))
        self.assertIn("unknown (executor said a\\|b c)", trow)
        self.assertEqual(trow.replace("\\|", "").count("|"), 3)
        # per-role row: role and model cells escaped (5 declared columns)
        rrow = next(l for l in md.splitlines() if l.startswith("| co\\|der |"))
        self.assertIn("mod\\|el-a v1×1", rrow)
        self.assertEqual(rrow.replace("\\|", "").count("|"), 6)
        # per-story row keeps its 9 declared columns
        srow = next(l for l in md.splitlines()
                    if l.startswith("| STORY-219 |"))
        self.assertEqual(srow.replace("\\|", "").count("|"), 10)
        # per-lane header cell for the role's lane is escaped
        self.assertIn("other", md)
        # the rows were never split by the embedded newlines
        self.assertNotIn("v1 |", md.replace("mod\\|el-a v1×1", ""))

    def test_plain_metrics_rows_unchanged(self):
        c = MT.collect(json.loads(json.dumps([])))
        md = MT.render_md(c, "t", "H", 0)
        self.assertNotIn("\\|", md)


if __name__ == "__main__":
    unittest.main(verbosity=2)
