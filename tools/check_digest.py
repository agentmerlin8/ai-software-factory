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
    "abstract", "final",
    # R18-S1: more declaration keywords — `export` alone was filtered
    # but `interface`/`const`/`struct`/`enum` were not, so an anchor
    # `export interface MissingShape` passed against
    # `export interface ExistingShape {}` (only `export` was checked
    # as the first symbol token). The DECLARED NAME must be the first
    # checked symbol token for these declaration forms too.
    "interface", "const", "struct", "enum"})

# Same token grammar as the plan gate's base-sha ladder (plan_checks
# SHA_RE) — the declared-vs-supplied check must parse the digest with
# the identical rules plan_checks.resolve_base_sha() applies to it.
SHA_RE = r"[0-9a-f]{7,40}"


def _trailing_signature(desc: str) -> tuple[str | None, bool]:
    """Round 32 AG2: the member name of a trailing call signature.

    Returns (name, ambiguous). `name` is the member identifier of a
    trailing `NAME(…)`, `NAME<T>(…)`, or `NAME<Map<K,V>>(…)` form
    (nested generic parameter lists supported; optional trailing `:`),
    None when the description does not end in a call signature.
    `ambiguous` is True when the description DOES end in parens but the
    shape before them is not a plain (optionally generic) identifier —
    an unsupported signature shape refuses by name instead of falling
    through to a weaker token check.
    """
    m = re.search(r"\([^()]*\):?\s*$", desc)
    if not m:
        return None, False
    head = desc[:m.start()].rstrip()
    if head.endswith(">"):
        # strip a BALANCED generic parameter list `<…>` (scan backwards,
        # so nesting like `<Map<K,V>>` peels correctly)
        depth = 0
        cut = None
        for i in range(len(head) - 1, -1, -1):
            ch = head[i]
            if ch == ">":
                depth += 1
            elif ch == "<":
                depth -= 1
                if depth == 0:
                    cut = i
                    break
        if cut is None:
            return None, True  # unbalanced `>` — unsupported shape
        head = head[:cut].rstrip()
    nm = re.search(r"([A-Za-z_]\w*)\s*$", head)
    if nm is None:
        # ends in parens but the head is not a plain identifier (e.g.
        # `operator[](int)`, `items[0](x)`) — refuse, never guess
        return None, True
    return nm.group(1), False


def anchor_symbols(desc: str) -> tuple[list[list[str]] | None, str | None]:
    """Resolve a §4 anchor description to the symbol token(s) to check.

    Round 32 S1 — the ACCEPTED description grammar (closed set; each
    form checks the RIGHT symbol, every unsupported shape with a
    declaration/assignment refuses BY NAME — never a silent weaker
    check):

    1. `NAME`                             → [NAME]
    2. keyword-prefixed `NAME`            → [NAME] (leading
       DECL_KEYWORDS skipped, R8-I4)
    3. `TYPE NAME` (no parens, no `=`)    → two ordered groups:
       [FIRST non-keyword symbol] (the frozen R8-I4 check), then
       [LAST non-keyword symbol] (the declared-name position) — a
       ghost in EITHER position flags, naming the offending symbol;
       an all-keyword description degrades to the first two tokens
       (one group, R8-I4)
    4. `NAME(…)` / `NAME<T>(…)` /         → [NAME] (member identifier
       `NAME<Map<K,V>>(…)`, optional       before the parens; generic
       trailing `:`                        parameter lists stripped,
       (R24-Y1, round 32 AG2)              nesting supported)
    5. `[TYPE] NAME(…)` with keywords     → [NAME]
    6. `NAME = init…`                     → [NAME] (declared name =
       LAST identifier before the depth-0 `=`; R29 AD3/R30 AE1/R31 AF1)
    7. `[TYPE] NAME = init…`              → [NAME]
    8. `NAME: TYPE = init…`               → [NAME] (annotated
       declaration, TYPE a plain identifier — the annotation is
       stripped; round 32 AG1)

    REFUSED BY NAME (named `ambiguous anchor symbol …` errors):
    * multiple declarators — more than one depth-0 `=` (`a = 1, b = 2`)
    * destructuring / subscript targets (`[a, b] = …`, `{a} = …`,
      `items[0] = …`) — initializer-form refusal
    * a non-plain annotated target (`items[0]: int = …`) —
      annotated-declaration refusal
    * a non-plain annotation TYPE (`x: Map<K,V> = …`) — falls to the
      initializer-form refusal (gap between the last identifier and
      the `=` is not whitespace)
    * a trailing call signature whose head is not a plain (optionally
      generic) identifier — signature-form refusal

    Returns (check_groups, None) on resolution — an ordered list of
    token groups, each ALL-of which must appear on the anchored line;
    the FIRST failing group is the one reported — or (None, message) on
    refusal. Callers only invoke this when the description has word
    tokens.
    """
    # depth-0 `=` scan (R30 AE1: a `=` inside a parameter list is a
    # default, not an assignment; R29 AD3 operator lookarounds keep
    # `==`/`<=`/`+=`/… out). ALL depth-0 assignments are collected:
    # more than one is a multiple-declarator form (S1) and refuses.
    asg_positions: list[int] = []
    depth = 0
    for i, ch in enumerate(desc):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        elif (ch == "=" and depth == 0
              and desc[i + 1:i + 2] != "="
              and not re.match(r"[<>=!+\-*/&|^%]", desc[i - 1:i])):
            asg_positions.append(i)
    if len(asg_positions) > 1:
        return None, ("ambiguous anchor symbol for multiple-declarator "
                      f"form: {desc!r} — name one declared symbol per "
                      "anchor")
    if asg_positions:
        asg = asg_positions[0]
        head = desc[:asg]
        # Round 32 AG1: an annotated declaration `TARGET : TYPE = init`
        # (TYPE a plain identifier) checks the TARGET — the old last-
        # identifier-before-`=` rule selected the TYPE (`Missing: int
        # = 1` checked `int` and passed against `Existing: int = 1`).
        ann = re.fullmatch(r"(.*?)\s*:\s*([A-Za-z_]\w*)\s*", head)
        if ann:
            pre = ann.group(1)
            tgt = None
            for tid in re.finditer(r"[A-Za-z_]\w*", pre):
                tgt = tid
            if tgt is not None and re.fullmatch(r"\s*", pre[tgt.end():]):
                return [[tgt.group(0)]], None
            # non-plain annotated target (`items[0]: int = …`) — refuse
            return None, ("ambiguous anchor symbol for annotated "
                          f"declaration: {desc!r} — name the declared "
                          "symbol explicitly")
        # R31 AF1: plain/typed initializer — declared name is the LAST
        # identifier before the `=`, with ONLY whitespace in between;
        # anything else (brackets, parens, operators) is ambiguous.
        last_id = None
        for mid in re.finditer(r"[A-Za-z_]\w*", head):
            last_id = mid
        if last_id is not None and \
                re.fullmatch(r"\s*", head[last_id.end():asg]):
            return [[last_id.group(0)]], None
        return None, ("ambiguous anchor symbol for initializer "
                      f"form: {desc!r} — name the declared symbol "
                      "explicitly")
    name, ambiguous = _trailing_signature(desc)
    if ambiguous:
        return None, ("ambiguous anchor symbol for signature "
                      f"form: {desc!r} — name the declared symbol "
                      "explicitly")
    if name is not None:
        return [[name]], None
    # No signature, no assignment: two ordered groups (S1 form 3) —
    # the FIRST non-keyword symbol (frozen R8-I4 check) and the LAST
    # non-keyword symbol (the declared-name position). A ghost in
    # either position flags and the first failing group is reported,
    # so `public static Missing Bar` still reports ['Missing'] while
    # a matching `public static Foo Bar` passes on both groups.
    # All-keyword descriptions degrade to one group of the first two
    # tokens (R8-I4).
    tokens = re.findall(r"\w+", desc)
    symbols = [t for t in tokens if t not in DECL_KEYWORDS]
    if not symbols:
        return [tokens[:2]], None
    if symbols[0] == symbols[-1]:
        return [[symbols[0]]], None
    return [[symbols[0]], [symbols[-1]]], None


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
            # Round 28 AC2: the line-number group is SIGNED — an anchor
            # like `src/ghost.py:-1` used to miss the unsigned regex and
            # be silently skipped; capturing the sign lets the lineno < 1
            # check below reject it by name.
            # Round 29 AD2: parenthesized ordered markers (`1)`) are
            # accepted alongside `1.` — a `1)`-numbered ghost anchor
            # must not be silently skipped either.
            mm = re.match(r"^\s*(?:[-*+]|\d+[.)])?\s*"
                          r"`?([^`:\s]+):(-?\d+)`?\s*—?\s*([^—]*)",
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
                # Round 32 S1: the description→symbol resolution moved
                # to anchor_symbols() — the closed ACCEPTED grammar
                # (plain NAME; keyword-prefixed NAME; TYPE NAME;
                # NAME(...)/NAME<T>(...) signatures; NAME = init;
                # TYPE NAME = init; NAME: TYPE = init annotated
                # declarations) with named refusals for every
                # unsupported declaration/assignment shape (multiple
                # declarators, destructuring/subscript targets,
                # non-plain annotated targets, non-plain signature
                # heads). History: R24-Y1 call signatures check the
                # member identifier before the `(`; R29 AD3 the
                # declared name wins over the trailing initializer;
                # R30 AE1 a parameter default is not an assignment;
                # R31 AF1 the declared name is the LAST identifier
                # before the depth-0 `=`; round 32 AG1 an annotated
                # declaration checks the TARGET (annotation stripped);
                # round 32 AG2 generic parameter lists (`NAME<T>(...)`,
                # nested `NAME<Map<K,V>>(...)`) are supported in the
                # signature form.
                check_groups, refusal = anchor_symbols(desc)
                if refusal is not None or check_groups is None:
                    errors.append(refusal or "ambiguous anchor symbol")
                    continue
                # R9-J2: COMPLETE token match, not substring — anchor
                # `def total` must NOT pass against
                # `def compute_total(items):` (a substring pass on an
                # incorrect anchor would authorize planner dispatch).
                line_tokens = set(re.findall(r"\w+", src_lines[lineno - 1]))
                for check in check_groups:
                    if not all(t in line_tokens for t in check):
                        errors.append(f"anchor symbol not on line {lineno} "
                                      f"of {path}: {check}")
                        break

    # 5. risk markers valid
    rk = re.search(r"## 7\. Risk list[ \t]*\n(.*?)(?=\n## 8\.)", text, re.S)
    if rk is None and "## 7. Risk list" in text:
        errors.append("unparseable section: ## 7. Risk list")
    if rk:
        for line in rk.group(1).splitlines():
            # R19-T1: parse the COMPLETE severity token following the
            # delimiter (—/–/spaced hyphen, per the section grammar).
            # The old single-[A-Z] pattern produced NO candidate for
            # `- stale cache — CRITICAL`, so the membership check never
            # ran and an invalid severity passed. A trailing token after
            # the last delimiter is the severity declaration; anything
            # outside L/M/H is a named finding. No delimiter (or no
            # single trailing token) = missing severity = unchanged.
            m2 = re.search(r"(?:—|–|\s-\s)\s*`?(\S+?)`?\s*$", line)
            if m2 and m2.group(1) not in ("L", "M", "H"):
                errors.append(f"bad risk marker: {m2.group(1)}")
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
