#!/usr/bin/env python3
"""tools/render_dashboard.py — regenerate factory/DASHBOARD.md FROM the
ledger + git (architecture.md §Control plane — generated renders, never
hand-edited). No hand-maintained counters.

    python3 tools/render_dashboard.py [--root <control-home>]   # write
    python3 tools/render_dashboard.py [--root R] --check        # exit 1 if stale
    python3 tools/render_dashboard.py [--root R] --ledger PATH ...  # alt ledger

The dashboard reports RAW COUNTS + breakdowns — never a single pooled
percentage. Stories without loop/finding instrumentation are excluded from
the correction-free measures and listed separately (unavailable ≠ zero).

Render byte contract (architecture.md §Data model): ledger + git only;
LF endings; trailing newline; NO wall-clock content — same ledger + same
tree renders byte-identical output. The repo HEAD short SHA is git-derived
and part of the deterministic body.

Title genericization: the workspace display name comes from
<root>/workspace.toml (`workspace` key) when present, else `factory` —
renders are never project-named. The process-version line comes from the
`process-version:` line in <root>/factory/runbook.md when present, else it
is omitted — never a hardcoded version.

Exit codes (config_validator convention): 0 ok (incl. --check fresh) ·
1 --check stale · 2 usage / ledger refusal. Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

try:
    from . import ledger_lib as L
    from . import ledger_schema as S
    from . import render_state as RSt
except ImportError:  # direct-run layout: load the tools package itself
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools import ledger_lib as L
    from tools import ledger_schema as S
    from tools import render_state as RSt


def process_version(root: Path) -> str | None:
    """The runbook's frozen `process-version:` line, verbatim (None when
    absent — never a hardcoded version)."""
    p = root / "factory" / "runbook.md"
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^process-version:\s*(\S.*)$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def git_head(root: Path) -> str:
    """Short HEAD SHA (git-derived; deterministic for a fixed tree)."""
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              cwd=root, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return "?"


def _names_plan_review(evidence_path) -> bool:
    """v0.1 plan-review association: True when a finding's evidence_path
    names the plan review (case-insensitive `plan-review` / `plan review`).
    Uses only the supported finding schema — no out-of-schema fields."""
    ev = str(evidence_path or "").lower()
    return "plan-review" in ev or "plan review" in ev


def render(events: list[dict], root: Path, header: str) -> str:
    name = RSt.workspace_name(root)
    out: list[str] = [header, "", f"# {name} — Factory Dashboard", ""]
    pv = process_version(root)
    if pv:
        out += [f"**process-version: {pv}**"]
    out += [f"_{len(events)} ledger events · repo HEAD {git_head(root)}_", ""]

    # ---------- per-story rollup
    stories: dict[str, dict] = defaultdict(dict)
    for e in events:
        sid = e.get("story_id")
        if not isinstance(sid, str):
            continue
        s = stories[sid]
        et = e["event"]
        if et == "assignment":
            # claim/handover/release fold into ONE owner cell — latest row
            # wins: claim sets the owner, handover moves it, release clears
            op = e.get("op")
            if op == "claim":
                s["owner"] = e.get("owner")
            elif op == "handover":
                s["owner"] = e.get("to")
            elif op == "release":
                s["owner"] = None
        elif et == "transition":
            if not e.get("repo"):  # per-repo sub-events never move state
                s["state"] = e["to_state"]
            s.setdefault("transitions", 0)
            s["transitions"] += 1
        elif et == "loop":
            s.setdefault("loops", []).append(e)
        elif et == "finding":
            s.setdefault("findings", []).append(e)
        elif et == "review_attribution":
            s.setdefault("review_attributions", []).append(e)
        elif et == "outcome":
            s["outcome"] = e
        elif et == "stage" and e.get("stage") == "full_suite":
            s["suite"] = e

    for sid, story in stories.items():
        story["findings"] = L.latest_findings(events, sid)

    def formal_loops(s: dict) -> int:
        return len(s.get("loops", []))

    def instrumented(s: dict) -> bool:
        """Was loop/finding data actually recorded for this story?
        Unavailable ≠ zero — a story without instrumentation is never
        counted as correction-free."""
        totals = s.get("outcome", {}).get("totals")
        return bool(isinstance(totals, dict)) or \
            bool(s.get("findings")) or bool(s.get("loops"))

    def external_rounds(s: dict) -> int:
        rounds = {f.get("evidence_path") for f in s.get("findings", [])
                  if f.get("source") == "external"
                  and f.get("evidence_path")}
        return len(rounds)

    def correction_rounds(s: dict) -> int:
        """ALL correction rounds incl. external + nit-labeled (two distinct
        measures: formal loops vs every correction). Plan-review findings
        fold into their plan loop (one round, not N) — but only up to as
        many folds as there are plan loops; a plan-review-evidenced fixed
        finding WITHOUT a plan loop row counts individually (an unmatched
        fix never folds away to zero). Post-planning fixes count
        individually."""
        # v0.1 association convention (SUPPORTED schema fields only): a
        # fixed finding folds into its plan loop when its evidence_path
        # names the plan review; a future ledger link may replace this.
        fixed = [f for f in s.get("findings", [])
                 if f.get("disposition") in ("fixed",)]
        plan_fixed = [f for f in fixed
                      if _names_plan_review(f.get("evidence_path"))]
        other = [f for f in fixed
                 if not _names_plan_review(f.get("evidence_path"))]
        # R5-F1: fold ONLY into PLAN-REVIEW loops — a code-review loop
        # is not a fold target for a plan-review-evidenced fix.
        plan_loops = [l for l in s.get("loops", [])
                      if l.get("stage") == "plan_review"]
        folded = min(len(plan_fixed), len(plan_loops))
        return formal_loops(s) + len(other) + (len(plan_fixed) - folded)

    merged = {sid: s for sid, s in stories.items()
              if s.get("outcome", {}).get("merged_sha")
              or (s.get("state") in ("MERGED", "LIVE_PROVEN", "CLOSED"))}

    # ---------- metrics (raw counts + breakdowns, never a pooled percentage)
    out += ["## Metrics (raw counts — never a pooled percentage)", ""]
    out += ["| Measure | Value |", "|---|---|"]
    out.append(f"| Stories in ledger | {len(stories)} |")
    out.append(f"| Merged (git-verified outcomes) | {len(merged)} |")
    cf = [sid for sid, s in merged.items()
          if instrumented(s) and correction_rounds(s) == 0]
    ff = [sid for sid, s in merged.items()
          if instrumented(s) and formal_loops(s) == 0]
    uninstr = sorted(sid for sid, s in merged.items() if not instrumented(s))
    out.append(f"| Correction-free deliveries (0 correction rounds of any "
               f"source/class; instrumented stories only — unavailable ≠ 0) "
               f"| {len(cf)} — {', '.join(sorted(cf)) or 'none'} |")
    out.append(f"| Formal-loop-free deliveries (0 plan/code loops; external "
               f"rounds counted separately; instrumented stories only) | "
               f"{len(ff)} — {', '.join(sorted(ff)) or 'none'} |")
    out.append(f"| Merged stories WITHOUT loop/finding instrumentation "
               f"(excluded from both measures above, never counted as zero) "
               f"| {len(uninstr)} — {', '.join(uninstr) or 'none'} |")
    out.append(f"| Formal loops recorded | "
               f"{sum(formal_loops(s) for s in merged.values())} "
               f"(plan: {sum(1 for s in merged.values() for l in s.get('loops', []) if l['stage'] == 'plan_review')}, "
               f"code: {sum(1 for s in merged.values() for l in s.get('loops', []) if l['stage'] == 'code_review')}) |")
    out.append(f"| Observed external correction rounds | "
               f"{sum(external_rounds(s) for s in merged.values())} |")
    out.append(f"| Logic-changing nits (schema-forbidden) | "
               f"{sum(1 for s in stories.values() for f in s.get('findings', []) if f.get('class') == 'nit' and f.get('logic_touched'))} |")
    hg = [e for e in events if e["event"] == "human_gate"]

    def truly_pending(g, idx):
        if g.get("answered_at"):
            return False
        # append-only ledger: an unanswered ask row is superseded when a
        # later answered row for the same (story, gate) records the reply
        return not any(s.get("answered_at")
                       and s.get("story_id") == g.get("story_id")
                       and s.get("gate") == g.get("gate")
                       for s in hg[idx + 1:])
    out.append(f"| Human gates (avoidable target 0/story) | total {len(hg)}, "
               f"avoidable {sum(1 for g in hg if g.get('gate') == 'avoidable')}, "
               f"pending {sum(1 for i, g in enumerate(hg) if truly_pending(g, i))} |")
    corr_events = [e for e in events if e["event"] == "correction"]
    out.append(f"| Reconciliation corrections applied | {len(corr_events)} |")
    attrs = [e for e in events if e["event"] == "review_attribution"]
    attr_counts = {k: sum(1 for a in attrs if a.get("classification") == k)
                   for k in sorted(S.REVIEW_ATTRIBUTIONS)}
    context_stories = {e.get("story_id") for e in events
                       if e.get("event") == "review_context"}
    # finding ids are unique only WITHIN a story — attribution joins are
    # (story_id, finding_id) tuples, never bare finding_ids
    attributed_keys = {(a.get("story_id"), a.get("finding_id"))
                       for a in attrs}
    activated_external = [(sid, f) for sid, s in stories.items()
                          if sid in context_stories
                          for f in s.get("findings", [])
                          if f.get("source") == "external"]
    unattributed = sum(1 for sid, f in activated_external
                       if (sid, f.get("finding_id")) not in attributed_keys)
    uninstrumented = sum(1 for sid, s in stories.items()
                         if sid not in context_stories
                         for f in s.get("findings", [])
                         if f.get("source") == "external")
    out.append("| External-unique finding attribution | "
               f"MISSED_WITH_CONTEXT {attr_counts['MISSED_WITH_CONTEXT']}; "
               f"CONTEXT_ABSENT {attr_counts['CONTEXT_ABSENT']}; "
               f"INTRODUCED_AFTER_INTERNAL "
               f"{attr_counts['INTRODUCED_AFTER_INTERNAL']}; "
               f"UNATTRIBUTED {unattributed}; without-instrumentation "
               f"{uninstrumented} |")
    out.append("")

    # ---------- per-story rows
    out += ["## Per-story state", "",
            "| Story | Owner | State | P/I loops | Ext rounds | "
            "Corrections | Merged | Live proof |", "|---|---|---|---|---|---|---|---|"]

    def sort_key(sid: str):
        s = stories[sid]
        o = s.get("outcome", {})
        return (o.get("merged_at") or "9999", sid)
    for sid in sorted(stories, key=sort_key):
        s = stories[sid]
        o = s.get("outcome", {})
        pl = sum(1 for l in s.get("loops", []) if l["stage"] == "plan_review")
        il = sum(1 for l in s.get("loops", []) if l["stage"] == "code_review")
        state = s.get("state") or ("MERGED*" if o.get("merged_sha")
                                   else "no transitions (events only)")
        out.append(
            f"| {sid} | {s.get('owner') or '—'} | `{state}` "
            f"| {pl}/{il} | {external_rounds(s)} | {correction_rounds(s)} "
            f"| {o.get('merged_sha') or '—'} "
            f"| {o.get('live_proof_status') or '—'} |")
    out += ["", "_MERGED* = merged outcome recorded without controller "
            "transitions (backfilled history)._", ""]

    # ---------- corrections register
    out += ["## Corrections register (correct forward, never rewrite "
            "history)", ""]
    for c in corr_events:
        cv = str(c.get("corrected_value") or "")
        out.append(f"- `{c['event_id']}` **{c.get('story_id') or 'run-level'}**: "
                   f"{cv[:220]}{'…' if len(cv) > 220 else ''} "
                   f"(_{c['source_citation']}_)")
    out.append("")

    # ---------- current run notes sidecar
    side = root / "factory" / "dashboard-notes.md"
    if side.exists():
        # embedded VERBATIM (no strip): hand-written notes may start with
        # an indented code block; the list-join supplies separators
        out += ["## Run notes (from factory/dashboard-notes.md sidecar)", "",
                side.read_text(encoding="utf-8"), ""]

    out += ["---", "_Regenerate: `python3 factory/tools/render_dashboard.py`_"]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="render factory/DASHBOARD.md from the ledger")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when the rendered DASHBOARD.md is stale")
    ap.add_argument("--root", default=".", metavar="control-home",
                    help="control home root (default: cwd)")
    ap.add_argument("--ledger", default=None,
                    help="ledger path (default <root>/factory/ledger/events.jsonl)")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    ledger = Path(args.ledger) if args.ledger \
        else root / "factory" / "ledger" / "events.jsonl"
    try:
        events = L.load_events(ledger)
    except L.LedgerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    header = RSt.generated_header(RSt.ledger_display(root, ledger))
    text = render(events, root, header)
    dash = root / "factory" / "DASHBOARD.md"
    if args.check:
        cur = dash.read_text(encoding="utf-8") if dash.exists() else ""
        if not cur.startswith("<!-- GENERATED from"):
            print("dashboard not yet generated")
            return 0
        if cur != text:
            print(f"OUT OF DATE: {dash}")
            return 1
        print("dashboard up to date")
        return 0
    dash.parent.mkdir(parents=True, exist_ok=True)
    dash.write_text(text, encoding="utf-8", newline="\n")
    print(f"rendered {dash} ({len(events)} events)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
