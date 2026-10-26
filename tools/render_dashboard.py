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

Render byte contract (architecture.md §Data model): ledger only; LF
endings; trailing newline; NO wall-clock content — same ledger renders
byte-identical output across commit boundaries (R7-H4: the freshness
stamp is the LEDGER-derived digest `ledger events · ledger digest
<sha256[:12]>`, the same convention as render_card's footer — the old
repo-HEAD stamp made a committed dashboard perpetually stale).

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
import hashlib
import json
import re
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


def _cell(s) -> str:
    """Escape a value for a markdown table cell — round 32 AG3/S2:
    identical semantics to render_state._cell (pipe → `\\|`, CR/CRLF/
    newline → space). A free-form owner like `ops | backup` used to add
    a column and shift every following cell of the per-story row."""
    return (str(s).replace("|", "\\|")
            .replace("\r\n", " ").replace("\r", " ").replace("\n", " "))


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


def ledger_digest(events: list[dict], ledger_path: Path | None = None) -> str:
    """Stable LEDGER-derived freshness stamp (R7-H4; same convention as
    render_card's footer: sha256[:12] of the ledger content). With a
    ledger path the file bytes are hashed exactly like render_card does;
    without one (in-process event lists) the canonical JSON of the events
    is hashed. Never git/wall-clock derived — a committed dashboard stays
    fresh across commit boundaries while its ledger is unchanged."""
    if ledger_path is not None and ledger_path.exists():
        data = ledger_path.read_bytes()
    else:
        data = json.dumps(events, sort_keys=True,
                          ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:12]


def _names_plan_review(evidence_path) -> bool:
    """v0.1 plan-review association: True when a finding's evidence_path
    names the plan review (case-insensitive `plan-review` / `plan review`).
    Uses only the supported finding schema — no out-of-schema fields."""
    ev = str(evidence_path or "").lower()
    return "plan-review" in ev or "plan review" in ev


def _normalize_eol(text: str) -> str:
    """Round 34 AI1: flatten line endings of the ASSEMBLED output —
    CRLF→LF first, then any lone CR→LF. Values embedded OUTSIDE markdown
    tables (correction bullets, sidecar prose) never pass through `_cell`,
    so a CR carried by a free-form ledger value would otherwise land in
    the artifact and violate the frozen LF byte contract; writing with
    `newline="\\n"` does not remove it. Normalizing the complete output
    makes the LF guarantee structural instead of per-insertion-site."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def render(events: list[dict], root: Path, header: str,
           ledger_path: Path | None = None) -> str:
    name = RSt.workspace_name(root)
    out: list[str] = [header, "", f"# {name} — Factory Dashboard", ""]
    pv = process_version(root)
    if pv:
        out += [f"**process-version: {pv}**"]
    out += [f"_{len(events)} ledger events · ledger digest "
            f"{ledger_digest(events, ledger_path)}_", ""]

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
                # R18-S2: a story-level CLOSED transition is an implicit
                # release (controller.open_claim_owner treats it so) —
                # the owner cell must clear even without a release row.
                if e.get("to_state") == "CLOSED":
                    s["owner"] = None
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
        # R23-X1: keep the RAW (unfolded) finding history alongside the
        # latest-row fold — the external-round counter must see every
        # lifecycle row (a finding first cited in round 1 and updated in
        # round 2 spans TWO rounds), while dispositions keep the fold.
        story["finding_history"] = list(story.get("findings", []))
        story["findings"] = L.latest_findings(events, sid)

    def _valid_counter(v) -> bool:
        """A VALID loop counter value: non-negative int, not a bool
        (the R8-I5 validity rule)."""
        return isinstance(v, int) and not isinstance(v, bool) and v >= 0

    def _valid_total(totals, key: str) -> int:
        """A supported outcome-total counter when VALID, else 0."""
        v = totals.get(key)
        return v if _valid_counter(v) else 0

    def loop_breakdown(s: dict) -> tuple[int, int]:
        """(plan, code) loop counts by stage. Loop rows win; when NO rows
        exist, fall back to the valid `plan_loops`/`code_loops` aggregates
        from the outcome totals (R9-J1 — the same fallback formal_loops
        applies to the total; the breakdowns must not show 0/0 beside a
        non-zero aggregate total)."""
        rows = s.get("loops", [])
        if rows:
            return (sum(1 for l in rows if l.get("stage") == "plan_review"),
                    sum(1 for l in rows if l.get("stage") == "code_review"))
        totals = s.get("outcome", {}).get("totals")
        if not isinstance(totals, dict):
            return (0, 0)
        return (_valid_total(totals, "plan_loops"),
                _valid_total(totals, "code_loops"))

    def formal_loops(s: dict) -> int:
        """Loop rows win; when a story recorded an outcome `totals`
        aggregate WITHOUT loop rows (R8-I5), the aggregate is the count
        — an outcome with `plan_loops: 2` and no rows is NOT loop-free.
        Aggregate = sum of the valid supported counters `plan_loops` /
        `code_loops` (bools/negatives are not counts)."""
        rows = s.get("loops", [])
        if rows:
            return len(rows)
        return sum(loop_breakdown(s))

    def instrumented(s: dict) -> bool:
        """Was loop/finding data actually recorded for this story?
        Unavailable ≠ zero — a story without instrumentation is never
        counted as correction-free. R9-J3: a `totals` dict counts as
        instrumentation ONLY when it carries at least one VALID supported
        counter (`plan_loops`/`code_loops`, non-negative int, not bool) —
        `totals: {}` or only unrelated counters is a MISSING measurement
        (excluded), while an explicit recorded zero still counts."""
        if s.get("findings") or s.get("loops"):
            return True
        totals = s.get("outcome", {}).get("totals")
        if not isinstance(totals, dict):
            return False
        return any(k in ("plan_loops", "code_loops") and _valid_counter(v)
                   for k, v in totals.items())

    def external_rounds(s: dict) -> int:
        # R23-X1: count DISTINCT rounds across ALL lifecycle rows (raw
        # history), not the latest-row fold — an update that moves a
        # finding to a later round must not erase the earlier one.
        rows = s.get("finding_history")
        if rows is None:  # defensive: fall back to the folded rows
            rows = s.get("findings", [])
        rounds = {f.get("evidence_path") for f in rows
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
    plan_sum = sum(loop_breakdown(s)[0] for s in merged.values())
    code_sum = sum(loop_breakdown(s)[1] for s in merged.values())
    out.append(f"| Formal loops recorded | "
               f"{sum(formal_loops(s) for s in merged.values())} "
               f"(plan: {plan_sum}, code: {code_sum}) |")
    out.append(f"| Observed external correction rounds | "
               f"{sum(external_rounds(s) for s in merged.values())} |")
    out.append(f"| Logic-changing nits (schema-forbidden) | "
               f"{sum(1 for s in stories.values() for f in s.get('findings', []) if f.get('class') == 'nit' and f.get('logic_touched'))} |")
    hg = [e for e in events if e["event"] == "human_gate"]

    def superseded(g, idx):
        """An ask row whose reply was appended later (append-only ledger):
        unanswered here, but a later answered row for the same
        (story, gate) records the reply — one human intervention, not
        two. R23-X2: totals/avoidable must count LIFECYCLES, not rows."""
        if g.get("answered_at"):
            return False
        return any(s.get("answered_at")
                   and s.get("story_id") == g.get("story_id")
                   and s.get("gate") == g.get("gate")
                   for s in hg[idx + 1:])

    def truly_pending(g, idx):
        return not g.get("answered_at") and not superseded(g, idx)

    live_gates = [(i, g) for i, g in enumerate(hg) if not superseded(g, i)]
    out.append(f"| Human gates (avoidable target 0/story) | "
               f"total {len(live_gates)}, "
               f"avoidable {sum(1 for _, g in live_gates if g.get('gate') == 'avoidable')}, "
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
    # R7-H5: external findings carrying an `overlap_id` are excluded
    # from the attribution requirement by the controller — the census
    # must not count them as UNATTRIBUTED / without-instrumentation.
    activated_external = [(sid, f) for sid, s in stories.items()
                          if sid in context_stories
                          for f in s.get("findings", [])
                          if f.get("source") == "external"
                          and not f.get("overlap_id")]
    unattributed = sum(1 for sid, f in activated_external
                       if (sid, f.get("finding_id")) not in attributed_keys)
    uninstrumented = sum(1 for sid, s in stories.items()
                         if sid not in context_stories
                         for f in s.get("findings", [])
                         if f.get("source") == "external"
                         and not f.get("overlap_id"))
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
        pl, il = loop_breakdown(s)
        state = s.get("state") or ("MERGED*" if o.get("merged_sha")
                                   else "no transitions (events only)")
        # round 32 AG3: owner (and every other ledger-sourced free-text
        # cell) is escaped before insertion — a pipe/newline in a
        # free-form owner must not corrupt the row
        out.append(
            f"| {_cell(sid)} | {_cell(s.get('owner') or '—')} "
            f"| `{_cell(state)}` "
            f"| {pl}/{il} | {external_rounds(s)} | {correction_rounds(s)} "
            f"| {_cell(o.get('merged_sha') or '—')} "
            f"| {_cell(o.get('live_proof_status') or '—')} |")
    out += ["", "_MERGED* = merged outcome recorded without controller "
            "transitions (backfilled history)._", ""]

    # ---------- corrections register
    out += ["## Corrections register (correct forward, never rewrite "
            "history)", ""]
    for c in corr_events:
        raw_cv = c.get("corrected_value")
        # falsy-but-recorded values (0, False) must render, not collapse
        cv = "" if raw_cv is None else str(raw_cv)
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
    # round 34 AI1: normalize the ASSEMBLED output (CRLF→LF, lone CR→LF)
    return _normalize_eol("\n".join(out)) + "\n"


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
    text = render(events, root, header, ledger_path=ledger)
    dash = root / "factory" / "DASHBOARD.md"
    if args.check:
        # R16-Q2: compare raw BYTES — read_text() would normalize a
        # CRLF-mangled dashboard back to LF and let it pass --check
        # despite violating the frozen LF byte contract (as
        # render_state/render_provenance already do).
        # Round 28 AC4: a MISSING dashboard is STALE, not "not yet
        # generated" — deleting factory/DASHBOARD.md after generation used
        # to set cur to b"" and take the success branch. The handwritten
        # exemption stays: an EXISTING file without the generated marker
        # is prose the renderer never owned.
        if not dash.exists():
            print(f"OUT OF DATE: {dash} (missing)")
            return 1
        cur = dash.read_bytes()
        if not cur.startswith(b"<!-- GENERATED from"):
            print("dashboard not yet generated")
            return 0
        if cur != text.encode("utf-8"):
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
