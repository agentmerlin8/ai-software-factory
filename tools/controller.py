#!/usr/bin/env python3
"""tools/controller.py — pipeline state machine: the transition engine.

A small deterministic CLI, no daemon. The orchestrator *requests*
transitions; the controller checks prerequisites against the ledger + git
+ story artifacts, appends a `transition` event via tools/ledger_lib.py,
and prints what failed. The controller never makes judgment calls; it
refuses to advance on unmet checks — no silent advancement, no
"orchestrator remembers to check." Refusals name every failed check and
NEVER write to the ledger.

Authorities (called, never re-implemented):
  * tools/state_machine.py  — STATES / EVIDENCE_KEYS / EVIDENCE_FORMATS /
    order helpers (next_state, order_ok, blocked_origin, resume_ok)
  * tools/ledger_schema.py  — row schemas + ledger-wide laws
  * tools/ledger_lib.py     — the ONLY ledger write path (append_event)
  * tools/contract_lib.py   — impl-plan contract block (PLANNED)
  * tools/multirepo.py      — set-level completeness (multi-repo stories)
  * tools/role_catalog.py   — dispatch role canonicalization (dispatch-id)

Usage:
    python3 tools/controller.py [--root <control-home>] state <story-id>
    python3 tools/controller.py [--root R] request <story-id> <TO_STATE>
            [--by <requester>] [--evidence k=v ...] [--approved-by <human>]
    python3 tools/controller.py [--root R] blocked <story-id> --reason <why>
            [--gate <human-gate kind>]
    python3 tools/controller.py [--root R] validate-story-id <candidate>
    python3 tools/controller.py [--root R] claim <story-id> --owner <o>
            --host <h> --session <s> (--start-sha <sha> |
            --start-shas <repo>=<sha>[,<repo>=<sha>...])
    python3 tools/controller.py [--root R] handover <story-id>
            --from <owner> --to <owner> --reason <why> [--flagged]
    python3 tools/controller.py [--root R] release <story-id> --owner <o>
    python3 tools/controller.py [--root R] dispatch-id <story-id> <role>
    python3 tools/controller.py [--root R] override

FULL TRANSITION TABLE (architecture.md §Transition table; --evidence
supplies per-check inputs; machine-readable: tools/state_machine.py):

| to                | hard checks (controller-enforced)                                    |
|-------------------|----------------------------------------------------------------------|
| GRILLED           | story.md exists; Given/When/Then ACs; <=6 top-level ACs; one         |
|                   | structural signal                                                    |
| TEST_PLAN         | story.md re-checked; DEV + QA grill records: >=80% correct, zero     |
|                   | major misunderstandings, signed by an allowlisted human; signer/     |
|                   | author overlap logged (never blocks)                                 |
| SPEC_FROZEN       | story id canonical ^STORY-\\d{3}$; not aliased/retired; next-in-      |
|                   | sequence unless --evidence sequence_reason=...; test-plan.md          |
|                   | committed at spec_freeze_sha; spec file exists; spec_freeze_sha      |
|                   | resolves in git; branch convention story/<id>-<slug> when supplied;  |
|                   | spec-freeze human approval (answered human_gate gate=spec_freeze OR  |
|                   | allowlisted human --approved-by)                                     |
| PLANNED           | SPEC_FROZEN reached; impl-plan contract block present + validates;   |
|                   | planner lane closed dispatch or lane-skip deviation with human       |
|                   | approval                                                             |
| GATE_PASSED       | plan gate script exit 0 (run live: factory/gates/pre_review_gate.py  |
|                   | else tools/gates/pre_review_gate.py, argv: plan <story-dir>)         |
| PLAN_APPROVED     | plan-review verdict recorded; verdict in {PASS, PASS-WITH-NITS};     |
|                   | plan loops <= 2 (escalate beyond: blocked --gate loop_cap)           |
| STORYBOOK_APPROVAL| answered human_gate gate=visual signed by an allowlisted human       |
| CODING            | PLAN_APPROVED reached; open claim exists (the first gated            |
|                   | transition); non-UI skip requires the declared non-UI line           |
| BUILD_GREEN       | commit_sha resolves; build green; named tests green; coder lane      |
|                   | closed dispatch or lane-skip deviation                               |
| DRAFT_OPENED      | pr_number digits; review_request_mechanism == review-lane (the only   |
|                   | new-transition value; ONE authenticated request); no duplicate        |
|                   | DRAFT_OPENED                                                          |
| REVIEWING         | DRAFT_OPENED reached; a valid review record at the pinned head:       |
|                   | repo-relative record path under the control home with review.json,    |
|                   | sha256(review.json) == review_record_sha, record head ==              |
|                   | review_head_sha, record pr == the DRAFT_OPENED pr_number, and the     |
|                   | record authority (tools/review/record.py check via                    |
|                   | tools/review/record_gate.py) passes                                   |
| FIXING            | REVIEWING reached (or post-review reentry from {FULL_TESTS_GREEN,    |
|                   | READY_FOR_REVIEW, MERGE_ELIGIBLE})                                   |
| FULL_TESTS_GREEN  | exact suite membership: projects, counts, exclusions, exit code;     |
|                   | exit_code=0; tests_failed=0                                          |
| READY_FOR_REVIEW  | internal verdict in {PASS, PASS-WITH-NITS}; threads all resolved OR  |
|                   | external-review-unavailable watchdog exception logged (absence is    |
|                   | never recorded as PASS); <=1 fix push where both first-round sets    |
|                   | arrived pre-edit; a valid review record at review_head_sha           |
|                   | (record-tail commits allowed; v1 ancestor allowed) with no open      |
|                   | blocker/major findings                                               |
| MERGE_ELIGIBLE    | tested_sha prefix-matches head_sha; verdict eligible; no             |
|                   | logic-touched nits; external findings dispositioned; external-unique |
|                   | findings attributed when instrumentation present;                    |
|                   | unresolved_threads=0; answered human_gate gate=merge; a valid review |
|                   | record at head_sha (record-tail commits allowed; v1 ancestor         |
|                   | allowed) with no open blocker/major findings; multi-repo:            |
|                   | set complete (tools/multirepo.set_completeness)                      |
| MERGED            | merged_sha resolves in git; multi-repo: every repo folded >= MERGED  |
| LIVE_PROVEN       | live-proof evidence is an existing path or http(s) URL; multi-repo:  |
|                   | every repo >= MERGED + recorded proof                                |
| CLOSED            | outcome event with merged_sha; doc-sync done or an owed docs commit  |
|                   | recorded; closeout checklist complete; accepted findings validated;  |
|                   | an open claim is released implicitly (release event appended)        |
| BLOCKED           | any state; reason required; resumes at the originating state;        |
|                   | nothing else allowed while blocked                                   |

Evidence keys are passed `--evidence k=v` and persisted into the
transition event's `check_results`. Required evidence per transition is
tools/state_machine.EVIDENCE_KEYS — the controller refuses without it (it
never invents values).

Claim lifecycle (architecture.md §Claim lifecycle): ONE open claim per
story. A claim while another is open is refused with the handover path
(first-wins; the loser hands over — abrupt takeover = flagged handover).
The OVERRIDE file (factory/locks/OVERRIDE, architecture.md §Override
file) is consulted on that lock ONLY: valid + committed + scoped passes
the claim and annotates it `override_used` (incl. the blob SHA); absent,
malformed, placeholder-filled, or uncommitted is fail-closed and the
refusal names the bad field. An override never bypasses evidence, gate,
or order checks.

Exit codes (config_validator convention): 0 success · 1 refusal · 2
usage. Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

try:
    from . import contract_lib as C
    from . import ledger_lib as L
    from . import ledger_schema as Sch
    from . import multirepo as MR
    from . import role_catalog as RC
    from . import state_machine as SM
    from .review import record_gate
except ImportError:  # direct-run layout: load the tools package itself
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools import contract_lib as C
    from tools import ledger_lib as L
    from tools import ledger_schema as Sch
    from tools import multirepo as MR
    from tools import role_catalog as RC
    from tools import state_machine as SM
    from tools.review import record_gate

STATES = SM.STATES
LINEAR = SM.LINEAR
EVIDENCE_KEYS = SM.EVIDENCE_KEYS

REVIEW_MECHANISMS = SM.REVIEW_MECHANISMS
PASS_VERDICTS = ("PASS", "PASS-WITH-NITS")
R1_ARRIVALS = ("both-pre-edit", "other")
PLAN_LOOP_CAP = 2
_BRANCH_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class ControllerError(ValueError):
    """Named refusal from the controller (fail-closed; nothing written)."""


# ------------------------------------------------------------- git helpers --

def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    """Run git in `root`; never raises on nonzero exit (callers decide).
    All repo paths are parameterized — no repo/owner constants."""
    try:
        return subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True)
    except OSError as exc:  # missing git binary — fail closed, never crash
        return subprocess.CompletedProcess(["git", *args], 1, "", str(exc))


def commit_exists(root: Path, sha: str) -> bool:
    """True when `sha` (7-40 hex) resolves as a commit in root's object DB."""
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha or ""):
        return False
    return _git(root, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def committed_at(root: Path, sha: str, relpath: str) -> bool:
    """True when `relpath` is tracked in the tree of commit `sha` (not just
    present in the working tree)."""
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha or ""):
        return False
    r = _git(root, "cat-file", "-e", f"{sha}:{relpath}")
    return r.returncode == 0


# ------------------------------------------------- human-approver allowlist --

def load_approvers(root: Path) -> tuple[str, ...]:
    """The allowlisted-human vocabulary: `identity.approvers` from the
    control home's workspace.toml (architecture.md §Configuration —
    fail closed: absent file, malformed TOML, or an empty/deferred list
    means NO human-approved action can pass)."""
    p = root / "workspace.toml"
    try:
        doc = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ()
    identity = doc.get("identity")
    if not isinstance(identity, dict):
        return ()
    raw = identity.get("approvers")
    if not isinstance(raw, list):
        return ()
    out = []
    for a in raw:
        if isinstance(a, str) and a.strip() and \
                not re.fullmatch(r"<.*>", a.strip()):
            out.append(a.strip().lower())
    return tuple(out)


def is_human_approver(approved_by: str | None,
                      approvers: tuple[str, ...]) -> bool:
    """True when the value names an allowlisted human: an exact token, or
    the "<marker> (...)" attribution convention (parenthesized attribution
    closing the value). The loose "<marker> <free text>" suffix form is
    rejected — a free-text tail is not an identity. Substrings and
    lookalikes ("superapprover", "approver-one-imposter") never satisfy
    the check. An empty allowlist fails closed."""
    v = str(approved_by or "").strip().lower()
    if not v or not approvers:
        return False
    if v in approvers:
        return True
    return any(v.startswith(m + " (") and v.endswith(")") for m in approvers)


# ------------------------------------------------------ override (§locks) --

OVERRIDE_REL = Path("factory") / "locks" / "OVERRIDE"
OVERRIDE_FIELDS = ("scope", "by", "reason", "date")
_PLACEHOLDER_VALUES = ("TODO", "CHANGEME")


def override_status(root: Path) -> tuple[dict | None, list[str]]:
    """Read the OVERRIDE escape hatch (architecture.md §Override file).

    Returns (override, errors):
      * absent file            -> (None, [])   — no override in effect
      * present but not valid  -> (None, [errs]) — fail closed; each error
        names the bad field: malformed line, missing field,
        placeholder-filled (angle-bracket template value, empty value,
        literal TODO/CHANGEME), bad scope, or uncommitted (the override is
        the blob at HEAD of the current branch; a working-tree file that
        differs from HEAD — or is not in a git repo at all — is fail-closed
        uncommitted)
      * valid + committed      -> (override, []) with `blob_sha` = the
        committed blob's SHA at use time.
    """
    p = root / OVERRIDE_REL
    if not p.exists():
        return None, []
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [f"unreadable field set: {exc}"]
    fields: dict[str, str] = {}
    errs: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            errs.append(f"malformed field (expected `key: value`): {line!r}")
            continue
        key, _, value = line.partition(":")
        value = re.sub(r"\s+#.*$", "", value).strip()  # trailing comment
        fields[key.strip()] = value
    for key in OVERRIDE_FIELDS:
        if key not in fields:
            errs.append(f"missing field `{key}`")
            continue
        v = fields[key]
        if not v:
            errs.append(f"placeholder-filled field `{key}`: empty value")
        elif re.fullmatch(r"<.*>", v):
            errs.append(f"placeholder-filled field `{key}`: {v!r}")
        elif v.upper() in _PLACEHOLDER_VALUES:
            errs.append(f"placeholder-filled field `{key}`: {v!r}")
    if not errs and not re.fullmatch(r"ALL|STORY-\d{3}", fields["scope"]):
        errs.append(f"bad field `scope`: {fields['scope']!r} "
                    "(must be ALL or a canonical STORY-NNN)")
    if errs:
        return None, errs
    # committed check: the override is the blob at HEAD — working tree must
    # match it exactly (fail closed otherwise).
    head = _git(root, "rev-parse", "--verify", f"HEAD:{OVERRIDE_REL.as_posix()}")
    work = _git(root, "hash-object", OVERRIDE_REL.as_posix())
    blob = head.stdout.strip()
    if head.returncode != 0 or work.returncode != 0 or \
            not blob or work.stdout.strip() != blob:
        return None, ["uncommitted field: the OVERRIDE working-tree file "
                      "differs from the blob at HEAD (fail closed — commit "
                      "it or remove it)"]
    fields["blob_sha"] = blob
    return fields, []


def override_applies(override: dict | None, sid: str) -> bool:
    """True when a VALID override covers `sid` (scope ALL or the story)."""
    return bool(override) and override.get("scope") in ("ALL", sid)


# ------------------------------------------------- story artefact checks ----

_AC_RE = re.compile(r"^(?:#{2,6}\s+|[-*]\s+\**)AC-(\d+)", re.M)
GRILL_RECORDS = {"DEV": "grill-dev.md", "QA": "grill-qa.md"}
_NON_UI_RE = re.compile(r"^\W*ui\W*:\s*\**(none|no|false|non-ui)\b",
                        re.I | re.M)


def check_story_md(story_dir: Path) -> tuple[bool, Any]:
    """story.md: AC-N acceptance criteria with Given/When/Then, <=6
    top-level AC entries (duplicates count), no duplicate ids, one
    `Structural signal:` line."""
    sm = story_dir / "story.md"
    if not sm.exists():
        return False, f"{sm} missing"
    text = sm.read_text(encoding="utf-8")
    marks = list(_AC_RE.finditer(text))
    errs: list[str] = []
    if not marks:
        errs.append("no AC-N acceptance criteria found")
    if len(marks) > 6:
        errs.append(f"{len(marks)} top-level ACs (max 6)")
    ids = [m.group(1) for m in marks]
    dups = sorted({i for i in ids if ids.count(i) > 1}, key=int)
    if dups:
        errs.append(f"duplicate AC ids: {', '.join('AC-' + d for d in dups)}")
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.start():end]
        missing = [w for w in ("Given", "When", "Then")
                   if not re.search(rf"\b{w}\b", body, re.I)]
        if missing:
            errs.append(f"AC-{m.group(1)} lacks {'/'.join(missing)}")
    if not re.search(r"^\W*structural[ _-]signal\W*:\s*\S", text,
                     re.I | re.M):
        errs.append("no `Structural signal:` line")
    return not errs, errs or "story.md ok"


def _kv(text: str, key: str) -> str | None:
    m = re.search(rf"^\W*{key}\W*:\s*(.+?)\s*$", text, re.I | re.M)
    return m.group(1).strip() if m else None


def check_grill_record(story_dir: Path, persona: str,
                       approvers: tuple[str, ...]) -> tuple[bool, Any]:
    """Grill record: correct_pct 80-100, zero major misunderstandings,
    signed_by an allowlisted human (self-asserted text — the residual risk
    is recorded in the process docs, not hidden)."""
    f = story_dir / GRILL_RECORDS[persona]
    if not f.exists():
        return False, f"{f} missing"
    text = f.read_text(encoding="utf-8")
    errs: list[str] = []
    try:
        pct = float((_kv(text, "correct_pct") or "").rstrip("%"))
        if not (80 <= pct <= 100):  # NaN/inf fail this form
            errs.append(f"correct_pct={pct} (need 80-100)")
    except ValueError:
        errs.append("correct_pct missing/non-numeric")
    try:
        major = int(_kv(text, "major_misunderstandings") or "")
        if major != 0:
            errs.append(f"major_misunderstandings={major} (need 0)")
    except ValueError:
        errs.append("major_misunderstandings missing/non-numeric")
    signer = _kv(text, "signed_by")
    if not is_human_approver(signer, approvers):
        errs.append(f"signed_by={signer!r} is not an allowlisted human")
    return not errs, errs or f"{persona} grill record ok (signer {signer})"


def declares_non_ui(sid: str | None, stories_root: Path) -> bool:
    """True ONLY when stories/<sid>/story.md (or stories/<sid>.md)
    explicitly declares `UI: none` (also no/false/non-ui). No marker / no
    file => False: fail closed, STORYBOOK_APPROVAL is required."""
    if not sid:
        return False
    for f in (stories_root / sid / "story.md", stories_root / f"{sid}.md"):
        try:
            if f.exists() and _NON_UI_RE.search(f.read_text(encoding="utf-8")):
                return True
        except OSError:
            pass
    return False


# ------------------------------------------------------- story id rules ----

def load_alias_table(alias_file: Path) -> dict[str, str]:
    """Parse stories/ALIAS.md historical table: | historical id | note |.
    An absent file is an empty set."""
    aliases: dict[str, str] = {}
    if not alias_file.exists():
        return aliases
    for line in alias_file.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*`?([\w-]+)`?\s*\|\s*(.+?)\s*\|", line)
        if m and m.group(1).lower() not in ("historical", "---", ":---"):
            aliases[m.group(1)] = m.group(2)
    return aliases


def load_reserved_ids(alias_file: Path) -> set[int]:
    """Canonical IDs RESERVED by the ALIAS.md transition table. Reserved !=
    assigned: they don't count toward next-in-sequence until their spec
    freeze claims them."""
    reserved: set[int] = set()
    if not alias_file.exists():
        return reserved
    in_table = False
    for line in alias_file.read_text(encoding="utf-8").splitlines():
        if "Reserved canonical ID" in line:
            in_table = True
            continue
        if in_table:
            if not line.strip().startswith("|"):
                in_table = False
                continue
            for m in re.finditer(r"STORY-(\d{3})", line):
                reserved.add(int(m.group(1)))
    return reserved


def validate_story_id(candidate: str, stories_dir: Path, alias_file: Path,
                      events: list[dict] | None = None) -> list[str]:
    """Canonical form, not aliased/retired, next-in-sequence (a recorded
    sequence_reason escape is applied by the caller). Pure reads over the
    given paths — no hardcoded locations."""
    errs: list[str] = []
    if not Sch.STORY_ID_RE.match(candidate):
        errs.append(f"{candidate!r} is not canonical STORY-NNN "
                    "(zero-padded 3 digits, no suffixes, no parallel "
                    "series)")
        return errs
    aliases = load_alias_table(alias_file)
    if candidate in aliases:
        errs.append(f"{candidate} is in the alias/retired table "
                    f"({aliases[candidate]}) — numbers are never reused")
        return errs
    n = int(candidate.split("-")[1])
    reserved = load_reserved_ids(alias_file)
    # an ID is "frozen" once it has a SPEC_FROZEN transition or a story file
    frozen: set[int] = set()
    for d in stories_dir.glob("STORY-[0-9][0-9][0-9].md"):
        m = re.fullmatch(r"STORY-(\d{3})\.md", d.name)
        if m:
            frozen.add(int(m.group(1)))
    if events:
        for e in events:
            if e.get("event") == "transition" and \
                    e.get("to_state") == "SPEC_FROZEN":
                m = re.fullmatch(r"STORY-(\d{3})", str(e.get("story_id")))
                if m:
                    frozen.add(int(m.group(1)))
    reserved_unfrozen = reserved - frozen
    if n in reserved_unfrozen:
        # claiming a reserved ID: must be the FIRST unfrozen reservation
        if n != min(reserved_unfrozen):
            errs.append(f"{candidate} is reserved but STORY-"
                        f"{min(reserved_unfrozen):03d} is the first unfrozen "
                        "reservation — claim reserved IDs in dependency "
                        "order")
        return errs
    used: set[int] = set(frozen)
    backlog = stories_dir / "BACKLOG.md"
    for m in re.finditer(r"STORY-(\d{3})",
                         backlog.read_text(encoding="utf-8")
                         if backlog.exists() else ""):
        used.add(int(m.group(1)))
    if events:
        for e in events:
            sid = e.get("story_id")
            if isinstance(sid, str):
                m = re.fullmatch(r"STORY-(\d{3})", sid)
                if m and int(m.group(1)) not in reserved_unfrozen:
                    used.add(int(m.group(1)))
    used.discard(n)
    if used and n != max(used) + 1:
        errs.append(f"{candidate} is not next-in-sequence (highest known is "
                    f"STORY-{max(used):03d}; next must be "
                    f"STORY-{max(used)+1:03d}) unless a recorded "
                    "sequence_reason exists")
    return errs


# ---------------------------------------------------------- claim helpers --

def open_claim_owner(events: list[dict], sid: str) -> str | None:
    """The current open-claim owner for `sid`, or None. Ledger-order fold
    of the claim lifecycle: `claim` opens; `handover` transfers the whole
    story; `release` closes; a repo-less CLOSED transition releases
    implicitly (the record persists)."""
    owner: str | None = None
    for e in L.events_for(events, sid):
        etype = e.get("event")
        if etype == "assignment":
            op = e.get("op")
            if op == "claim":
                owner = e.get("owner")
            elif op == "handover":
                owner = e.get("to")
            elif op == "release":
                owner = None
        elif etype == "transition" and e.get("to_state") == "CLOSED" \
                and not e.get("repo"):
            owner = None
    return owner


def open_claim_event(events: list[dict], sid: str) -> dict | None:
    """The latest claim assignment row while the claim is open (None when
    no claim is open). Carries the start pin (start_sha / start_shas) used
    for the multi-repo declared-repo set."""
    if open_claim_owner(events, sid) is None:
        return None
    latest = None
    for e in L.events_for(events, sid, "assignment"):
        if e.get("op") == "claim":
            latest = e
    return latest


def declared_repos(events: list[dict], sid: str) -> list[str]:
    """The story's declared repo-id set: the open claim's `start_shas`
    keys (multi-repo pin). A single-repo `start_sha` claim (or no claim)
    declares no repo set — set-level rules then pass vacuously and are
    skipped."""
    claim = open_claim_event(events, sid)
    if not claim:
        return []
    shas = claim.get("start_shas")
    if isinstance(shas, dict):
        return sorted(shas)
    return []


# ------------------------------------------------------------ controller ----

class Controller:
    """The transition engine over one control home (`--root`).

    Path resolution (architecture.md §Canonical layouts): ledger
    <root>/factory/ledger/events.jsonl; locks <root>/factory/locks/OVERRIDE;
    stories <root>/stories/; alias/retired registry <root>/stories/ALIAS.md
    (absent file = empty sets). No other hardcoded paths.
    """

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.ledger_path = self.root / "factory" / "ledger" / "events.jsonl"
        self.stories = self.root / "stories"
        self.alias_file = self.stories / "ALIAS.md"
        self.approvers = load_approvers(self.root)
        try:
            self.events = L.load_events(self.ledger_path)
        except L.LedgerError as exc:
            raise ControllerError(f"ledger rejected (fail closed): {exc}") \
                from exc

    # -- ledger reads (state derivation via ledger_schema helpers only)
    def story_events(self, sid: str, etype: str | None = None) -> list[dict]:
        return L.events_for(self.events, sid, etype)

    def latest_findings(self, sid: str) -> list[dict]:
        return L.latest_findings(self.events, sid)

    def state(self, sid: str) -> str:
        st = L.current_state(self.events, sid)
        return st if st else "BACKLOG"

    def blocked_from(self, sid: str) -> str | None:
        return SM.blocked_origin(self.story_events(sid, "transition"))

    def _append(self, evt: dict[str, Any]) -> None:
        """The ONLY write path: ledger_lib.append_event (validates before
        writing; a refused event never touches the file)."""
        try:
            L.append_event(self.ledger_path, evt)
        except L.LedgerError as exc:
            raise ControllerError(f"ledger write refused: {exc}") from exc
        self.events.append(evt)

    # -- gate script resolution (§Gate catalog invocation semantics)
    def gate_script(self) -> Path | None:
        for p in (self.root / "factory" / "gates" / "pre_review_gate.py",
                  self.root / "tools" / "gates" / "pre_review_gate.py"):
            if p.exists():
                return p
        return None

    # -- review-record gate evidence (S3.5b cutover) ------------------------
    def _contained_record_dir(self, rel: str) -> Path | None:
        """A repo-relative review-record directory UNDER the control home,
        or None. Refuses empty values, absolute paths, and `..` escapes
        (Path.resolve + is_relative_to — never string surgery)."""
        if not rel:
            return None
        p = Path(rel)
        if p.is_absolute():
            return None
        resolved = (self.root / p).resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            return None
        return resolved

    def _review_evidence(self, rel: str,
                         pr_head: str | None) -> tuple[Path | None,
                                                       dict | None,
                                                       list[str]]:
        """Resolve the `review_record` evidence and authority-evaluate it
        (tools.review.record_gate). Returns (record_dir_abs | None, record
        dict | None, error strings — empty = pass). pr_head None skips the
        head rule (REVIEWING pins via review_head_sha equality instead)."""
        rec = self._contained_record_dir(rel)
        if rec is None or not (rec / "review.json").exists():
            return rec, None, [
                f"review_record={rel!r} must be a repo-relative record "
                "directory under the control home containing review.json "
                "(absolute paths and `..` escapes are refused)"]
        try:
            record = record_gate.load_record(str(rec))
        except ValueError as exc:
            return rec, None, [str(exc)]
        errs = record_gate.evaluate(str(rec), str(self.root), pr_head,
                                    allow_v1_ancestor=True)
        return rec, record, errs

    def _draft_opened_pr(self, sid: str) -> str | None:
        """pr_number from the last DRAFT_OPENED transition's persisted
        evidence (None when the row carries no evidence detail — historical
        rows are read, never re-judged)."""
        drafts = [t for t in self.story_events(sid, "transition")
                  if t.get("to_state") == "DRAFT_OPENED"]
        if not drafts:
            return None
        cr = drafts[-1].get("check_results") or {}
        es = cr.get("evidence_supplied") or {}
        detail = es.get("detail") if isinstance(es, dict) else None
        if isinstance(detail, dict):
            pr = detail.get("pr_number")
            if pr is not None:
                return str(pr)
        return None

    def _put_record_checks(self, put, rel: str, rec: Path | None,
                           record: dict | None, errs: list[str],
                           head_ev: str | None) -> None:
        """The shared READY_FOR_REVIEW / MERGE_ELIGIBLE record keys:
        review_record_valid, review_head_match, no_open_blocker_major."""
        head_errs = [e for e in errs if e.startswith("head rule")]
        if record is None:
            # An unreadable record cannot establish a head match: fail the
            # named check instead of letting an empty error list read green.
            put("review_head_match", False,
                "record unreadable — head match cannot be established "
                "(fail closed)")
        else:
            put("review_head_match", not head_errs,
                head_errs or (f"record valid at head {head_ev!r} (exact match, "
                              "record-tail commits, or a v1 ancestor)"
                              if head_ev else "head rule not evaluated"))
        if record is None:
            put("no_open_blocker_major", False,
                "record unreadable — open blocker/major findings cannot be "
                "ruled out (fail closed)")
        else:
            open_bm = record_gate.open_blocker_majors(record)
            put("no_open_blocker_major", not open_bm,
                open_bm and ("open blocker/major findings without "
                             f"disposition: {', '.join(open_bm)}")
                or "no blocker/major findings without disposition")
        put("review_record_valid", not errs,
            errs or f"review record {rel!r} passes the record authority "
                    "(tools/review/record.py check via record_gate)")

    # -- transition checks. Each returns (ok, detail) via put().
    def check(self, sid: str, to: str, ev: dict[str, str],
              approved_by: str | None = None) -> dict[str, Any]:
        results: dict[str, Any] = {}

        def put(name: str, ok: bool, detail: Any) -> None:
            results[name] = {"ok": bool(ok), "detail": detail}

        cur = self.state(sid)

        if to != "BLOCKED":
            if cur == "BLOCKED":
                origin = self.blocked_from(sid)
                put("block_resumption", SM.resume_ok(origin, to),
                    f"resuming from BLOCKED at originating state {origin}; "
                    f"requested {to}")
            elif cur in LINEAR and to in LINEAR:
                # UI-skip is fail-closed: PLAN_APPROVED -> CODING without
                # STORYBOOK_APPROVAL requires the explicit non-UI
                # declaration; FIXING reentry only from the post-review
                # states. Both live in state_machine.order_ok.
                put("order",
                    SM.order_ok(cur, to, declares_non_ui(sid, self.stories)),
                    f"current {cur} -> requested {to} (must be the next "
                    "state, a legal FIXING reentry, a declared non-UI skip, "
                    "or BLOCKED)")

        if to == "GRILLED":
            ok, detail = check_story_md(self.stories / sid)
            put("story_md", ok, detail)

        if to == "TEST_PLAN":
            # re-run the GRILLED gate: story.md may have changed since
            ok, detail = check_story_md(self.stories / sid)
            put("story_md", ok, detail)
            signers = []
            for persona in ("DEV", "QA"):
                ok, detail = check_grill_record(self.stories / sid, persona,
                                                self.approvers)
                put(f"grill_{persona.lower()}", ok, detail)
                fp = self.stories / sid / GRILL_RECORDS[persona]
                signers.append(_kv(fp.read_text(encoding="utf-8"), "signed_by")
                               if fp.exists() else None)
            sm = self.stories / sid / "story.md"
            author = _kv(sm.read_text(encoding="utf-8"), "author") \
                if sm.exists() else None
            overlap = bool(author) and any(
                (x or "").lower() == author.lower() for x in signers)
            # overlap is logged, never blocks
            put("signer_author_overlap_logged", True,
                f"author={author!r} signers={signers} overlap={overlap}")

        if to == "SPEC_FROZEN":
            rel = f"stories/{sid}/test-plan.md"
            fsha = ev.get("spec_freeze_sha", "")
            put("test_plan_committed", committed_at(self.root, fsha, rel),
                f"{rel} must be tracked in git at spec_freeze_sha "
                f"{fsha!r} (an untracked working-tree file does not count)")
            id_errs = validate_story_id(sid, self.stories, self.alias_file,
                                        self.events)
            if ev.get("sequence_reason"):
                id_errs = [e for e in id_errs if "next-in-sequence" not in e]
            put("canonical_id", not id_errs, id_errs or "ok")
            spec = ev.get("spec_path", "")
            put("spec_exists", bool(spec) and (self.root / spec).exists(),
                f"spec_path={spec!r}")
            put("spec_freeze_sha", commit_exists(self.root, fsha),
                f"sha={fsha!r} (must resolve in the repo object DB)")
            branch = ev.get("branch")
            branch_ok = True
            branch_detail = "branch not supplied (record it with " \
                            "--evidence branch=...)"
            if branch is not None:
                m = re.fullmatch(r"story/(.+)", branch)
                slug_ok = False
                if m:
                    expected = f"{sid}-"
                    slug = m.group(1)
                    slug_ok = slug.startswith(expected) and \
                        _BRANCH_SLUG_RE.fullmatch(slug[len(expected):]) \
                        is not None
                branch_ok = slug_ok
                branch_detail = f"branch={branch!r} must be " \
                                f"story/{sid}-<slug> (slug [a-z0-9]+ with " \
                                "single hyphens, <=48 chars)"
            put("branch_convention", branch_ok, branch_detail)
            gates = [g for g in self.story_events(sid, "human_gate")
                     if g.get("gate") == "spec_freeze" and g.get("answered_at")]
            human = is_human_approver(approved_by, self.approvers)
            put("spec_freeze_approval", bool(gates) or human,
                "spec freeze is a human gate: requires an answered "
                "human_gate gate=spec_freeze row, or an allowlisted human "
                "via --approved-by (neither present; allowlist = "
                "identity.approvers in workspace.toml)")

        if to == "PLANNED":
            frozen = [t for t in self.story_events(sid, "transition")
                      if t["to_state"] == "SPEC_FROZEN"]
            put("spec_frozen", bool(frozen), "SPEC_FROZEN transition missing")
            plan = self.stories / sid / "impl-plan.md"
            ok, detail = False, f"{plan} missing"
            if plan.exists():
                try:
                    contract = C.extract_contract(
                        plan.read_text(encoding="utf-8"))
                    errs = C.validate_contract(contract)
                    ok, detail = not errs, errs or "contract validates"
                except C.ContractParseError as e:
                    detail = str(e)
            put("contract_block", ok, detail)
            put("planner_dispatch", self._lane_ok(sid, "planner"),
                "planner lane must be a dispatched child (role=planner, "
                "closed row); skipping it requires a deviation "
                "`lane-skip:planner` approved by an allowlisted human")

        if to == "GATE_PASSED":
            script = self.gate_script()
            if script is None:
                put("plan_gate", False,
                    "no plan gate script found (looked for "
                    "factory/gates/pre_review_gate.py, then "
                    "tools/gates/pre_review_gate.py under the control home)")
            else:
                cmd = [sys.executable, str(script), "plan",
                       str(self.stories / sid)]
                r = subprocess.run(cmd, capture_output=True, text=True)
                put("plan_gate", r.returncode == 0,
                    (r.stdout + r.stderr).strip()[-500:] or
                    f"exit {r.returncode}")

        if to == "PLAN_APPROVED":
            stages = self.story_events(sid, "stage")
            verdict_events = [s for s in stages
                              if s.get("stage") == "plan_review"]
            v = ev.get("verdict", "")
            put("verdict_recorded",
                bool(verdict_events) or v in PASS_VERDICTS,
                f"stage events: {len(verdict_events)}; evidence verdict={v!r}"
                " (record a stage event stage=plan_review with the verdict, "
                "or pass verdict=)")
            if v:
                put("verdict_pass", v in PASS_VERDICTS,
                    f"FAIL cannot advance to PLAN_APPROVED (verdict={v})")
            loops = [l for l in self.story_events(sid, "loop")
                     if l.get("stage") == "plan_review"]
            put("loop_cap", len(loops) <= PLAN_LOOP_CAP,
                f"{len(loops)} plan loops (cap {PLAN_LOOP_CAP}) — escalate: "
                "blocked <sid> --reason 'loop cap' --gate loop_cap")

        if to == "STORYBOOK_APPROVAL":
            vis = [g for g in self.story_events(sid, "human_gate")
                   if g.get("gate") == "visual" and g.get("answered_at")
                   and is_human_approver(g.get("answered_by"),
                                         self.approvers)]
            put("storybook_visual_gate", bool(vis),
                "STORYBOOK_APPROVAL requires an answered human_gate "
                "gate=visual row signed by an allowlisted human")

        if to == "CODING" and cur == "PLAN_APPROVED":
            put("non_ui_declared", declares_non_ui(sid, self.stories),
                "skipping STORYBOOK_APPROVAL requires story.md to declare "
                "`UI: none` (missing/ambiguous = UI, fail closed)")

        if to == "CODING":
            owner = open_claim_owner(self.events, sid)
            put("claim_exists", owner is not None,
                "CODING is the first gated transition: an open claim "
                "(assignment op=claim) must exist before coding starts "
                f"(current owner: {owner or 'none'})")

        if to == "BUILD_GREEN":
            sha = ev.get("commit_sha", "")
            put("commit_sha", commit_exists(self.root, sha),
                f"commit_sha={sha!r} (must resolve in the repo object DB)")
            put("build_result", ev.get("build_result") == "green",
                f"build_result={ev.get('build_result')!r} (must be 'green')")
            put("named_tests", ev.get("named_tests_result") == "green",
                f"named_tests_result={ev.get('named_tests_result')!r}")
            put("coder_dispatch", self._lane_ok(sid, "coder"),
                "coder lane must be a dispatched child (role=coder, closed "
                "row); skipping it requires a deviation `lane-skip:coder` "
                "approved by an allowlisted human")

        if to == "DRAFT_OPENED":
            pr = ev.get("pr_number", "")
            put("pr_number", bool(re.fullmatch(r"\d+", pr)),
                f"pr_number={pr!r} (digits)")
            mech = ev.get("review_request_mechanism", "")
            put("review_request_mechanism", mech in REVIEW_MECHANISMS,
                f"review_request_mechanism={mech!r} — ONE authenticated "
                f"request; the only allowed value for a NEW transition is "
                f"{SM.REVIEW_MECHANISM!r} (historical mechanisms "
                f"{list(SM.HISTORICAL_REVIEW_MECHANISMS)} remain readable "
                "in ledger history but are never accepted as new evidence)")
            prior = [t for t in self.story_events(sid, "transition")
                     if t["to_state"] == "DRAFT_OPENED"]
            put("no_duplicate_trigger", not prior,
                "DRAFT_OPENED already recorded — duplicate review requests "
                "are a success-metric violation")

        if to == "REVIEWING":
            # The review record IS the gate evidence (S3.5b cutover): the
            # record must exist under the control home, its review.json
            # hash and pinned head must match the supplied evidence, the
            # record must name the DRAFT_OPENED pr, and the record
            # authority must accept it. pr_head=None: REVIEWING pins by
            # review_head_sha equality, not by the head rule.
            rel = ev.get("review_record", "")
            rec, record, errs = self._review_evidence(rel, None)
            put("review_record_path",
                rec is not None and (rec / "review.json").exists(),
                f"review_record={rel!r} must be a repo-relative record "
                "directory under the control home containing review.json "
                "(absolute paths and `..` escapes are refused)")
            sha_ev = ev.get("review_record_sha", "")
            sha_ok = False
            sha_detail = f"review_record_sha={sha_ev!r} must equal " \
                         "sha256(review.json)"
            if rec is not None and (rec / "review.json").exists():
                try:
                    actual = record_gate.record_sha256(str(rec))
                    sha_ok = actual == sha_ev
                    sha_detail = (f"review_record_sha={sha_ev!r} vs "
                                  f"sha256(review.json)={actual!r}")
                except OSError as exc:
                    sha_detail = f"review.json unreadable: {exc}"
            put("review_record_sha", sha_ok, sha_detail)
            head_ev = ev.get("review_head_sha", "")
            rec_head = str((record or {}).get("head_sha") or "")
            put("review_head_match",
                bool(record) and bool(rec_head)
                and (rec_head == head_ev or rec_head.startswith(head_ev)
                     or head_ev.startswith(rec_head)),
                f"review_head_sha={head_ev!r} must equal the record's "
                f"pinned head {rec_head or '(unreadable)'!r}")
            pr_expect = self._draft_opened_pr(sid)
            rec_pr = (record or {}).get("pr")
            put("review_record_pr",
                pr_expect is not None
                and str(rec_pr) == pr_expect,
                f"record pr={rec_pr!r} must equal the DRAFT_OPENED "
                f"pr_number={pr_expect!r}")
            put("review_record_valid", not errs,
                errs or f"review record {rel!r} passes the record authority "
                        "(tools/review/record.py check via record_gate)")

        if to == "FULL_TESTS_GREEN":
            need = ("projects", "tests_passed", "tests_failed",
                    "tests_skipped", "exclusions", "exit_code")
            missing = [k for k in need if not ev.get(k)]
            put("suite_membership", not missing,
                f"missing: {missing} — exact suite membership required "
                "(projects, skip/exclusion reasons, exit code)")
            put("exit_zero", ev.get("exit_code") == "0",
                f"exit_code={ev.get('exit_code')!r}")
            put("failed_zero", ev.get("tests_failed") == "0",
                f"tests_failed={ev.get('tests_failed')!r}")

        if to == "READY_FOR_REVIEW":
            iv = ev.get("internal_verdict", "")
            put("internal_verdict", iv in PASS_VERDICTS, iv or "missing")
            ts = ev.get("threads_state", "")
            watchdog = any(
                "external-review-unavailable" in json.dumps(e)
                for e in self.story_events(sid))
            put("threads_resolved_or_watchdog",
                ts == "all-resolved" or (ts == "watchdog-exception"
                                         and watchdog),
                f"threads_state={ts!r}; watchdog exception logged={watchdog} "
                "(absence is NEVER recorded as PASS)")
            pushes = ev.get("fix_pushes", "")
            r1 = ev.get("r1_arrival", "")
            put("r1_arrival_format", r1 in R1_ARRIVALS,
                f"r1_arrival={r1!r} (allowed: {list(R1_ARRIVALS)})")
            ok_push = pushes.isdigit() and (
                int(pushes) <= 1 or r1 != "both-pre-edit")
            put("merged_fix_pass", ok_push,
                f"fix_pushes={pushes!r}, r1_arrival={r1!r} — <=1 push where "
                "both first-round sets arrived pre-edit (target, not safety "
                "constraint; extra pushes need finding+fix events)")
            # the record must be valid AT the pinned head: exact match, a
            # record-only tail (v2), or a v1 ancestor.
            rel = ev.get("review_record", "")
            head_ev = ev.get("review_head_sha", "")
            rec, record, errs = self._review_evidence(rel, head_ev)
            self._put_record_checks(put, rel, rec, record, errs, head_ev)

        if to == "MERGE_ELIGIBLE":
            tested, head = ev.get("tested_sha", ""), ev.get("head_sha", "")
            put("tested_sha_is_head",
                bool(tested) and head.startswith(tested),
                f"tested_sha={tested!r} vs head_sha={head[:12]!r} — exact "
                "tested SHA == current head (prefix match)")
            findings = self.latest_findings(sid)
            nits_bad = [f for f in findings
                        if f.get("class") == "nit" and f.get("logic_touched")]
            ivd = self._last_ready_verdict(sid)
            put("internal_verdict_eligible",
                ivd in PASS_VERDICTS or ev.get("internal_verdict")
                in PASS_VERDICTS,
                f"verdict must be PASS or PASS-WITH-NITS "
                f"(logic_touched=false nits only): {ivd or ev.get('internal_verdict')!r}")
            put("no_logic_nits", not nits_bad,
                f"{len(nits_bad)} nit(s) with logic_touched — schema-level "
                "ban; PASS-WITH-NITS cannot reach MERGE_ELIGIBLE")
            undecided = [f for f in findings
                         if f.get("source") == "external" and
                         f.get("disposition") not in
                         ("fixed", "dismissed", "accepted", "deferred")]
            put("external_dispositioned", not undecided,
                f"{len(undecided)} external finding(s) without disposition "
                "(accepted -> fixed+verified, or dismissed with reason)")
            contexts = self.story_events(sid, "review_context")
            if contexts:
                attributed = {a.get("finding_id") for a in
                              self.story_events(sid, "review_attribution")}
                unique_ext = [f for f in findings
                              if f.get("source") == "external" and
                              not f.get("overlap_id")]
                missing_attr = [f.get("finding_id") for f in unique_ext
                                if f.get("finding_id") not in attributed]
                put("external_context_attribution", not missing_attr,
                    f"unattributed external-unique findings: "
                    f"{missing_attr or 'none'}")
            put("zero_unresolved_threads",
                ev.get("unresolved_threads") == "0",
                f"unresolved_threads={ev.get('unresolved_threads')!r}")
            gates = [g for g in self.story_events(sid, "human_gate")
                     if g.get("gate") == "merge" and g.get("answered_at")]
            put("merge_approval", bool(gates),
                "human_gate gate=merge with answered_at required (the human "
                "merge gate is NEVER weakened)")
            # the record must be valid AT the current head: exact match, a
            # record-only tail (v2), or a v1 ancestor.
            rel = ev.get("review_record", "")
            rec, record, errs = self._review_evidence(rel, head)
            self._put_record_checks(put, rel, rec, record, errs, head)

        if to == "MERGED":
            sha = ev.get("merged_sha", "")
            put("merged_sha", commit_exists(self.root, sha),
                f"merged_sha={sha!r} (must resolve in the repo object DB)")

        if to == "LIVE_PROVEN":
            p = ev.get("live_proof_evidence", "")
            put("live_proof_evidence",
                bool(p) and ((self.root / p).exists()
                             or p.startswith(("http://", "https://"))),
                f"live_proof_evidence={p!r} must be a real path under the "
                "control home or an http(s) URL")

        if to == "CLOSED":
            outcomes = self.story_events(sid, "outcome")
            merged = [o for o in outcomes if o.get("merged_sha")]
            put("outcome_recorded", bool(merged),
                "outcome event with merged_sha required")
            put("doc_sync", ev.get("doc_sync") == "done" or
                bool(ev.get("docs_commit")),
                f"doc_sync={ev.get('doc_sync')!r} docs_commit="
                f"{ev.get('docs_commit')!r} — obligations done or an owed "
                "docs commit recorded")
            put("closeout_checklist",
                ev.get("closeout_checklist") == "complete",
                f"closeout_checklist={ev.get('closeout_checklist')!r} "
                "(tested==merged SHA, live proof path, doc-sync, findings "
                "dispositioned, observation window opened)")
            undecided = [f for f in self.latest_findings(sid)
                         if f.get("disposition") == "accepted" and
                         not f.get("validation")]
            put("findings_dispositioned", not undecided,
                f"{len(undecided)} accepted finding(s) without "
                "disposition+validation events")

        if to == "BLOCKED":
            put("block_reason", bool(ev.get("reason")),
                f"reason={ev.get('reason')!r}")
            g = ev.get("gate")
            if g:
                put("escalation_gate", g in Sch.HUMAN_GATE_KINDS,
                    f"gate={g!r} (allowed: {sorted(Sch.HUMAN_GATE_KINDS)})")

        # multi-repo set-level completeness (tools/multirepo): refuses on an
        # incomplete set, naming every missing (repo, requirement).
        if to in MR.SET_LEVEL_RULES:
            repos = declared_repos(self.events, sid)
            if repos:
                errs = MR.set_completeness(
                    self.events, sid, repos, to,
                    proof_evidence=ev.get("live_proof_evidence", ""))
                put("set_complete", not errs, errs or "all declared repos "
                    f"complete for {to}: {repos}")

        return results

    # -- lane discipline (planner/coder dispatch or approved lane-skip)
    def _lane_ok(self, sid: str, role: str) -> bool:
        disp = self.story_events(sid, "dispatch")
        if any(d.get("role") == role and d.get("ended_at") is not None
               for d in disp):
            return True
        return any(
            re.fullmatch(rf"lane-skip:{role}", d.get("contract_field") or "")
            and is_human_approver(d.get("approved_by"), self.approvers)
            for d in self.story_events(sid, "deviation"))

    def _last_ready_verdict(self, sid: str) -> str | None:
        """internal_verdict detail from the latest READY_FOR_REVIEW
        transition (the READY_FOR_REVIEW check persists the bare verdict
        value as this check's detail)."""
        verdicts = [t for t in self.story_events(sid, "transition")
                    if t.get("to_state") == "READY_FOR_REVIEW"]
        if not verdicts:
            return None
        cr = verdicts[-1].get("check_results") or {}
        iv = cr.get("internal_verdict")
        if isinstance(iv, dict):
            detail = iv.get("detail")
            if isinstance(detail, str):
                return detail
        return None

    # -- public API
    def request(self, sid: str, to: str, evidence: dict[str, str],
                requested_by: str, approved_by: str | None) -> int:
        if to not in STATES:
            print(f"REFUSED: {to!r} is not a state. States: {STATES}")
            return 2
        missing = [k for k in EVIDENCE_KEYS.get(to, ())
                   if not evidence.get(k)]
        if missing:
            print(f"REFUSED: {sid} -> {to} requires evidence: "
                  f"{', '.join(missing)} (pass --evidence {missing[0]}=...)")
            return 2
        results = self.check(sid, to, evidence, approved_by)
        failed = {k: v for k, v in results.items() if not v["ok"]}
        frm = self.state(sid)
        if failed:
            print(f"REFUSED: {sid} {frm} -> {to}. Failed checks:")
            for k, v in sorted(failed.items()):
                print(f"  x {k}: {v['detail']}")
            # transitions record SUCCESSFUL state changes only; refusals
            # print and exit non-zero — the ledger is never written.
            return 1
        evt = self._transition_event(sid, frm, to, evidence, requested_by,
                                     approved_by, results)
        # CLOSED releases an open claim implicitly — capture the owner
        # BEFORE the transition lands (the CLOSED row itself releases).
        close_owner = open_claim_owner(self.events, sid) \
            if to == "CLOSED" else None
        self._append(evt)
        print(f"OK: {sid} {frm} -> {to} "
              f"(checks: {', '.join(sorted(results))})")
        if to == "CLOSED" and close_owner:
            self._append({"event": "assignment", "story_id": sid,
                          "op": "release", "owner": close_owner,
                          "requested_by": requested_by})
            print(f"OK: {sid} claim released at CLOSED "
                  f"(owner {close_owner})")
        return 0

    def _transition_event(self, sid: str, frm: str, to: str,
                          evidence: dict[str, str], requested_by: str,
                          approved_by: str | None,
                          results: dict[str, Any]) -> dict[str, Any]:
        # --evidence intake persists into check_results (evidence_supplied)
        results = dict(results)
        results["evidence_supplied"] = {"ok": True,
                                        "detail": dict(sorted(evidence.items()))}
        evt: dict[str, Any] = {
            "event": "transition", "story_id": sid,
            "from_state": frm, "to_state": to,
            "requested_by": requested_by,
            "checks_run": sorted(results.keys()),
            "check_results": results,
            "approved_by": approved_by,
            "repo": None, "repo_reason": "story-level transition",
        }
        evt["approved_by_reason"] = None if approved_by else \
            "no approver recorded for this transition"
        return evt

    def blocked(self, sid: str, reason: str, requested_by: str,
                gate: str | None = None) -> int:
        if gate is not None and gate not in Sch.HUMAN_GATE_KINDS:
            print(f"REFUSED: gate {gate!r} not a human-gate kind "
                  f"(allowed: {sorted(Sch.HUMAN_GATE_KINDS)})")
            return 2
        frm = self.state(sid)
        results: dict[str, Any] = {
            "block_reason": {"ok": True, "detail": reason}}
        if gate:
            results["escalation_gate"] = {"ok": True, "detail": f"gate={gate}"}
        evt = {
            "event": "transition", "story_id": sid,
            "from_state": frm, "to_state": "BLOCKED",
            "requested_by": requested_by,
            "checks_run": sorted(results.keys()),
            "check_results": results,
            "approved_by": None,
            "approved_by_reason": "loop cap / escalation blocks are recorded, "
                                  "not approved",
            "repo": None, "repo_reason": "story-level transition",
        }
        self._append(evt)
        suffix = f", gate={gate}" if gate else ""
        print(f"OK: {sid} {frm} -> BLOCKED ({reason}{suffix}) — resumes at "
              f"{frm}")
        return 0

    # -- claim lifecycle (assignment events; §Claim lifecycle)
    def claim(self, sid: str, owner: str, host: str, session: str,
              start_sha: str | None, start_shas: dict[str, str] | None,
              requested_by: str) -> int:
        current = open_claim_owner(self.events, sid)
        annotation = None
        if current is not None:
            override, errs = override_status(self.root)
            if errs:
                # fail closed: a malformed/placeholder/uncommitted OVERRIDE
                # is not an override — the refusal names the bad field(s).
                print(f"REFUSED: {sid} has an open claim by {current!r}; the "
                      "OVERRIDE file was consulted and rejected:")
                for e in errs:
                    print(f"  x {e}")
                return 1
            if not override_applies(override, sid):
                print(f"REFUSED: {sid} has an open claim by {current!r} — "
                      "first claim wins; the sanctioned transfer is a "
                      f"handover: handover {sid} --from {current} --to "
                      f"{owner} --reason <why> (abrupt takeover = --flagged)")
                return 1
            annotation = {"scope": override.get("scope"),
                          "by": override.get("by"),
                          "blob_sha": override.get("blob_sha")}
        evt: dict[str, Any] = {
            "event": "assignment", "story_id": sid, "op": "claim",
            "owner": owner, "host": host, "session": session,
            "claimed_at": L.utcnow(), "requested_by": requested_by,
        }
        if start_sha is not None:
            evt["start_sha"] = start_sha
        else:
            evt["start_shas"] = start_shas or {}
        if annotation:
            evt["override_used"] = annotation
        self._append(evt)
        pin = start_sha if start_sha is not None else \
            ",".join(f"{k}@{v}" for k, v in sorted((start_shas or {}).items()))
        note = " (via OVERRIDE)" if annotation else ""
        print(f"OK: {sid} claimed by {owner} at {pin}{note}")
        return 0

    def handover(self, sid: str, frm: str, to: str, reason: str,
                 flagged: bool, requested_by: str) -> int:
        current = open_claim_owner(self.events, sid)
        if current is None:
            print(f"REFUSED: {sid} has no open claim to hand over")
            return 1
        if current != frm and not flagged:
            print(f"REFUSED: {sid} is claimed by {current!r}, not {frm!r} — "
                  "an unflagged handover must come from the current owner; "
                  "abrupt takeover requires --flagged")
            return 1
        self._append({"event": "assignment", "story_id": sid, "op": "handover",
                      "from": frm, "to": to, "reason": reason,
                      "flagged": flagged, "requested_by": requested_by})
        mark = " (flagged takeover)" if flagged else ""
        print(f"OK: {sid} handed over {frm} -> {to}{mark}")
        return 0

    def release(self, sid: str, owner: str, requested_by: str) -> int:
        current = open_claim_owner(self.events, sid)
        if current is None:
            print(f"REFUSED: {sid} has no open claim to release")
            return 1
        if current != owner:
            print(f"REFUSED: {sid} is claimed by {current!r}, not {owner!r}")
            return 1
        self._append({"event": "assignment", "story_id": sid, "op": "release",
                      "owner": owner, "requested_by": requested_by})
        print(f"OK: {sid} claim released by {owner}")
        return 0

    # -- pre-spawn dispatch attempt allocation
    def dispatch_id(self, sid: str, role: str) -> int:
        """Allocate the next pre-spawn dispatch id <story>-<role>-<attempt>-
        <unix>: attempt numbers are allocated by the controller at
        pre-spawn. A dispatch already recorded `ended` is never re-started
        by a stale notification (ledger law) — allocation always moves to
        the NEXT attempt."""
        try:
            canonical = RC.canonical_role(role)
        except ValueError as exc:
            print(f"REFUSED: {exc}")
            return 2
        attempts = [d.get("attempt") for d in self.story_events(sid, "dispatch")
                    if d.get("role") == canonical]
        attempt = max((a for a in attempts if isinstance(a, int)),
                      default=0) + 1
        did = f"{sid}-{canonical}-{attempt}-{int(time.time())}"
        print(did)
        return 0

    # -- override consultation (read path; renders show active overrides)
    def show_override(self) -> int:
        override, errs = override_status(self.root)
        if errs:
            print("OVERRIDE present but NOT in effect (fail closed):")
            for e in errs:
                print(f"  x {e}")
            return 1
        if override is None:
            print("no override in effect")
            return 0
        active = override
        print("OVERRIDE active:")
        for k in ("scope", "by", "reason", "date", "blob_sha"):
            print(f"  {k}: {active.get(k)}")
        return 0


# --------------------------------------------------------------------- CLI ----

def _parse_start_shas(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        repo, sep, sha = part.partition("=")
        if not sep:
            raise ControllerError(f"malformed --start-shas entry {part!r} "
                                  "(expected repo=sha)")
        out[repo.strip()] = sha.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    """Exit 0 success · 1 refusal · 2 usage (config_validator convention)."""
    ap = argparse.ArgumentParser(prog="controller",
                                 description="pipeline transition engine "
                                             "(judgment-free)")
    ap.add_argument("--root", default=".", metavar="control-home",
                    help="control home root (default: cwd); ledger, locks, "
                         "stories and gates are resolved from it")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("state", help="print a story's current state")
    p.add_argument("story_id")

    p = sub.add_parser("request", help="request a transition")
    p.add_argument("story_id")
    p.add_argument("to_state")
    p.add_argument("--by", default="orchestrator")
    p.add_argument("--approved-by", default=None)
    p.add_argument("--evidence", action="append", default=[], metavar="K=V")

    p = sub.add_parser("blocked",
                       help="record BLOCKED (loop cap / escalation)")
    p.add_argument("story_id")
    p.add_argument("--reason", required=True)
    p.add_argument("--gate", default=None,
                   help="human-gate kind for the escalation (e.g. loop_cap)")
    p.add_argument("--by", default="orchestrator")

    p = sub.add_parser("validate-story-id", help="canonical id validation")
    p.add_argument("candidate")

    p = sub.add_parser("claim", help="claim a story (one open claim each)")
    p.add_argument("story_id")
    p.add_argument("--owner", required=True)
    p.add_argument("--host", required=True)
    p.add_argument("--session", required=True)
    p.add_argument("--start-sha", default=None)
    p.add_argument("--start-shas", default=None, metavar="R=SHA[,R=SHA...]",
                   help="multi-repo start pin: repo-id=sha pairs")
    p.add_argument("--by", default="orchestrator")

    p = sub.add_parser("handover", help="transfer an open claim")
    p.add_argument("story_id")
    p.add_argument("--from", dest="from_owner", required=True)
    p.add_argument("--to", dest="to_owner", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--flagged", action="store_true",
                   help="abrupt takeover (from != current owner)")
    p.add_argument("--by", default="orchestrator")

    p = sub.add_parser("release", help="release an open claim")
    p.add_argument("story_id")
    p.add_argument("--owner", required=True)
    p.add_argument("--by", default="orchestrator")

    p = sub.add_parser("dispatch-id",
                       help="allocate the next pre-spawn dispatch id")
    p.add_argument("story_id")
    p.add_argument("role")

    sub.add_parser("override", help="show the active OVERRIDE (if any)")

    args = ap.parse_args(argv)
    ev = dict(x.partition("=")[::2] for x in
              (args.evidence if args.cmd == "request" else []))
    try:
        c = Controller(args.root)
    except ControllerError as exc:
        print(f"REFUSED: {exc}")
        return 1
    try:
        if args.cmd == "state":
            print(f"{args.story_id}: {c.state(args.story_id)}")
            b = c.blocked_from(args.story_id)
            if b:
                print(f"  (BLOCKED from {b} — resumes there)")
            return 0
        if args.cmd == "request":
            return c.request(args.story_id, args.to_state, ev, args.by,
                             args.approved_by)
        if args.cmd == "blocked":
            return c.blocked(args.story_id, args.reason, args.by, args.gate)
        if args.cmd == "validate-story-id":
            errs = validate_story_id(args.candidate, c.stories, c.alias_file,
                                     c.events)
            if errs:
                print("INVALID:")
                for e in errs:
                    print("  - " + e)
                return 1
            print(f"VALID: {args.candidate}")
            return 0
        if args.cmd == "claim":
            if (args.start_sha is None) == (args.start_shas is None):
                print("REFUSED: claim takes exactly one of --start-sha, "
                      "--start-shas")
                return 2
            shas = None
            if args.start_shas is not None:
                try:
                    shas = _parse_start_shas(args.start_shas)
                except ControllerError as exc:
                    print(f"REFUSED: {exc}")
                    return 2
            return c.claim(args.story_id, args.owner, args.host,
                           args.session, args.start_sha, shas, args.by)
        if args.cmd == "handover":
            return c.handover(args.story_id, args.from_owner, args.to_owner,
                              args.reason, args.flagged, args.by)
        if args.cmd == "release":
            return c.release(args.story_id, args.owner, args.by)
        if args.cmd == "dispatch-id":
            return c.dispatch_id(args.story_id, args.role)
        if args.cmd == "override":
            return c.show_override()
    except ControllerError as exc:
        print(f"REFUSED: {exc}")
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
