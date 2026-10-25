#!/usr/bin/env python3
"""tools/metrics_report.py — deterministic token/efficiency report from the
ledger. ZERO LLM calls. Tokens only (the kit's usage schema is tokens-only;
no cost fields, no API-call column — the dispatch schema carries neither).

    python3 tools/metrics_report.py [--root <control-home>]
    python3 tools/metrics_report.py [--root R] --ledger PATH ...
    python3 tools/metrics_report.py [--root R] --snapshot <path.json>
    python3 tools/metrics_report.py [--root R] --freeze <name>
    python3 tools/metrics_report.py [--root R] --diff <snapshot.json> [--story ID]

Reads the ledger (dispatch + usage + loop + finding + transition + outcome
rows) and renders:

  - <root>/factory/metrics/TOKEN-METRICS.md  (deterministic; default output)
  - a machine snapshot JSON ONLY at an explicit `--snapshot <path>` — never
    auto-named with wall-clock content (render byte contract)
  - `--freeze <name>` writes factory/metrics/<name>.json as a comparison
    baseline; `--diff <file>` prints a delta table against a named snapshot

Proof-of-improvement loop:
  1. freeze a baseline:   metrics_report.py --freeze baseline-a
  2. after each story:    metrics_report.py
  3. compare:             metrics_report.py --diff factory/metrics/baseline-a.json

Adoption rule: an improvement is KEPT only if its target metric moves AND
`reasoning`-class loop tags do not rise.

Includes the unrouted-dispatch census: dispatches whose requested_model was
not recorded (null + reason) are counted as `unrouted` — never guessed.

Render byte contract (architecture.md §Data model): ledger only; sorted
keys in snapshots; LF endings; trailing newline; NO wall-clock content —
same ledger renders byte-identical output.

Output carries the header:
    <!-- GENERATED from factory/ledger/events.jsonl — do not hand-edit;
         append events instead -->

Exit codes (config_validator convention): 0 ok · 1 --diff against an
unreadable/malformed baseline · 2 usage / ledger refusal. Stdlib only.
Python >= 3.11.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from . import ledger_lib as L
    from . import render_state as RSt
except ImportError:  # direct-run layout: load the tools package itself
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools import ledger_lib as L
    from tools import render_state as RSt

# Role → lane grouping for the report (canonical kit roles per
# tools/role_catalog.py; coder+fixer = build lane).
LANES = {
    "coder": "build", "fixer": "build",
    "planner": "plan", "decomposer": "plan", "test_plan_generator": "plan",
    "context_acquisition": "context",
    "plan_reviewer": "plan-review", "plan_reviewer_a": "plan-review",
    "plan_reviewer_b": "plan-review",
    "code_reviewer": "code-review", "behavior_reviewer": "code-review",
    "contract_reviewer": "code-review",
}


def norm_role(r: str | None) -> str:
    return str(r or "?").replace("-", "_")


def lane_of(role: str) -> str:
    return LANES.get(role, "other")


def collect(events: list[dict]) -> dict[str, Any]:
    # dispatch lifecycle: last row per event_id wins (the completion row
    # supersedes the pre-spawn row; fields merge so pre-spawn values
    # survive unless overwritten)
    dispatches: dict[str, dict] = {}
    for e in events:
        if e.get("event") == "dispatch":
            dispatches[e["event_id"]] = {**dispatches.get(e["event_id"], {}),
                                         **e}
    # usage rows: keep ALL of them in ledger order for totals (a null
    # dispatch_id is still a real usage row — never collapsed away); the
    # by-id join dict is built ONLY for non-null dispatch_ids.
    usage_rows: list[dict] = [e for e in events
                              if e.get("event") == "usage"]
    usage: dict[str, dict] = {}
    for e in usage_rows:
        did = e.get("dispatch_id")
        if did is not None:
            usage[str(did)] = e
    loops = [e for e in events if e.get("event") == "loop"]
    findings = L.latest_findings(events)
    outcomes = [e for e in events if e.get("event") == "outcome"]

    per_story: dict[str, dict] = defaultdict(lambda: {
        "dispatches": 0, "input_tokens": 0, "output_tokens": 0,
        "cached_input_tokens": 0,
        "per_lane": defaultdict(lambda: {"dispatches": 0, "input_tokens": 0,
                                         "output_tokens": 0}),
        "loops": defaultdict(int), "findings": 0,
        "routed": 0, "unrouted": 0, "unrouted_ids": [],
        "usage_recorded": 0, "served_model_known": 0, "merged": None,
    })
    per_role: dict[str, dict] = defaultdict(lambda: {
        "dispatches": 0, "input_tokens": 0, "output_tokens": 0,
        "models": defaultdict(int)})

    for did, d in sorted(dispatches.items()):
        sid = str(d.get("story_id") or "?")
        role = norm_role(d.get("role"))
        u = usage.get(did, {})
        it = int(u.get("input_tokens") or 0)
        ot = int(u.get("output_tokens") or 0)
        ct = int(u.get("cached_input_tokens") or 0)
        st = per_story[sid]
        st["dispatches"] += 1
        st["input_tokens"] += it
        st["output_tokens"] += ot
        st["cached_input_tokens"] += ct
        # unrouted census: requested_model absent/null = routing not
        # recorded — never guessed (null+reason law)
        if d.get("requested_model") is not None:
            st["routed"] += 1
        else:
            st["unrouted"] += 1
            st["unrouted_ids"].append(did)
        st["usage_recorded"] += 1 if u else 0
        st["served_model_known"] += \
            1 if d.get("reported_model") and not d.get("model_unverified") \
            else 0
        ln = st["per_lane"][lane_of(role)]
        ln["dispatches"] += 1
        ln["input_tokens"] += it
        ln["output_tokens"] += ot
        pr = per_role[role]
        pr["dispatches"] += 1
        pr["input_tokens"] += it
        pr["output_tokens"] += ot
        # a requested model is not a served model — tally reported_model
        # only; dispatches without one are `(unreported)`, never guessed
        pr["models"][str(d.get("reported_model"))
                     if d.get("reported_model") is not None
                     else "(unreported)"] += 1

    for lp in loops:
        per_story[str(lp.get("story_id"))]["loops"][
            str(lp.get("class") or "?")] += 1
    for f in findings:
        per_story[str(f.get("story_id"))]["findings"] += 1
    for o in outcomes:
        per_story[str(o.get("story_id"))]["merged"] = \
            (o.get("merged_sha") or "?")[:10]
    return {"per_story": per_story, "per_role": per_role,
            "totals": {
                "dispatches": len(dispatches),
                "usage_rows": len(usage_rows),
                "input_tokens": sum(int(u.get("input_tokens") or 0)
                                    for u in usage_rows),
                "output_tokens": sum(int(u.get("output_tokens") or 0)
                                     for u in usage_rows),
                "cached_input_tokens": sum(
                    int(u.get("cached_input_tokens") or 0)
                    for u in usage_rows),
                "loops": len(loops),
            }}


def to_jsonable(c: dict) -> dict:
    """Snapshot JSON: sorted keys, no wall-clock content — byte-identical
    for the same ledger."""
    out = {}
    for sid, st in sorted(c["per_story"].items()):
        out[sid] = {**{k: v for k, v in st.items() if k != "per_lane"},
                    "per_lane": {k: dict(v) for k, v in
                                 sorted(st["per_lane"].items())}}
        out[sid]["loops"] = dict(sorted(st["loops"].items()))
    roles = {r: {**{k: v for k, v in d.items() if k != "models"},
                 "models": dict(sorted(d["models"].items()))}
             for r, d in sorted(c["per_role"].items())}
    return {"per_story": out, "per_role": roles, "totals": c["totals"]}


def snapshot_bytes(snap: dict) -> bytes:
    return (json.dumps(snap, indent=1, sort_keys=True) + "\n").encode()


def render_md(c: dict, title: str, header: str, n_events: int) -> str:
    out = [header, "", f"# {title}", "",
           f"_{n_events} ledger events_", "",
           "## Totals", ""]
    t = c["totals"]
    out += ["| Measure | Value |", "|---|---|"]
    for k in sorted(t):
        v = t[k]
        out.append(f"| {k} | {v:,} |" if isinstance(v, int)
                   else f"| {k} | {v} |")
    out += ["", "## Per story", "",
            "| Story | Dispatches | Input tok | Output tok | Routed | "
            "Usage rows | Served model known | Loops | Merged |",
            "|---|---|---|---|---|---|---|---|---|"]
    for sid, st in sorted(c["per_story"].items()):
        loops = ",".join(f"{k}:{v}" for k, v in sorted(st["loops"].items())) \
            or "0"
        out.append(f"| {sid} | {st['dispatches']} | "
                   f"{st['input_tokens']:,} | {st['output_tokens']:,} | "
                   f"{st['routed']}/{st['dispatches']} | "
                   f"{st['usage_recorded']}/{st['dispatches']} | "
                   f"{st['served_model_known']}/{st['dispatches']} | "
                   f"{loops} | {st['merged'] or '—'} |")
    out += ["", "## Per story × lane (input tokens)", ""]
    lanes = sorted({ln for st in c["per_story"].values()
                    for ln in st["per_lane"]})
    if lanes:
        out += ["| Story | " + " | ".join(lanes) + " |",
                "|---|" + "---|" * len(lanes)]
        for sid, st in sorted(c["per_story"].items()):
            cells = [f"{st['per_lane'][ln]['input_tokens']:,}"
                     if ln in st["per_lane"] else "—" for ln in lanes]
            out.append(f"| {sid} | " + " | ".join(cells) + " |")
    else:
        out.append("(no dispatch rows recorded)")
    out += ["", "## Unrouted-dispatch census", ""]
    unrouted_total = sum(st["unrouted"] for st in c["per_story"].values())
    out += [f"Dispatches whose requested_model was not recorded (null + "
            f"reason — never guessed): **{unrouted_total}**", ""]
    if unrouted_total:
        for sid, st in sorted(c["per_story"].items()):
            if st["unrouted"]:
                out.append(f"- {sid}: {', '.join(st['unrouted_ids'])}")
        out.append("")
    out += ["## Per role", "",
            "| Role | Dispatches | Input tok | Output tok | Models served |",
            "|---|---|---|---|---|"]
    for r, d in sorted(c["per_role"].items()):
        models = ", ".join(f"{m}×{n}" for m, n in sorted(d["models"].items()))
        out.append(f"| {r} | {d['dispatches']} | {d['input_tokens']:,} | "
                   f"{d['output_tokens']:,} | {models} |")
    out += ["", "_Regenerate: `python3 factory/tools/metrics_report.py`_", ""]
    return "\n".join(out)


def validate_baseline(base: Any) -> str | None:
    """Baseline shape validation before diffing — returns None when the
    snapshot has the documented shape, else the detail for the named
    refusal `error: malformed baseline snapshot: <detail>` (rc 1)."""
    if not isinstance(base, dict):
        return f"top level must be an object, got {type(base).__name__}"
    for key in ("per_story", "per_role", "totals"):
        if key not in base:
            return f"missing `{key}` section"
        if not isinstance(base[key], dict):
            return (f"`{key}` must be an object, got "
                    f"{type(base[key]).__name__}")
    for st, sv in base["per_story"].items():
        if not isinstance(sv, dict):
            return (f"`per_story.{st}` must be an object, got "
                    f"{type(sv).__name__}")
        loops = sv.get("loops")
        if loops is not None and not isinstance(loops, dict):
            return (f"`per_story.{st}.loops` must be an object, got "
                    f"{type(loops).__name__}")
        for lk, lv in (loops or {}).items():
            if not isinstance(lv, (int, float)) or isinstance(lv, bool):
                return (f"`per_story.{st}.loops.{lk}` must be numeric, got "
                        f"{lv!r}")
        for nk in ("input_tokens", "output_tokens", "dispatches"):
            nv = sv.get(nk)
            if nv is not None and \
                    (not isinstance(nv, (int, float)) or isinstance(nv, bool)):
                return (f"`per_story.{st}.{nk}` must be numeric, got "
                        f"{nv!r}")
    for tk, tv in base["totals"].items():
        if not isinstance(tv, (int, float)) or isinstance(tv, bool):
            return f"`totals.{tk}` must be numeric, got {tv!r}"
    return None


def diff(base: dict, cur: dict, story: str | None) -> str:
    out = ["# Delta vs baseline", "",
           "| Metric | Baseline | Current | Δ |", "|---|---|---|---|"]

    def rows(scope_base, scope_cur, label):
        pairs = [
            ("input_tokens", scope_base.get("input_tokens", 0),
             scope_cur.get("input_tokens", 0)),
            ("output_tokens", scope_base.get("output_tokens", 0),
             scope_cur.get("output_tokens", 0)),
            ("dispatches", scope_base.get("dispatches", 0),
             scope_cur.get("dispatches", 0)),
        ]
        for name, b, c in pairs:
            d = c - b
            pct = f" ({d / b * 100:+.0f}%)" if b else ""
            out.append(f"| {label} {name} | {b:,} | {c:,} | {d:+,}{pct} |")

    if story:
        rows(base.get("per_story", {}).get(story, {}),
             cur.get("per_story", {}).get(story, {}), story)
    else:
        rows(base.get("totals", {}), cur.get("totals", {}), "TOTAL")

    # reasoning-loop guard (adoption rule) — with an explicit story scope,
    # ALWAYS scope per story on each side: an absent side contributes 0,
    # never a fall-back to all stories
    def reasoning(snap, st=None):
        ps = snap.get("per_story", {})
        items = [ps.get(st, {})] if st else list(ps.values())
        return sum(int(v.get("loops", {}).get("reasoning", 0) or 0)
                   for v in items)
    rb, rc = reasoning(base, story), reasoning(cur, story)
    verdict = "OK" if rc <= rb else "REGRESSION — reasoning loops rose"
    out += ["", f"Reasoning-loop guard: baseline {rb} → current {rc} — "
            f"{verdict}"]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="deterministic token metrics report from the ledger")
    ap.add_argument("--root", default=".", metavar="control-home",
                    help="control home root (default: cwd)")
    ap.add_argument("--ledger", default=None,
                    help="ledger path (default <root>/factory/ledger/events.jsonl)")
    ap.add_argument("--out-md", default=None,
                    help="markdown destination (default "
                         "<root>/factory/metrics/TOKEN-METRICS.md)")
    ap.add_argument("--snapshot", metavar="PATH",
                    help="also write the machine snapshot JSON to this "
                         "EXPLICIT path (never auto-named)")
    ap.add_argument("--freeze", metavar="NAME",
                    help="also write factory/metrics/NAME.json as a "
                         "comparison baseline")
    ap.add_argument("--diff", metavar="BASELINE_JSON",
                    help="print a delta table against a named snapshot")
    ap.add_argument("--story", default=None, help="scope --diff to one story")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    ledger = Path(args.ledger) if args.ledger \
        else root / "factory" / "ledger" / "events.jsonl"
    try:
        events = L.load_events(ledger)
    except L.LedgerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    c = collect(events)
    snap = to_jsonable(c)
    metrics_dir = root / "factory" / "metrics"
    header = RSt.generated_header(RSt.ledger_display(root, ledger))
    title = f"{RSt.workspace_name(root)} — Token & Efficiency Metrics"
    md = render_md(c, title, header, len(events))

    out_md = Path(args.out_md) if args.out_md \
        else metrics_dir / "TOKEN-METRICS.md"
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_bytes(md.encode())
    print(f"wrote {out_md}")

    payload = snapshot_bytes(snap)
    if args.snapshot:
        sp = Path(args.snapshot)
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_bytes(payload)
        print(f"snapshot → {sp}")
    if args.freeze:
        metrics_dir.mkdir(parents=True, exist_ok=True)
        fp = metrics_dir / f"{args.freeze}.json"
        fp.write_bytes(payload)
        print(f"froze baseline → {fp}")
    if args.diff:
        bp = Path(args.diff)
        try:
            base = json.loads(bp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"error: cannot read baseline {bp}: {e}", file=sys.stderr)
            return 1
        detail = validate_baseline(base)
        if detail:
            print(f"error: malformed baseline snapshot: {detail}",
                  file=sys.stderr)
            return 1
        try:
            print(diff(base, snap, args.story))
        except (AttributeError, TypeError) as e:
            # residual shape problems surface here — same named rc-1
            # refusal, never a traceback
            print(f"error: malformed baseline snapshot: {e}",
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
