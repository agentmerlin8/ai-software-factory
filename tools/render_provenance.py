#!/usr/bin/env python3
"""tools/render_provenance.py — render stories/<id>/PROVENANCE.md FROM the
ledger (architecture.md §Control plane — the traceability artifact;
generated renders, never hand-edited): trace a delivered feature back to
the decisions and evidence that produced it — ownership → state timeline →
reviews/loops → delivery → token usage → evidence index. Tokens only (no
cost fields — the kit's usage schema is tokens-only).

    python3 tools/render_provenance.py [--root <control-home>] <story-id> [...]
    python3 tools/render_provenance.py [--root R] --all   # every story with events
    python3 tools/render_provenance.py [--root R] <id> --check  # exit 1 if stale
    python3 tools/render_provenance.py [--root R] --ledger PATH ...  # alt ledger

House rule: a null value is rendered as "not recorded: <reason>" from its
sibling <field>_reason — NEVER as an invented zero.

Render byte contract (architecture.md §Data model): ledger only; LF
endings; trailing newline; NO wall-clock content — same ledger + same tree
renders byte-identical output (the reference's generated-at banner stamp is
dropped by design; deviation noted in the port).

Output carries the header:
    <!-- GENERATED from factory/ledger/events.jsonl — do not hand-edit;
         append events instead -->

Exit codes (config_validator convention): 0 ok (incl. --check fresh) ·
1 --check stale · 2 usage / ledger refusal. Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

try:
    from . import ledger_lib as L
    from . import render_state as RSt
except ImportError:  # direct-run layout: load the tools package itself
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools import ledger_lib as L
    from tools import render_state as RSt

TOOL = "tools/render_provenance.py"
HEADER_START = "<!-- GENERATED from"


def fmt_null(v, reason_key: str, evt: dict) -> str:
    """House rule: nulls render as 'not recorded: <reason>', never as zeros."""
    if v is not None:
        return str(v)
    return f"not recorded: {evt.get(reason_key) or 'no reason recorded'}"


def stories_in_ledger(events: list[dict]) -> list[str]:
    """Every story_id with ledger events, in first-appearance order (same
    canonical order as render_state.py; usage rows carry no story_id and
    never introduce one)."""
    seen: list[str] = []
    for e in events:
        sid = e.get("story_id")
        if isinstance(sid, str) and sid not in seen:
            seen.append(sid)
    return seen


def usage_for_story(events: list[dict], sid: str) -> list[dict]:
    """usage rows carry dispatch_id, not story_id — join them to the story
    through their dispatch row; fall back to the dispatch_id prefix
    (<story>-<role>-<attempt>-<unix>) for usage rows whose dispatch row is
    missing."""
    dispatch_story = {e["event_id"]: e.get("story_id")
                      for e in events if e.get("event") == "dispatch"}
    out = []
    for e in events:
        if e.get("event") != "usage":
            continue
        did = e.get("dispatch_id")
        if did is None:
            continue
        owner = dispatch_story.get(did)
        if owner is None and isinstance(did, str) and did.startswith(f"{sid}-"):
            owner = sid
        if owner == sid:
            out.append(e)
    return out


def dispatch_rows(events: list[dict], sid: str) -> dict[str, dict]:
    """Latest dispatch row per dispatch_id (the completion row supersedes
    the pre-spawn row — the two-row dispatch lifecycle; fields merge so
    pre-spawn values survive unless the completion overwrites them)."""
    rows: dict[str, dict] = {}
    for e in L.events_for(events, sid, "dispatch"):
        rows[e["event_id"]] = {**rows.get(e["event_id"], {}), **e}
    return rows


def _cell(s) -> str:
    """Escape a value for a markdown table cell."""
    return str(s).replace("|", "\\|").replace("\n", " ")


def _tally_table(counter: Counter, header: str) -> list[str]:
    out = [f"| {header} | Findings |", "|---|---|"]
    for key in sorted(counter, key=lambda k: (-counter[k], str(k))):
        out.append(f"| {_cell(key)} | {counter[key]} |")
    return out


def render(events: list[dict], sid: str, header: str) -> str:
    evs = L.events_for(events, sid)
    out: list[str] = [header, "", f"# Provenance: {sid}", ""]

    # ---- Identity & ownership
    out += ["## Identity & ownership", ""]
    assignments = [e for e in evs if e["event"] == "assignment"]
    if assignments:
        for a in assignments:
            op = a.get("op")
            out.append(f"- **op:** {op}")
            if op == "claim":
                out.append(f"- **owner:** {a.get('owner')}")
                out.append(f"- **host:** {a.get('host')}")
                out.append(f"- **session:** {a.get('session')}")
                out.append(f"- **claimed_at:** "
                           f"{fmt_null(a.get('claimed_at'), 'claimed_at_reason', a)}")
                pin = a.get("start_sha")
                pin_label = "start_sha"
                if pin is None and isinstance(a.get("start_shas"), dict):
                    pin = {r: s for r, s in sorted(a["start_shas"].items())}
                    pin_label = "start_shas"
                out.append(f"- **{pin_label}:** "
                           f"{fmt_null(pin, 'start_sha_reason', a)}")
            elif op == "handover":
                out.append(f"- **from → to:** {a.get('from')} → {a.get('to')}")
                out.append(f"- **reason:** {a.get('reason')}")
                out.append(f"- **flagged:** {a.get('flagged')}")
            elif op == "release":
                out.append(f"- **owner:** {a.get('owner')}")
            out.append("")
    else:
        out += ["No assignment event recorded for this story.", ""]

    # ---- State timeline
    out += ["## State timeline", ""]
    trans = [e for e in evs if e["event"] == "transition"]
    if trans:
        out += ["| Recorded at (UTC) | From → To | Requested by | Approved by "
                "(human gate) | Checks |", "|---|---|---|---|---|"]
        for t in trans:
            ap = t.get("approved_by")
            gate = _cell(ap) if ap else \
                f"_{fmt_null(None, 'approved_by_reason', t)}_"
            checks = ", ".join(t.get("checks_run", []))
            out.append(f"| {t['recorded_at']} | {t['from_state']} → "
                       f"**{t['to_state']}** | {_cell(t.get('requested_by'))} "
                       f"| {gate} | {_cell(checks)} |")
        gated = [t for t in trans if t.get("approved_by")]
        out += ["", f"_{len(trans)} transition(s); {len(gated)} human-gated "
                "(approved_by present)._", ""]
    else:
        out += ["No controller transitions recorded (events-only or "
                "backfilled history).", ""]

    # ---- Review & loops
    out += ["## Review & loops", ""]
    loops = [e for e in evs if e["event"] == "loop"]
    out += [f"### Revision loops ({len(loops)})", ""]
    if loops:
        out += ["| Date | Stage | Loop | Class | Root cause (one line) |",
                "|---|---|---|---|---|"]
        for lp in loops:
            co = f" (+{','.join(lp['co_classes'])})" if lp.get("co_classes") else ""
            out.append(f"| {lp.get('occurred_on', lp['recorded_at'][:10])} "
                       f"| {lp['stage']} | {lp['loop_n']} | {lp['class']}{co} "
                       f"| {_cell(lp['root_cause_one_line'])} |")
        out.append("")

    finds = L.latest_findings(evs)
    out += [f"### Findings tally ({len(finds)} latest lifecycle rows)", ""]
    if finds:
        by_source = Counter(f.get("source") for f in finds)
        by_class = Counter(f.get("class") for f in finds)
        by_sev = Counter(f.get("severity") if f.get("severity") is not None
                         else "not recorded" for f in finds)
        for tbl in (_tally_table(by_source, "Source"),
                    _tally_table(by_class, "Class"),
                    _tally_table(by_sev, "Severity")):
            out += tbl + [""]
    else:
        out += ["No findings recorded.", ""]

    gates = [e for e in evs if e["event"] == "human_gate"]
    out += [f"### Human gates ({len(gates)})", ""]
    if gates:
        out += ["| Gate | Question | Asked | Answered |", "|---|---|---|---|"]
        for g in gates:
            ans = g.get("answered_at") or \
                f"PENDING — {g.get('answered_at_reason') or 'no reason recorded'}"
            out.append(f"| {g['gate']} | {_cell(g['question'])} "
                       f"| {g['asked_at']} | {_cell(ans)} |")
        out.append("")

    contexts = [e for e in evs if e["event"] == "review_context"]
    attrs = [e for e in evs if e["event"] == "review_attribution"]
    out += ["### Review contexts & attribution", ""]
    if contexts or attrs:
        out.append(f"- review_context events: {len(contexts)}"
                   + (f" ({', '.join(c['review_role'] for c in contexts)})"
                      if contexts else ""))
        if attrs:
            kinds = Counter(a["classification"] for a in attrs)
            detail = "; ".join(f"{k} {kinds[k]}" for k in sorted(kinds))
            out.append(f"- review_attribution events: {len(attrs)} — {detail}")
        else:
            out.append("- review_attribution events: 0")
        out.append("")
    else:
        out += ["No review_context / review_attribution events recorded "
                "(unavailable is never guessed).", ""]

    # ---- Delivery
    out += ["## Delivery", ""]
    outcomes = [e for e in evs if e["event"] == "outcome"]
    if outcomes:
        for o in outcomes:
            out.append(f"- **merged_sha:** "
                       f"{fmt_null(o.get('merged_sha'), 'merged_sha_reason', o)}")
            out.append(f"- **merged_at:** "
                       f"{fmt_null(o.get('merged_at'), 'merged_at_reason', o)}")
            out.append(f"- **live_proof_status:** "
                       f"{fmt_null(o.get('live_proof_status'), 'live_proof_status_reason', o)}")
            out.append(f"- **observation_end:** "
                       f"{fmt_null(o.get('observation_end'), 'observation_end_reason', o)}")
            out.append(f"- **escaped_defects:** "
                       f"{fmt_null(o.get('escaped_defects'), 'escaped_defects_reason', o)}")
            if o.get("totals"):
                out.append(f"- **totals:** `{o['totals']}`")
            if o.get("recommendation"):
                out.append(f"- **recommendation:** {_cell(o['recommendation'])}")
            out.append("")
    else:
        out += ["No outcome event recorded yet.", ""]

    # ---- Token usage
    out += ["## Token usage", ""]
    usage = usage_for_story(events, sid)
    dispatches = dispatch_rows(events, sid)
    out.append(f"Dispatches recorded: {len(dispatches)} "
               f"(distinct dispatch_id lifecycles); usage events: "
               f"{len(usage)}.")
    out.append("")
    if usage:
        def _sum(key: str) -> int | None:
            vals = [u[key] for u in usage if u.get(key) is not None]
            return sum(vals) if vals else None

        in_tok, out_tok = _sum("input_tokens"), _sum("output_tokens")
        cached = _sum("cached_input_tokens")
        cached_s = str(cached) if cached is not None else \
            f"not recorded: {usage[0].get('cached_input_tokens_reason') or 'no reason recorded'}"
        out += ["| Measure | Value |", "|---|---|",
                f"| Dispatches with usage | {len(usage)} |",
                f"| Input tokens | {in_tok} |",
                f"| Output tokens | {out_tok} |",
                f"| Cached input tokens | {cached_s} |", "",
                "### Per-dispatch usage", "",
                "| Dispatch | Role | Model | Input | Output | Cached |",
                "|---|---|---|---|---|---|"]
        for u in usage:
            did = u["dispatch_id"]
            d = dispatches.get(did, {})
            model = d.get("reported_model") or (
                f"{d.get('requested_model')} (requested; reported "
                f"{fmt_null(None, 'reported_model_reason', d)})"
                if d.get("requested_model") else
                fmt_null(None, "reported_model_reason", d))
            role = d.get("role") or (did.rsplit("-", 3)[-3]
                                     if did and did.count("-") >= 3 else "—")
            out.append(f"| {_cell(did)} | {_cell(role)} | {_cell(model)} "
                       f"| {u['input_tokens']} | {u['output_tokens']} "
                       f"| {fmt_null(u.get('cached_input_tokens'), 'cached_input_tokens_reason', u)} |")
        out += ["", f"_token_semantics: {usage[0].get('token_semantics')} · "
                f"usage_source: `{usage[0].get('usage_source')}`_", ""]
    else:
        out += ["No usage events recorded for this story.", ""]

    # ---- Evidence index
    out += ["## Evidence index", ""]
    paths = sorted({e["evidence_path"] for e in evs
                    if isinstance(e.get("evidence_path"), str)
                    and e["evidence_path"].strip()})
    if paths:
        out += [f"- `{p}`" for p in paths]
    else:
        out += ["No evidence_path values recorded."]
    out.append("")

    out += ["---",
            f"_Rendered from {len(evs)} ledger events "
            f"({sum(1 for e in evs if e.get('provenance') == 'backfilled')} "
            f"backfilled) by {TOOL}._"]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="render stories/<id>/PROVENANCE.md from the ledger")
    ap.add_argument("story_ids", nargs="*")
    ap.add_argument("--all", action="store_true",
                    help="every story_id with ledger events")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when any rendered PROVENANCE.md on disk is "
                         "out of date")
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
    stories_root = root / "stories"

    sids = stories_in_ledger(events) if args.all else args.story_ids
    if not sids:
        print("usage: render_provenance.py <story-id>... | --all",
              file=sys.stderr)
        return 2
    rc = 0
    wrote = 0
    for sid in sids:
        text = render(events, sid, header)
        p = stories_root / sid / "PROVENANCE.md"
        if args.check:
            cur = p.read_text(encoding="utf-8") if p.exists() else None
            if cur is not None and cur.startswith(HEADER_START) \
                    and cur != text:
                print(f"OUT OF DATE: {p}")
                rc = 1
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        wrote += 1
        print(f"rendered {p}")
    if args.check:
        print("--check: STALE" if rc else "--check: CLEAN")
    else:
        print(f"wrote {wrote} file(s)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
