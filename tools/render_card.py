#!/usr/bin/env python3
"""tools/render_card.py — the factory story card (render + issue upsert).

Renders the current factory card for one story FROM THE LEDGER — current
state, owner, next legal moves, open findings summary, evidence pointers —
and, with `--issue`, upserts it as the marker-anchored card comment on a
GitHub issue. The card is generated, never hand-edited.

    python3 tools/render_card.py [--root R] STORY-NNN
    python3 tools/render_card.py [--root R] STORY-NNN --issue 42 [--repo O/N]
    python3 tools/render_card.py [--root R] STORY-NNN --issue 42 --dry-run

Determinism (frozen render contract): derives from the ledger only; NO
wall-clock content — same ledger + same tree renders byte-identical
output. The footer pins the ledger digest instead of a timestamp.

Behaviors:
* Upsert target selection matches the PIN marker
  ``<!-- factory-card:pin -->`` (written only by the upsert path); the
  card body carries the identity marker ``<!-- factory-card -->``. A
  markdown-only reply embedding the card can never capture the pin.
  Newest pinned comment wins (PATCH); no pin yet → POST. `--dry-run`
  prints the exact payload and performs no API call.
* Unanswered human_gate rows superseded by a later answered row for the
  same gate are NOT pending — the ledger is append-only, so resolution is
  a new row, never an edit.
* Unknown story ids (no ledger events, no story file, no intake mention)
  render an explanatory refusal card and are NEVER pinned.
* Next-move hints use the installed CLI form:
  ``python3 factory/tools/controller.py request <sid> <STATE> ...``
  (canonical-layout display). Legal moves come from tools/state_machine
  order helpers (next state / BLOCKED resume / FIXING reentry / the
  declared non-UI skip) — never re-derived here.
* Review vocabulary is `external` (no copilot strings).

The gh seam is injectable (`gh_fn`) exactly like the reference: the real
transport shells `gh api ...` (issues:write assumed); tests stub it — no
network, no gh calls.

Exit codes (config_validator convention): 0 success · 1 refusal ·
2 usage / configuration. Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

try:
    from . import controller as Ctl
    from . import ledger_lib as L
    from . import state_machine as SM
    from . import sync_board as SB
except ImportError:  # direct-run layout: load the tools package itself
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools import controller as Ctl
    from tools import ledger_lib as L
    from tools import state_machine as SM
    from tools import sync_board as SB

# Identity marker: present on every rendered card (replies included).
CARD_MARKER = "<!-- factory-card -->"
# Pin marker: written ONLY by the upsert path; the upsert selector matches
# THIS, so markdown-only replies can never become the target.
PIN_MARKER = "<!-- factory-card:pin -->"

# Curated intake surfaces scanned for story-id mentions (pre-ledger
# candidates are "known" before any ledger event exists).
KNOWN_SCAN_FILES = ("stories/ALIAS.md", "stories/BACKLOG.md",
                    "factory/DASHBOARD.md")

# Latest-disposition values that leave a finding OPEN on the card.
OPEN_DISPOSITIONS = ("accepted", "deferred", "fix_in_revision",
                     "record_only")


def ledger_digest(ledger_path: Path) -> str:
    """Deterministic ledger fingerprint for the footer (no wall-clock)."""
    data = ledger_path.read_bytes() if ledger_path.exists() else b""
    return hashlib.sha256(data).hexdigest()[:12]


def known_story_ids(events: list[dict], root: Path) -> set[str]:
    """Story ids the factory recognizes: ledger events, story files, and
    the curated intake surfaces. This separates a pre-ledger backlog
    candidate from a nonexistent id."""
    ids: set[str] = {str(e.get("story_id")) for e in events
                     if e.get("story_id")}
    stories = root / "stories"
    if stories.is_dir():
        for p in stories.iterdir():
            if p.is_dir() and L.S.STORY_ID_RE.fullmatch(p.name):
                ids.add(p.name)
            elif p.suffix == ".md" and \
                    L.S.STORY_ID_RE.fullmatch(p.stem):
                ids.add(p.stem)
    for rel in KNOWN_SCAN_FILES:
        f = root / rel
        if f.exists():
            for m in f.read_text(encoding="utf-8").split():
                bare = m.strip().strip("`,.:;()").strip().rstrip(":")
                if L.S.STORY_ID_RE.fullmatch(bare):
                    ids.add(bare)  # store the NORMALIZED id, not the token
    return ids


def story_known(sid: str, events: list[dict], root: Path) -> bool:
    return sid in known_story_ids(events, root)


def unknown_card(sid: str) -> str:
    return "\n".join([
        CARD_MARKER,
        f"## Factory Card — {sid}",
        "",
        f"**State:** unknown — `{sid}` appears nowhere: no ledger events, "
        "no story file, and no ALIAS/BACKLOG/DASHBOARD entry.",
        "",
        "Check the canonical id (`STORY-NNN`) against `stories/BACKLOG.md` "
        "and the alias registry, or file the story first. "
        "**Nothing was pinned.**",
    ])


def next_moves(sid: str, state: str, blocked_from: str | None,
               stories_root: Path) -> list[str]:
    """The story's next legal move(s), from state_machine's own tables —
    never re-derived here. Hints use the installed CLI form."""
    if state == "CLOSED":
        return ["— none: story CLOSED —"]
    if state == "BLOCKED":
        origin = blocked_from or "(origin not recorded)"
        return [
            f"resolve the blocker, then request the resume transition "
            f"back to **{origin}**",
            f"CLI: `python3 factory/tools/controller.py request {sid} "
            f"{origin}` (+ the evidence keys the target state requires)",
        ]
    nxt = SM.next_state(state)
    if nxt is None:
        return ["— none —"]
    cmd = f"python3 factory/tools/controller.py request {sid} {nxt}"
    keys = SM.EVIDENCE_KEYS.get(nxt, ())
    if keys:
        # The controller declares --evidence action="append" (one value
        # per occurrence): repeat the flag per key, whole command in the
        # inline-code span so it copies as one parseable command.
        cmd += "".join(f" --evidence {k}=…" for k in keys)
    moves = [f"advance to **{nxt}**", f"CLI: `{cmd}`"]
    # FIXING reentry: post-review states may legally route back to FIXING.
    if state in SM.FIXING_REENTRY_FROM:
        moves.append(f"or route back to **FIXING** (reentry legal from "
                     f"{', '.join(sorted(SM.FIXING_REENTRY_FROM))}): "
                     f"`python3 factory/tools/controller.py request {sid} "
                     f"FIXING`")
    # Declared non-UI skip: PLAN_APPROVED → CODING without the visual gate
    # (fail closed — missing/ambiguous declaration keeps the gate).
    if state == "PLAN_APPROVED" and Ctl.declares_non_ui(sid, stories_root):
        moves.append("↷ legal skip — story.md declares non-UI, so the "
                     "visual approval gate is not required: `python3 "
                     f"factory/tools/controller.py request {sid} "
                     f"{SM.UI_SKIP[1]}`")
    return moves


def pending_gates(gates: list[dict]) -> list[dict]:
    """Truly-pending human_gate rows: unanswered and not superseded by a
    later answered row for the same gate (append-only semantics)."""
    pending: list[dict] = []
    for i, g in enumerate(gates):
        if g.get("answered_at"):
            continue
        superseded = any(s.get("answered_at") and s.get("gate") == g.get("gate")
                         for s in gates[i + 1:])
        if not superseded:
            pending.append(g)
    return pending


def owner_line(events: list[dict], sid: str) -> str:
    """Current owner from the claim lifecycle — delegated to
    controller.open_claim_owner (R18-S2): claim/handover sets it,
    release clears it, and a repo-less CLOSED transition clears it
    IMPLICITLY (latest row wins) — the card must never display a
    former owner after an implicit release."""
    owner = Ctl.open_claim_owner(events, sid)
    return str(owner) if owner else "(none recorded)"


def findings_lines(findings: list[dict]) -> list[str]:
    """Open-findings summary from latest-per-finding rows."""
    if not findings:
        return ["No findings recorded."]
    open_f = [f for f in findings
              if f.get("disposition") in OPEN_DISPOSITIONS]
    closed_n = len(findings) - len(open_f)
    out = [f"{len(findings)} finding(s) recorded; {closed_n} "
           "closed/fixed; "
           f"{len(open_f)} open."]
    for f in open_f:
        sev = f.get("severity") or "(no severity)"
        out.append(f"- `{f.get('finding_id')}` {f.get('source')} / "
                   f"{f.get('class')} / {sev} — disposition "
                   f"{f.get('disposition')}")
    return out


def evidence_lines(events: list[dict]) -> list[str]:
    """Evidence pointers: distinct evidence_path values in ledger order."""
    paths: list[str] = []
    for e in events:
        p = e.get("evidence_path")
        if isinstance(p, str) and p and p not in paths:
            paths.append(p)
    if not paths:
        return ["No evidence_path values recorded."]
    return [f"- `{p}`" for p in paths]


def render_card_text(sid: str, events: list[dict], state: str,
                     blocked_from: str | None, stories_root: Path,
                     digest: str) -> str:
    """Pure renderer: card markdown from ledger events + computed state.
    Deterministic — no wall-clock content."""
    story_events = [e for e in events if e.get("story_id") == sid]
    # Last-transition line is STORY scope: repo-tagged sub-events (which
    # can legitimately differ from the story state, e.g. one repo MERGED
    # while the story is still CODING) never display as the story's last
    # transition — fall through to the latest story-scope transition.
    transitions = [e for e in story_events
                   if e.get("event") == "transition" and not e.get("repo")]
    gates = [e for e in story_events if e.get("event") == "human_gate"]
    findings = L.latest_findings(story_events, sid)

    state_txt = state
    if state == "BLOCKED" and blocked_from:
        state_txt = f"BLOCKED (from {blocked_from})"

    lines: list[str] = [CARD_MARKER, f"## Factory Card — {sid}", ""]
    lines.append(f"**State:** {state_txt}")
    lines.append(f"**Owner:** {owner_line(story_events, sid)}")
    lines.append("")

    if transitions:
        t = transitions[-1]
        lines.append(f"**Last transition:** {t.get('from_state')} → "
                     f"{t.get('to_state')} (by "
                     f"{t.get('requested_by') or '—'})")
    else:
        lines.append("**Last transition:** none — no recorded transitions "
                     "yet")
    open_gates = pending_gates(gates)
    if open_gates:
        g = open_gates[-1]
        lines.append(f"**Pending human gate:** {g.get('gate')} — "
                     f"“{g.get('question')}”")
    else:
        lines.append("**Pending human gate:** none")
    lines.append("")

    lines.append("### Next legal move(s)")
    for i, m in enumerate(next_moves(sid, state, blocked_from,
                                     stories_root), 1):
        lines.append(f"{i}. {m}")
    lines.append("")

    lines.append("### Open findings")
    lines += findings_lines(findings)
    lines.append("")

    lines.append("### Evidence pointers")
    lines += evidence_lines(story_events)
    lines.append("")

    lines.append(f"<sub>generated from ledger @ `{digest}` — never "
                 "hand-edit · render_card v1.0</sub>")
    return "\n".join(lines)


def build_card(sid: str, events: list[dict], root: Path,
               digest: str) -> tuple[str, bool]:
    """(card markdown, known?). Unknown ids render the refusal card and
    are never pinned."""
    if not story_known(sid, events, root):
        return unknown_card(sid), False
    state = L.current_state(events, sid) or "BACKLOG"
    blocked_from = SM.blocked_origin(
        [e for e in events
         if e.get("story_id") == sid and e.get("event") == "transition"]) \
        if state == "BLOCKED" else None
    return render_card_text(sid, events, state, blocked_from,
                            root / "stories", digest), True


# ---------------------------------------------------------------------------
# gh seam — injectable (tests stub gh_fn; no network, no gh calls)
# ---------------------------------------------------------------------------

def gh(*args: str, stdin: str | None = None) -> str:
    r = subprocess.run(["gh", *args], input=stdin, capture_output=True,
                       text=True)
    if r.returncode != 0:
        raise SB.BoardError(
            f"gh {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r.stdout.strip()


def pick_target(ids: list[str]) -> str | None:
    """Newest pin-marker comment wins; None means no card exists yet."""
    return ids[-1] if ids else None


def upsert_comment(repo: str, issue: int, body: str,
                   gh_fn=None, dry_run: bool = False) -> str:
    """Upsert the card comment; matches ONLY the pin marker. The stored
    body starts with the pin marker (written by THIS path only). With
    `dry_run`, prints the exact payload (endpoint + JSON body) and calls
    NO API at all — the POST form is printed; at apply time an existing
    pinned comment retargets the write to PATCH."""
    gh_fn = gh_fn or gh
    stored = f"{PIN_MARKER}\n{body}"
    payload = json.dumps({"body": stored})
    if dry_run:
        print(f"POST repos/{repo}/issues/{issue}/comments")
        print(payload)
        return "dry-run"
    listing = gh_fn("api", f"repos/{repo}/issues/{issue}/comments",
                    "--paginate", "--jq",
                    # R14-O3: anchor to the START of the body — this path
                    # always stores the marker as the first line, and a
                    # `contains` selector would also match a later reply
                    # QUOTING the card (blockquote/code fence) and PATCH it.
                    f'.[] | select(.body | startswith("{PIN_MARKER}")) | .id')
    ids = [ln for ln in listing.splitlines() if ln.strip()]
    target = pick_target(ids)
    if target:
        method = "PATCH"
        endpoint = f"repos/{repo}/issues/comments/{target}"
    else:
        method = "POST"
        endpoint = f"repos/{repo}/issues/{issue}/comments"
    return gh_fn("api", "--method", method, endpoint,
                 "--input", "-", stdin=payload)


def post_comment(repo: str, issue: int, body: str,
                 gh_fn=None) -> str:
    """Plain comment (NO pin marker) — unknown-story explanations."""
    gh_fn = gh_fn or gh
    payload = json.dumps({"body": body})
    return gh_fn("api", "--method", "POST",
                 f"repos/{repo}/issues/{issue}/comments",
                 "--input", "-", stdin=payload)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="render_card",
        description="render the factory story card from the ledger")
    ap.add_argument("story", help="canonical STORY-NNN")
    ap.add_argument("--issue", type=int,
                    help="GitHub issue number to upsert the card comment on")
    ap.add_argument("--repo", default=None, metavar="<owner>/<name>",
                    help="repo for the issue comment (default: first "
                         "repos[].url binding in workspace.toml)")
    ap.add_argument("--root", default=".", metavar="control-home",
                    help="control home root (default: cwd)")
    ap.add_argument("--ledger", default=None,
                    help="ledger path (default "
                         "<root>/factory/ledger/events.jsonl)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the exact upsert payload instead of "
                         "calling the API")
    args = ap.parse_args(argv)

    if not L.S.STORY_ID_RE.match(args.story):
        print(f"REFUSED: {args.story!r} is not a canonical STORY-NNN id")
        return 2
    root = Path(args.root).resolve()
    ledger = Path(args.ledger) if args.ledger \
        else root / "factory" / "ledger" / "events.jsonl"
    try:
        events = L.load_events(ledger)
    except L.LedgerError as exc:
        print(f"REFUSED: ledger rejected (fail closed): {exc}")
        return 2

    card, known = build_card(args.story, events, root,
                             ledger_digest(ledger))
    if args.issue is None:
        print(card)
        return 0
    try:
        owner, name = SB.load_repo(root, args.repo)
        repo = f"{owner}/{name}"
        if known:
            upsert_comment(repo, args.issue, card, dry_run=args.dry_run)
            if args.dry_run:
                print("dry-run: no write performed")
            else:
                print(f"upserted factory-card comment on {repo}"
                      f"#{args.issue}")
        else:
            if args.dry_run:
                print(f"POST repos/{repo}/issues/{args.issue}/comments")
                print(json.dumps({"body": card}))
                print("dry-run: no write performed")
            else:
                post_comment(repo, args.issue, card)
                print(f"unknown story — explanation posted (no pin) on "
                      f"{repo}#{args.issue}")
    except SB.BoardError as exc:
        print(str(exc))
        return exc.rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
