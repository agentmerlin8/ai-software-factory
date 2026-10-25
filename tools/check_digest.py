#!/usr/bin/env python3
"""tools/check_digest.py — deterministic gate for the context-acquisition
digest (gate catalog slug: `digest-gate`).

Validates that stories/<STORY-ID>/context-digest.md is complete and honest
at a pinned base SHA, BEFORE the planner is dispatched on it:

1. All 8 schema sections present (no omissions).
2. Every path in the File manifest (§1) exists at BASE_SHA (git cat-file).
3. The AC count in §2 matches the actual count of top-level AC bullets in
   the story file (ground truth = story file at BASE_SHA).
4. Every code anchor in §4 resolves AT BASE_SHA (path:line exists, symbol
   token present on that line) — anchors are the hallucination risk, so
   they get the strictest check.
5. §7 risk list has only L/M/H markers.
6. Under the 150-line cap.

Exit 0 = pass (planner may dispatch). Exit 1 = fail with listed reasons
(re-dispatch context acquisition, never hand-patch the digest). Exit 2 =
usage.

Genericization notes:
  * The repo root is `--root` (default cwd) — no layout constants.
  * The 8-section schema wording is project-neutral and kept as-is; the
    kit does not (yet) ship its own context-digest template, so this list
    IS the kit's digest format until one is frozen elsewhere.
  * Story-file ground truth is looked for at the canonical spec path
    `stories/<sid>/spec.md` first (architecture.md §Installed control
    home; same order as the plan gate's `resolve_spec()`), then
    `stories/<sid>/story.md` (kit transition-table layout), then
    `stories/<sid>.md` (reference layout).

USAGE: python3 tools/check_digest.py <STORY-ID> [BASE_SHA] [--root DIR]
  (installed control-home form: python3 factory/tools/check_digest.py ...)
  (BASE_SHA defaults to origin/main)

Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:  # package layout (tools.check_digest)
    from .dispatch_prep import git
except ImportError:  # direct-run layout: load the tools package itself
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.dispatch_prep import git

SECTIONS = ["## 1. File manifest", "## 2. Story contract", "## 3. Frozen decisions",
            "## 4. Code anchors", "## 5. Conventions", "## 6. Open questions",
            "## 7. Risk list", "## 8. Verification block"]
LINE_CAP = 150


def story_file_candidates(root: Path, story: str) -> list[Path]:
    """Ground-truth story file locations, canonical spec path first
    (R8-I2: `stories/<sid>/spec.md` per architecture.md §Installed
    control home — the same order as the plan gate's `resolve_spec()`),
    then the kit transition-table layout, then the reference layout."""
    return [root / "stories" / story / "spec.md",
            root / "stories" / story / "story.md",
            root / "stories" / f"{story}.md"]


DECL_KEYWORDS = frozenset({
    "async", "def", "class", "function", "fn", "func", "export",
    "public", "private", "protected", "internal", "static",
    "abstract", "final"})

# Same token grammar as the plan gate's base-sha ladder (plan_checks
# SHA_RE) — the declared-vs-supplied check must parse the digest with
# the identical rules plan_checks.resolve_base_sha() applies to it.
SHA_RE = r"[0-9a-f]{7,40}"


def declared_base_sha(text: str) -> str | None:
    """The base sha the digest DECLARES, parsed with the digest-side
    rules of plan_checks.resolve_base_sha() (same ladder, digest rungs
    only): the `BASE_SHA` token first, then the §8 verification block's
    `git rev-parse <sha>` / `pinned … <sha>` forms. None when the digest
    declares no base (that case is unchanged — nothing to compare)."""
    m = re.search(rf"\bBASE_SHA\b[^\S\n]*[:=]?[^\S\n]*({SHA_RE})\b", text)
    if m:
        return m.group(1)
    m8 = re.search(r"## 8\. Verification block\n(.*)", text, re.S)
    section = m8.group(1) if m8 else text
    rm = re.search(rf"git rev-parse\s+({SHA_RE})\b", section)
    if rm:
        return rm.group(1)
    pm = re.search(rf"\b(?:pinned at|pinned|at)\s+({SHA_RE})\b", section)
    return pm.group(1) if pm else None


def run_gate(root: Path, story: str, sha: str) -> list[str]:
    """Pure gate body: returns the list of failure reasons ([] = pass).
    Raises RuntimeError only when git itself is unusable."""
    # 0. R8-I3: validate the base revision AT ENTRY — with an empty-path
    # manifest and `- none` anchors the only git reads left are ones whose
    # failures fall back to the working tree, so a NONEXISTENT base sha
    # could otherwise pass every check. Unresolvable → named failure and
    # no further checks.
    try:
        git(root, "rev-parse", "--verify", f"{sha}^{{commit}}")
    except RuntimeError:
        return [f"unresolvable base sha: {sha}"]
    digest_path = root / "stories" / story / "context-digest.md"
    if not digest_path.exists():
        return [f"no digest at {digest_path.relative_to(root)}"]
    text = digest_path.read_text(encoding="utf-8")
    errors: list[str] = []

    # 0b. R11-L2: declared-vs-supplied base must be the SAME commit — a
    # digest declaring `BASE_SHA: <B>` validated against supplied commit
    # A would let plan_checks.resolve_base_sha() consume a DIFFERENT
    # snapshot than this gate validated. Both sides resolve to commit
    # ids (`rev-parse --verify <rev>^{commit}`), so a matching
    # full/abbrev pair passes. A digest with NO declared base is
    # unchanged (nothing to compare).
    declared = declared_base_sha(text)
    if declared is not None:
        try:
            declared_id = git(root, "rev-parse", "--verify",
                              f"{declared}^{{commit}}").strip()
        except RuntimeError:
            return [f"unresolvable declared base sha: {declared}"]
        supplied_id = git(root, "rev-parse", "--verify",
                          f"{sha}^{{commit}}").strip()
        if declared_id != supplied_id:
            return [f"base sha mismatch: digest declares {declared}, "
                    f"invocation supplied {sha}"]

    # 1. sections present
    for s in SECTIONS:
        if s not in text:
            errors.append(f"missing section: {s}")

    # 6. line cap
    lines = text.splitlines()
    if len(lines) > LINE_CAP:
        errors.append(f"digest too long: {len(lines)} lines (>{LINE_CAP})")

    # 2. manifest paths exist at sha (first cell of each table row only).
    # R6-G2: the heading regexes tolerate trailing whitespace after the
    # heading text, and a REQUIRED section that is present-but-unparseable
    # fails closed with a named error instead of silently skipping its
    # validation.
    m = re.search(r"## 1\. File manifest[ \t]*\n(.*?)(?=\n## 2\.)",
                  text, re.S)
    if m is None and "## 1. File manifest" in text:
        errors.append("unparseable section: ## 1. File manifest")
    if m:
        for line in m.group(1).splitlines():
            # R14-O1: the leading pipe is OPTIONAL — Markdown allows
            # table rows without it (`src/x.py | subject`), and a
            # pipe-less ghost row must not slip past the existence
            # check. Header (`path | role`) and separator (`---|---`)
            # skips below already key off the CELL text, so they keep
            # working with or without the leading pipe.
            mm = re.match(r"^\s*\|?\s*`?([^`|\n]+?)`?\s*\|", line)
            if not mm:
                continue
            p = mm.group(1).strip()
            # strip a literal "./" prefix only — a character-set strip
            # would eat the leading dot of hidden paths (.github/…) and
            # fail valid digests
            p = p[2:] if p.startswith("./") else p
            if not p or p.lower() == "path" or (
                    set(p) <= {"-", ":"} and "-" in p):
                # skip ONLY the literal path-table header cell and
                # separator rows whose cell contains only `-`/`:`
                # characters (incl. aligned forms `:---` / `---:` /
                # `:---:` — R7-H1) — real paths like `paths/config.json`
                # or `pathlib.py` must still hit the existence check
                continue
            try:
                git(root, "cat-file", "-e", f"{sha}:{p}")
            except RuntimeError:
                errors.append(f"manifest path missing at {sha}: {p}")

    # 3. AC count matches story file — BOTH inputs are REQUIRED (fail
    # closed): without the declaration or without the ground-truth story
    # file the honesty check cannot run, and "cannot check" is a refusal.
    # R15-P1: the ground truth is the PINNED TREE — probe the canonical
    # candidates at the sha first (`git cat-file -e <sha>:<path>`) and read
    # the winner from the pinned tree. Probing the working tree first let a
    # local layout change (delete the committed two-AC spec.md, drop in a
    # one-AC story.md) hide the spec that exists at the supplied SHA and
    # pass a digest declaring the wrong count. Only when NO candidate
    # exists at the sha does the INTENTIONAL uncommitted-story fallback
    # (working tree, same candidate order) apply.
    ac_match = re.search(r"Acceptance criteria: exactly (\d+)", text)
    pinned_rel = None
    for f in story_file_candidates(root, story):
        rel = f.relative_to(root).as_posix()
        try:
            git(root, "cat-file", "-e", f"{sha}:{rel}")
        except RuntimeError:
            continue
        pinned_rel = rel
        break
    story_file = None
    if pinned_rel is None:
        story_file = next((f for f in story_file_candidates(root, story)
                           if f.exists()), None)
    if ac_match is None:
        errors.append('missing AC-count declaration '
                      '("Acceptance criteria: exactly N")')
    elif pinned_rel is None and story_file is None:
        cands = ", ".join(f.relative_to(root).as_posix()
                          for f in story_file_candidates(root, story))
        errors.append("story file not found for AC-count ground truth "
                      f"(looked at {cands})")
    else:
        if pinned_rel is not None:
            story_text = git(root, "show", f"{sha}:{pinned_rel}")
        else:  # uncommitted story — intentional working-tree fallback
            story_text = story_file.read_text(encoding="utf-8")
        actual = sum(1 for l in story_text.splitlines()
                     if re.match(r"^\s*(?:#{1,6}\s+|[-*]\s+\*{0,2})?"
                                 r"AC-\d+\b", l))
        declared = int(ac_match.group(1))
        if actual != declared:
            errors.append(f"AC count mismatch: digest says {declared}, "
                          f"story file has ~{actual}")

    # 4. code anchors grep-resolve at sha. R6-G3: the path token drops the
    # extension requirement — extensionless anchors (Dockerfile:12,
    # bin/build:3) get the same existence/line-bound checks. R11-L3: the
    # bullet marker accepts all Markdown unordered-list forms (`-`, `*`,
    # `+`) — a `*`-bulleted ghost anchor must not be silently skipped.
    # R14-O2: ORDERED-list markers (`1.`) are accepted too — a numbered
    # ghost anchor must not fall through to `continue` unchecked.
    am = re.search(r"## 4\. Code anchors[ \t]*\n(.*?)(?=\n## 5\.)",
                   text, re.S)
    if am is None and "## 4. Code anchors" in text:
        errors.append("unparseable section: ## 4. Code anchors")
    if am:
        for line in am.group(1).splitlines():
            mm = re.match(r"^\s*(?:[-*+]|\d+\.)?\s*"
                          r"`?([^`:\s]+):(\d+)`?\s*—?\s*([^—]*)",
                          line)
            if not mm:
                continue
            path, lineno, desc = mm.group(1), int(mm.group(2)), mm.group(3)
            if lineno < 1:
                errors.append(f"anchor line must be >= 1: {path}:{lineno}")
                continue
            try:
                src = git(root, "show", f"{sha}:{path}")
            except RuntimeError:
                errors.append(f"anchor path missing at {sha}: {path}")
                continue
            src_lines = src.splitlines()
            if lineno > len(src_lines):
                errors.append(f"anchor line {lineno} beyond file {path} "
                              f"({len(src_lines)} lines)")
                continue
            tokens = re.findall(r"\w+", desc)
            if tokens:
                # R8-I4: skip leading declaration keywords/modifiers —
                # `async def missing_symbol` must NOT pass against
                # `async def existing():` because only the keyword tokens
                # were checked. The first remaining SYMBOL token must
                # appear on the line; when every token is a keyword the
                # check degrades to the old first-two-tokens behavior.
                symbols = [t for t in tokens if t not in DECL_KEYWORDS]
                check = symbols[:1] if symbols else tokens[:2]
                # R9-J2: COMPLETE token match, not substring — anchor
                # `def total` must NOT pass against
                # `def compute_total(items):` (a substring pass on an
                # incorrect anchor would authorize planner dispatch).
                line_tokens = set(re.findall(r"\w+", src_lines[lineno - 1]))
                if not all(t in line_tokens for t in check):
                    errors.append(f"anchor symbol not on line {lineno} of "
                                  f"{path}: {check}")

    # 5. risk markers valid
    rk = re.search(r"## 7\. Risk list[ \t]*\n(.*?)(?=\n## 8\.)", text, re.S)
    if rk is None and "## 7. Risk list" in text:
        errors.append("unparseable section: ## 7. Risk list")
    if rk:
        for line in rk.group(1).splitlines():
            for m2 in re.findall(
                    r"(?<![A-Za-z])`?([A-Z])`?(?=\s*(?:$|—|–|-))", line):
                if m2 not in ("L", "M", "H"):
                    errors.append(f"bad risk marker: {m2}")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="check_digest",
        description="Digest gate: context-digest.md completeness + honesty "
                    "at a pinned base sha.")
    ap.add_argument("story", metavar="STORY-ID")
    ap.add_argument("sha", nargs="?", default="origin/main", metavar="BASE_SHA")
    ap.add_argument("--root", default=".",
                    help="repo root (default: cwd)")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()

    errors = run_gate(root, args.story, args.sha)
    if errors:
        print("DIGEST GATE FAIL:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    digest = root / "stories" / args.story / "context-digest.md"
    n_lines = len(digest.read_text(encoding="utf-8").splitlines()) \
        if digest.exists() else 0
    print(f"digest {args.story} OK at {args.sha} "
          f"({n_lines} lines, all checks pass)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
