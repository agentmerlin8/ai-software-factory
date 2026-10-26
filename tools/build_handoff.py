#!/usr/bin/env python3
"""tools/build_handoff.py — deterministic role context packages
(architecture.md §Dispatch package; §Control plane row
`build_handoff.py`).

    python3 tools/build_handoff.py <story-id> <role> --root <control-home>
        [--repo <git-repo>] [--budget N] [--out <path>] [--record-context]
        [--dispatch-id <pre-spawn id>]

Roles in v0.1 scope (canonical snake_case per tools/role_catalog.py):
`coder`, `behavior_reviewer` (pass 1, plan-blind), `contract_reviewer`
(pass 2). CLI role spellings canonicalize through
role_catalog.canonical_role (hyphen variants accepted).

Packages are assembled from the ledger + repo artifacts ONLY —
deterministic, stdlib-only, no judgment. What each role sees/doesn't see
is enforced by construction: the behavior reviewer's package CANNOT
contain the plan (it is never read for that role).

Every package embeds:
  - prompt_version  (versioned — trials compare models, not drifting
                    prompts; format <role>-v<kit-version>.<rev>)
  - the resume protocol preamble (DEFAULT preamble of every subagent
                    prompt)
  - remaining-budget placeholder (fill before dispatch)
  - retrieval-request rule: missing context → SPECIFIC request (file
                    path / question); open-ended repo exploration
                    prohibited

Genericization notes:
  * No default paths, no cwd assumptions: `--root` is the control home
    (stories/, factory/ledger/events.jsonl); `--repo` (default: --root)
    is the git repo used for SHAs/diffs; `--out` the destination.
  * Ownership facts come from the kit ledger schema: worktree from the
    story's dispatch events; the start pin from its claim event; the
    BUILD_GREEN commit_sha from the transition's evidence. The
    reference's trial telemetry fields were dropped and do not exist
    here.
  * Delivery surfaces are the frozen vocabulary: `dispatch-package`
    (internal review roles). The reference's external-PR context record
    is out of v0.1 scope for this tool.
  * The `review_context` ledger event goes through ledger_lib
    .append_event — the ONLY write path (validates before writing).

Exit codes (config_validator convention): 0 ok · 2 usage / missing
required input. Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    from . import contract_lib as C
    from . import ledger_lib as L
    from . import role_catalog as RC
except ImportError:  # direct-run layout: load the tools package itself
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools import contract_lib as C
    from tools import ledger_lib as L
    from tools import role_catalog as RC

PROMPT_VERSION_BASE = "v0.1"
PROMPT_REVISIONS = {
    "coder": 1,
    "behavior_reviewer": 1,
    "contract_reviewer": 1,
}
BUILD_ROLES = ("coder", "behavior_reviewer", "contract_reviewer")
REVIEW_ROLES = ("behavior_reviewer", "contract_reviewer")
DELIVERY_SURFACE = "dispatch-package"

# Round 38 AM1: WITHHELD artifacts must stay out of EVERY supplied review
# input (diff, manifest, digest, fallback diff instructions) — not just out
# of the package prose. The behavior reviewer's withheld list names the
# plan, prior verdicts, coder justifications, and contract reviewer's
# notes; story artifacts live under `stories/` and verdict/finding rows in
# the ledger, so when the control home sits INSIDE the reviewed tree both
# are excluded from the supplied diff context via git pathspecs. Contract
# review withholds nothing from its diff (its plan paths are EXPLICITLY
# supplied inputs, listed in additional_supplied_paths).
WITHHELD_DIFF_PATHS = {
    "behavior_reviewer": ("stories/", "factory/ledger/"),
}


def withheld_paths(role: str) -> tuple[str, ...]:
    """Repo paths withheld from the supplied diff inputs for `role`."""
    return WITHHELD_DIFF_PATHS.get(RC.canonical_role(role), ())


def withheld_pathspecs(role: str) -> list[str]:
    """Git exclude pathspecs for `role`'s withheld diff paths."""
    return [f":(exclude){p}" for p in withheld_paths(role)]

RESUME_PREAMBLE = """\
## Resume protocol (DEFAULT preamble)

Check `git status`, `git log -5`, and your artifact directory FIRST;
resume from the last checkpoint; prior artifacts are READ-ONLY (do not
re-verify frozen decisions); your remaining tool-call budget is {budget}
calls; write your first edit within 10 tool calls. On any blocker outside
this package: STOP and report — do not self-patch plans or skip gates.

## Retrieval rule

If context you need is missing from this package, issue a SPECIFIC
retrieval request (exact file path or a single question) and stop.
Open-ended repo exploration is prohibited: a full budget burned reading
with zero output is the failure class this rule closes.
"""

CODER_RULES = """\
## Coder rules (handoff contract)

- You are the SOLE code author. The plan is a design contract, not code —
  write the implementation yourself; follow the contract's files/tests/
  invariants/verify_commands exactly.
- Work ONLY in worktree `{worktree}` on branch `{branch}`. Never a bare
  `git checkout -b` in the canonical tree. Never switch branches there.
- TDD order: named test first, watch it fail, implement, watch it pass,
  commit (conventional commits: `test(<id>): …`, `feat(<id>): …`).
- Read ONLY the files listed in this package. Judgment calls: log them in
  your completion report; do not expand scope.
- Pre-verified "known traps" content (when pinned into this package) is
  applied directly — do not spend test-drive rounds re-deriving it.
- Exit condition (all three, else you are not done): build green, the
  story's named tests green, work committed. Those results feed the
  review prompt.
- Deviation from contract files/tests with justification → report it;
  the orchestrator records a `deviation` ledger event. Do not rewrite
  the plan.
"""

BEHAVIOR_REVIEWER_RULES = """\
## Pass 1 — plan-blind behavior review (STANDING mode)

You have NOT seen the plan, prior verdicts, coder justifications, or the
contract reviewer's notes — by design. Judge the DIFF as behavior.

Before adversarial probes, produce a **scope-dimension table** for every
changed query, cache key, list/aggregate, and mutation:

| Changed behavior | Dimensions that define scope | Producer → consumers | Propagation check | Verdict |
|---|---|---|---|---|

Enumerate every applicable dimension rather than assuming a generic ID is
enough: tenant/account, user, parent-child ownership, page, pageSize,
sort, filter, time window, locale/currency, mode/status, and any
domain-specific scope visible in the diff. For each dimension, trace
request/input → validation → query/cache key → aggregation/mutation →
response/invalidation. Mark a dimension N/A only with one-line evidence.
A scope dimension present at one layer but absent at another is a
reasoning finding, not a nit.

Required output — counterexample table (one row per probe, minimum):

| Boundary input / state transition | Predicted failure | Exact check performed | Result | Remaining uncertainty |
|---|---|---|---|---|

- Cover, when relevant: config equivalence (flag ON/OFF, env vs code
  defaults), wrapper/decorator delegation (sync AND async paths), copied
  guards (precedent is NOT proof — name the inherited assumption and run
  one adversarial test), first frame after a state transition, same-mount
  close/reopen (not only fresh render).
- Copied boundary logic: require one named inherited assumption + one
  adversarial test.
- Mutation evidence: high-risk assertions ONLY (bounded).
- Write your findings to the report file INCREMENTALLY (write, then
  append); your final chat message is a verdict line + file path.
- Every finding carries: reviewed_sha, introduced_sha if known, source
  (internal), class (mechanical/reasoning/contract/nit), severity,
  disposition proposal. A fix that changes logic is NEVER class nit.
- Findings from this pass are recorded BEFORE the plan is exposed to
  anyone in the review chain.
"""

CONTRACT_REVIEWER_RULES = """\
## Pass 2 — contract review

You see the approved contract, the diff, the gate's manifest
reconciliation, and the deviations list. You do NOT see pass 1's raw
reasoning — findings only (they will be attached as a findings list).

- Check the diff against the contract: files (CREATE/EXTEND), named
  tests, invariants, verify_commands, frozen decisions.
- Invariants bind independently of the test list: a behavior violating an
  invariant is a FAIL even if no planned test covers it.
- Frozen decisions: ENGAGE the stated rationale, never re-litigate from
  absence. You may FAIL a listed decision only by saying why the
  rationale is wrong.
- Deviations: explicit disposition each — legitimate (justification
  recorded) or contract violation. Accepted risk is recorded separately
  from mechanical nit.
- Verdict: PASS / PASS-WITH-NITS / FAIL. Nits enumerated, mechanical,
  logic_touched=false.
"""


def git(repo: Path, *args: str) -> str:
    """Run git in `repo`; raise CalledProcessError on failure."""
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True,
                          check=True).stdout.strip()


def head_sha(repo: Path) -> str:
    try:
        return git(repo, "rev-parse", "HEAD")
    except subprocess.CalledProcessError:
        return "<unknown>"


def prompt_version(role: str, rev: int | None = None) -> str:
    role = RC.canonical_role(role)
    revision = PROMPT_REVISIONS.get(role, 1) if rev is None else rev
    return f"{role}-{PROMPT_VERSION_BASE}.{revision}"


def stories_root(root: Path) -> Path:
    return root / "stories"


def ledger_path(root: Path) -> Path:
    return root / "factory" / "ledger" / "events.jsonl"


def load_contract(stories: Path, story_id: str) -> tuple[dict | None, str]:
    plan = stories / story_id / "impl-plan.md"
    if not plan.exists():
        return None, str(plan)
    try:
        return C.extract_contract(plan.read_text(encoding="utf-8")), str(plan)
    except C.ContractParseError as e:
        return None, f"{plan} (contract error: {e})"


def raw_contract_yaml(stories: Path, story_id: str) -> str:
    """The contract fence text verbatim (what the gate parses)."""
    plan = stories / story_id / "impl-plan.md"
    if not plan.exists():
        return ""
    m = C.CONTRACT_FENCE_RE.search(plan.read_text(encoding="utf-8"))
    return m.group(1).strip() if m else ""


def story_events(root: Path, story_id: str,
                 etype: str | None = None) -> list[dict]:
    events = L.load_events(ledger_path(root))
    return L.events_for(events, story_id, etype)


def _dispatch_worktree(root: Path, story_id: str) -> str | None:
    """Worktree from the story's dispatch events (kit schema: worktree
    rides on dispatch rows, nullable with a reason sibling)."""
    wt = None
    for e in story_events(root, story_id, "dispatch"):
        if e.get("worktree"):
            wt = e["worktree"]
    return wt


def claim(root: Path, story_id: str) -> dict | None:
    """Latest claim assignment event (None when never claimed)."""
    got = None
    for e in story_events(root, story_id, "assignment"):
        if e.get("op") == "claim":
            got = e
    return got


def branch_name(stories: Path, story_id: str) -> str:
    """`story/<id>-<slug>` (branch convention). Slug from handoff.md if
    present, else a placeholder the orchestrator fills at dispatch."""
    short = story_id.lower().replace("story-", "")
    handoff = stories / story_id / "handoff.md"
    if handoff.exists():
        m = re.search(r"story/" + re.escape(short) + r"-([\w-]+)",
                      handoff.read_text(encoding="utf-8"))
        if m:
            return f"story/{short}-{m.group(1)}"
    return f"story/{short}-<slug>"


def reviewed_sha(root: Path, story_id: str) -> str:
    """Exact BUILD_GREEN commit_sha (carried in the transition's evidence,
    persisted by the controller into check_results.evidence_supplied)."""
    reviewed = None
    for event in story_events(root, story_id, "transition"):
        if event.get("to_state") != "BUILD_GREEN":
            continue
        supplied = ((event.get("check_results") or {})
                    .get("evidence_supplied") or {}).get("detail") or {}
        if supplied.get("commit_sha"):
            reviewed = supplied["commit_sha"]
    return reviewed or ("<BUILD_GREEN commit_sha — record it in the "
                        "ledger first>")


def build_review_context_manifest(root: Path, repo: Path, story_id: str,
                                  role: str,
                                  target_sha: str | None = None,
                                  base_ref: str = "main") -> dict:
    """Return the exact diff files/hunks supplied to one review surface.

    The manifest is intentionally plan-blind for behavior review;
    contract review adds only the frozen plan/test-plan paths.
    Additional files read by a reviewer must be listed in its report so
    attribution can distinguish CONTEXT_ABSENT from a true
    MISSED_WITH_CONTEXT finding.
    """
    role = RC.canonical_role(role)
    requested = target_sha or reviewed_sha(root, story_id)
    manifest: dict = {
        "manifest_version": 1,
        "story_id": story_id,
        "review_role": role,
        "base_ref": base_ref,
        "base_sha": None,
        "reviewed_sha": requested,
        "delivery_surface": DELIVERY_SURFACE,
        "diff_sha256": None,
        "files": [],
        "additional_supplied_paths": [],
        # Round 38 AM1: the withheld boundary is explicit in the manifest —
        # paths excluded from every supplied input for this role.
        "withheld_paths": list(withheld_paths(role)),
    }
    if role == "contract_reviewer":
        manifest["additional_supplied_paths"] = [
            f"stories/{story_id}/impl-plan.md",
            f"stories/{story_id}/test-plan.md",
        ]
    if str(requested).startswith("<"):
        manifest["error"] = "BUILD_GREEN SHA missing; do not dispatch review"
        return manifest
    try:
        target = git(repo, "rev-parse", f"{requested}^{{commit}}")
        base_sha = git(repo, "merge-base", base_ref, target)
        manifest["reviewed_sha"] = target
        manifest["base_sha"] = base_sha
        # Round 38 AM1: withheld artifacts are filtered OUT of every
        # supplied input — the name-status list, the canonical patch text,
        # and therefore the diff_sha256 digest — via git exclude pathspecs.
        specs = withheld_pathspecs(role)
        statuses = git(repo, "diff", "--name-status", base_sha, target,
                       *specs)
        patch_text = git(repo, "diff", "--binary", "--unified=0",
                         base_sha, target, *specs)
        manifest["diff_sha256"] = \
            hashlib.sha256(patch_text.encode()).hexdigest()
    except subprocess.CalledProcessError as exc:
        manifest["error"] = f"git diff context unavailable: {exc}"
        return manifest

    files: dict[str, dict] = {}
    for line in statuses.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            path = parts[-1]
            files[path] = {"path": path, "status": parts[0], "hunks": []}
    current: str | None = None
    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            files.setdefault(current,
                             {"path": current, "status": "?", "hunks": []})
        elif current and line.startswith("@@"):
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            files[current]["hunks"].append({
                "header": line,
                "new_start": int(m.group(1)) if m else None,
                "new_count": int(m.group(2) or "1") if m else None,
            })
    manifest["files"] = [files[p] for p in sorted(files)]
    return manifest


def manifest_json(manifest: dict) -> str:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"))


def manifest_bytes(manifest: dict) -> bytes:
    return (manifest_json(manifest) + "\n").encode()


def manifest_digest(manifest: dict) -> str:
    return hashlib.sha256(manifest_bytes(manifest)).hexdigest()


def inline_diff_block(repo: Path, manifest: dict,
                      cap_lines: int = 4000) -> list[str]:
    """Embed the reviewed diff IN the reviewer package so reviewers don't
    burn tool calls round-tripping `git diff` through the terminal.
    Capped: over the cap we fall back to a per-file stat list + the exact
    git command. The manifest's sha256 stays on the --unified=0 canonical
    form."""
    base, target = manifest.get("base_sha"), manifest.get("reviewed_sha")
    if not base or not target or str(target).startswith("<"):
        return []
    # Round 38 AM1: the inlined/fallback diff honors the same withheld
    # boundary as the manifest — exclude pathspecs on the unified=3 patch,
    # the --stat fallback, AND the exact read command handed to the
    # reviewer, so a withheld artifact cannot be re-fetched by following
    # the supplied instruction.
    role = manifest.get("review_role", "")
    specs = withheld_pathspecs(role) if role else []
    spec_args = f" {' '.join(repr(s) for s in specs)}" if specs else ""
    try:
        patch = git(repo, "diff", "--unified=3", base, target, *specs)
    except subprocess.CalledProcessError:
        return []
    lines = patch.splitlines()
    if len(lines) > cap_lines:
        stat = git(repo, "diff", "--stat", base, target, *specs)
        return ["## Branch diff (TOO LARGE to inline — "
                f"{len(lines):,} lines > {cap_lines} cap)", "",
                "```", stat, "```", "",
                f"Read it with: `git diff {base[:12]} {target[:12]}"
                f"{spec_args}` "
                "(paged reads, only the files your findings touch).", ""]
    return ["## Branch diff (INLINED — do not re-run git diff for this "
            "range)",
            "", f"`git diff {base[:12]} {target[:12]}{spec_args}` — "
            f"{len(lines):,} lines:",
            "", "```diff", patch.rstrip(), "```", ""]


def review_context_event(root: Path, story_id: str, role: str,
                         manifest: dict, manifest_path: Path,
                         dispatch_id: str | None) -> dict:
    """The review_context ledger row (schema: ledger_schema.REQUIRED —
    validated by ledger_lib.append_event on write)."""
    role = RC.canonical_role(role)
    if role not in REVIEW_ROLES:
        raise ValueError(f"review_context applies to {REVIEW_ROLES}, "
                         f"got {role!r}")
    if manifest.get("error") or not manifest.get("base_sha") or \
            str(manifest.get("reviewed_sha", "")).startswith("<"):
        raise ValueError("cannot record review context without an exact "
                         "base/diff SHA")
    if not dispatch_id:
        raise ValueError("internal review_context requires its pre-spawn "
                         "dispatch_id")
    return {
        "event": "review_context",
        "story_id": story_id,
        "review_role": role,
        "reviewed_sha": manifest["reviewed_sha"],
        "base_sha": manifest["base_sha"],
        "context_manifest_path": f"stories/{story_id}/"
                                 f"context-manifest-{role}.json",
        "context_manifest_digest": manifest_digest(manifest),
        "prompt_version": prompt_version(role),
        "dispatch_id": dispatch_id,
        "dispatch_id_reason": None,
        "delivery_surface": manifest["delivery_surface"],
        "requested_at": L.utcnow(),
    }


# ------------------------------------------------------------ packages ----

def build_coder(root: Path, repo: Path, story_id: str, budget: str) -> str:
    stories = stories_root(root)
    contract, plan_path = load_contract(stories, story_id)
    c = claim(root, story_id) or {}
    worktree = _dispatch_worktree(root, story_id) or \
        f"<worktree — pin it in the dispatch event>"
    branch = branch_name(stories, story_id)
    start_sha = c.get("start_sha") or head_sha(repo)
    handoff = stories / story_id / "handoff.md"

    out = [f"# Coder package — {story_id}", ""]
    out.append(f"prompt_version: `{prompt_version('coder')}` · "
               f"generated {L.utcnow()} · ledger-driven, deterministic")
    out.append("")
    out.append(RESUME_PREAMBLE.format(budget=budget))
    out.append(CODER_RULES.format(worktree=worktree, branch=branch))
    out += ["## Ownership (ledger claim + dispatch)", "",
            f"- worktree: `{worktree}`",
            f"- branch: `{branch}`",
            f"- start_sha: `{start_sha}`",
            f"- claim owner: `{c.get('owner') or '(no open claim)'}`",
            ""]
    if handoff.exists():
        out += ["## handoff.md (verbatim)", "",
                handoff.read_text(encoding="utf-8").strip(), ""]
    if contract:
        out += ["## Approved contract block (the binding part of the plan)",
                "", "```yaml", raw_contract_yaml(stories, story_id),
                "```", ""]
        creates, extends = C.contract_paths(contract)
        out += ["## Files in scope (EXACT — nothing else)", ""]
        for p in sorted(creates):
            out.append(f"- CREATE `{p}`")
        for p in sorted(extends):
            out.append(f"- EXTEND `{p}`")
        out.append("")
        out += ["## Verified commands (run exactly these)", ""]
        for cmd in contract.get("verify_commands") or []:
            out.append(f"- `{cmd}`")
        out.append("")
        out += ["## Named tests (TDD targets, per AC)", "",
                "| AC | Project | Test | Asserts |", "|---|---|---|---|"]
        for t in contract.get("tests") or []:
            out.append(f"| {t.get('ac')} | `{t.get('project') or '—'}` "
                       f"| `{t.get('name')}` | {t.get('asserts') or '—'} |")
        out.append("")
        out += ["## Invariants (binding product behavior)", ""]
        for v in contract.get("invariants") or []:
            out.append(f"- **{v.get('id')}**: {v.get('text')}")
        out.append("")
    else:
        out += ["## CONTRACT MISSING — STOP", "",
                f"No valid contract block at `{plan_path}`. Do not proceed; "
                "report this as a specific retrieval request.", ""]
    # withheld by construction: full plan prose history, other stories,
    # planning conversation — none of it is read here.
    out += ["## Withheld from you (by design)", "",
            "- full plan prose history, other stories, planning conversation",
            "- reviewer notes and prior verdicts", ""]
    return "\n".join(out)


def build_behavior_reviewer(root: Path, repo: Path, story_id: str,
                            budget: str,
                            base_ref: str = "main") -> str:
    a = claim(root, story_id) or {}
    reviewed = reviewed_sha(root, story_id)
    manifest = build_review_context_manifest(root, repo, story_id,
                                             "behavior_reviewer", reviewed,
                                             base_ref)
    reviewed = manifest["reviewed_sha"]
    base_sha = manifest.get("base_sha") or "<base sha unavailable>"
    suite = None
    for e in story_events(root, story_id, "stage"):
        if e.get("stage") == "full_suite":
            suite = e
    out = [f"# Behavior-reviewer package (pass 1, PLAN-BLIND) — {story_id}",
           ""]
    out.append(f"prompt_version: `{prompt_version('behavior_reviewer')}` · "
               f"generated {L.utcnow()}")
    out.append("")
    out.append(RESUME_PREAMBLE.format(budget=budget))
    out.append(BEHAVIOR_REVIEWER_RULES)
    out += ["## Review target", "",
            f"- exact reviewed SHA: `{reviewed}`",
            f"- immutable diff: `git diff {base_sha} {reviewed} "
            f"{' '.join(repr(s) for s in withheld_pathspecs('behavior_reviewer'))} --` — "
            "INLINED below; do not re-run git for this range. Use git only "
            "for adjacent context; if you must build, use a scratch "
            "worktree — never the shared tree",
            f"- claim owner (read-only reference): "
            f"`{a.get('owner') or '(ledger claim missing)'}`",
            "",
            "## Exact supplied-context manifest", "",
            f"- digest: `{manifest_digest(manifest)}`",
            "- Every file/hunk supplied by the branch diff is listed below.",
            "- In your report, append every additional path + line range "
            "you read; omission makes a later external finding "
            "CONTEXT_ABSENT, not a model miss.",
            "", "```json",
            json.dumps(manifest, indent=2, sort_keys=True), "```", ""]
    out += inline_diff_block(repo, manifest)
    out += ["## Neighboring context to READ (specific paths only)", "",
            "- changed production/test/config files (from the diff)",
            "- adjacent callers and registrations of every changed seam",
            "- actual dependency APIs (read the real signatures, not doc "
            "comments)",
            ""]
    if suite:
        out += ["## Test names + results (workload facts)", "",
                f"- full suite: passed {suite.get('tests_passed')}, failed "
                f"{suite.get('tests_failed')}, skipped "
                f"{suite.get('tests_skipped')}, excluded "
                f"{suite.get('tests_excluded')}",
                f"- evidence: `{suite.get('evidence_path')}`", ""]
    out += ["## Counterexample-table template (REQUIRED output)", "",
            "| Boundary input / state transition | Predicted failure | "
            "Exact check performed | Result | Remaining uncertainty |",
            "|---|---|---|---|---|",
            "| | | | | |", "",
            "## Withheld from you (by construction)", "",
            "- the plan (never read for this role), prior verdicts, coder "
            "justifications, contract reviewer's notes", ""]
    return "\n".join(out)


def build_contract_reviewer(root: Path, repo: Path, story_id: str,
                            budget: str,
                            base_ref: str = "main") -> str:
    stories = stories_root(root)
    contract, plan_path = load_contract(stories, story_id)
    reviewed = reviewed_sha(root, story_id)
    manifest = build_review_context_manifest(root, repo, story_id,
                                             "contract_reviewer", reviewed,
                                             base_ref)
    reviewed = manifest["reviewed_sha"]
    base_sha = manifest.get("base_sha") or "<base sha unavailable>"
    devs = story_events(root, story_id, "deviation")
    out = [f"# Contract-reviewer package (pass 2) — {story_id}", ""]
    out.append(f"prompt_version: `{prompt_version('contract_reviewer')}` · "
               f"generated {L.utcnow()}")
    out.append("")
    out.append(RESUME_PREAMBLE.format(budget=budget))
    out.append(CONTRACT_REVIEWER_RULES)
    out += ["## Review target", "",
            f"- exact reviewed SHA: `{reviewed}`",
            f"- immutable diff: `git diff {base_sha} {reviewed} --` — "
            "INLINED below; do not re-run git for this range",
            "",
            "## Exact supplied-context manifest", "",
            f"- digest: `{manifest_digest(manifest)}`",
            "- Report every additional path + line range read beyond this "
            "manifest.",
            "", "```json",
            json.dumps(manifest, indent=2, sort_keys=True), "```", ""]
    out += inline_diff_block(repo, manifest)
    if contract:
        out += ["## Approved contract (binding)", "", "```yaml",
                raw_contract_yaml(stories, story_id), "```", ""]
        creates, extends = C.contract_paths(contract)
        out += ["## Manifest reconciliation input (build-gate output)", "",
                "Run the build gate: `pre_review_gate.py build "
                f"stories/{story_id}`", "",
                f"- declared CREATE: {sorted(creates) or '—'}",
                f"- declared EXTEND: {sorted(extends) or '—'}", ""]
    else:
        out += [f"## CONTRACT MISSING at `{plan_path}` — STOP and report", ""]
    out += ["## Deviations list (ledger)", ""]
    if devs:
        for d in devs:
            out.append(f"- `{d.get('contract_field')}`: planned "
                       f"{d.get('planned')!r} → actual {d.get('actual')!r} — "
                       f"{d.get('justification')}")
    else:
        out.append("(none recorded)")
    out += ["", "## Withheld from you (by construction)", "",
            "- pass 1's raw reasoning — FINDINGS ONLY will be attached by "
            "the orchestrator after this package is built", ""]
    return "\n".join(out)


BUILDERS = {
    "coder": build_coder,
    "behavior_reviewer": build_behavior_reviewer,
    "contract_reviewer": build_contract_reviewer,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("story_id")
    ap.add_argument("role")
    ap.add_argument("--root", required=True, help="control home")
    ap.add_argument("--repo", default=None,
                    help="git repo for SHAs/diffs (default: --root)")
    ap.add_argument("--base-ref", default="main")
    ap.add_argument("--budget", default="<N>",
                    help="remaining tool-call budget to embed")
    ap.add_argument("--out", default=None)
    ap.add_argument("--record-context", action="store_true",
                    help="append review_context event (review roles only)")
    ap.add_argument("--dispatch-id", default=None,
                    help="pre-spawn dispatch id for review_context")
    args = ap.parse_args(argv)

    try:
        role = RC.canonical_role(args.role)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if role not in BUILDERS:
        print(f"error: no handoff package builder for role {role!r}; "
              f"built roles: {sorted(BUILDERS)}", file=sys.stderr)
        return 2

    root = Path(args.root).resolve()
    repo = Path(args.repo).resolve() if args.repo else root
    stories = stories_root(root)
    if not (stories / args.story_id).exists() and \
            not (stories / f"{args.story_id}.md").exists():
        print(f"error: no story artifacts under {stories / args.story_id}",
              file=sys.stderr)
        return 2

    if role in REVIEW_ROLES:
        text = BUILDERS[role](root, repo, args.story_id, args.budget,
                              args.base_ref)
    else:
        text = BUILDERS[role](root, repo, args.story_id, args.budget)
    out = Path(args.out) if args.out else \
        stories / args.story_id / f"handoff-{role}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    package_bytes = (text + "\n").encode()
    out.write_bytes(package_bytes)
    digest = hashlib.sha256(package_bytes).hexdigest()
    print(f"package: {out}")
    print(f"prompt_version: {prompt_version(role)}")
    print(f"package_sha256: {digest}")
    if role in REVIEW_ROLES:
        manifest = build_review_context_manifest(root, repo, args.story_id,
                                                 role,
                                                 base_ref=args.base_ref)
        manifest_path = stories / args.story_id / \
            f"context-manifest-{role}.json"
        manifest_path.write_bytes(manifest_bytes(manifest))
        print(f"context_manifest: {manifest_path}")
        print(f"context_manifest_sha256: {manifest_digest(manifest)}")
        print("record a review_context ledger event BEFORE dispatch")
        if args.record_context:
            # graceful refusal (config_validator convention): a
            # review_context_event precondition failure (missing exact
            # base/diff SHA, missing --dispatch-id) prints `error: <msg>`
            # and exits 2 — never an uncaught traceback. The package and
            # manifest files are already written and stay as-is.
            try:
                event = review_context_event(root, args.story_id, role,
                                             manifest, manifest_path,
                                             args.dispatch_id)
                L.append_event(ledger_path(root), event)
            except ValueError as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
            print(f"review_context_event: {event.get('event_id') or '(id assigned on write)'}")
    elif args.record_context:
        print("error: --record-context applies only to reviewer roles",
              file=sys.stderr)
        return 2
    print("record BOTH package prompt_version/digest and the context "
          "manifest in the ledger so reviewer misses can be attributed to "
          "capability, context, or timing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
