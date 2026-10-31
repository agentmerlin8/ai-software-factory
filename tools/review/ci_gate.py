#!/usr/bin/env python3
"""tools/review/ci_gate.py — the review-record required CI check (S3.5b T3).

Deterministic gate for the ``review-record`` required check: given a PR
number and its head sha, find the PR's review record(s) under the records
roots, fail closed on missing / stale / mismatched records, and verify the
record's internal panel against the declared panel (review-panel.toml).

The record AUTHORITY and the head rule are never re-implemented here: this
script delegates to tools/review/record_gate.py (which itself runs
record.py check as a subprocess) and only adds the CI-level rules:

* discovery: every ``<records-root>/**/review.json`` whose ``pr`` matches;
* head selection: record_gate.head_rule_errors(..., allow_v1_ancestor=False)
  — exact head, or a later head whose every changed path is under the
  record directory (the v2 record-tail rule);
* schema: a qualifying record must be ``review.record.v2`` — a v1 record
  at the exact head is refused for a new PR;
* panel declaration: the record's internal (role, model) reviewer slots
  must EQUAL lenses x models from the panel file, with no duplicates.  A
  missing or malformed panel file is a refusal, never a skip.

Relative ``--records-root`` and ``--panel`` paths resolve against
``--repo``.

CLI::

    ci_gate.py --repo PATH --pr N --head SHA
        [--records-root PATH]... [--panel PATH]

Exit codes: 0 pass, 1 named refusal, 2 usage.  Every refusal starts with
``review record gate: ``; nothing prints a bare traceback.  Stdlib only.
Python >= 3.11 (tomllib).
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path
from typing import NoReturn

# Tools modules must also run when invoked directly as a file path, not
# only as a module (S3.5a T1 lesson): repository root on sys.path first.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.review import record_gate

PREFIX = "review record gate: "
DEFAULT_RECORDS_ROOT = "kit-v0.1/reviews"
DEFAULT_PANEL = "kit-v0.1/review-panel.toml"
SHA_RE = record_gate.SHA_RE


def refuse(message: str) -> NoReturn:
    """Print one named refusal on stderr and exit 1."""
    line = message if message.startswith(PREFIX) else PREFIX + message
    print(line, file=sys.stderr)
    raise SystemExit(1)


def _rel_to_repo(path: Path, repo: Path) -> str:
    """Repo-relative POSIX path when possible, else the path itself."""
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path)


def discover(repo: Path, roots: list[Path]) -> list[tuple[Path, dict]]:
    """Every review.json under the roots whose ``pr`` field loads; returns
    [(record_dir, record)].  Unreadable/unparseable files are skipped at
    discovery (a corrupted PR record still fails closed: it cannot match
    the PR, so the gate refuses with `no review record for PR N`)."""
    out: list[tuple[Path, dict]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob(record_gate.REVIEW_JSON)):
            try:
                record = record_gate.load_record(str(path.parent))
            except ValueError:
                continue
            out.append((path.parent, record))
    return out


def load_panel(panel_path: Path) -> tuple[list[str], list[str]]:
    """Parse the declared panel TOML: (lenses, models), both non-empty
    lists of strings.  Any problem is a named refusal, never a skip."""
    try:
        with panel_path.open("rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        refuse(f"panel file unreadable: {panel_path} ({exc})")
    except tomllib.TOMLDecodeError as exc:
        refuse(f"panel file malformed: {panel_path} ({exc})")
    if not isinstance(data, dict):
        refuse(f"panel file malformed: {panel_path} (top level must be a "
               "table)")
    lenses = data.get("lenses")
    models = data.get("models")
    checked: list[list[str]] = []
    for name, value in (("lenses", lenses), ("models", models)):
        if (not isinstance(value, list) or not value
                or not all(isinstance(v, str) and v for v in value)):
            refuse(f"panel file malformed: {panel_path} ({name!r} must be "
                   f"a non-empty list of strings, got {value!r})")
        else:
            checked.append(list(value))
    return checked[0], checked[1]


def panel_slot_errors(record_dir: Path, record: dict,
                      lenses: list[str], models: list[str]) -> list[str]:
    """Internal (role, model) slots must EQUAL lenses x models, no
    duplicates.  Returns named error strings ([] = match)."""
    slots: list[tuple[str, str]] = []
    for reviewer in record.get("reviewers", []):
        if not isinstance(reviewer, dict):
            continue
        if reviewer.get("role") == "external":
            continue  # ingested external documents are outside the panel
        slots.append((str(reviewer.get("role")), str(reviewer.get("model"))))
    seen = set(slots)
    if len(slots) != len(seen):
        dupes = sorted(s for s in seen if slots.count(s) > 1)
        return [f"panel mismatch for {record_dir}: duplicate reviewer "
                "slots: " + ", ".join(f"({r}, {m})" for r, m in dupes)]
    declared = {(l, m) for l in lenses for m in models}
    missing = sorted(declared - seen)
    unexpected = sorted(seen - declared)
    if missing or unexpected:
        parts = []
        if missing:
            parts.append("missing: " + ", ".join(f"({r}, {m})"
                                                 for r, m in missing))
        if unexpected:
            parts.append("unexpected: " + ", ".join(f"({r}, {m})"
                                                    for r, m in unexpected))
        return [f"panel mismatch for {record_dir}: " + "; ".join(parts)]
    return []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ci_gate.py",
        description="review-record required CI check (S3.5b T3)")
    parser.add_argument("--repo", required=True,
                        help="repository path (PR head checkout)")
    parser.add_argument("--pr", required=True, type=int,
                        help="pull request number")
    parser.add_argument("--head", required=True,
                        help="PR head sha (7-40 lowercase hex)")
    parser.add_argument("--records-root", action="append", default=None,
                        dest="records_roots",
                        help=f"records root to scan, repeatable "
                             f"(default: {DEFAULT_RECORDS_ROOT})")
    parser.add_argument("--panel", default=DEFAULT_PANEL,
                        help=f"declared panel TOML "
                             f"(default: {DEFAULT_PANEL})")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not SHA_RE.fullmatch(args.head or ""):
        parser.error(f"--head must be 7-40 lowercase hex, got {args.head!r}")

    repo = Path(args.repo)
    if not repo.is_dir():
        parser.error(f"--repo is not a directory: {args.repo}")
    head = args.head
    pr = args.pr

    roots = [Path(r) if Path(r).is_absolute() else repo / r
             for r in (args.records_roots or [DEFAULT_RECORDS_ROOT])]
    panel_path = (Path(args.panel) if Path(args.panel).is_absolute()
                  else repo / args.panel)

    # 1. discovery: every record under the roots that names this PR
    records = [(d, rec) for d, rec in discover(repo, roots)
               if rec.get("pr") == pr]
    if not records:
        refuse(f"no review record for PR {pr} under "
               + ", ".join(str(r) for r in roots))

    # 2. head selection: exact head, or v2 record-tail only (never the v1
    #    ancestor rule in CI)
    qualifying: list[tuple[Path, dict]] = []
    for record_dir, record in records:
        rel = _rel_to_repo(record_dir, repo)
        errs = record_gate.head_rule_errors(record, str(repo), rel, head,
                                            allow_v1_ancestor=False)
        if not errs:
            qualifying.append((record_dir, record))
    if not qualifying:
        print(f"{PREFIX}no review record at head {head}", file=sys.stderr)
        for record_dir, record in records:
            path = record_dir / record_gate.REVIEW_JSON
            print(f"{PREFIX}  {path} (head {record.get('head_sha')})",
                  file=sys.stderr)
        raise SystemExit(1)

    # 3. per-record: schema rule, then the full gate evaluation (the
    #    authority + head rule + invariants, delegated to record_gate)
    for record_dir, record in qualifying:
        schema = record.get("schema")
        if schema != record_gate.SCHEMA_V2:
            refuse(f"legacy record schema {schema} is not accepted for a "
                   f"new PR ({record_dir / record_gate.REVIEW_JSON})")
        errs = record_gate.evaluate(_rel_to_repo(record_dir, repo),
                                    str(repo), head,
                                    allow_v1_ancestor=False)
        if errs:
            for err in errs:
                line = err if err.startswith(PREFIX) else PREFIX + err
                print(line, file=sys.stderr)
            raise SystemExit(1)

    # 4. declared panel: internal slots == lenses x models, no duplicates
    lenses, models = load_panel(panel_path)
    for record_dir, record in qualifying:
        errs = panel_slot_errors(record_dir, record, lenses, models)
        for err in errs:
            print(PREFIX + err, file=sys.stderr)
        if errs:
            raise SystemExit(1)

    print(f"{PREFIX}OK (PR {pr}, {len(qualifying)} record(s), "
          f"head {head})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # never a bare traceback
        print(f"{PREFIX}internal error: {exc}", file=sys.stderr)
        raise SystemExit(1)
