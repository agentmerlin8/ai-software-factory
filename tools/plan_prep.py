#!/usr/bin/env python3
"""tools/plan_prep.py — deterministic planner verify-pack.

ZERO LLM calls. Every fact the plan asserts about the code tree at
`--sha` is extracted mechanically into ONE "ALREADY VERIFIED" markdown
block (the verify-pack, architecture.md §Control plane row
`plan_prep.py`) that the planning agent pastes into its review-loop
resolutions or cites from the plan. The "doc-shaped memory" failure
class (vocabularies, schemas, seam signatures re-verified from prose
instead of the tree) becomes structurally impossible — every
determinable fact arrives pre-verified at path:line granularity, and
every miss is an explicit negative line.

Input text sources (read from the WORKTREE under `--root` — they are the
current spec):
  stories/<ID>.md or stories/<ID>/story.md   (required — exit 2 if missing)
  stories/<ID>/context-digest.md             (may be absent)
  stories/<ID>/impl-plan.md                  (may be absent)
Facts are checked against the CODE TREE at --sha (default HEAD) using
the same git plumbing the coder will see.

Sections:
  1. Vocabulary facts       — snake_case candidates grepped in the
                              declared vocabulary globs (--vocab-glob,
                              repeatable); zero hits -> explicit ABSENT
                              negatives. SECTION OMITTED when no glob is
                              declared (the reference's migrations glob
                              was project-specific).
  2. Symbol resolve         — PascalCase + backticked snake candidates
                              via word-boundary grep in the declared
                              tree globs; path:line + declaration line;
                              misses -> "NOT FOUND at base".
  3. Call-site facts        — resolved method-ish symbols: production
                              call sites (declarations and delegations
                              excluded), capped per symbol / total.
  4. Named-test inventory   — the spec's per-AC "Test hooks:" surfaces
                              plus backticked test-name tokens; grep of
                              the test globs -> EXISTS @ path:line /
                              MISSING (mirrors dispatch_prep inventory).
  5. File-path facts        — repo-path tokens containing '/': cat-file
                              at sha -> EXISTS/ABSENT (the CREATE-vs-
                              EXTEND determinant).
  6. Precedent extracts     — prose marker tokens (e.g. NON-DATA-
                              PRESERVING) grepped; hits with path:line.

Genericization notes:
  * No layout constants: repo is `--root`, tree scoping is `--tree-glob`
    (repeatable; empty = whole tree), vocabulary scoping is
    `--vocab-glob`, stop-prefixes for qualified symbols are
    `--skip-prefix`. The reference's migrations glob, DDL column-block
    extraction (CREATE TABLE parsing) and project-file fixture-link
    section were project-specific and are OMITTED — declare a vocab-glob
    to get vocabulary facts; column/link extraction is project gate
    territory, not kit territory.
  * Determinism: no timestamps; identical sha + story files + flags ->
    byte-identical output. Boundedness: candidate caps plus a hard line
    budget; caps are stated in the output footer.

NOT covered by this tool (reviewer/planner territory): semantic shape or
right-purpose judgement of a seam, whether prose-only requirements are
implemented at all, signature equality for EXTEND seams (see
dispatch_prep.seam_verify — equality is deliberately not checked), and
plan task-ordering/coverage logic.

USAGE:
  python3 tools/plan_prep.py STORY-NNN --root <repo> [--sha HEAD]
      [--out FILE] [--tree-glob G]... [--vocab-glob G]... [--skip-prefix P]...
Exit codes (config_validator convention): 0 normal (negatives are fine),
2 missing story file / unreadable sha. Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

try:
    from . import dispatch_prep as DP
except ImportError:  # direct-run layout: load the tools package itself
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools import dispatch_prep as DP

git = DP.git
grep_tree = DP.grep_tree
_evidence_line = DP._evidence_line
_split_hit = DP._split_hit

SNAKE_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
PASCAL_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*(?:\.[A-Z][A-Za-z0-9]+)?\b")
MARKER_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+\b")
ID_MARKER_RE = re.compile(
    r"(?:STORY|DEC|AC|AMB|INV|CM|PRL|BR|CR|F|B|D|M|N|G|T|A|C|Q|E|K|H|L)"
    r"-?\d+$")
PATH_RE = re.compile(
    r"\b[A-Za-z0-9_@.-]+(?:/[A-Za-z0-9_@.-]+)+"
    r"\.(?:py|js|ts|tsx|cs|json|md|sh|toml|yml|yaml|sql|html|css)\b")
BT_RE = re.compile(r"`([^`\n]+)`")
TESTS_TOKEN_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9_]*Tests?(?:\.[A-Za-z0-9_]+)?|\btest_[a-z0-9_]+")
HOOK_RE = re.compile(r".*\bTest hooks?:?\b.*")
DECL_RE = re.compile(
    r"\b(public|private|internal|protected|def|class|fn|func|export)\b")

# token-budget caps (stated in the output footer too)
CAP_VOCAB = 37
CAP_SYMBOLS = 43
CAP_PATHS = 20
CAP_TESTS = 28
CAP_S1_HITS = 4          # lines per vocab candidate
CAP_S1_REFS = 2          # raw ref lines when a token has no anchor lines
CAP_S2_HITS = 2          # extra "also" lines per resolved symbol
CAP_CALL_PER_SYM = 5
CAP_CALL_TOTAL = 40
CAP_S6 = 16
LINE_BUDGET = 215

STOP_PASCAL = frozenset({
    "Tests", "Test", "The", "A", "An", "Task", "Tasks", "Stage",
    "Application", "Fixtures", "Worker", "Release", "Rejected", "No",
    "IDs", "Live", "Provider", "One", "Two", "Three", "All",
    "Any", "Each", "Every", "None", "Note", "Notes", "First", "Next",
    "Same", "New", "Old", "Only", "Case", "Base", "Status", "Story",
    "Stories", "Phase", "Issue", "Draft", "Frozen", "Open", "Zero",
    "High", "Low", "Medium", "Major", "Minor", "Fix", "Nit", "Real",
    "True", "False", "Given", "When", "Then", "Commit", "Commits",
    "Services", "Integration", "Origin", "Proposal", "Duplicate",
    "Risks", "Risk", "Mitigations", "Mitigation", "Requirement",
    "Requirements", "Traceability", "Verification", "And", "For", "From",
    "Also", "Yes", "Its", "Is", "As", "Are", "Can", "May", "Must",
    "Will", "Would", "Should", "This", "That", "These", "Those", "Them",
    "Than", "There", "Here", "With", "Without", "What", "Which", "Who",
    "Whose", "Why", "How", "Where", "Were", "Under", "Over", "Into",
    "Onto", "Off", "Up", "Down", "In", "On", "Out", "At", "Or", "Do",
    "Did", "Does", "Too", "Very", "Way", "Use", "Uses", "Used", "Row",
    "Rows", "Line", "Lines", "Table", "Tables", "Null", "Both",
    "Either", "Neither", "Once", "Twice", "Together", "Yet", "Still",
    "Even", "Never", "Always", "Keep", "Set", "Get", "Put", "Runs",
})


def _clean_pascal(tok: str, skip_prefixes: tuple[str, ...] = ()):
    """None -> skip this Pascal token. Qualified-namespace stop prefixes
    are a project declaration (--skip-prefix), never hardcoded."""
    if tok in STOP_PASCAL or len(tok) < 4:
        return None
    if any(tok.startswith(p) for p in skip_prefixes):
        return None
    return tok.split(".")[0]


def _methodish(sym: str) -> bool:
    return bool(re.search(
        r"(Async$|Append|Create|Update|Insert|Delete|Get[A-Z]|Find|Resolve|"
        r"Record|Enqueue|Claim|Publish|Fail[A-Z]?$|Process|Save|Load|"
        r"Validate|Try[A-Z]|Send[A-Z]|Add[A-Z]|Remove[A-Z]?|Query|Write|"
        r"Read|Build|Run|Execute|Handle|Cancel|Complete)", sym))


def _ranked(counter: Counter, backticked: set[str], cap: int, clean=None):
    """Frequency-ranked tokens; backticked (plan-cited) tokens rank first."""
    entries = []
    for tok, freq in counter.items():
        c = clean(tok) if clean else tok
        if c is None:
            continue
        entries.append((0 if c in backticked else 1, -freq, c, tok))
    seen, out = set(), []
    for _bt, _f, c, tok in sorted(entries):
        if c in seen:
            continue
        seen.add(c)
        out.append(tok)
    return out[:cap]


def _sources(stories: Path, story_id: str) -> list[str]:
    """The story's text sources from the worktree (they are the current
    spec). Index 0 is the required spec; digest/plan may be absent."""
    texts = []
    for spec in (stories / f"{story_id}.md", stories / story_id / "story.md"):
        if spec.exists():
            texts.append(spec.read_text(encoding="utf-8"))
            break
    digest = stories / story_id / "context-digest.md"
    if digest.exists():
        texts.append(digest.read_text(encoding="utf-8"))
    plan = stories / story_id / "impl-plan.md"
    if plan.exists():
        texts.append(plan.read_text(encoding="utf-8"))
    return texts


def _backticked(texts: list[str]) -> set[str]:
    """Symbol-ish tokens found inside backticks (plan citations = priority)."""
    out: set[str] = set()
    for text in texts:
        for bt in BT_RE.findall(text):
            out.update(SNAKE_RE.findall(bt))
            for m in PASCAL_RE.findall(bt):
                if _clean_pascal(m):
                    out.add(m)
    return out


def _trim(s: str, width: int = 88) -> str:
    s = s.strip()
    return s if len(s) <= width else s[:width].rstrip() + " …"


def _word_in(w: str, s: str) -> bool:
    return bool(re.search(rf"\b{re.escape(w)}\b", s))


# ------------------------------------------------------------- sections ---

def s1_vocab(root: Path, snakes: list[str], sha: str,
             vocab_globs: tuple[str, ...]):
    """Vocabulary facts against the DECLARED vocab globs. Returns
    (lines, zero_hit_tokens)."""
    lines: list[tuple[str, bool]] = []
    zero_hit: set[str] = set()
    for t in snakes:
        hits = grep_tree(root, rf"\b{re.escape(t)}\b", sha, vocab_globs)
        if not hits:
            zero_hit.add(t)
            continue
        for h in hits[:CAP_S1_HITS]:
            p, ln, c = _split_hit(h) or ("?", "?", h)
            lines.append((f"- `{t}` {p}:{ln} — {_trim(c, 160)}", False))
    return lines, zero_hit


def s2_symbols(root: Path, symbols: list[str], sha: str,
               tree_globs: tuple[str, ...]) -> list[tuple[str, bool]]:
    out: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for tok in symbols:
        typed = tok.split(".")[0]
        if typed in seen:
            continue
        seen.add(typed)
        hits = grep_tree(root, rf"\b{re.escape(typed)}\b", sha, tree_globs)
        if not hits:
            out.append((f"- {typed} NOT FOUND at base (CREATE-class or "
                        "misnamed — verify)", False))
            continue
        # prefer the declaration hit as the canonical locator
        decls = [h for h in hits
                 if DECL_RE.search(h.split(":", 3)[-1] if ":" in h else "")]
        best = (decls or hits)[0]
        p, ln, c = _split_hit(best) or ("?", "?", hits[0])
        tag = " [declaration]" if best in decls else ""
        out.append((f"- {typed} FOUND — {p}:{ln} — {_trim(c, 100)}{tag}",
                    False))
        shown = 0
        for h in hits:
            if h != best and shown < CAP_S2_HITS:
                p2, ln2, _c2 = _split_hit(h) or ("?", "?", h)
                out.append((f"  - also {p2}:{ln2}", False))
                shown += 1
    return out


def s3_callsites(root: Path, symbols: list[str], sha: str,
                 tree_globs: tuple[str, ...]) -> list[tuple[str, bool]]:
    out: list[tuple[str, bool]] = []
    total = 0
    for tok in symbols:
        if total >= CAP_CALL_TOTAL:
            break
        name = tok.split(".")[-1]
        if not _methodish(name):
            continue
        hits = grep_tree(root, rf"\b{re.escape(name)}\b", sha, tree_globs)
        calls, delgs = [], []
        for h in hits:
            p, ln, c = _split_hit(h) or ("?", "?", h)
            if re.search(rf"\b(def|class|fn|func|public|private|internal|"
                         rf"protected)\b.{{0,80}}\b{re.escape(name)}\b", c):
                continue  # declaration / implementation
            if not re.search(rf"\b{re.escape(name)}\s*\(", c):
                continue  # not an invocation-form site
            if re.search(rf"\breturn\s+[A-Za-z0-9_.!?]*\.\s*"
                         rf"{re.escape(name)}\s*\(", c):
                delgs.append((p, ln, c))
            else:
                calls.append((p, ln, c))
        if not calls:
            continue
        shown = calls[:CAP_CALL_PER_SYM]
        total += len(shown)
        if len(calls) == 1:
            p, ln, c = shown[0]
            out.append((f"- {name} at {p}:{ln} as the ONE production call "
                        f"site — {_trim(c)}", False))
        else:
            locs = " ".join(f"{p}:{ln}" for p, ln, _ in shown)
            out.append((f"- {name} — {len(calls)} production call sites "
                        f"[{locs}] — {_trim(shown[0][2])}", False))
        for p, ln, _ in delgs[:2]:
            out.append((f"  (delegation, not counted: {p}:{ln})", False))
    return out


def s4_test_inventory(root: Path, texts: list[str], sha: str,
                      test_globs: tuple[str, ...]) -> list[tuple[str, bool]]:
    names: list[str] = []
    if texts:  # spec only for Test-hooks surfaces
        for line in texts[0].splitlines():
            if not HOOK_RE.match(line):
                continue
            names.extend(TESTS_TOKEN_RE.findall(line))
            for bt in BT_RE.findall(line):
                names.extend(TESTS_TOKEN_RE.findall(bt))
    for text in texts:  # backticked test-name tokens anywhere
        for bt in BT_RE.findall(text):
            names.extend(TESTS_TOKEN_RE.findall(bt))
    seen: set[str] = set()
    out: list[tuple[str, bool]] = []
    for n in names:
        cls = n.split(".")[0]
        if re.search(r"\.(py|md|json|toml|ya?ml|sh)$", cls):
            continue  # filename, not a test name
        if cls in seen or len(out) >= CAP_TESTS:
            continue
        seen.add(cls)
        hits = grep_tree(root, rf"\b{re.escape(cls)}\b", sha, test_globs)
        loc, content = "", ""
        for h in hits[:2]:
            p, ln, c = _split_hit(h) or ("", "", h)
            loc, content = f"{p}:{ln}", c
            break
        if loc:
            out.append((f"- `{n}` — EXISTS @ {loc} — {_trim(content)}", False))
        else:
            out.append((f"- `{n}` — MISSING (plan/CREATE-class test)", True))
    return out


def s5_paths(root: Path, paths: list[str], sha: str) -> list[tuple[str, bool]]:
    out: list[tuple[str, bool]] = []
    for p in paths[:CAP_PATHS]:
        norm = p.split(":")[0].lstrip("./").rstrip(".")
        try:
            git(root, "cat-file", "-e", f"{sha}:{norm}")
            out.append((f"- {norm} — EXISTS at base", False))
        except RuntimeError:
            out.append((f"- {norm} — ABSENT at base (CREATE-vs-EXTEND "
                        "determinant)", False))
    return out


def s6_markers(root: Path, texts: list[str], sha: str,
               tree_globs: tuple[str, ...]) -> list[tuple[str, bool]]:
    counter: Counter = Counter()
    for text in texts:
        counter.update(MARKER_RE.findall(text))
    out: list[tuple[str, bool]] = []
    for m, _freq in counter.most_common():
        if ID_MARKER_RE.match(m):
            continue
        hits = grep_tree(root, rf"\b{re.escape(m)}\b", sha, tree_globs)
        if not hits:
            continue
        for h in hits[:2]:
            p, ln, c = _split_hit(h) or ("?", "?", h)
            out.append((f"- {m} — {p}:{ln} — {_trim(c)}", False))
        if len(out) >= CAP_S6:
            break
    return out


# ------------------------------------------------------------------ build ---

def build(root: Path, story_id: str, sha: str, texts: list[str],
          tree_globs: tuple[str, ...] = (),
          vocab_globs: tuple[str, ...] = (),
          test_globs: tuple[str, ...] = (),
          skip_prefixes: tuple[str, ...] = ()) -> list[tuple[str, bool]]:
    """Assemble the ALREADY VERIFIED block. Returns (line, droppable)
    pairs — the budget trimmer drops droppable (negative/derived) lines
    from the end first."""
    joined = "\n".join(texts)
    clean = lambda tok: _clean_pascal(tok, skip_prefixes)  # noqa: E731
    bt_clean = _backticked(texts)
    snakes = _ranked(Counter(SNAKE_RE.findall(joined)), bt_clean, CAP_VOCAB)
    symbols = _ranked(Counter(PASCAL_RE.findall(joined)), bt_clean,
                      CAP_SYMBOLS, clean=clean)
    # call-site facts scan the FULL method-ish pool (bounded downstream by
    # CAP_CALL_TOTAL), not just the §2 top-N — the one production call site
    # for a cited seam is exactly the fact the planner needs.
    symbols_all = _ranked(Counter(PASCAL_RE.findall(joined)), bt_clean,
                          10**6, clean=clean)
    paths = _ranked(Counter(PATH_RE.findall(joined)), set(), CAP_PATHS)

    sections: list[tuple[str, list[tuple[str, bool]]]] = []
    if vocab_globs:
        s1, zero_hit = s1_vocab(root, snakes, sha, vocab_globs)
        s1neg: list[tuple[str, bool]] = []
        for t in snakes:
            if t in zero_hit:
                s1neg.append((f"- `{t}` — ABSENT in the declared vocab "
                              "globs at base (not a base vocabulary "
                              "term)", True))
        # negatives right after the positive facts inside the section
        sections.append(("## 1. Vocabulary facts (declared globs @ base)",
                         s1 + s1neg))
    sections += [
        ("## 2. Symbol resolve (@ base)", s2_symbols(root, symbols, sha,
                                                     tree_globs)),
        ("## 3. Call-site facts (production callers)",
         s3_callsites(root, symbols_all, sha, tree_globs)),
        ("## 4. Named-test inventory (spec hooks + backticked tests)",
         s4_test_inventory(root, texts, sha, test_globs or tree_globs)),
        ("## 5. File-path facts (cat-file @ base)",
         s5_paths(root, paths, sha)),
        ("## 6. Precedent extracts (grep-verified markers)",
         s6_markers(root, texts, sha, tree_globs)),
    ]

    out: list[tuple[str, bool]] = []
    out.append((f"# ALREADY VERIFIED — {story_id} @ {sha[:12]} (plan_prep)",
                False))
    out.append(("", False))
    out.append(("_Deterministic facts extracted from the tree at this SHA. "
                "Do NOT re-verify; cite these lines in the plan. Anything "
                "NOT in this block must be resolved from the tree, not "
                "from prose._", False))
    out.append(("", False))
    for title, body in sections:
        out.append((title, False))
        for line, drop in body:
            out.append((line, drop))
        out.append(("", False))

    # hard budget: drop droppable (negative/derived) lines from the end
    dropped = 0
    while len(out) > LINE_BUDGET:
        for i in range(len(out) - 1, -1, -1):
            if out[i][1]:
                out.pop(i)
                dropped += 1
                break
        else:
            out.pop()
            dropped += 1
    if dropped:
        out.append((f"_({dropped} negative/derived lines trimmed to the "
                    f"{LINE_BUDGET}-line budget — over-listing beats silent "
                    "omission within the cap)_", False))
    out.append((f"- candidate caps: {CAP_VOCAB} vocab / {CAP_SYMBOLS} "
                f"symbols / {CAP_PATHS} paths / {CAP_TESTS} tests; line "
                f"budget {LINE_BUDGET}.", False))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("story_id")
    ap.add_argument("--root", required=True, help="git repo / control home")
    ap.add_argument("--sha", default="HEAD")
    ap.add_argument("--out", default=None)
    ap.add_argument("--tree-glob", action="append", default=[],
                    help="tree-scoping glob for whole-tree scans (repeatable)")
    ap.add_argument("--vocab-glob", action="append", default=[],
                    help="vocabulary-facts glob (repeatable; section 1 is "
                         "omitted when none declared)")
    ap.add_argument("--test-glob", action="append", default=[],
                    help="test-tree glob for the named-test inventory "
                         "(repeatable; defaults to --tree-glob)")
    ap.add_argument("--skip-prefix", action="append", default=[],
                    help="qualified-symbol stop prefix (repeatable; a "
                         "project declaration, never hardcoded)")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    stories = root / "stories"
    if not ((stories / f"{args.story_id}.md").exists()
            or (stories / args.story_id / "story.md").exists()):
        print(f"error: no story spec at {stories / (args.story_id + '.md')} "
              f"or {stories / args.story_id / 'story.md'}", file=sys.stderr)
        return 2
    try:
        sha = git(root, "rev-parse", "--verify",
                  f"{args.sha}^{{commit}}").strip()
    except RuntimeError as e:
        print(f"error: unreadable sha `{args.sha}`: {e}", file=sys.stderr)
        return 2

    texts = _sources(stories, args.story_id)
    out = build(root, args.story_id, sha, texts,
                tuple(args.tree_glob), tuple(args.vocab_glob),
                tuple(args.test_glob), tuple(args.skip_prefix))

    dest = Path(args.out) if args.out else \
        stories / args.story_id / "plan-prep.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(t for t, _ in out) + "\n",
                    encoding="utf-8", newline="\n")

    neg = sum(1 for line, _ in out if ("ABSENT" in line
                                       or "NOT FOUND" in line
                                       or "MISSING" in line))
    print(f"plan_prep {args.story_id} @ {sha[:12]}: "
          f"{len(out)} lines, {neg} negative/ABSENT-bearing lines → {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
