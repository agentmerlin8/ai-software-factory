#!/usr/bin/env python3
"""plan_checks.py — plan-lane mechanical checks (anchor resolution,
seam verification, coverage set-diffs). Genericized port for the kit.

Check families (gate catalog: `plan-gate`):

  anchor-resolution — the plan cites `file:line[@sha]` evidence that must
                      resolve at the pinned base sha (doc-shaped-memory
                      defect class). Base-sha ladder: `--base-sha` flag >
                      `BASE_SHA` token / verification block of
                      context-digest.md > the impl-plan.md `Base SHA`
                      header > `--base-ref` (default `main`).
  seam-verification — contract.seams declarations verified via
                      dispatch_prep.seam_verify at the base sha
                      (declared-only: no seams block = vacuous).
  coverage-set-diffs — (d1) failure_model discriminating tests verbatim
                      in tests:; (d2) duplicate test names reject;
                      (d3) a dotted test name's class prefix must match a
                      declared file stem (any extension) or be declared
                      as a new file in the plan text; (d4) tests[].ac
                      must exist in the spec; (e1) per-AC Test-hooks
                      blocks whose AC has ZERO named tests reject
                      (rename-latitude gaps listed informationally).

Genericization notes (vs the reference implementation):
  * No repo constant: every function takes `root` (the gate's --root).
  * The reference's hard-FAIL root prefixes (src/, tests/, dashboard/,
    ...) were project layout vocabulary. Structural replacement: an
    anchor path is repo-rooted (hard-checked) iff it contains '/' —
    bare filenames stay informational (partial paths).
  * d3 matched `.cs` file stems; the port matches ANY declared file's
    stem, and the plan-text latitude is `"<Class>."` (any extension).
  * G12 verifies paths+lines, not symbols (honest limit, as in the
    reference): anchor symbols stay reviewer-visible via the notes.

Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable

_HERE = Path(__file__).resolve().parent            # tools/gates (repo) | factory/gates (installed)
_TOOLS = _HERE.parent if (_HERE.parent / "contract_lib.py").exists() else _HERE.parent / "tools"

try:  # factory/tools is on sys.path (pre_review_gate); guarded import:
    import dispatch_prep as DP  # noqa: E402
except ImportError:  # pragma: no cover - direct import of this module
    import sys
    sys.path.insert(0, str(_TOOLS))
    import dispatch_prep as DP  # noqa: E402

# ---------------------------------------------------------------- anchors ----

# sane extensions to recognize as repo FILE anchors (directories are not
# anchors — a directory grep verifies a search, not a file).
_EXT = (r"cs|json|md|ts|tsx|js|jsx|py|sql|xml|toml|yml|yaml|html|css|sh|txt"
        r"|rs|go|java|rb|c|h|cpp|hpp")
ANCHOR_EXT_RE = re.compile(rf"\.(?:{_EXT})$")
PATH_CHARS_RE = re.compile(r"^[A-Za-z0-9/._-]+$")
SHA_RE = r"[0-9a-f]{7,40}"


def looks_like_repo_path(tok: str) -> bool:
    """Conservative anchor-path guard: charset check, sane extension, and
    a '/' (repo-rooted). Bare filenames are NOT anchors (partial paths —
    the reference's root-prefix list was project layout vocabulary)."""
    tok = tok.strip().rstrip(".,;)")
    if not PATH_CHARS_RE.match(tok) or not ANCHOR_EXT_RE.search(tok):
        return False
    return "/" in tok


def extract_anchors(text: str) -> list[dict]:
    """Pull code anchors out of impl-plan.md text.

    Forms (conservative set):
      1. backticked `path.ext:NNN[ @sha]` / `path.ext@sha` / `path.ext @sha`
         (multi-line refs like :79,95-96 keep their first number)
      2. `git show <sha>:path`
      3. `git grep ... <sha> -- path`
      4. path:line table cells (cell is EXACTLY the anchor)
    Returns [{path, line, sha, form, plan_line}] — deduped."""
    anchors: list[dict] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    lines = text.splitlines()

    def add(path: str, line: str | None, sha: str | None, form: str,
            plan_line: int) -> None:
        key = (path, line, sha)
        if key in seen or not looks_like_repo_path(path):
            return
        seen.add(key)
        anchors.append({"path": path, "line": line, "sha": sha,
                        "form": form, "plan_line": plan_line})

    backtick = re.compile(
        rf"`((?:[A-Za-z0-9/._-]+\.(?:{_EXT}))"
        rf"(?::\d+(?:[-,]\d+)*)?(?:\s*@\s*{SHA_RE}|:{SHA_RE})?)`")
    git_show = re.compile(rf"git show\s+({SHA_RE}):([^\s`)\]]+)")
    git_grep = re.compile(
        rf"git grep(?:\s+--?[\w-]+)*\s+(?:\"[^\"]*\"|'[^']*'|\S+)\s+"
        rf"({SHA_RE})\s+--\s+([^\s`)\]]+)")
    # table-cell citations keep path/line/sha APART: group 1 is the path
    # ONLY — capturing the whole `path:line[@sha]` cell as the path made
    # looks_like_repo_path() reject it (contains ':') and the row yielded
    # ZERO anchors, bypassing validation entirely.
    table_cell = re.compile(
        rf"^([A-Za-z0-9/._-]+\.(?:{_EXT})):(\d+)"
        rf"(?:\s*@\s*({SHA_RE}))?$")

    for idx, raw in enumerate(lines, 1):
        for m in backtick.finditer(raw):
            inner = m.group(1)
            lnm = re.search(r":(\d+)", inner)
            sham = re.search(rf"(?:@\s*|:)({SHA_RE})\s*$", inner)
            p = re.split(r"[:@\s]", inner)[0]
            add(p, lnm.group(1) if lnm else None,
                sham.group(1) if sham else None, "backtick", idx)
        for m in git_show.finditer(raw):
            add(m.group(2), None, m.group(1), "git show", idx)
        for m in git_grep.finditer(raw):
            add(m.group(2), None, m.group(1), "git grep", idx)
        if "|" in raw:
            for cell in raw.split("|"):
                cell = cell.strip().strip("`")
                m = table_cell.match(cell)
                if m:
                    add(m.group(1), m.group(2), m.group(3), "table cell", idx)
    return anchors


def git_show_at(root: Path, sha: str, path: str) -> str | None:
    """File content at <sha>:<path>, or None when git disagrees anywhere."""
    r = subprocess.run(["git", "-C", str(root), "show", f"{sha}:{path}"],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def resolves_to_commit(root: Path, rev: str) -> bool:
    """True when `rev` names a readable commit object. The missing-file
    exemption must NEVER fire on an unresolvable revision: git_show_at()
    returns None for every Git failure alike, so `path@deadbee` would be
    accepted as a confirmed CREATE absence even when the commit does not
    exist. Type-qualified (^{commit}) so tags/blobs/trees refuse."""
    r = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "-q",
         f"{rev}^{{commit}}"],
        capture_output=True, text=True)
    return r.returncode == 0 and bool(r.stdout.strip())


def resolve_base_sha(story_dir: Path, base_sha_flag: str | None,
                     base_ref: str = "main") -> tuple[str, str]:
    """Base-sha ladder (rung used is reported in the notes):
    --base-sha flag > 'BASE_SHA' token / verification block of
    <story-dir>/context-digest.md > the impl-plan.md `Base SHA` header
    (plans without a digest re-verify at their own declared base instead
    of a drifted default) > --base-ref (default `main`)."""
    if base_sha_flag:
        return base_sha_flag, "--base-sha flag"
    digest = story_dir / "context-digest.md"
    if digest.exists():
        text = digest.read_text(encoding="utf-8")
        m = re.search(rf"\bBASE_SHA\b[^\S\n]*[:=]?[^\S\n]*({SHA_RE})\b", text)
        if m:
            return m.group(1), f"BASE_SHA in {digest.name}"
        m8 = re.search(r"## 8\. Verification block\n(.*)", text, re.S)
        section = m8.group(1) if m8 else text
        rm = re.search(rf"git rev-parse\s+({SHA_RE})\b", section)
        if rm:
            return rm.group(1), f"`git rev-parse` in {digest.name} §8"
        pm = re.search(rf"\b(?:pinned at|pinned|at)\s+({SHA_RE})\b", section)
        if pm:
            return pm.group(1), f"pinned sha in {digest.name} §8"
    plan = story_dir / "impl-plan.md"
    if plan.exists():
        hm = re.search(rf"(?<![A-Za-z0-9])Base\s+SHA\b[^\S\n]*[:=]?[^\S\n]*"
                       rf"({SHA_RE})\b", plan.read_text(encoding="utf-8"))
        if hm:
            return hm.group(1), "`Base SHA` in impl-plan.md header"
    return base_ref, f"default (--base-ref {base_ref})"


def check_plan_anchors(root: Path, story_dir: Path, sid: str, base_sha: str,
                       create_paths: set[str],
                       fail: Callable[[str], None],
                       info: Callable[[str], None]) -> dict:
    """Anchor resolution (plan): every repo-rooted code anchor in
    impl-plan.md resolves at its embedded sha (or the base sha): file
    exists and the cited line is within EOF (and >= 1 — a line number
    below one cannot identify a source line). Line-less `path@sha` pins
    carry one extra semantics: a pin whose file is ABSENT at the sha is a
    legitimate absence claim when the contract declares the path CREATE
    ("git cat-file -e fails → CREATE op correct") AND the revision itself
    resolves to a readable commit (`git rev-parse --verify <rev>^{commit}`
    — an unresolvable revision is a named failure, never an exemption);
    absent + NOT declared CREATE = reject. Informational: non-base shas
    (count), partial paths, zero anchors."""
    plan = story_dir / "impl-plan.md"
    if not plan.exists():
        fail("impl-plan.md missing — cannot verify anchor resolution")
        return {}
    anchors = extract_anchors(plan.read_text(encoding="utf-8"))
    stats: dict = {"total": len(anchors), "non_base_sha": 0, "failed": 0,
                   "partial_skipped": [], "absence_ok": 0}
    if not anchors:
        info("anchors: no code anchors found in impl-plan.md "
             "(informational — anchor resolution is only as strong as the "
             "plan's citations)")
        return stats
    for a in anchors:
        sha = a["sha"] or base_sha
        is_pin = not a["line"]
        label = f"`{a['path']}{':' + a['line'] if a['line'] else ''}@{sha}` " \
                f"(impl-plan.md line {a['plan_line']}, {a['form']})"
        if a["sha"] and a["sha"] != base_sha:
            stats["non_base_sha"] += 1
        content = git_show_at(root, sha, a["path"])
        if content is None:
            if is_pin and a["path"] in create_paths:
                # legitimate absence claim: new-file pin against a CREATE —
                # but ONLY when the revision itself resolves to a readable
                # commit; git_show_at() returns None for every Git failure
                # alike, so an unresolvable revision must never be laundered
                # into a confirmed absence (resolve BEFORE exempting).
                if not resolves_to_commit(root, sha):
                    stats["failed"] += 1
                    fail(f"anchor {label} — revision `{sha}` does not "
                         "resolve to a readable commit (git rev-parse "
                         f"--verify {sha}^{{commit}} failed) — the "
                         "missing-file CREATE exemption cannot confirm an "
                         "absence at a revision that does not exist")
                    continue
                stats["absence_ok"] += 1
                info(f"anchors: anchor {label} — file absent at {sha} and "
                     "declared CREATE in files: (absence claim consistent)")
                continue
            stats["failed"] += 1
            fail(f"anchor {label} — file `{a['path']}` missing at {sha} "
                 "(plan cites evidence that does not resolve — doc-shaped "
                 "memory class)")
            continue
        if a["line"]:
            n = int(a["line"])
            if n < 1:
                # a line number below one cannot identify a source line
                # (`:0` on an empty file passed the upper-bound-only check)
                stats["failed"] += 1
                fail(f"anchor {label} — line {a['line']} is below 1 — not "
                     f"a valid source line of `{a['path']}`")
                continue
            n_lines = len(content.splitlines())
            if n > n_lines:
                stats["failed"] += 1
                fail(f"anchor {label} — line {a['line']} beyond EOF of "
                     f"`{a['path']}` ({n_lines} lines at {sha})")
    if stats["non_base_sha"]:
        info(f"anchors: {stats['non_base_sha']} anchor(s) pinned to a sha "
             f"other than the base ({base_sha}) — counted, not failed")
    return stats


# ----------------------------------------------------------------- seams ----

SEAM_VERIFY = getattr(DP, "seam_verify", None)


def check_seams(root: Path, contract: dict, sha: str,
                fail: Callable[[str], None],
                info: Callable[[str], None]) -> str:
    """Seam verification (plan): verify contract.seams via
    dispatch_prep.seam_verify at the base sha (declared-only — no seams
    block = vacuous). Any status==FAIL rejects the gate with the returned
    detail + evidence; WARN and integration-pending are informational."""
    seams = contract.get("seams") or []
    if not seams:
        return "vacuous"
    fn = SEAM_VERIFY
    if fn is None:
        info("seams: seam_verify not available in dispatch_prep "
             "(integration pending) — seams block present but not "
             f"machine-verified ({len(seams)} entries)")
        return "pending"
    results = fn(root, contract, sha)
    if not isinstance(results, list):
        fail(f"seam_verify returned a non-list result: {results!r} "
             "(integration contract break)")
        return "failed"
    ok = warn = bad = 0
    for r in results:
        if not isinstance(r, dict) or "status" not in r:
            fail(f"seam_verify returned a malformed entry: {r!r}")
            continue
        status, sym = r.get("status"), r.get("symbol", "?")
        ev = r.get("evidence") or []
        evs = "; ".join(str(e) for e in ev[:3])
        tail = f" [{sym}]" + (f" — {evs}" if evs else "")
        if status == "FAIL":
            bad += 1
            fail(f"{r.get('detail', 'seam verification failed')}{tail}")
        elif status == "WARN":
            warn += 1
            info(f"seams (warn): {r.get('detail', '')}{tail}")
        elif status == "OK":
            ok += 1
    info(f"seams: seam_verify @ {sha}: {ok} OK / {warn} WARN / {bad} FAIL "
         f"across {len(seams)} seam declarations")
    return "failed" if bad else "ok"


# -------------------------------------------------------------- coverage ----

AC_TOKEN_RE = re.compile(r"\bAC-(\d+)\b")
_SURFACE_SUFFIX_RE = re.compile(
    r"(?:Tests?|Harness|Fixture|Service|Repository|Validator|Job|Client"
    r"|Endpoint|Controller|Extensions?)$")
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"


def _hook_blocks(spec_text: str) -> list[tuple[str, str]]:
    """Per-AC `**Test hooks:**` paragraphs: (ac_id, paragraph text)."""
    blocks: list[tuple[str, str]] = []
    lines = spec_text.splitlines()
    cur_ac: str | None = None
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = re.sub(r"^[-*]\s+", "", raw.strip())
        m = re.match(r"^#{1,4}\s*AC[- ]+(\d+)", stripped) or \
            re.match(r"^#{0,3}\s*(?:Acceptance [Cc]riteria|AC)\s+(\d+)",
                     stripped)
        if m:
            cur_ac = m.group(1)
        if stripped.startswith("**Test hooks"):
            buf = [stripped]
            j = i + 1
            while j < len(lines):
                raw_next = lines[j].strip()
                if not raw_next or raw_next.startswith(
                        ("#", "**", "- ", "* ")):
                    break  # house style: hooks live on one bullet line
                buf.append(raw_next)
                j += 1
            blocks.append((cur_ac or "?", " ".join(buf)))
            i = j
            continue
        i += 1
    return blocks


def _hook_surfaces(paragraph: str) -> tuple[list[str], list[str]]:
    """Backticked tokens that are test-surface identifiers. Returns
    (surfaces, stray) — stray tokens are informational only, never fail."""
    surfaces: list[str] = []
    stray: list[str] = []
    for tok in re.findall(r"`([^`\n]+)`", paragraph):
        t = tok.strip()
        if not re.fullmatch(rf"{_IDENT}(?:\.{_IDENT})?", t):
            continue  # paths, config keys, '…'-elisions — never surfaces
        if "." in t:
            cls, meth = t.split(".", 1)
            if _SURFACE_SUFFIX_RE.search(cls) or "Tests" in cls or \
                    _SURFACE_SUFFIX_RE.search(meth):
                surfaces.append(t)
            else:
                stray.append(t)
        elif _SURFACE_SUFFIX_RE.search(t) or "Tests" in t:
            surfaces.append(t)
        else:
            stray.append(t)
    return surfaces, stray


def _named_stuff(contract: dict) -> tuple[list[str], list[str], set[str]]:
    """(test names, file paths, declared file stems) from the contract.
    Stems are extension-agnostic (the reference matched `.cs` stems)."""
    tests = [t for t in contract.get("tests") or []
             if isinstance(t, dict)]
    names = [str(t["name"]) for t in tests if t.get("name")]
    paths = [str(f["path"]) for f in contract.get("files") or []
             if isinstance(f, dict) and f.get("path")]
    stems = {Path(p).stem for p in paths}
    return names, paths, stems


def check_coverage_sets(contract: dict, spec_text: str, plan_text: str,
                        fail: Callable[[str], None]) -> dict:
    """Coverage set-diffs (plan):
      (d1) every failure_model[].discriminating_test appears verbatim in
           tests[].name
      (d2) duplicate tests[].name = reject
      (d3) a dotted tests[].name's class prefix must be a declared file
           stem in files: (or clearly declared as a new file in the plan
           text — `"<Class>."` appears); bare names are skipped
      (d4) every tests[].ac is an AC id present in the story spec text."""
    names, paths, stems = _named_stuff(contract)
    stats = {"d1": 0, "d2": 0, "d3": 0, "d4": 0}
    for e in contract.get("failure_model") or []:
        if not isinstance(e, dict):
            continue
        dt = e.get("discriminating_test")
        if dt and dt not in names:
            stats["d1"] += 1
            fail(f"(d1) failure_model.discriminating_test `{dt}` does NOT "
                 f"appear verbatim in tests[].name — risk "
                 f"`{str(e.get('risk', '?'))[:60]}` has no pinned test")
    seen: dict[str, int] = {}
    for n in names:
        seen[n] = seen.get(n, 0) + 1
        if seen[n] == 2:
            stats["d2"] += 1
            fail(f"(d2) duplicate tests[].name `{n}` — every entry must "
                 "name a distinct test")
    for t in (x for x in contract.get("tests") or []
              if isinstance(x, dict)):
        n = str(t.get("name") or "")
        if "." in n:
            cls = n.split(".", 1)[0]
            if cls not in stems and f"{cls}." not in plan_text:
                stats["d3"] += 1
                fail(f"(d3) tests[].name `{n}` names class `{cls}` which "
                     "has no declared file with that stem in files: and is "
                     "not declared as a new file anywhere in the plan")
    spec_acs = set(AC_TOKEN_RE.findall(spec_text))
    for t in (x for x in contract.get("tests") or []
              if isinstance(x, dict)):
        ac = str(t.get("ac") or "").replace("AC-", "").strip()
        if ac and ac.isdigit() and ac not in spec_acs:
            stats["d4"] += 1
            fail(f"(d4) tests[].name `{str(t.get('name', '?'))}` declares "
                 f"ac AC-{ac} which is not an AC in the story spec")
    return stats


def check_hook_coverage(contract: dict, spec_text: str, plan_text: str,
                        fail: Callable[[str], None],
                        info: Callable[[str], None]) -> dict:
    """Spec Test-hooks coverage (e1):
      - parse per-AC `**Test hooks:**` paragraphs;
      - identifier surfaces are covered by tests[].name / files[].path
        (substring), by hook_exceptions, or waived when the surface's
        class is the prefix of a named test (rename latitude);
      - an AC whose Test-hooks block declares surfaces but which has ZERO
        named contract tests = FAIL (explicit frozen AC elements shipped
        with zero named tests);
      - uncovered surfaces + non-identifier prose tokens = informational
        (name-freezing latitude: strict verbatim matching would reject
        plans that legitimately rename hooks into named tests)."""
    blocks = _hook_blocks(spec_text)
    stats = {"blocks": len(blocks), "surfaces": 0, "uncovered": 0,
             "zero_test_acs": 0}
    if not blocks:
        return stats
    tests = [t for t in contract.get("tests") or []
             if isinstance(t, dict)]
    names, paths, _ = _named_stuff(contract)
    exc = {str(e.get("hook")) for e in contract.get("hook_exceptions") or []
           if isinstance(e, dict) and e.get("hook")}
    for ac, par in blocks:
        surfaces, strays = _hook_surfaces(par)
        stats["surfaces"] += len(surfaces)
        for s in strays:
            info(f"coverage (info): AC-{ac} Test-hooks token `{s}` is not "
                 "a test-surface identifier — informational only")
        uncovered: list[str] = []
        for s in surfaces:
            if s in exc:
                continue
            if any(s in n for n in names) or any(s in p for p in paths):
                continue
            cls = s.split(".", 1)[0]
            if any(n.startswith(cls + ".") for n in names):
                continue  # same class, renamed method — rename latitude
            uncovered.append(s)
        stats["uncovered"] += len(uncovered)
        ac_tests = [t for t in tests
                    if str(t.get("ac") or "").replace("AC-", "").strip()
                    == ac]
        if surfaces and not ac_tests:
            stats["zero_test_acs"] += 1
            fail(f"(e1) AC-{ac} declares Test hooks surfaces {surfaces} "
                 "but has ZERO named contract tests — explicit frozen AC "
                 "elements shipped with zero named tests")
        for s in uncovered:
            info(f"coverage (info): AC-{ac} Test-hooks surface `{s}` has "
                 "no verbatim test/file in the contract — rename latitude "
                 "(name-freezing); a reviewer must confirm the hook's "
                 "intent landed as a named test")
    return stats
