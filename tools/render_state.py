#!/usr/bin/env python3
"""tools/render_state.py — render stories/<id>/state.md FROM the ledger.

state.md is an OUTPUT, never an input (architecture.md §Canonical layouts —
generated renders, never hand-edited). Hand-narrative prose lives in a
`narrative.md` sidecar next to it, which this renderer inlines verbatim.

    python3 tools/render_state.py [--root <control-home>] <story-id> [...]
    python3 tools/render_state.py [--root R] --all     # every story with events
    python3 tools/render_state.py [--root R] <id> --check   # exit 1 if stale
    python3 tools/render_state.py [--root R] --ledger PATH ...  # alt ledger

Render byte contract (architecture.md §Data model): derives from the ledger
only; UTC ISO-8601 timestamps come from the events themselves; LF endings;
trailing newline; NO wall-clock content — same ledger + same tree renders
byte-identical output.

Output carries the header:
    <!-- GENERATED from factory/ledger/events.jsonl — do not hand-edit;
         append events instead -->

Exit codes (config_validator convention): 0 ok (incl. --check fresh) ·
1 --check stale · 2 usage / ledger refusal. Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

try:
    from . import ledger_lib as L
except ImportError:  # direct-run layout: load the tools package itself
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools import ledger_lib as L


def _cell(s) -> str:
    """Escape a value for a markdown table cell (mirrors
    render_provenance._cell exactly) — an unescaped `|` adds a column and
    shifts every following field; an embedded newline breaks the row."""
    return str(s).replace("|", "\\|").replace("\n", " ")


def workspace_name(root: Path) -> str:
    """Workspace display name from <root>/workspace.toml (`workspace` key),
    else the generic default `factory` — renders are never project-named."""
    p = root / "workspace.toml"
    try:
        doc = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return "factory"
    name = doc.get("workspace")
    return name if isinstance(name, str) and name.strip() else "factory"


def generated_header(ledger_rel: str) -> str:
    """The frozen render header (T5 byte contract)."""
    return (f"<!-- GENERATED from {ledger_rel} — do not hand-edit; "
            "append events instead -->")


def ledger_display(root: Path, ledger_path: Path) -> str:
    """Ledger path relative to the control home when possible."""
    try:
        return str(ledger_path.resolve().relative_to(root))
    except ValueError:
        return str(ledger_path)


def is_canonical_sid(sid) -> bool:
    """True only for canonical STORY-NNN ids (fullmatch on ledger_schema's
    STORY_ID_RE). Story ids become path components under stories/ — a
    schema-valid but non-canonical string (`../outside`, an absolute path)
    must never be used to build a path."""
    return isinstance(sid, str) and L.S.STORY_ID_RE.fullmatch(sid) is not None


def stories_in_ledger(events: list[dict]) -> list[str]:
    """Canonical story order for --all: first-appearance order of story_id
    (usage rows carry no story_id and never introduce one). Non-canonical
    ids are ledger data, never path components — each distinct skipped id
    is reported once on stderr."""
    seen: list[str] = []
    skipped: set[str] = set()
    for e in events:
        sid = e.get("story_id")
        if not isinstance(sid, str):
            continue
        if not is_canonical_sid(sid):
            if sid not in skipped:
                skipped.add(sid)
                print(f"skipping non-canonical story id from ledger: {sid}",
                      file=sys.stderr)
            continue
        if sid not in seen:
            seen.append(sid)
    return seen


def fmt_null(v, reason_key: str, evt: dict) -> str:
    """House rule: nulls render with their <field>_reason — never as an
    invented zero."""
    if v is not None:
        return str(v)
    return f"— ({evt.get(reason_key) or 'no reason recorded'})"


def render(events: list[dict], sid: str, header: str,
           stories_root: Path) -> str:
    evs = L.events_for(events, sid)
    out: list[str] = [header, "", f"# Story: {sid}", ""]

    # ---- current status
    state = L.current_state(events, sid)
    if state is None:
        outcomes = [e for e in evs if e["event"] == "outcome"
                    and e.get("merged_sha")]
        if outcomes:
            state = ("LIVE_PROVEN (derived from backfilled outcome)"
                     if outcomes[-1].get("live_proof_status") == "proven"
                     else "MERGED (derived from backfilled outcome)")
        elif evs:
            state = "no transitions recorded (events only; see below)"
        else:
            state = "BACKLOG (no events)"
    out += [f"**Current state:** `{state}`", ""]

    # ---- ownership (claim lifecycle — kit assignment ops)
    assigns = [e for e in evs if e["event"] == "assignment"]
    if assigns:
        out += ["## Ownership (claim lifecycle)", ""]
        for a in assigns:
            op = a.get("op")
            if op == "claim":
                pin = a.get("start_sha")
                if pin is None and isinstance(a.get("start_shas"), dict):
                    pin = ", ".join(f"{r}@{s}" for r, s in
                                    sorted(a["start_shas"].items()))
                out.append(f"- **claim** by {a.get('owner')} on "
                           f"{a.get('host')} (session {a.get('session')}) "
                           f"@ {fmt_null(a.get('claimed_at'), 'claimed_at_reason', a)}")
                out.append(f"  - start pin: {fmt_null(pin, 'start_sha_reason', a)}")
            elif op == "handover":
                out.append(f"- **handover** {a.get('from')} → {a.get('to')}: "
                           f"{a.get('reason')}"
                           + (" _(flagged: abrupt takeover)_"
                              if a.get("flagged") else ""))
            elif op == "release":
                out.append(f"- **release** by {a.get('owner')} "
                           f"(implicit at CLOSED; record persists)")
        out.append("")

    # ---- transitions
    trans = [e for e in evs if e["event"] == "transition"]
    if trans:
        out += ["## Transitions (controller-enforced)", "",
                "| Recorded at (UTC) | From | To | Repo | Requested by | Checks | Approved by |",
                "|---|---|---|---|---|---|---|"]
        for t in trans:
            checks = ", ".join(t.get("checks_run", []))
            ap = t.get("approved_by") or fmt_null(None, "approved_by_reason", t)
            out.append(f"| {t['recorded_at']} | {t['from_state']} | "
                       f"**{t['to_state']}** | {t.get('repo') or '—'} | "
                       f"{t.get('requested_by')} | "
                       f"{checks} | {ap} |")
        out.append("")

    # ---- stages
    stages = [e for e in evs if e["event"] == "stage"]
    if stages:
        out += ["## Stages", "",
                "| Stage | Passed | Failed | Skipped | Excluded | Commit | Evidence |",
                "|---|---|---|---|---|---|---|"]
        for s in stages:
            out.append(
                f"| {s.get('stage')} "
                f"| {_cell(fmt_null(s.get('tests_passed'), 'tests_passed_reason', s))} "
                f"| {_cell(fmt_null(s.get('tests_failed'), 'tests_failed_reason', s))} "
                f"| {_cell(fmt_null(s.get('tests_skipped'), 'tests_skipped_reason', s))} "
                f"| {_cell(fmt_null(s.get('tests_excluded'), 'tests_excluded_reason', s))} "
                f"| {s.get('commit_sha') or '—'} "
                f"| {s.get('evidence_path') or '—'} |")
        out.append("")

    # ---- loops
    loops = [e for e in evs if e["event"] == "loop"]
    out += [f"## Revision loops ({len(loops)}) — tagged at the moment they "
            "happened, never backfilled", ""]
    if loops:
        out += ["| Date | Stage | Loop | Class | Root cause (one line) |",
                "|---|---|---|---|---|"]
        for lp in loops:
            co = f" (+{','.join(lp['co_classes'])})" if lp.get("co_classes") else ""
            out.append(f"| {lp.get('occurred_on', lp['recorded_at'][:10])} "
                       f"| {lp['stage']} | {lp['loop_n']} "
                       f"| {lp['class']}{co} "
                       f"| {_cell(lp['root_cause_one_line'])} |")
    else:
        out.append("(none)")
    out.append("")

    # ---- immutable review contexts
    contexts = [e for e in evs if e["event"] == "review_context"]
    if contexts:
        out += ["## Review contexts", "",
                "| Event | Role | Reviewed SHA | Base SHA | Prompt | Manifest |",
                "|---|---|---|---|---|---|"]
        for ctx in contexts:
            out.append(f"| {ctx['event_id']} | {ctx['review_role']} "
                       f"| `{ctx['reviewed_sha']}` | `{ctx['base_sha']}` "
                       f"| {ctx['prompt_version']} "
                       f"| `{ctx['context_manifest_path']}` "
                       f"(`{ctx['context_manifest_digest'][:12]}…`) |")
        out.append("")

    # ---- findings (latest lifecycle row only)
    finds = L.latest_findings(evs)
    out += [f"## Findings ({len(finds)})", ""]
    if finds:
        out += ["| ID | Source | Class | Severity | Logic touched | Disposition | Fix | Overlap |",
                "|---|---|---|---|---|---|---|---|"]
        for f in finds:
            fix = f.get("fix_sha") or fmt_null(None, "fix_sha_reason", f)
            ov = f.get("overlap_id") or "—"
            out.append(
                f"| {f.get('finding_id') or '—'} "
                f"| {f.get('source')} | {f.get('class')} "
                f"| {f.get('severity') or fmt_null(None, 'severity_reason', f)} "
                f"| {f.get('logic_touched')} | {f.get('disposition')} "
                f"| {fix} | {ov} |")
        out.append("")

    # ---- same-snapshot reviewer attribution
    attrs = [e for e in evs if e["event"] == "review_attribution"]
    if attrs:
        out += ["## Review attribution (same snapshot + context)", "",
                "| Finding | Classification | Internal context | External context | Evidence |",
                "|---|---|---|---|---|"]
        for a in attrs:
            out.append(f"| {a['finding_id']} | {a['classification']} "
                       f"| {a['internal_context_event_id']} "
                       f"| {a['external_context_event_id']} "
                       f"| {a['evidence_path']} |")
        out.append("")

    # ---- human gates
    gates = [e for e in evs if e["event"] == "human_gate"]
    if gates:
        # R15-P3: the state table carries the DECISION and the RESPONDENT —
        # an approved and a rejected gate must render distinguishably.
        # Pending gates show the house-rule null form (`— (<reason>)`)
        # from their sibling <field>_reason, never an invented value.
        out += ["## Human gates", "",
                "| Gate | Question | Asked | Answered | Decision | Answered by |",
                "|---|---|---|---|---|---|"]
        for i, g in enumerate(gates):
            answered = g.get("answered_at") or "PENDING"
            if not g.get("answered_at"):
                # append-only ledger: an ask row stays unanswered forever once
                # a later answered row for the same (story, gate) records the
                # reply
                supers = [s.get("event_id") for s in gates[i + 1:]
                          if s.get("gate") == g.get("gate")
                          and s.get("answered_at")]
                if supers:
                    answered = f"superseded by {supers[0]}"
            if g.get("answered_at"):
                dec = g["decision"]
                aby = g["answered_by"]
            else:
                dec = fmt_null(g.get("decision"), "decision_reason", g)
                aby = fmt_null(g.get("answered_by"), "answered_by_reason", g)
            out.append(f"| {g['gate']} | {_cell(g['question'])} "
                       f"| {g['asked_at']} "
                       f"| {answered} | {dec} | {aby} |")
        out.append("")

    # ---- outcome
    for o in [e for e in evs if e["event"] == "outcome"]:
        out += ["## Outcome", ""]
        out.append(f"- merged: {fmt_null(o.get('merged_sha'), 'merged_sha_reason', o)}"
                   f" @ {fmt_null(o.get('merged_at'), 'merged_at_reason', o)}")
        out.append(f"- live proof: "
                   f"{fmt_null(o.get('live_proof_status'), 'live_proof_status_reason', o)}")
        if o.get("totals"):
            out.append(f"- totals: {o['totals']}")
        out.append(f"- observation window end: "
                   f"{fmt_null(o.get('observation_end'), 'observation_end_reason', o)}")
        out.append("")

    # ---- corrections (never rewrite history — correct forward)
    corr = [e for e in evs if e["event"] == "correction"]
    if corr:
        out += ["## Corrections (correct forward, never rewrite history)", ""]
        for c in corr:
            out.append(f"- **Contradicted:** {c['contradicted_claim']}")
            out.append(f"  **Corrected:** {c['corrected_value']}")
            out.append(f"  **Citation:** {c['source_citation']} → "
                       f"{c['corrected_artifact']}")
        out.append("")

    # ---- narrative sidecar (inlined verbatim — no strip: hand-written
    # prose may open with an indented block or carry trailing whitespace)
    narr = stories_root / sid / "narrative.md"
    if narr.exists():
        out += ["## History (from narrative.md sidecar)", "",
                narr.read_text(encoding="utf-8"), ""]
    else:
        out += ["## History", "",
                "_No narrative.md sidecar yet. Hand-written history belongs "
                "in git; move prose here when convenient._", ""]

    out += ["---",
            f"_Rendered from {len(evs)} ledger events "
            f"({sum(1 for e in evs if e.get('provenance') == 'backfilled')} "
            "backfilled) by tools/render_state.py._"]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="render stories/<id>/state.md from the ledger")
    ap.add_argument("story_ids", nargs="*")
    ap.add_argument("--all", action="store_true",
                    help="every story_id with ledger events")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when any rendered state.md is out of date")
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
    header = generated_header(ledger_display(root, ledger))
    stories_root = root / "stories"

    sids = stories_in_ledger(events) if args.all else args.story_ids
    # R16-Q3: an EMPTY --all discovery (empty/missing ledger, or only
    # non-canonical ids deliberately skipped) is a successful no-op —
    # the usage error belongs to invocations WITHOUT --all only.
    if not sids and not args.all:
        print("usage: render_state.py <story-id>... | --all", file=sys.stderr)
        return 2
    for sid in sids:  # refuse BEFORE building any path (no read, no write)
        if not is_canonical_sid(sid):
            print(f"REFUSED: story id {sid} is not a canonical STORY-NNN",
                  file=sys.stderr)
            return 1
    rc = 0
    for sid in sids:
        text = render(events, sid, header, stories_root)
        p = stories_root / sid / "state.md"
        if args.check:
            # R14-O4: compare raw BYTES — read_text() would normalize a
            # CRLF-mangled file back to LF and let it pass --check
            # despite violating the required LF format.
            cur = p.read_bytes() if p.exists() else None
            if cur is not None \
                    and cur.startswith("<!-- GENERATED from"
                                       .encode("utf-8")) \
                    and cur != text.encode("utf-8"):
                print(f"OUT OF DATE: {p}")
                rc = 1
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        print(f"rendered {p}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
