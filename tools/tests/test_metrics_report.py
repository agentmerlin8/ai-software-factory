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


if __name__ == "__main__":
    unittest.main(verbosity=2)
