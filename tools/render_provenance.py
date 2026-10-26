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
    never introduce one). Non-canonical ids are ledger data, never path
    components — each distinct skipped id is reported once on stderr."""
    seen: list[str] = []
    skipped: set[str] = set()
    for e in events:
        sid = e.get("story_id")
        if not isinstance(sid, str):
            continue
        if not RSt.is_canonical_sid(sid):
            if sid not in skipped:
                skipped.add(sid)
                print(f"skipping non-canonical story id from ledger: {sid}",
                      file=sys.stderr)
            continue
        if sid not in seen:
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
    """Escape a value for a markdown table cell — round 32 S2: made
    IDENTICAL to render_state._cell (CR/CRLF flattened too, not just
    LF — a lone CR value would otherwise split the row and violate the
    LF byte contract)."""
    return (str(s).replace("|", "\\|")
            .replace("\r\n", " ").replace("\r", " ").replace("\n", " "))


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
        out += ["| Recorded at (UTC) | From → To | Repo | Requested by | "
                "Approved by (human gate) | Checks |",
                "|---|---|---|---|---|---|"]
        for t in trans:
            ap = t.get("approved_by")
            # round 26 AA2: the formatted null reason is untrusted text —
            # escape it BEFORE wrapping in emphasis so a `|`/newline in
            # approved_by_reason cannot corrupt the timeline row
            gate = _cell(ap) if ap else \
                _cell(f"_{fmt_null(None, 'approved_by_reason', t)}_")
            checks = ", ".join(_cell(c) for c in t.get("checks_run", []))
            # round 32 S2: recorded_at/from_state/to_state escaped too
            out.append(f"| {_cell(t['recorded_at'])} "
                       f"| {_cell(t['from_state'])} → "
                       f"**{_cell(t['to_state'])}** "
                       f"| {_cell(t.get('repo') or '—')} "
                       f"| {_cell(t.get('requested_by'))} "
                       f"| {gate} | {checks} |")
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
            co = f" (+{','.join(_cell(c) for c in lp['co_classes'])})" \
                if lp.get("co_classes") else ""
            # round 32 S2: date/stage/loop_n/class escaped too
            out.append(f"| {_cell(lp.get('occurred_on', lp['recorded_at'][:10]))} "
                       f"| {_cell(lp['stage'])} | {_cell(lp['loop_n'])} "
                       f"| {_cell(lp['class'])}{co} "
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
        # R15-P2: the trace carries the DECISION and the RESPONDENT —
        # identical approved and rejected gates must not render
        # byte-identical PROVENANCE.md. Pending gates render the
        # house-rule null form from their sibling <field>_reason.
        out += ["| Gate | Question | Asked | Answered | Decision | Answered by |",
                "|---|---|---|---|---|---|"]
        for i, g in enumerate(gates):
            if g.get("answered_at"):
                ans = g["answered_at"]
            else:
                # append-only ledger: an ask row stays unanswered forever
                # once a later answered row for the same (story, gate)
                # records the reply — mirror render_state.py's supersede
                supers = [s.get("event_id") for s in gates[i + 1:]
                          if s.get("gate") == g.get("gate")
                          and s.get("answered_at")]
                ans = f"superseded by {supers[0]}" if supers else \
                    f"PENDING — {g.get('answered_at_reason') or 'no reason recorded'}"
            if g.get("answered_at"):
                dec = _cell(g["decision"])
                aby = _cell(g["answered_by"])
            else:
                # round 26 AA2: formatted null reasons are untrusted text
                dec = _cell(fmt_null(g.get("decision"),
                                     "decision_reason", g))
                aby = _cell(fmt_null(g.get("answered_by"),
                                     "answered_by_reason", g))
            # round 32 S2: gate/asked_at escaped too
            out.append(f"| {_cell(g['gate'])} | {_cell(g['question'])} "
                       f"| {_cell(g['asked_at'])} | {_cell(ans)} "
                       f"| {dec} | {aby} |")
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
        def _total_s(key: str) -> str:
            """Totals preserve unknown/partial status (mirrors
            metrics_report.tok_cell): every row measurable → plain total;
            mixed → '<sum> partial (N unknown rows)'; all unknown →
            'unknown (<reason>)' — never an unqualified number, never a
            zero."""
            vals = [u.get(key) for u in usage]
            known = [v for v in vals if v is not None]
            n_unknown = len(vals) - len(known)
            if known:
                total = sum(known)
                if n_unknown:
                    return f"{total:,} partial ({n_unknown} unknown rows)"
                return str(total)
            reasons = Counter(u.get(key + "_reason") or "no reason recorded"
                              for u in usage)
            why = "; ".join(r if n == 1 else f"{r} ×{n}"
                            for r, n in sorted(reasons.items()))
            return f"unknown ({why})" if why else "unknown"

        distinct_usage = len({u.get("dispatch_id") for u in usage})
        out += ["| Measure | Value |", "|---|---|",
                f"| Dispatches with usage | {distinct_usage} |",
                f"| Input tokens | {_cell(_total_s('input_tokens'))} |",
                f"| Output tokens | {_cell(_total_s('output_tokens'))} |",
                f"| Cached input tokens | "
                f"{_cell(_total_s('cached_input_tokens'))} |",
                "",
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
                       f"| {_cell(fmt_null(u.get('input_tokens'), 'input_tokens_reason', u))} "
                       f"| {_cell(fmt_null(u.get('output_tokens'), 'output_tokens_reason', u))} "
                       f"| {_cell(fmt_null(u.get('cached_input_tokens'), 'cached_input_tokens_reason', u))} |")
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
    # R19-T2: an EMPTY --all discovery (empty/missing ledger, or only
    # non-canonical ids deliberately skipped) is a successful no-op —
    # the usage error belongs to invocations WITHOUT --all only (same
    # rule as render_state R16-Q3).
    if not sids and not args.all:
        print("usage: render_provenance.py <story-id>... | --all",
              file=sys.stderr)
        return 2
    for sid in sids:  # refuse BEFORE building any path (no read, no write)
        if not RSt.is_canonical_sid(sid):
            print(f"REFUSED: story id {sid} is not a canonical STORY-NNN",
                  file=sys.stderr)
            return 1
    # Round 28 AC1 (sibling of render_state): spelling is not confinement —
    # validate the RESOLVED story dir / PROVENANCE.md target / sidecar for
    # EVERY id BEFORE any render or write, so a canonical id whose story
    # directory is a SYMLINK outward refuses instead of overwriting an
    # unrelated PROVENANCE.md.
    for sid in sids:
        esc = RSt.story_dir_escape(stories_root, sid, "PROVENANCE.md")
        if esc:
            print(esc, file=sys.stderr)
            return 1
    rc = 0
    wrote = 0
    for sid in sids:
        text = render(events, sid, header)
        p = stories_root / sid / "PROVENANCE.md"
        if args.check:
            # R14-O4: compare raw BYTES — read_text() would normalize a
            # CRLF-mangled file back to LF and let it pass --check
            # despite violating the required LF format.
            # Round 26 AA4: a MISSING artifact for a story in the ledger
            # is stale too (rc 1) — deleting PROVENANCE.md must not make
            # --check report fresh. The handwritten-file exemption stays:
            # an existing file WITHOUT the generated marker is prose the
            # renderer never owned, so it is skipped.
            cur = p.read_bytes() if p.exists() else None
            if cur is None:
                print(f"OUT OF DATE: {p} (missing)")
                rc = 1
            elif cur.startswith(HEADER_START.encode("utf-8")) \
                    and cur != text.encode("utf-8"):
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
