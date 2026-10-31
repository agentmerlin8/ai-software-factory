"""tools/review/record_gate.py — shared review-record gate evidence (S3.5b T2).

The review record (tools/review/record.py output) is the gate evidence for
the pipeline states REVIEWING / READY_FOR_REVIEW / MERGE_ELIGIBLE.  This
module is the single shared reader used by the controller (and, later, the
CI gate): it never re-implements the record AUTHORITY — ``check`` runs
tools/review/record.py as a subprocess — it only adds the state-machine
rules on top:

* record identity: sha256 of ``review.json`` (record_sha256)
* head rule: exact equality always passes; ``review.record.v2`` allows a
  later PR head ONLY when every changed path is under the record directory
  (the record-tail rule); ``review.record.v1`` may opt into the
  ancestor rule (merge-base) via ``allow_v1_ancestor``.
* open blockers/majors: findings of severity blocker|major without a
  matching disposition entry can never pass a gate.
* invariants re-asserted: ``undispositioned == []`` and the verdict
  decision derived from blocker/major counts.

All functions return error-string lists and never raise on a bad record —
the caller decides how to name the refusal.  Stdlib only. Python >= 3.11.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):  # direct-run layout (kit + installed)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

RECORD_PY = Path(__file__).resolve().parent / "record.py"
REVIEW_JSON = "review.json"
SCHEMA_V2 = "review.record.v2"
SCHEMA_V1 = "review.record.v1"
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def record_sha256(record_dir: str) -> str:
    """sha256 of the record's review.json bytes (lowercase hex)."""
    return hashlib.sha256(
        (Path(record_dir) / REVIEW_JSON).read_bytes()).hexdigest()


def load_record(record_dir: str) -> dict:
    """Parse review.json; ValueError with a named message on failure."""
    path = Path(record_dir) / REVIEW_JSON
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"review record gate: record unreadable: {path} "
                         f"({exc})") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"review record gate: record unparseable: {path} "
                         f"({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError(f"review record gate: record must be a mapping: "
                         f"{path}")
    return data


def check_record(record_dir: str, repo: str) -> list[str]:
    """Run the AUTHORITY as a subprocess: record.py check --record --repo.

    [] on exit 0; otherwise one named error string extracted from the last
    ``review record: `` line (fallback: the raw output).  The authority is
    called, never re-implemented.  A relative `record_dir` resolves against
    `repo`.
    """
    rec = Path(record_dir)
    if not rec.is_absolute():
        rec = Path(repo) / rec
    cmd = [sys.executable, str(RECORD_PY), "check",
           "--record", str(rec.resolve()),
           "--repo", str(Path(repo).resolve())]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=repo)
    except OSError as exc:
        return [f"review record gate: authority failed to run: {exc}"]
    if proc.returncode == 0:
        return []
    out = (proc.stdout + proc.stderr).strip()
    named = ""
    for line in out.splitlines():
        line = line.strip()
        if "review record: " in line:
            named = line[line.index("review record: "):]
    return [named or (out or f"record check exited {proc.returncode}")]


def open_blocker_majors(record: dict) -> list[str]:
    """Ids of blocker|major findings with no matching disposition entry
    (match by disposition.id == finding.id)."""
    dispositioned = {d.get("id") for d in record.get("dispositions", [])
                     if isinstance(d, dict)}
    out = []
    for f in record.get("findings", []):
        if not isinstance(f, dict):
            continue
        if f.get("severity") in ("blocker", "major") and \
                f.get("id") not in dispositioned:
            out.append(str(f.get("id")))
    return out


def head_rule_errors(record: dict, repo: str, record_dir: str,
                     pr_head: str, allow_v1_ancestor: bool) -> list[str]:
    """The head rule: is `pr_head` covered by this record's pinned head?

    Exact equality always passes.  review.record.v2 additionally allows a
    LATER head when every changed path between record head and pr_head is
    under the record directory (the record-tail rule).  review.record.v1
    allows the ancestor rule (merge-base --is-ancestor) only when
    allow_v1_ancestor is true; otherwise exact only.
    """
    record_head = str(record.get("head_sha") or "")
    if not record_head or not SHA_RE.fullmatch(pr_head or ""):
        return [f"head rule: unusable heads: record {record_head!r}, "
                f"pr_head {pr_head!r} (7-40 hex required)"]
    # prefix-tolerant exactness: resolve both to 40-hex for comparison
    if pr_head.startswith(record_head) or record_head.startswith(pr_head):
        return []
    schema = record.get("schema")
    if schema == SCHEMA_V2:
        proc = _git(repo, "diff", "--name-only",
                    f"{record_head}..{pr_head}")
        if proc.returncode != 0:
            err = proc.stderr.strip().decode("utf-8", "replace")
            return [f"head rule: cannot diff {record_head}..{pr_head}: "
                    f"{err or f'exit {proc.returncode}'}"]
        paths = [p for p in proc.stdout.decode("utf-8", "replace")
                 .splitlines() if p.strip()]
        prefix = record_dir.strip("/").replace(os.sep, "/") + "/"
        outside = [p for p in paths
                   if not p.replace(os.sep, "/").startswith(prefix)]
        if paths and not outside:
            return []
        if not paths:
            return [f"head rule: pr head {pr_head} is later than the record "
                    f"head {record_head} but the diff between them is empty "
                    "(the record-tail rule requires record-only changes)"]
        return [f"head rule: pr head {pr_head} is later than the record "
                f"head {record_head} with changes outside the record "
                f"directory: {', '.join(sorted(outside))}"]
    if schema == SCHEMA_V1 and allow_v1_ancestor:
        if _git(repo, "merge-base", "--is-ancestor",
                record_head, pr_head).returncode == 0:
            return []
        return [f"head rule: v1 record head {record_head} is not an "
                f"ancestor of pr head {pr_head}"]
    return [f"head rule: pr head {pr_head} differs from the record head "
            f"{record_head} (exact match required for {schema!r})"]


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, env=env)


def evaluate(record_dir: str, repo: str, pr_head: str | None,
             allow_v1_ancestor: bool = False) -> list[str]:
    """Full gate evaluation of one review record.  Empty list = pass.

    Loads the record (never raises on a bad one), runs the authority
    subprocess, applies the head rule when a pr head is given, and
    re-asserts the record's own invariants (no open blocker/major,
    undispositioned empty, verdict decision derived from severities).
    """
    errs: list[str] = []
    rec = Path(record_dir)
    if not rec.is_absolute():
        rec = Path(repo) / rec
    try:
        record = load_record(str(rec))
    except ValueError as exc:
        return [str(exc)]
    errs.extend(check_record(str(rec), repo))
    if pr_head is not None:
        # the tail rule matches repo-relative POSIX paths: derive them when
        # the caller passed an absolute record dir
        try:
            rel = rec.resolve().relative_to(Path(repo).resolve()).as_posix()
        except ValueError:
            rel = record_dir
        errs.extend(head_rule_errors(record, repo, rel, pr_head,
                                     allow_v1_ancestor))
    open_bm = open_blocker_majors(record)
    if open_bm:
        errs.append("review record gate: open blocker/major findings "
                    "without disposition: " + ", ".join(open_bm))
    undis = record.get("undispositioned")
    if undis != []:
        errs.append(f"review record gate: undispositioned findings must be "
                    f"empty, got {undis!r}")
    verdict = record.get("verdict")
    if isinstance(verdict, dict):
        blockers = verdict.get("blockers")
        majors = verdict.get("majors")
        expected = "CHANGES_REQUESTED" if blockers or majors else "APPROVED"
        if verdict.get("decision") != expected:
            errs.append(f"review record gate: verdict decision "
                        f"{verdict.get('decision')!r} contradicts counts "
                        f"(blockers={blockers}, majors={majors}; expected "
                        f"{expected!r})")
    else:
        errs.append(f"review record gate: verdict missing/not a mapping "
                    f"({verdict!r})")
    return errs
