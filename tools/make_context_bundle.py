#!/usr/bin/env python3
"""tools/make_context_bundle.py — deterministic context-bundle assembler.

ZERO LLM judgment. Concatenates ALL pinned inputs at a base SHA into ONE
file with per-source headers and line counts, so the context-acquisition
agent reads the bundle instead of taking a discovery tour through the
pinned inputs one at a time (architecture.md §Control plane: "Feeds
dispatch packages"; §Dispatch package: "Pinned inputs — every input
inlined or referenced at recorded SHA (bundle pattern)").

Completeness is by construction: the bundle lists exactly the sources the
orchestrator pinned, with their SHA-verified content (git plumbing — the
same object DB view the child will get). The output header carries the
story id, base SHA, per-source byte/line counts, and a sha256 of the
bundle body — pin that digest in the context-acquisition dispatch event.
Source content is inserted VERBATIM (no trimming): trailing whitespace
and blank lines survive into the bundle, matching the manifest's
untrimmed byte/line counts.

Genericization notes:
  * No repo/layout constants: the git repo is `--root`, the output path is
    `-o` (default `<root>/stories/<id>/context-bundle.md`), the base is
    `--sha`. No cwd assumptions.
  * No wall-clock content: the reference stamped a generation time into
    the header; the bundle must be byte-deterministic (same sha + sources
    → same bytes), so the stamp is dropped and the whole file is
    reproducible.

USAGE:
  python3 tools/make_context_bundle.py STORY-NNN --root <repo> \
      --sha <base-sha> docs/architecture.md stories/STORY-NNN/story.md \
      [-o <out.md>]

Exit codes (config_validator convention): 0 ok · 2 usage / missing input
(unreadable sha, a source not present at the pinned SHA). Stdlib only.
Python >= 3.11.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

try:
    from .ledger_schema import STORY_ID_RE
except ImportError:  # direct-run layout: the tools dir holds the schema
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ledger_schema import STORY_ID_RE


def git(root: Path, *args: str) -> str:
    """Run git in `root`; raise RuntimeError on nonzero exit (callers
    decide). All repo paths are parameterized — no repo constants."""
    r = subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def show_at(root: Path, sha: str, path: str) -> tuple[str, int]:
    """SHA-verified file content from the object DB at `sha`, as
    (decoded text, RAW byte length). stdout is captured as BYTES —
    `text=True` would apply universal-newline translation and silently
    LF-normalize a committed CRLF file, breaking the verbatim promise
    and the manifest byte count. The decode does no newline
    translation either."""
    r = subprocess.run(["git", "-C", str(root), "show", f"{sha}:{path}"],
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"git show {sha}:{path} failed: "
                           f"{r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout.decode("utf-8"), len(r.stdout)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("story_id")
    ap.add_argument("--root", required=True, help="git repo the sources are pinned in")
    ap.add_argument("--sha", default="HEAD", help="pinned base SHA (default HEAD)")
    ap.add_argument("sources", nargs="+", help="repo-relative paths to pin")
    ap.add_argument("-o", "--out", default=None,
                    help="output file (default <root>/stories/<id>/context-bundle.md)")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    try:
        full_sha = git(root, "rev-parse", "--verify",
                       f"{args.sha}^{{commit}}").strip()
    except RuntimeError as e:
        print(f"error: unreadable sha `{args.sha}`: {e}", file=sys.stderr)
        return 2

    parts = [f"# Context Bundle — {args.story_id}",
             f"_Base SHA: {full_sha} · assembled by make_context_bundle.py "
             "(deterministic)_",
             "",
             "The context-acquisition agent reads THIS FILE ONLY (plus "
             "targeted greps for anchors). Every pinned source is inlined "
             "verbatim below. Anything not inlined is out of scope: if a "
             "fact you need is missing, it goes in the digest's open "
             "questions.", ""]
    manifest: list[tuple[str, int, int]] = []
    missing: list[str] = []
    for src in args.sources:
        try:
            content, nbytes = show_at(root, full_sha, src)
        except RuntimeError:
            missing.append(src)
            continue
        lines = len(content.splitlines())
        manifest.append((src, nbytes, lines))
        parts += [f"\n\n<!-- ===== SOURCE {src} ({lines} lines, pinned at "
                  f"{full_sha[:12]}) ===== -->\n",
                  f"## Source: `{src}`", "", content, ""]
    if missing:
        print("error: sources not present at the pinned SHA: "
              + ", ".join(missing), file=sys.stderr)
        return 2

    body = "\n".join(parts)
    digest = hashlib.sha256(body.encode()).hexdigest()
    header = ["## Bundle manifest (what the digest agent may rely on)", "",
              "| source | bytes | lines |", "|---|---|---|"]
    header += [f"| `{s}` | {b:,} | {l} |" for s, b, l in manifest]
    header += ["", f"_bundle sha256: `{digest}`_"]
    # insert the manifest after the intro block (before the first SOURCE
    # marker) — the digest covers intro + sources, not the manifest itself
    idx = body.index("\n\n<!-- ===== SOURCE")
    text = body[:idx] + "\n\n" + "\n".join(header) + body[idx:]
    if not text.endswith("\n"):
        text += "\n"

    # Destination confinement: story_id is untrusted input that becomes a
    # path component. Canonical STORY-NNN ids (ledger_schema.STORY_ID_RE —
    # the controller's validate-story-id form) cannot contain separators;
    # anything else must resolve strictly under <root>/stories or the run
    # refuses BEFORE any mkdir/write — `../../outside` must never escape.
    # An explicit --out is the user's own override (not id-derived), so it
    # is taken as given.
    stories_root = (root / "stories").resolve()
    if args.out:
        dest = Path(args.out)
    elif STORY_ID_RE.fullmatch(args.story_id):
        dest = stories_root / args.story_id / "context-bundle.md"
    else:
        cand = (root / "stories" / args.story_id
                / "context-bundle.md").resolve()
        if not cand.is_relative_to(stories_root):
            print(f"error: refusing story id {args.story_id!r} — the default "
                  f"bundle destination ({cand}) escapes the stories "
                  f"directory ({stories_root}); canonical story ids are "
                  "STORY-NNN (zero-padded 3 digits)", file=sys.stderr)
            return 2
        dest = cand
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {dest}")
    print(f"bundle sha256: {digest}")
    print(f"sources: {len(manifest)} · total lines: "
          f"{len(text.splitlines())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
