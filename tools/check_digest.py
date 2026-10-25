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
  * Story-file ground truth is looked for at `stories/<sid>/story.md`
    first (kit transition-table layout), then `stories/<sid>.md`
    (reference layout).

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
    """Ground-truth story file locations, kit layout first."""
    return [root / "stories" / story / "story.md", root / "stories" / f"{story}.md"]


def run_gate(root: Path, story: str, sha: str) -> list[str]:
    """Pure gate body: returns the list of failure reasons ([] = pass).
    Raises RuntimeError only when git itself is unusable."""
    digest_path = root / "stories" / story / "context-digest.md"
    if not digest_path.exists():
        return [f"no digest at {digest_path.relative_to(root)}"]
    text = digest_path.read_text(encoding="utf-8")
    errors: list[str] = []

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
            mm = re.match(r"^\s*\|\s*`?([^`|\n]+?)`?\s*\|", line)
            if not mm:
                continue
            p = mm.group(1).strip()
            # strip a literal "./" prefix only — a character-set strip
            # would eat the leading dot of hidden paths (.github/…) and
            # fail valid digests
            p = p[2:] if p.startswith("./") else p
            if not p or p.lower() == "path" or set(p) == {"-"}:
                # skip ONLY the literal path-table header cell and
                # separator rows — real paths like `paths/config.json`
                # or `pathlib.py` must still hit the existence check
                continue
            try:
                git(root, "cat-file", "-e", f"{sha}:{p}")
            except RuntimeError:
                errors.append(f"manifest path missing at {sha}: {p}")

    # 3. AC count matches story file — BOTH inputs are REQUIRED (fail
    # closed): without the declaration or without the ground-truth story
    # file the honesty check cannot run, and "cannot check" is a refusal.
    ac_match = re.search(r"Acceptance criteria: exactly (\d+)", text)
    story_file = next((f for f in story_file_candidates(root, story)
                       if f.exists()), None)
    if ac_match is None:
        errors.append('missing AC-count declaration '
                      '("Acceptance criteria: exactly N")')
    elif story_file is None:
        cands = ", ".join(f.relative_to(root).as_posix()
                          for f in story_file_candidates(root, story))
        errors.append("story file not found for AC-count ground truth "
                      f"(looked at {cands})")
    else:
        rel = story_file.relative_to(root).as_posix()
        try:
            story_text = git(root, "show", f"{sha}:{rel}")
        except RuntimeError:  # story file may not be committed yet — working tree
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
    # bin/build:3) get the same existence/line-bound checks.
    am = re.search(r"## 4\. Code anchors[ \t]*\n(.*?)(?=\n## 5\.)",
                   text, re.S)
    if am is None and "## 4. Code anchors" in text:
        errors.append("unparseable section: ## 4. Code anchors")
    if am:
        for line in am.group(1).splitlines():
            mm = re.match(r"^\s*-?\s*`?([^`:\s]+):(\d+)`?\s*—?\s*([^—]*)",
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
                if not all(t in src_lines[lineno - 1] for t in tokens[:2]):
                    errors.append(f"anchor symbol not on line {lineno} of "
                                  f"{path}: {tokens[:2]}")

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
