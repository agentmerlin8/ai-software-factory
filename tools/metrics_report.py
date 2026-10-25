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


# Token-count fields carried by usage rows (tokens-only law).
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens")


class TokSum:
    """Aggregated token count that preserves unknown measurements.

    A null count with a recorded reason is an UNAVAILABLE measurement,
    never a measured zero: known values sum into `known`; null rows are
    censused in `unknown`/`reasons`. `value` is None only when rows
    existed and every one was unknown — partial scope sums keep the known
    total plus the unknown-row marker.
    """

    __slots__ = ("known", "has_known", "unknown", "reasons")

    def __init__(self) -> None:
        self.known = 0
        self.has_known = False
        self.unknown = 0
        self.reasons: dict[str, int] = {}

    def add(self, v: Any, reason: Any = None) -> None:
        if v is None:
            self.unknown += 1
            key = str(reason) if reason is not None else "?"
            self.reasons[key] = self.reasons.get(key, 0) + 1
        else:
            self.has_known = True
            self.known += int(v)

    def merge(self, other: "TokSum") -> None:
        self.known += other.known
        self.has_known = self.has_known or other.has_known
        self.unknown += other.unknown
        for k, n in other.reasons.items():
            self.reasons[k] = self.reasons.get(k, 0) + n

    @property
    def value(self) -> int | None:
        # no rows at all → 0 (nothing recorded); every row unknown → None
        return self.known if (self.has_known or self.unknown == 0) else None

    def resolve_into(self, d: dict, key: str) -> None:
        d[key] = self.value
        if self.unknown:
            d[key + "_unknown"] = self.unknown
            d[key + "_reasons"] = dict(sorted(self.reasons.items()))


def tok_cell(scope: dict, field: str) -> str:
    """Render one token count preserving unknown/partial status:
    `unknown (<reason>)` when no row was measurable, `<sum> partial
    (N unknown rows)` when some were, plain `<sum>` when all known."""
    v = scope.get(field)
    unk = scope.get(field + "_unknown") or 0
    if v is None:
        reasons = scope.get(field + "_reasons") or {}
        why = "; ".join(r if n == 1 else f"{r} ×{n}"
                        for r, n in sorted(reasons.items()))
        return f"unknown ({why})" if why else "unknown"
    if unk:
        return f"{v:,} partial ({unk} unknown rows)"
    return f"{v:,}"


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
    # by-id join groups EVERY row of a dispatch (a dispatch may carry
    # several usage events — they SUM at every scope, never overwrite).
    usage_rows: list[dict] = [e for e in events
                              if e.get("event") == "usage"]
    usage_by_did: dict[str, list[dict]] = {}
    for e in usage_rows:
        did = e.get("dispatch_id")
        if did is not None:
            usage_by_did.setdefault(str(did), []).append(e)
    loops = [e for e in events if e.get("event") == "loop"]
    findings = L.latest_findings(events)
    outcomes = [e for e in events if e.get("event") == "outcome"]

    def fresh_tok() -> dict[str, TokSum]:
        return {f: TokSum() for f in TOKEN_FIELDS}

    per_story: dict[str, dict] = defaultdict(lambda: {
        "dispatches": 0, "tok": fresh_tok(),
        "per_lane": defaultdict(lambda: {"dispatches": 0, "tok": fresh_tok()}),
        "loops": defaultdict(int), "findings": 0,
        "routed": 0, "unrouted": 0, "unrouted_ids": [],
        "usage_recorded": 0, "served_model_known": 0, "merged": None,
    })
    per_role: dict[str, dict] = defaultdict(lambda: {
        "dispatches": 0, "tok": fresh_tok(),
        "models": defaultdict(int)})

    for did, d in sorted(dispatches.items()):
        sid = str(d.get("story_id") or "?")
        role = norm_role(d.get("role"))
        rows = usage_by_did.get(did, [])
        # sum EVERY usage row of this dispatch, preserving null counts as
        # unknown (never coerced to a measured zero)
        per_field = fresh_tok()
        for u in rows:
            for f in TOKEN_FIELDS:
                per_field[f].add(u.get(f), u.get(f + "_reason"))
        st = per_story[sid]
        st["dispatches"] += 1
        for f in TOKEN_FIELDS:
            st["tok"][f].merge(per_field[f])
        # unrouted census: requested_model absent/null = routing not
        # recorded — never guessed (null+reason law)
        if d.get("requested_model") is not None:
            st["routed"] += 1
        else:
            st["unrouted"] += 1
            st["unrouted_ids"].append(did)
        st["usage_recorded"] += 1 if rows else 0
        st["served_model_known"] += \
            1 if d.get("reported_model") and not d.get("model_unverified") \
            else 0
        ln = st["per_lane"][lane_of(role)]
        ln["dispatches"] += 1
        for f in TOKEN_FIELDS:
            ln["tok"][f].merge(per_field[f])
        pr = per_role[role]
        pr["dispatches"] += 1
        for f in TOKEN_FIELDS:
            pr["tok"][f].merge(per_field[f])
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

    # resolve accumulators → plain snapshot-safe values (None = unknown;
    # `_unknown`/`_reasons` markers only where a null row was recorded)
    def resolve(acc: dict, tok: dict[str, TokSum]) -> dict:
        out = dict(acc)
        for f, ts in tok.items():
            ts.resolve_into(out, f)
        return out

    story_out = {}
    for sid, st in per_story.items():
        d = resolve({k: v for k, v in st.items()
                     if k not in ("tok", "per_lane")}, st["tok"])
        d["per_lane"] = {name: resolve({k: v for k, v in ln.items()
                                        if k != "tok"}, ln["tok"])
                         for name, ln in st["per_lane"].items()}
        story_out[sid] = d
    role_out = {r: resolve({k: v for k, v in d.items() if k != "tok"},
                           d["tok"])
                for r, d in per_role.items()}
    total_tok = fresh_tok()
    for u in usage_rows:
        for f in TOKEN_FIELDS:
            total_tok[f].add(u.get(f), u.get(f + "_reason"))
    totals = resolve({"dispatches": len(dispatches),
                      "usage_rows": len(usage_rows),
                      "loops": len(loops)}, total_tok)
    return {"per_story": story_out, "per_role": role_out, "totals": totals}


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
        if k.endswith("_unknown") or k.endswith("_reasons"):
            continue  # folded into the token field's cell below
        v = t[k]
        if k in TOKEN_FIELDS:
            out.append(f"| {k} | {tok_cell(t, k)} |")
        elif isinstance(v, int):
            out.append(f"| {k} | {v:,} |")
        else:
            out.append(f"| {k} | {v} |")
    out += ["", "## Per story", "",
            "| Story | Dispatches | Input tok | Output tok | Routed | "
            "Usage rows | Served model known | Loops | Merged |",
            "|---|---|---|---|---|---|---|---|---|"]
    for sid, st in sorted(c["per_story"].items()):
        loops = ",".join(f"{k}:{v}" for k, v in sorted(st["loops"].items())) \
            or "0"
        out.append(f"| {sid} | {st['dispatches']} | "
                   f"{tok_cell(st, 'input_tokens')} | "
                   f"{tok_cell(st, 'output_tokens')} | "
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
            cells = [tok_cell(st["per_lane"][ln], "input_tokens")
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
        out.append(f"| {r} | {d['dispatches']} | "
                   f"{tok_cell(d, 'input_tokens')} | "
                   f"{tok_cell(d, 'output_tokens')} | {models} |")
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
        if tk in TOKEN_FIELDS:
            # an unknown total is null (never coerced) — allowed
            if tv is not None and \
                    (not isinstance(tv, (int, float)) or isinstance(tv, bool)):
                return f"`totals.{tk}` must be numeric or null, got {tv!r}"
        elif tk.endswith("_unknown"):
            if not isinstance(tv, int) or isinstance(tv, bool):
                return f"`totals.{tk}` must be an integer, got {tv!r}"
        elif tk.endswith("_reasons"):
            if not isinstance(tv, dict):
                return (f"`totals.{tk}` must be an object, got "
                        f"{type(tv).__name__}")
        elif not isinstance(tv, (int, float)) or isinstance(tv, bool):
            return f"`totals.{tk}` must be numeric, got {tv!r}"
    return None


def diff(base: dict, cur: dict, story: str | None) -> str:
    out = ["# Delta vs baseline", "",
           "| Metric | Baseline | Current | Δ |", "|---|---|---|---|"]

    def side(scope: dict, name: str) -> dict:
        """One metric on one side: value (None = unknown), plus the
        unknown/partial status that suppresses a numeric delta."""
        v = scope.get(name, 0)
        unk = scope.get(name + "_unknown") or 0
        return {"value": v, "unk": unk}

    def fmt(scope: dict, name: str, val: Any) -> str:
        return tok_cell(scope, name) if name in TOKEN_FIELDS \
            else format(val, ",")

    def rows(scope_base, scope_cur, label):
        for name in ("input_tokens", "output_tokens", "dispatches"):
            sb, sc = side(scope_base, name), side(scope_cur, name)
            # unknown/partial on either side → no numeric delta ever:
            # an unavailable measurement can never back a savings claim
            if None in (sb["value"], sc["value"]):
                status = "unknown"
            elif sb["unk"] or sc["unk"]:
                status = "partial"
            else:
                status = None
            if status:
                out.append(f"| {label} {name} | "
                           f"{fmt(scope_base, name, sb['value'])} | "
                           f"{fmt(scope_cur, name, sc['value'])} | "
                           f"{status} |")
                continue
            b, c = sb["value"], sc["value"]
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
