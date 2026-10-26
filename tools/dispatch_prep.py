#!/usr/bin/env python3
"""tools/dispatch_prep.py — deterministic seam recon for coder/fixer
dispatches. ZERO LLM calls.

Every fact a coder/fixer would otherwise burn discovery calls on is
extracted mechanically from the plan's contract block + the REAL tree at
`--sha`, and emitted as ONE "ALREADY DECIDED" markdown block that the
orchestrator pastes into the dispatch package (architecture.md §Dispatch
package; §Control plane row `dispatch_prep.py`).

Sections produced:
  1. contract slice        — the binding ```contract.yaml block verbatim
  2. named-test inventory  — every contract test: EXISTS (extend, keep
                             siblings intact) vs MISSING (create), with
                             the path:line where it lives
  3. seams                 — only when the contract declares `seams`
                             (omitted otherwise — not part of the frozen
                             v1 schema; a project may extend its plans)
  4. scope boundary        — files in THIS dispatch's contract vs the rest
                             of the plan (stop rules)
  5. hard read gate        — the hard-read gate block: the allowed-reads
                             list + STOP rule, generated from the plan
                             manifest + the named-test inventory
  6. ripple call sites     — only with declared `--ripple` patterns
                             (parameterized: the reference's C#/DI grep
                             was project-specific — omitted when nothing
                             is declared)
  7. known traps           — only with a declared `--traps` file (bullets
                             inlined verbatim; omitted otherwise)

The output IS the read gate: the child reads ONLY what section 5 lists.
The "full budget burned on exploration" failure class becomes
structurally impossible — there is nothing left to discover.

This module also owns the shared git/grep helpers (`git`, `grep_tree`,
`_evidence_line`, `seam_verify`, `seam_check`) reused by plan_prep and
later gate work — keep them importable under these names.

Genericization notes:
  * No repo/layout constants: `--root` is the git repo, `--plan` the plan
    file (default `<root>/stories/<id>/impl-plan.md`), `--sha` the pinned
    base, `--out` the destination (default
    `<root>/stories/<id>/dispatch-prep.md`). No cwd assumptions.
  * Test-project normalization is structural (any declared `project`
    becomes the grep glob directory); no project-file extension rules.
  * Seam declaration matching uses a language-generic declaration
    pattern (class/interface/record/struct/enum/def/fn/func/type/trait/
    module) instead of the reference's C#-only list.

USAGE:
  python3 tools/dispatch_prep.py STORY-NNN --root <repo> [--sha HEAD]
      [--plan <impl-plan.md>] [--task N] [--out FILE]
      [--ripple 'PATTERN with {name}']... [--traps FILE]

Exit codes (config_validator convention): 0 ok · 2 usage / missing
required input (no plan, no parseable contract block, unreadable sha,
failed grep, escaping default destination).
Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    from . import contract_lib as C
except ImportError:  # direct-run layout: load the tools package itself
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools import contract_lib as C

# Language-generic declaration pattern for seam symbols (no project
# vocabulary — the reference's list was C#-specific). Plain group, not
# `(?:…)`: the pattern runs through git grep -E (POSIX ERE), which has
# no non-capturing groups.
DECL_KEYWORDS = r"(class|interface|record|struct|enum|def|fn|func|type|trait|module)"


def git(root: Path, *args: str) -> str:
    """Run git in `root`; raise RuntimeError on nonzero exit (callers
    decide). All repo paths are parameterized — no repo constants."""
    r = subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


class GrepError(RuntimeError):
    """git grep itself failed (invalid regex, bad revision, ...). Exit 1
    means NO MATCHES and stays an empty result; every other nonzero exit
    is a hard failure the caller must surface — never a silent empty
    list (an invalid ripple pattern must not read as 'no call sites')."""


def _grep_run(args: list[str], pattern: str) -> list[str]:
    """Run one git grep; exit 0/1 -> hits (possibly empty); anything
    else -> GrepError naming the pattern and git's stderr."""
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode not in (0, 1):
        raise GrepError(
            f"git grep failed for pattern {pattern!r} "
            f"(exit {r.returncode}): {r.stderr.strip()}")
    return r.stdout.splitlines()


def grep_tree(root: Path, pattern: str, sha: str,
              globs: tuple[str, ...] = ()) -> list[str]:
    """git grep at a pinned SHA — the same view the child will get.
    Empty `globs` greps the whole tree (no layout assumption). Raises
    GrepError on any grep failure other than 'no matches' (exit 1)."""
    out: list[str] = []
    for g in globs or ("",):
        args = ["git", "-C", str(root), "grep", "-n", "-E", pattern, sha]
        if g:
            args += ["--", g]
        out.extend(_grep_run(args, pattern))
    return out


def _split_hit(h: str) -> tuple[str, str, str] | None:
    """'<rev>:<path>:<line>:<content>' -> (path, line, content)."""
    parts = h.split(":", 3)
    if len(parts) < 3:
        return None
    return parts[1], parts[2], parts[3].strip()


def _evidence_line(hit: str, width: int = 96) -> str:
    """git-grep hit '<rev>:<path>:<line>:<content>' -> 'path:line — content'."""
    parts = hit.split(":", 3)
    if len(parts) < 4:
        return hit
    content = parts[3].strip()
    if len(content) > width:
        content = content[:width].rstrip() + " …"
    return f"{parts[1]}:{parts[2]} — {content}"


def contract_from_plan(plan_path: Path) -> tuple[dict | None, str]:
    """Reuse contract_lib's parser (same one the gate uses — no drift)."""
    text = plan_path.read_text(encoding="utf-8")
    try:
        return C.extract_contract(text), text
    except C.ContractParseError as e:
        print(f"WARN: contract parse failed: {e}", file=sys.stderr)
        return None, text


def raw_contract(text: str) -> str:
    m = C.CONTRACT_FENCE_RE.search(text)
    return m.group(1).strip() if m else ""


def _pinned_type(root: Path, sha: str, path: str) -> str | None:
    """`git cat-file -t <sha>:<path>` -> 'tree' | 'blob' | None when the
    path is absent at the pinned sha (AN1, round 39)."""
    r = subprocess.run(["git", "-C", str(root), "cat-file", "-t",
                        f"{sha}:{path}"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def _test_glob(entry: dict, root: Path, sha: str) -> tuple[str, ...]:
    """Grep glob for one contract test entry: the declared `project`
    directory when present (any path shape — structural normalization,
    no project-file extension rules), else the whole tree.

    Tree-vs-file is decided at the PINNED SHA (`git cat-file -t`), never
    by the dotted-suffix heuristic (AN1, round 39): a project DIRECTORY
    whose name carries a suffix (`tests/unit.v1`) stays itself —
    reducing it to `tests/` would report a sibling project's tests
    (`tests/unit.v2`) as EXISTS for the WRONG project and pull the
    sibling file into the allowed reads. Only an actual FILE reduces to
    its parent directory. A path absent at the sha is treated as a
    directory (nothing to reduce; the grep simply finds no hits)."""
    proj = entry.get("project") or ""
    if not isinstance(proj, str) or not proj.strip():
        return ()
    g = proj.removeprefix("./").rstrip("/")
    if g and _pinned_type(root, sha, g) == "blob":
        g = str(Path(g).parent).removeprefix("./").rstrip("/")  # project FILE
    return (g + "/",) if g else ()


def _member_decl_lines(src: list[str], owner: str, member: str) -> list[int]:
    """1-based line numbers where `member` is DECLARED inside the
    indentation block of an `owner` declaration (AN2/AN3, round 39).
    BOTH identifiers must appear in a declaration shape (a
    `DECL_KEYWORDS` keyword followed by the name) — a bare reference
    (call site, comment, string mention) never counts. Definition-only
    evidence; deliberately NO signature enforcement (an EXTEND seam
    legitimately changes shape)."""
    owner_re = re.compile(rf"\b{DECL_KEYWORDS}\s+{re.escape(owner)}\b")
    member_re = re.compile(rf"\b{DECL_KEYWORDS}\s+{re.escape(member)}\b")
    found: list[int] = []
    i, n = 0, len(src)
    while i < n:
        line = src[i]
        if owner_re.search(line):
            oind = len(line) - len(line.lstrip())
            j = i + 1
            while j < n:
                body = src[j]
                if body.strip():
                    if len(body) - len(body.lstrip()) <= oind:
                        break  # dedent — left the owner's block
                    if member_re.search(body):
                        found.append(j + 1)
                j += 1
            i = j
        else:
            i += 1
    return found


def _qualified_test_locs(root: Path, name: str, sha: str,
                         globs: tuple[str, ...]) -> list[str]:
    """Resolve a qualified test name `Owner.member` WITHIN its declared
    owner (AN2, round 39): locate the owner declaration at the pinned
    sha, then the member declaration inside the owner's block. Evidence
    is the MEMBER definition line only (definition-only, same standard
    as the unqualified grep) — the old whole-name declaration pattern
    reported an existing `ParserTests.test_x` as MISSING because no
    declaration keyword ever precedes the DOTTED form. A same-named
    member of a DIFFERENT class is not evidence."""
    owner, member = name.rsplit(".", 1)
    locs: list[str] = []
    ohits = grep_tree(root, rf"\b{DECL_KEYWORDS}\s+{re.escape(owner)}\b",
                      sha, globs)
    seen: set[str] = set()
    for h in ohits:
        parts = h.split(":", 3)  # sha:path:line:content
        if len(parts) < 3 or parts[1] in seen:
            continue
        seen.add(parts[1])
        try:
            src = git(root, "show", f"{sha}:{parts[1]}").splitlines()
        except RuntimeError:
            continue
        for ln in _member_decl_lines(src, owner, member):
            locs.append(f"{parts[1]}:{ln}")
            if len(locs) >= 3:
                return locs
    return locs


def test_inventory(root: Path, contract: dict, sha: str) -> list[dict]:
    """EXISTS/MISSING inventory of the contract's named tests at `sha`.
    Existence evidence is a real test DEFINITION (`def`/`class`-style
    declaration matching the name) — never a bare mention: with
    `tests[].project` omitted the whole-tree search would otherwise
    count the committed plan's OWN test-name declaration as evidence
    the test exists (reporting EXISTS + 'extend the plan')."""
    inv = []
    for t in contract.get("tests") or []:
        name = t.get("name") or ""
        proj = t.get("project") or ""
        globs = _test_glob(t, root, sha)
        if "." in name:
            # qualified `Owner.member`: resolve the member WITHIN its
            # owner's declaration block (AN2, round 39)
            locs = _qualified_test_locs(root, name, sha, globs)
        else:
            pattern = rf"\b{DECL_KEYWORDS}\s+{re.escape(name)}\b"
            hits = grep_tree(root, pattern, sha, globs)
            locs = []
            for h in hits[:3]:
                parts = h.split(":", 3)  # sha:path:line:content
                if len(parts) >= 3:
                    locs.append(f"{parts[1]}:{parts[2]}")
        status = "EXISTS" if locs else "MISSING"
        # a MISSING name the contract declares via a CREATE test file is
        # expected — the coder creates it (TDD). Action column says so.
        inv.append({
            "ac": t.get("ac"), "name": name, "project": proj,
            "status": status, "at": locs,
            "asserts": t.get("asserts"),
        })
    return inv


def _grep_file(root: Path, pattern: str, sha: str, path: str) -> list[str]:
    args = ["git", "-C", str(root), "grep", "-n", "-E", pattern,
            sha, "--", path]
    return _grep_run(args, pattern)


def seam_verify(root: Path, contract: dict, sha: str,
                tree_globs: tuple[str, ...] = ()) -> list[dict]:
    """Signature-level seam check. Returns one row per seam: {symbol,
    file, op, status, detail, evidence}; op CREATE|EXTEND, status
    OK|FAIL|WARN, evidence = list of 'path:line — <trimmed source>'.

    Only runs when the plan declares `seams` (not part of the frozen v1
    contract schema — a project extension). Rules:
      CREATE: file must NOT exist at <sha> (FAIL if it does); the type
        part of the symbol must not already resolve tree-wide at <sha>,
        otherwise WARN with the collision locations.
      EXTEND: file must exist at <sha> (FAIL otherwise); the symbol must
        resolve IN that file — a QUALIFIED symbol `Owner.member`
        requires an OWNER-MATCHED MEMBER DECLARATION (the member
        declared inside the owner's declaration block; AN3 round 39),
        declaration-level grep otherwise. There is deliberately
        NO signature equality check — an EXTEND seam legitimately changes
        shape; the frozen signature is the target, the base evidence is
        the starting point.
    """
    creates, _ = C.contract_paths(contract)
    rows: list[dict] = []
    for s in contract.get("seams") or []:
        f = (s or {}).get("file") or ""
        sym = (s or {}).get("symbol") or "?"
        sig = (s or {}).get("signature") or "?"
        op = "CREATE" if f in creates else "EXTEND"
        row = {"symbol": sym, "file": f, "op": op, "status": "OK",
               "detail": "", "evidence": []}
        try:
            git(root, "cat-file", "-e", f"{sha}:{f}")
            file_exists = True
        except RuntimeError:
            file_exists = False
        if op == "CREATE":
            if file_exists:
                row["status"] = "FAIL"
                row["detail"] = (f"declared CREATE but `{f}` already exists "
                                 f"at {sha[:12]}")
            else:
                name = sym.split(".")[0]
                hits = grep_tree(root, rf"\b{re.escape(name)}\b", sha,
                                 tree_globs)
                if hits:
                    row["status"] = "WARN"
                    row["detail"] = (f"`{name}` already resolves tree-wide at "
                                     f"base (outside the CREATE file) — verify "
                                     "this is not an unintended collision")
                    row["evidence"] = [_evidence_line(h) for h in hits[:5]]
                else:
                    row["detail"] = (f"`{f}` and `{name}` absent at "
                                     f"{sha[:12]} — CREATE confirmed")
            rows.append(row)
            continue
        # EXTEND
        if not file_exists:
            row["status"] = "FAIL"
            row["detail"] = (f"EXTEND file `{f}` does not exist at "
                             f"{sha[:12]} — plan defect")
            rows.append(row)
            continue
        if "." in sym:
            # Qualified seam `Owner.member` (AN3, round 39): require an
            # OWNER-MATCHED MEMBER DECLARATION — the member must be
            # declared INSIDE the owner's block in this file. The old
            # bare member grep accepted `MissingParser.parse_data` when
            # only `Parser.parse_data` existed, and a call site or a
            # comment carrying the member token also satisfied it.
            # No signature enforcement (an EXTEND legitimately changes
            # shape) — declaration presence only.
            owner, member = sym.rsplit(".", 1)
            try:
                src = git(root, "show", f"{sha}:{f}").splitlines()
            except RuntimeError:
                src = []
            member_lines = _member_decl_lines(src, owner, member)
            if member_lines:
                row["detail"] = f"declared signature: {sig}"
                row["evidence"] = [
                    _evidence_line(f"{sha}:{f}:{ln}:{src[ln - 1].strip()}")
                    for ln in member_lines[:3]]
            else:
                row["status"] = "FAIL"
                row["detail"] = (
                    f"symbol `{sym}` does not resolve in `{f}`@{sha[:12]}"
                    f" — no `{member}` DECLARATION inside an `{owner}` "
                    "declaration (references/calls do not count); "
                    "misnamed, wrong owner, or needs CREATE")
            rows.append(row)
            continue
        pattern = rf"\b{DECL_KEYWORDS}\s+{re.escape(sym)}\b"
        hits = _grep_file(root, pattern, sha, f)
        if not hits:
            row["status"] = "FAIL"
            row["detail"] = (f"symbol `{sym}` does not resolve in `{f}`"
                             f"@{sha[:12]} — misnamed, wrong file, or "
                             "needs CREATE")
        else:
            row["detail"] = f"declared signature: {sig}"
            row["evidence"] = [_evidence_line(h) for h in hits[:3]]
        rows.append(row)
    return rows


def seam_check(root: Path, contract: dict, sha: str,
               tree_globs: tuple[str, ...] = ()) -> list[str]:
    """Rendered FROM seam_verify(): OK rows as '- `sym` → `sig` (OP
    `file`)'; FAIL rows lead with '- FAIL: seam … STOP and report'."""
    lines = []
    seams = contract.get("seams") or []
    for s, r in zip(seams, seam_verify(root, contract, sha, tree_globs)):
        sym, f = r["symbol"], r["file"]
        sig = (s or {}).get("signature") or "?"
        if r["status"] == "OK":
            lines.append(f"- `{sym}` → `{sig}` ({r['op']} `{f}`)")
        elif r["status"] == "FAIL":
            lines.append(f"- FAIL: seam `{sym}` ({r['op']} `{f}`): "
                         f"{r['detail']} — STOP and report")
        else:  # WARN
            lines.append(f"- WARNING: seam `{sym}` ({r['op']} `{f}`): "
                         f"{r['detail']}")
            for ev in r["evidence"][:5]:
                lines.append(f"  - {ev}")
    return lines


def ripple_scan(root: Path, contract: dict, sha: str,
                patterns: list[str], tree_globs: tuple[str, ...] = (),
                cap: int = 40) -> list[str]:
    """Declared-pattern ripple scan (parameterized replacement for the
    reference's project-specific constructor/DI grep). Each pattern may
    contain `{name}`, substituted with the stem of every declared file
    path. Omitted entirely when no patterns are declared."""
    lines = []
    names = sorted({Path(f.get("path") or "").stem
                    for f in contract.get("files") or []
                    if f.get("path")})
    truncated = 0
    for name in names:
        for pat in patterns:
            hits = grep_tree(root, pat.replace("{name}", re.escape(name)),
                             sha, tree_globs)
            for h in hits:
                if len(lines) >= cap:
                    truncated += 1
                    continue
                parts = h.split(":", 3)  # sha:path:line:content — drop sha
                loc = ":".join(parts[1:3]) if len(parts) >= 3 else h
                lines.append(f"- `{loc}` — `{name}`")
    if truncated:
        lines.append(f"- … {truncated} more call sites (grep the declared "
                     "ripple patterns in the worktree if one is needed)")
    return lines


def build(root: Path, story_id: str, sha: str, plan_path: Path,
          task: str | None = None, ripple: list[str] | None = None,
          traps: Path | None = None,
          tree_globs: tuple[str, ...] = ()) -> tuple[list[str], int]:
    """Assemble the ALREADY DECIDED block. Returns (lines, exit-code-ish
    status: 0 ok, 2 missing/unparseable plan)."""
    if not plan_path.exists():
        print(f"error: no plan at {plan_path}", file=sys.stderr)
        return [], 2
    contract, plan_text = contract_from_plan(plan_path)
    if not contract:
        print("error: no parseable contract.yaml block in the plan",
              file=sys.stderr)
        return [], 2

    creates, extends = C.contract_paths(contract)
    inv = test_inventory(root, contract, sha)
    seams = seam_check(root, contract, sha, tree_globs) \
        if contract.get("seams") else []
    allowed_reads = sorted(set(
        [f.get("path") for f in contract.get("files") or []]
        + [i["at"][0].rsplit(":", 1)[0] for i in inv if i["at"]]
    ))

    out = [f"# ALREADY DECIDED — {story_id}"
           + (f" task {task}" if task else "") +
           f" (dispatch_prep @ {sha[:12]})",
           "",
           "_Deterministic seam recon. Do NOT re-verify anything in this "
           "block._",
           "",
           "## 1. Contract slice (binding)", "",
           "```yaml", raw_contract(plan_text) or json.dumps(contract, indent=1),
           "```", "",
           "## 2. Named-test inventory", "",
           "| AC | Test | Project | Status | Action |", "|---|---|---|---|---|"]
    for i in inv:
        action = ("EXTEND that file — keep existing tests intact"
                  if i["status"] == "EXISTS" else "CREATE")
        out.append(f"| {i['ac']} | `{i['name']}` | `{i['project']}` | "
                   f"{i['status']}{' @ ' + i['at'][0] if i['at'] else ''} | "
                   f"{action} |")
    if seams:
        out += ["", "## 3. Seams (exact signatures — make ZERO API-shape "
                "decisions)", ""]
        out += seams
    out += ["",
            "## 4. Scope boundary", "",
            f"- THIS dispatch owns: "
            f"{', '.join(f'`{p}`' for p in sorted(creates | extends)) or '—'}",
            "- Anything else in the plan belongs to a LATER task — do not "
            "touch it.",
            "",
            "## 5. Hard read gate", "",
            "Read ONLY these paths (plus files you CREATE):"]
    for p in allowed_reads:
        out.append(f"- `{p}`")
    out += ["",
            "If anything else is needed: STOP and report a SPECIFIC "
            "retrieval request (exact path or one question). No repo "
            "exploration. Write your first edit within 10 tool calls.", ""]
    if ripple:
        scan = ripple_scan(root, contract, sha, ripple, tree_globs)
        out += ["## 6. Ripple call sites (design constraint)", ""]
        out += scan or ["- none detected at this SHA"]
        out += ["",
                "Call sites OUTSIDE the manifest are a design constraint: "
                "keep the touched unit source-compatible instead of "
                "widening the change set.", ""]
    if traps and traps.exists():
        lines = traps.read_text(encoding="utf-8").splitlines()
        # inline the bullet body (everything from the first "- " line on),
        # skipping any curated-doc header/preamble prose
        start = next((i for i, l in enumerate(lines) if l.startswith("- ")),
                     None)
        if start is not None:
            out += ["## 7. Known traps (verified lessons — do not "
                    "re-derive these)", ""]
            out += lines[start:]
            out.append("")
    return out, 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("story_id")
    ap.add_argument("--root", required=True, help="git repo the plan is pinned in")
    ap.add_argument("--sha", default="HEAD")
    ap.add_argument("--plan", default=None,
                    help="plan file (default <root>/stories/<id>/impl-plan.md)")
    ap.add_argument("--task", default=None,
                    help="task label for scope-boundary notes")
    ap.add_argument("--out", default=None)
    ap.add_argument("--ripple", action="append", default=[], metavar="PATTERN",
                    help="optional ripple-scan regex; {name} = declared file stem")
    ap.add_argument("--traps", default=None,
                    help="optional curated traps file to inline as section 7")
    ap.add_argument("--tree-glob", action="append", default=[],
                    help="optional tree glob for whole-tree scans (repeatable)")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    plan_path = Path(args.plan) if args.plan else \
        root / "stories" / args.story_id / "impl-plan.md"
    try:
        sha = git(root, "rev-parse", "--verify",
                  f"{args.sha}^{{commit}}").strip()
    except RuntimeError as e:
        print(f"error: unreadable sha `{args.sha}`: {e}", file=sys.stderr)
        return 2
    try:
        out, status = build(root, args.story_id, sha, plan_path, args.task,
                            args.ripple, Path(args.traps) if args.traps
                            else None, tuple(args.tree_glob))
    except GrepError as e:
        print(f"error: refusing to build the dispatch pack — {e}",
              file=sys.stderr)
        return 2
    if status:
        return status
    text = "\n".join(out)
    if not text.endswith("\n"):
        text += "\n"
    # Destination confinement (same rule as make_context_bundle): the
    # default destination is built from the untrusted story_id, so it
    # must resolve (following symlinks) strictly under <root>/stories
    # BEFORE any mkdir/write — `../../outside` must never escape, and a
    # canonical id whose story directory is a SYMLINK outward must not
    # write through it. An explicit --out is the user's own override
    # (not id-derived), so it is taken as given.
    stories_root = (root / "stories").resolve()
    if args.out:
        dest = Path(args.out)
    else:
        cand = (root / "stories" / args.story_id
                / "dispatch-prep.md").resolve()
        if not cand.is_relative_to(stories_root):
            print(f"error: refusing story id {args.story_id!r} — the default "
                  f"prep destination ({cand}) escapes the stories "
                  f"directory ({stories_root}); canonical story ids are "
                  "STORY-NNN (zero-padded 3 digits)", file=sys.stderr)
            return 2
        dest = cand
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {dest} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
