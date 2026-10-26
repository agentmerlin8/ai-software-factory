#!/usr/bin/env python3
"""tools/check_genericization.py — the genericization tripwire (S2-F3).

The kit's genericization law: no reference-project product vocabulary in
the tree. Until now that law was a review-time manual grep; this tool
automates it as a CI gate (see .github/workflows/kit-ci.yml).

Usage:
    python3 tools/check_genericization.py [--root DIR]

`--root` defaults to the repository root (this file's parent's parent).
Scans GIT-TRACKED files (`git -C <root> ls-files -z`, argv-list
subprocess) when root is a git repo; otherwise walks the tree, skipping
`.git/` and `__pycache__/`. Text files only: any file that is not valid
UTF-8 (or is unreadable) is skipped, never crashed on.

Token list (matched case-insensitively, substring — same semantics as
the manual `grep -rniE` sweep it replaces):
    podcast, media[-_. ]?ingestion, dapper, costentry,
    analysis[-_. ]?live, mtp, campaign, stratum, whisper, episode,
    randy, agentmerlin (with the carve-out below).

Documented carve-outs:
1. `agentmerlin` — occurrences on a line that ALSO contains
   `ai-software-factory` are the kit's own public identity (clone URLs
   like github.com/agentmerlin8-ops/ai-software-factory pass); every
   other agentmerlin hit fails.
2. `transcripts` is deliberately NOT in the token list (S2-F1): it is
   the kit contract's own `state.transcripts` schema key — present in
   the architecture at the merge-base and NOT a reference-runtime leak.
   Scanning for it would flag the kit's own contract vocabulary.
3. SELF_EXCLUDED_PATHS — this tool and its suite test necessarily
   CONTAIN the token list (that is the tripwire's job); they are
   excluded from their own scan so the self-reference never trips it.
4. IDENTITY_ALLOWLIST — exact (path, token) pairs for inherited kit
   files whose hits are the kit's own public/legal identity or generic
   ecosystem vocabulary, not product leaks:
     - LICENSE / randy: the author's copyright line (public identity).
     - ado/design-spec.md, ado/setup.py / agentmerlin: the kit's own
       Azure DevOps org URL (dev.azure.com/agentmerlin) — same class as
       carve-out 1 (the kit's public handle, not reference vocabulary).
     - docs/github-native-runtime.md / mtp: "MTP" here is Microsoft
       Test Platform (generic .NET ecosystem term in a runtime
       lessons-learned doc), not a product name.
   The allowlist is pair-scoped: any OTHER token in these files, or
   these tokens in any other file, still fails.

Output: one line per hit — `HIT <path>:<line>: <token> — <trimmed
line>` — then the summary `genericization: N hit(s) in M
file(s)`. Exit codes: 0 clean, 1 hits found, 2 usage (bad --root).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

SELF_EXCLUDED_PATHS = frozenset({
    "tools/check_genericization.py",
    "tools/tests/test_genericization_check.py",
})

# (relative path, token) pairs that are the kit's own public/legal
# identity or generic ecosystem vocabulary — see docstring carve-out 4.
IDENTITY_ALLOWLIST = frozenset({
    ("LICENSE", "randy"),
    ("ado/design-spec.md", "agentmerlin"),
    ("ado/setup.py", "agentmerlin"),
    ("docs/github-native-runtime.md", "mtp"),
})

# Token name -> matcher. `transcripts` is deliberately absent (S2-F1).
TOKENS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("podcast", re.compile(r"podcast", re.IGNORECASE)),
    ("media-ingestion", re.compile(r"media[-_. ]?ingestion", re.IGNORECASE)),
    ("dapper", re.compile(r"dapper", re.IGNORECASE)),
    ("costentry", re.compile(r"costentry", re.IGNORECASE)),
    ("analysis-live", re.compile(r"analysis[-_. ]?live", re.IGNORECASE)),
    ("mtp", re.compile(r"mtp", re.IGNORECASE)),
    ("campaign", re.compile(r"campaign", re.IGNORECASE)),
    ("stratum", re.compile(r"stratum", re.IGNORECASE)),
    ("whisper", re.compile(r"whisper", re.IGNORECASE)),
    ("episode", re.compile(r"episode", re.IGNORECASE)),
    ("randy", re.compile(r"randy", re.IGNORECASE)),
    ("agentmerlin", re.compile(r"agentmerlin", re.IGNORECASE)),
)

SKIP_DIRS = frozenset({".git", "__pycache__"})


def is_git_repo(root: Path) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def tracked_files(root: Path) -> list[str]:
    """Git-tracked relative paths (argv-list subprocess, NUL-split)."""
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                         capture_output=True, text=True,
                         check=True).stdout
    return [p for p in out.split("\0") if p]


def walked_files(root: Path) -> list[str]:
    """Non-git fallback: tree walk skipping .git/ and __pycache__/."""
    found: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        found.append(rel.as_posix())
    return found


def line_hits(rel: str, text: str) -> list[tuple[int, str]]:
    """[(line number, token name)] for every non-carved-out hit."""
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, rx in TOKENS:
            if not rx.search(line):
                continue
            # carve-out 1: the kit's own public identity on
            # ai-software-factory lines
            if name == "agentmerlin" and \
                    "ai-software-factory" in line.lower():
                continue
            # carve-out 4: pair-scoped identity allowlist
            if (rel, name) in IDENTITY_ALLOWLIST:
                continue
            hits.append((lineno, name))
    return hits


def scan(root: Path) -> tuple[list[str], int]:
    """Returns (HIT lines, number of files with at least one hit)."""
    files = tracked_files(root) if is_git_repo(root) else walked_files(root)
    out: list[str] = []
    files_with_hits = 0
    for rel in files:
        if rel in SELF_EXCLUDED_PATHS:
            continue  # carve-out 3: self-reference
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary/unreadable: skipped, never fatal
        hits = line_hits(rel, text)
        if hits:
            files_with_hits += 1
        for lineno, name in hits:
            trimmed = text.splitlines()[lineno - 1].strip()
            out.append(f"HIT {rel}:{lineno}: {name} — {trimmed}")
    return out, files_with_hits


def main(argv: list[str] | None = None) -> int:
    default_root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(
        prog="check_genericization",
        description="Genericization tripwire: fail on reference-project "
                    "product vocabulary in tracked files (see module "
                    "docstring for tokens + carve-outs).")
    ap.add_argument("--root", default=str(default_root),
                    help="repository/tree root to scan (default: the kit "
                         "repo root)")
    args = ap.parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print(f"usage: --root {args.root} is not a directory",
              file=sys.stderr)
        return 2
    hits, files_with_hits = scan(root.resolve())
    for line in hits:
        print(line)
    print(f"genericization: {len(hits)} hit(s) in "
          f"{files_with_hits} file(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
