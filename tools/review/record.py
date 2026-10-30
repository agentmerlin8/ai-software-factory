"""Review record renderer and disposition ledger (S3.5a T3).

``tools/review/record.py`` closes a review lane iteration: it verifies a
review package, one findings document per reviewer lens, the disposition
ledger, and each gate evidence file, then writes the two deterministic
render outputs:

- ``<out-dir>/review.json``   schema ``review.record.v2`` — the canonical
  machine-readable record: identity, lane, reviewers, gate results, findings,
  dispositions, verdict, prior rounds, and the open/undispositioned id lists.
  v2 runs a dual-model panel: every internal lens may appear once per model,
  each finding id is namespaced ``<lens-id>@<model>``, and each finding
  carries a ``location_key`` plus an ``agreement`` count (distinct models
  that raised a finding at the same artifact + dimension) so duplicates
  across models stay attributable and triage-able. ``review.record.v1``
  records remain checkable under v1 semantics.
- ``<out-dir>/REVIEW-<pr>.md`` — the human-readable rendering.

The verdict is *computed* from finding severities (any blocker or major
forces ``CHANGES_REQUESTED``); it is never accepted from input.  All inputs
are re-verified: the package against its own ``package_sha256`` header and
its Identity base/head, each findings document against the package, fixed
dispositions against the repository (the fix commit must resolve and must
touch the artifact), and gate evidence files are hashed into the record.

CLI::

    record.py render --repo PATH --story ID --pr N --base SHA --head SHA
        --package FILE --dispositions FILE
        (--findings ROLE@MODEL=FILE... | --findings ROLE=FILE... --model ROLE=MODEL...)
        --gate NAME=EXIT:EVIDENCE... [--loop N]
        [--loop-cap 2] [--prior DIR]... [--out-dir DIR]

    record.py check --record DIR --repo PATH

Exit codes: 0 ok, 1 named refusal, 2 usage.  Refusals always carry the
``review record: `` prefix; nothing prints a bare traceback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, NoReturn

# Tools modules must also run when invoked directly as a file path, not only
# as a module (S3.5a T1 lesson): put the repository root on sys.path before
# any package imports.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.review.findings import DECISIONS, FindingsError, load as findings_load
from tools.review.yamlish import YamlishError, loads as yamlish_loads

SCHEMA = "review.record.v2"
LEGACY_SCHEMA = "review.record.v1"
LANE_NAME = "review-lane-v1"
LANE_VERSION = 2
LEGACY_LANE_VERSION = 1
ROLES = ("review-contract", "review-adversary", "review-security", "external")
DISPOSITIONS = ("fixed", "rejected", "deferred")
SEVERITIES = ("blocker", "major", "minor", "info")

FIX_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
ISSUE_RE = re.compile(r"^https://github\.com/[^/]+/[^/]+/issues/[0-9]+$")
PACKAGE_HASH_RE = re.compile(r"package_sha256: ([0-9a-f]{64})$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
LINE_SUFFIX_RE = re.compile(r"^(.*):[0-9]+$")
PACKAGE_REF_PREFIX = "package:diff@"


def fail(message: str) -> NoReturn:
    """Print a named refusal and exit 1 (never a traceback)."""
    print(message, file=sys.stderr)
    raise SystemExit(1)


def git(repo, *args: str) -> subprocess.CompletedProcess:
    """Run git with a sanitised environment (deterministic bytes)."""
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def resolve_sha(repo, sha: str) -> str | None:
    proc = git(repo, "rev-parse", "--verify", f"{sha}^{{commit}}")
    if proc.returncode != 0:
        return None
    return proc.stdout.strip().decode("ascii", "replace")


def read_bytes(path: str, refusal: str) -> bytes:
    """Read a file or exit 1 with a named refusal."""
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        fail(f"{refusal}: {path} ({exc.strerror or exc})")


# -- review package parsing ------------------------------------------------

class PackageInfo(NamedTuple):
    path: str
    sha256: str
    base_sha: str
    head_sha: str
    withheld: tuple[str, ...]


def package_body(data: bytes) -> bytes:
    """Everything after the blank line following the package_sha256 line."""
    idx = data.find(b"\n\n")
    if idx == -1:
        first = data.find(b"\n")
        second = data.find(b"\n", first + 1) if first != -1 else -1
        return data[second + 1:] if second != -1 else b""
    return data[idx + 2:]


def first_section(body_text: str, heading: str) -> list[str]:
    """Lines of the FIRST `## <heading>` section (contract inputs follow)."""
    lines = body_text.splitlines()
    for i, line in enumerate(lines):
        if line == f"## {heading}":
            j = i + 1
            while j < len(lines) and not lines[j].startswith("## "):
                j += 1
            return lines[i + 1:j]
    return []


def parse_withheld(body_text: str) -> tuple[str, ...]:
    """The package ## Scope section's withheld_files list (lines `- path`)."""
    withheld: list[str] = []
    collecting = False
    for line in first_section(body_text, "Scope"):
        if line.strip() == "withheld_files:":
            collecting = True
            continue
        if collecting:
            if line.startswith("- "):
                withheld.append(line[2:])
            elif line.strip():
                collecting = False
    return tuple(withheld)


def record_path(path: str, repo: str) -> str:
    """The path form stored in a record: repo-relative when the file lives
    inside the repository, absolute otherwise. Records are committed
    artifacts, so a local absolute path may not leak into them."""
    absolute = os.path.abspath(path)
    root = os.path.abspath(repo)
    try:
        relative = os.path.relpath(absolute, root)
    except ValueError:  # different drives (Windows)
        return absolute
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return absolute
    return relative.replace(os.sep, "/")


def resolve_record_path(path: str, repo: str) -> str:
    """Resolve a stored record path for reading, confined to the repository.

    A record is a committed artifact; a stored absolute path or a relative
    one that escapes the repository (``..``) could steer ``check`` into
    arbitrary files.  Both are refused by name; inside-repo paths resolve
    under ``os.path.realpath(repo)`` so symlink tricks cannot escape
    either.
    """
    if os.path.isabs(path):
        fail(f"review record: unconfined record path: {path}")
    root = os.path.realpath(repo)
    resolved = os.path.realpath(os.path.join(root, path))
    if resolved != root and not resolved.startswith(root + os.sep):
        fail(f"review record: unconfined record path: {path}")
    return resolved


def parse_package(path: str) -> PackageInfo:
    """Verify a package against its own hash and extract identity + scope."""
    data = read_bytes(path, "review record: package unreadable")
    head_parts = data.split(b"\n", 2)
    if len(head_parts) < 2:
        fail(f"review record: package hash line missing: {path}")
    match = PACKAGE_HASH_RE.match(
        head_parts[1].strip().decode("utf-8", "replace"))
    if match is None:
        fail(f"review record: package hash line missing: {path}")
    recorded = match.group(1)
    computed = hashlib.sha256(package_body(data)).hexdigest()
    if computed != recorded:
        fail(f"review record: package hash mismatch: "
             f"recorded {recorded}, computed {computed}")
    body_text = package_body(data).decode("utf-8", "replace")
    base_sha = head_sha = None
    for line in first_section(body_text, "Identity"):
        found = re.match(r"^(base_sha|head_sha): ([0-9a-f]{40})$", line)
        if found:
            if found.group(1) == "base_sha":
                base_sha = found.group(2)
            else:
                head_sha = found.group(2)
    if base_sha is None or head_sha is None:
        fail(f"review record: package head/base mismatch: "
             f"package records no complete base_sha/head_sha identity: {path}")
    return PackageInfo(os.path.abspath(path), recorded, base_sha, head_sha,
                       parse_withheld(body_text))


def verify_cli_range(repo, cli_base: str, cli_head: str, pkg: PackageInfo) -> None:
    """The resolved --base/--head must equal the package's Identity shas."""
    resolved_base = resolve_sha(repo, cli_base)
    if resolved_base is None:
        fail(f"review record: package head/base mismatch: "
             f"base sha does not resolve: {cli_base}")
    resolved_head = resolve_sha(repo, cli_head)
    if resolved_head is None:
        fail(f"review record: package head/base mismatch: "
             f"head sha does not resolve: {cli_head}")
    if resolved_base != pkg.base_sha or resolved_head != pkg.head_sha:
        fail(f"review record: package head/base mismatch: "
             f"--base {resolved_base} --head {resolved_head} but package "
             f"records {pkg.base_sha}..{pkg.head_sha}")


# -- findings documents ----------------------------------------------------

def repo_artifact_path(reference: str) -> str:
    """The bare repo path a reference names: the ``package:diff@`` prefix
    and any ``:line`` suffix are stripped so that touch and withheld rules
    apply to package-diff references exactly as to plain repo paths."""
    if reference.startswith(PACKAGE_REF_PREFIX):
        reference = reference[len(PACKAGE_REF_PREFIX):]
    stripped = LINE_SUFFIX_RE.match(reference)
    return stripped.group(1) if stripped else reference


def load_review_lens(path: str, role: str, pkg: PackageInfo) -> dict:
    """Load one findings document and cross-check it against the package."""
    data = read_bytes(path, "review record: unreadable findings doc")
    try:
        doc = findings_load(path)
    except FindingsError as exc:
        detail = exc.message.splitlines()[0] if exc.message else str(exc)
        fail(f"review record: unreadable findings doc: {path} ({detail})")
    review = doc.get("review", {})
    if review.get("reviewer") != role:
        fail(f"review record: reviewer role mismatch: --findings {role}={path} "
             f"but the document declares reviewer {review.get('reviewer')!r}")
    stop = review.get("stop")
    if isinstance(stop, dict) and stop.get("rule"):
        # Shape B: the reviewer STOPPED, it did not approve.  Treating the
        # missing findings list as empty would let compose_record emit
        # APPROVED for a panel that never reviewed.
        fail(f"review record: stopped review: {role} ({stop['rule']})")
    mismatches = []
    if review.get("package_sha256") != pkg.sha256:
        mismatches.append(f"package_sha256 {review.get('package_sha256')!r} "
                          f"!= package {pkg.sha256}")
    if review.get("base_sha") != pkg.base_sha:
        mismatches.append(f"base_sha {review.get('base_sha')!r} "
                          f"!= package {pkg.base_sha}")
    if review.get("head_sha") != pkg.head_sha:
        mismatches.append(f"head_sha {review.get('head_sha')!r} "
                          f"!= package {pkg.head_sha}")
    if mismatches:
        fail(f"review record: findings doc package_sha256/head_sha mismatch: "
             f"{role} ({path}): " + "; ".join(mismatches))
    found = review.get("findings")
    return {
        "rubric_version": review.get("rubric_version"),
        "document_sha256": hashlib.sha256(data).hexdigest(),
        "findings": found if isinstance(found, list) else [],
    }


def location_key(finding: dict) -> str:
    """Digest of (repo artifact path, rubric dimension) — a triage grouping.

    Two models rarely phrase a finding identically, so the record does not
    merge their findings; it labels them with the same location key and an
    ``agreement`` count, which is what lets the render show ``2/2`` and a
    human triage duplicate clusters as pairs.  Location alone is never
    proof of duplication: distinct defects in one file under one dimension
    keep separate entries and separate dispositions.
    """
    key = f"{repo_artifact_path(finding['artifact_reference'])}\x00" \
          f"{finding['rubric_dimension']}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def check_panel_symmetry(reviewers: list[dict]) -> None:
    """Every model must cover the same lens set (a dual panel is symmetric).

    An asymmetric panel is refused by name: it means one model silently
    skipped a lens, which would read as coverage the run never had.
    """
    coverage: dict[str, set[str]] = {}
    for reviewer in reviewers:
        coverage.setdefault(reviewer["model"], set()).add(reviewer["role"])
    if len(coverage) <= 1:
        return
    models = sorted(coverage)
    base_model = models[0]
    base = coverage[base_model]
    for model in models[1:]:
        if coverage[model] != base:
            missing = sorted(base - coverage[model])
            extra = sorted(coverage[model] - base)
            detail = []
            if missing:
                detail.append("missing " + ", ".join(missing))
            if extra:
                detail.append("extra " + ", ".join(extra))
            fail(f"review record: incomplete panel: {model} covers a "
                 f"different lens set than {base_model} "
                 f"({'; '.join(detail)})")


def annotate_agreement(findings: list[dict]) -> None:
    """Set location_key and agreement (distinct models at that location)."""
    models_at: dict[str, set[str]] = {}
    for finding in findings:
        finding["location_key"] = location_key(finding)
        models_at.setdefault(finding["location_key"], set()).add(
            finding["model"])
    for finding in findings:
        finding["agreement"] = len(models_at[finding["location_key"]])


def collect_reviewers(specs: list[tuple[str, str, str]], pkg: PackageInfo,
                      repo: str):
    """Load every (role, model, path) panel slot; returns (reviewers, findings).

    The model is part of the slot, not a per-lens afterthought: two models
    running the same lens are two reviewers, and the record namespaces their
    finding ids so one model's ``RC-001`` cannot collide with the other's.
    """
    reviewers: list[dict] = []
    findings: list[dict] = []
    seen: set[tuple[str, str]] = set()
    # Namespacing exists to keep two models' RC-001 apart.  A single-model
    # panel has no collision to prevent, so it keeps the plain lens ids its
    # operators already read (and that single-model records have always had).
    qualify = len({model for _, model, _ in specs}) > 1
    for role, model, path in specs:
        if role not in ROLES:
            fail(f"review record: unknown reviewer role: {role}")
        if (role, model) in seen:
            fail(f"review record: duplicate reviewer role/model: "
                 f"{role}@{model}")
        seen.add((role, model))
        lens = load_review_lens(path, role, pkg)
        reviewers.append({
            "role": role,
            "model": model,
            "rubric_version": lens["rubric_version"],
            "document": record_path(path, repo),
            "document_sha256": lens["document_sha256"],
        })
        for finding in lens["findings"]:
            findings.append({
                "id": f"{finding['id']}@{model}" if qualify else finding["id"],
                "source": "external" if role == "external" else "internal",
                "lens": role,
                "model": model,
                "severity": finding["severity"],
                "rubric_dimension": finding["rubric_dimension"],
                "artifact_reference": finding["artifact_reference"],
                "evidence": finding["evidence"],
                "required_change": finding["required_change"],
                "actionable": finding["actionable"],
            })
    check_panel_symmetry(reviewers)
    reviewers.sort(key=lambda r: (r["role"], r["model"]))
    findings.sort(key=lambda f: f["id"])
    annotate_agreement(findings)
    return reviewers, findings


# -- gate evidence ---------------------------------------------------------

def collect_gates(specs: list[str], parser: argparse.ArgumentParser,
                  repo: str) -> list[dict]:
    """Parse NAME=EXIT:EVIDENCE specs and hash each evidence file."""
    parsed: list[dict] = []
    for spec in specs:
        name, sep, rest = spec.partition("=")
        if not sep or not name or not rest:
            parser.error(f"bad --gate spec (expected NAME=EXIT:EVIDENCE): {spec!r}")
        exit_text, path_sep, path = rest.rpartition(":")
        if not path_sep or not path or not exit_text.isdigit():
            parser.error(f"bad --gate spec (expected NAME=EXIT:EVIDENCE): {spec!r}")
        parsed.append({"gate": name, "exit": int(exit_text),
                       "evidence_path": record_path(path, repo),
                       "_input_path": path})
    missing: list[str] = []
    for item in parsed:
        try:
            # Hash the file the caller actually pointed at (the stored
            # evidence_path is repo-relative and only for the record).
            data = Path(item["_input_path"]).read_bytes()
        except OSError:
            missing.append(item["gate"])
            continue
        item["evidence_sha256"] = hashlib.sha256(data).hexdigest()
    if missing:
        fail("review record: gate evidence path missing: "
             + ", ".join(sorted(set(missing))))
    parsed.sort(key=lambda g: (g["gate"], g["exit"], g["evidence_path"]))
    return [{"gate": g["gate"], "exit": g["exit"],
             "evidence_path": g["evidence_path"],
             "evidence_sha256": g["evidence_sha256"]} for g in parsed]


# -- disposition ledger ----------------------------------------------------

def load_dispositions(path: str) -> list[dict]:
    """Parse the dispositions file (JSON or the yamlish YAML subset)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        fail(f"review record: missing dispositions file: {path}")
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as exc:
            fail(f"review record: invalid dispositions file: {path} ({exc.msg})")
    else:
        try:
            doc = yamlish_loads(text)
        except YamlishError as exc:
            fail(f"review record: invalid dispositions file: {path}: {exc.message}")
    if not isinstance(doc, dict) or not isinstance(doc.get("dispositions"), list):
        fail(f"review record: invalid dispositions file: {path}: "
             "expected a mapping holding a 'dispositions' list")
    entries = []
    for index, item in enumerate(doc["dispositions"]):
        label = f"disposition {index}"
        if not isinstance(item, dict):
            fail(f"review record: invalid dispositions file: {path}: "
                 f"{label} must be a mapping")
        entry = {
            "id": item.get("id"),
            "disposition": item.get("disposition"),
            "fix_sha": (str(item["fix_sha"])
                        if isinstance(item.get("fix_sha"), int)
                        else item.get("fix_sha")),
            "reason": item.get("reason"),
            "issue": item.get("issue"),
            "evidence": item.get("evidence"),
        }
        if not isinstance(entry["id"], str) or not entry["id"].strip():
            fail(f"review record: invalid dispositions file: {path}: "
                 f"{label} requires an 'id'")
        if entry["disposition"] not in DISPOSITIONS:
            fail(f"review record: invalid dispositions file: {path}: "
                 f"{label} disposition must be one of {DISPOSITIONS}")
        for key in ("fix_sha", "reason", "issue", "evidence"):
            if entry[key] is not None and not isinstance(entry[key], str):
                fail(f"review record: invalid dispositions file: {path}: "
                     f"{label} {key} must be a string")
        entries.append(entry)
    return entries


def commit_touches_path(repo, sha: str, path: str) -> bool:
    proc = git(repo, "-c", "core.quotepath=false", "show", "--name-only",
               "--format=", sha)
    if proc.returncode != 0:
        return False
    names = {line.decode("utf-8", "replace")
             for line in proc.stdout.splitlines() if line}
    return path in names


_PATH_IN_TEXT_RE = None


def _paths_in_text(text: str) -> list[str]:
    """Repo-relative-looking paths quoted in a finding's required_change."""
    global _PATH_IN_TEXT_RE
    if _PATH_IN_TEXT_RE is None:
        import re as _re
        _PATH_IN_TEXT_RE = _re.compile(
            r"[A-Za-z0-9_][A-Za-z0-9_./-]*\."
            r"(?:md|py|toml|ya?ml|json|ts|tsx|js|jsx|sh|txt)"
        )
    return sorted(set(_PATH_IN_TEXT_RE.findall(text or "")))


def fix_sha_violations(entries: list[dict], by_id: dict, repo) -> list[str]:
    """Violations for every `fixed` disposition, each naming its finding id."""
    bad: list[str] = []
    for entry in entries:
        if entry["disposition"] != "fixed":
            continue
        sha = entry["fix_sha"]
        if not isinstance(sha, str) or FIX_SHA_RE.fullmatch(sha) is None:
            bad.append(f"{entry['id']} ({sha!r}: fix_sha missing or not 7-40 hex)")
            continue
        full = resolve_sha(repo, sha)
        if full is None:
            bad.append(f"{entry['id']} ({sha} not a commit)")
            continue
        finding = by_id[entry["id"]]
        artifact = repo_artifact_path(finding["artifact_reference"])
        if (Path(repo) / artifact).exists():
            # The cited artifact exists in the repository: the fix must
            # touch that file, and only that file counts — paths merely
            # named in required_change do not widen the rule.  This is
            # enforced for package:diff@ references too: the prefix and
            # any line suffix are stripped, never exempted.
            targets = [artifact]
        else:
            # A doc finding can cite a non-file (a missing statement, a
            # gap); only then may required_change name the paths that
            # actually had to change (existence checked).
            targets = [
                named
                for named in _paths_in_text(finding.get("required_change", ""))
                if (Path(repo) / named).exists()
            ]
        if not any(commit_touches_path(repo, full, target)
                   for target in targets):
            bad.append(f"{entry['id']} ({sha} does not touch {artifact})")
    return bad


def match_dispositions(entries: list[dict], findings: list[dict]):
    """Unknown/duplicate/undispositioned refusals; entries in finding order."""
    by_finding = {f["id"]: f for f in findings}
    seen: dict[str, dict] = {}
    unknown: list[str] = []
    duplicates: list[str] = []
    for entry in entries:
        if entry["id"] not in by_finding:
            unknown.append(entry["id"])
            continue
        if entry["id"] in seen:
            duplicates.append(entry["id"])
            continue
        seen[entry["id"]] = entry
    if unknown:
        fail("review record: disposition for unknown id: " + ", ".join(unknown))
    if duplicates:
        fail("review record: duplicate disposition: " + ", ".join(duplicates))
    undisposed = [f["id"] for f in findings if f["id"] not in seen]
    if undisposed:
        fail("review record: undispositioned findings: " + ", ".join(undisposed))
    return [seen[f["id"]] for f in findings]


def validate_disposition_content(entries: list[dict], findings: list[dict]):
    """Reason/evidence and issue-URL evidence rules, all ids listed."""
    missing_reason: list[str] = []
    bad_issue: list[str] = []
    for finding, entry in zip(findings, entries):
        if entry["disposition"] == "rejected":
            reason = entry["reason"]
            evidence = entry["evidence"]
            if not (isinstance(reason, str) and reason.strip()
                    and isinstance(evidence, str) and evidence.strip()):
                missing_reason.append(finding["id"])
        elif entry["disposition"] == "deferred":
            issue = entry["issue"]
            if not isinstance(issue, str) or ISSUE_RE.fullmatch(issue) is None:
                bad_issue.append(finding["id"])
    if missing_reason:
        fail("review record: missing reason/evidence: "
             + ", ".join(missing_reason))
    if bad_issue:
        fail("review record: bad issue URL: " + ", ".join(bad_issue))


# -- prior rounds ----------------------------------------------------------

def load_prior_rounds(dirs: list[str], repo: str) -> list[dict]:
    rounds: list[dict] = []
    for directory in dirs:
        json_path = Path(directory) / "review.json"
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except OSError:
            fail(f"review record: invalid prior round: {directory} "
                 "(missing review.json)")
        except json.JSONDecodeError as exc:
            fail(f"review record: invalid prior round: {directory} "
                 f"(invalid review.json: {exc.msg})")
        try:
            verdict = data["verdict"]
            decision, loop = verdict["decision"], verdict["loop"]
            disposition_list = data["dispositions"]
            finding_list = data["findings"]
            if decision not in DECISIONS or not isinstance(loop, int):
                raise ValueError
            if not isinstance(disposition_list, list) \
                    or not isinstance(finding_list, list):
                raise ValueError
            fixed_count = sum(
                1 for entry in disposition_list
                if isinstance(entry, dict) and entry.get("disposition") == "fixed")
        except (KeyError, TypeError, ValueError):
            fail(f"review record: invalid prior round: {directory} "
                 "(review.json has no usable verdict/findings/dispositions)")
        rounds.append({
            "loop": loop,
            # Records are committed artifacts: store the repo-relative form
            # so a record moved to another checkout stays portable.
            "path": record_path(directory, repo),
            "decision": decision,
            "findings": len(finding_list),
            "dispositioned": fixed_count,
        })
    rounds.sort(key=lambda r: (r["loop"], r["path"]))
    return rounds


# -- the record ------------------------------------------------------------

def compose_record(args, parser: argparse.ArgumentParser) -> dict:
    pkg = parse_package(args.package)
    verify_cli_range(args.repo, args.base, args.head, pkg)

    specs: list[tuple[str, str, str]] = []
    inline_form = legacy_form = False
    for spec in args.findings:
        role_part, sep, path = spec.partition("=")
        if not sep or not role_part or not path:
            parser.error("bad --findings spec (expected ROLE@MODEL=FILE or "
                         f"ROLE=FILE): {spec!r}")
        if "@" in role_part:
            role, _, model = role_part.partition("@")
            inline_form = True
            if not role or not model.strip():
                parser.error("bad --findings spec (expected ROLE@MODEL=FILE): "
                             f"{spec!r}")
        else:
            role, model = role_part, ""
            legacy_form = True
        specs.append((role, model, path))
    if inline_form and legacy_form:
        fail("review record: mixed --findings forms: use ROLE@MODEL=FILE for "
             "every document, or ROLE=FILE with --model for a single-model "
             "panel")
    models: dict[str, str] = {}
    if legacy_form:
        for spec in args.model:
            role, sep, model = spec.partition("=")
            if not sep or not role or not model.strip():
                parser.error(f"bad --model spec (expected ROLE=MODEL): {spec!r}")
            if role not in ROLES:
                fail(f"review record: unknown model role: {role}")
            if role in models:
                fail(f"review record: duplicate model role: {role}")
            models[role] = model
        for role in models:
            if role not in {spec_role for spec_role, _, _ in specs}:
                fail(f"review record: model role without findings doc: {role}")
        resolved: list[tuple[str, str, str]] = []
        for role, _, path in specs:
            if role not in ROLES:
                fail(f"review record: unknown reviewer role: {role}")
            model = models.get(role)
            if not (isinstance(model, str) and model.strip()):
                fail(f"review record: missing reviewer model: {role}")
            resolved.append((role, model, path))
        specs = resolved
    elif args.model:
        fail("review record: --model is not accepted with ROLE@MODEL=FILE "
             "documents (the model is part of each slot)")
    reviewers, findings = collect_reviewers(specs, pkg, args.repo)

    withheld = set(pkg.withheld)
    withheld_ids = [f["id"] for f in findings
                    if repo_artifact_path(f["artifact_reference"]) in withheld]
    if withheld_ids:
        fail("review record: withheld artifact: " + ", ".join(withheld_ids))

    gate_results = collect_gates(args.gate, parser, args.repo)
    entries = load_dispositions(args.dispositions)
    matched = match_dispositions(entries, findings)
    by_id = {f["id"]: f for f in findings}

    bad_sha = fix_sha_violations(matched, by_id, args.repo)
    if bad_sha:
        fail("review record: bad fixed sha: " + "; ".join(bad_sha))
    validate_disposition_content(matched, findings)

    prior_rounds = load_prior_rounds(args.prior, args.repo)

    counts = {severity: 0 for severity in SEVERITIES}
    for finding in findings:
        counts[finding["severity"]] += 1
    decision = ("CHANGES_REQUESTED"
                if counts["blocker"] or counts["major"] else "APPROVED")

    return {
        "schema": SCHEMA,
        "story": args.story,
        "pr": args.pr,
        "base_sha": pkg.base_sha,
        "head_sha": pkg.head_sha,
        "package": {"path": record_path(pkg.path, args.repo),
                    "sha256": pkg.sha256},
        "lane": {"name": LANE_NAME, "version": LANE_VERSION},
        "reviewers": reviewers,
        "gate_results": gate_results,
        "findings": findings,
        "dispositions": [
            {"id": finding["id"],
             "disposition": matched[index]["disposition"],
             "fix_sha": matched[index]["fix_sha"],
             "reason": matched[index]["reason"],
             "issue": matched[index]["issue"],
             "evidence": matched[index]["evidence"]}
            for index, finding in enumerate(findings)
        ],
        "verdict": {
            "decision": decision,
            "blockers": counts["blocker"],
            "majors": counts["major"],
            "minors": counts["minor"],
            "infos": counts["info"],
            "loop": args.loop,
            "loop_cap": args.loop_cap,
        },
        "prior_rounds": prior_rounds,
        "open_findings": [f["id"] for f in findings
                          if by_id_disposition(matched, f["id"]) != "fixed"],
        "undispositioned": [],
    }


def by_id_disposition(matched: list[dict], finding_id: str) -> str:
    for entry in matched:
        if entry["id"] == finding_id:
            return entry["disposition"]
    return ""


# -- markdown rendering (pure function of the record) ----------------------

def md_cell(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).replace("|", "\\|")


def disposition_evidence(disposition: dict) -> str:
    if disposition["disposition"] == "fixed":
        return f"fixed: {disposition['fix_sha']}"
    if disposition["disposition"] == "rejected":
        return f"reason: {disposition['reason']} evidence: {disposition['evidence']}"
    return f"issue: {disposition['issue']}"


def render_markdown(record: dict) -> str:
    """Deterministic REVIEW-<pr>.md text, a pure function of the record."""
    out: list[str] = [
        f"# Review record — PR {record['pr']} ({record['story']})",
        "",
        "## Verdict",
        "",
    ]
    verdict = record["verdict"]
    out += [
        f"decision: {verdict['decision']}",
        f"blockers: {verdict['blockers']}",
        f"majors: {verdict['majors']}",
        f"minors: {verdict['minors']}",
        f"infos: {verdict['infos']}",
        "undispositioned findings: 0",
        f"loop {verdict['loop']} of {verdict['loop_cap']}",
        "",
        "## Package",
        "",
        f"path: {record['package']['path']}",
        f"sha256: {record['package']['sha256']}",
        f"range: {record['base_sha']} → {record['head_sha']}",
        "",
        "## Reviewers",
        "",
        "| role | model | rubric | document |",
        "| --- | --- | --- | --- |",
    ]
    for reviewer in record["reviewers"]:
        out.append(f"| {md_cell(reviewer['role'])} | "
                   f"{md_cell(reviewer['model'])} | "
                   f"{md_cell(reviewer['rubric_version'])} | "
                   f"{md_cell(reviewer['document'])} |")
    legacy = record.get("schema") == LEGACY_SCHEMA
    panel_models = sorted({reviewer["model"] for reviewer in record["reviewers"]})
    if not legacy:
        out += [
            "",
            "## Panel",
            "",
            f"models: {', '.join(panel_models)}",
            "",
            "| model | lenses | findings |",
            "| --- | --- | --- |",
        ]
        for model in panel_models:
            lenses = sorted(reviewer["role"] for reviewer in record["reviewers"]
                            if reviewer["model"] == model)
            count = sum(1 for finding in record["findings"]
                        if finding.get("model") == model)
            out.append(f"| {md_cell(model)} | {md_cell(', '.join(lenses))} | {count} |")
    out += [
        "",
        "## Gate results",
        "",
        "| gate | exit | evidence | sha256 |",
        "| --- | --- | --- | --- |",
    ]
    for gate in record["gate_results"]:
        out.append(f"| {md_cell(gate['gate'])} | {gate['exit']} | "
                   f"{md_cell(gate['evidence_path'])} | "
                   f"{gate['evidence_sha256']} |")
    out += [
        "",
        "## Findings",
        "",
    ]
    if legacy:
        out += [
            "| id | lens | severity | dimension | artifact | disposition | evidence |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    else:
        out += [
            "| id | lens | model | severity | dimension | artifact | at | disposition | evidence |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    dispositions = {d["id"]: d for d in record["dispositions"]}
    for finding in record["findings"]:
        disposition = dispositions.get(finding["id"], {})
        if legacy:
            out.append(
                f"| {md_cell(finding['id'])} | {md_cell(finding['lens'])} | "
                f"{md_cell(finding['severity'])} | "
                f"{md_cell(finding['rubric_dimension'])} | "
                f"{md_cell(finding['artifact_reference'])} | "
                f"{md_cell(disposition.get('disposition'))} | "
                f"{md_cell(disposition_evidence(disposition))} |"
            )
        else:
            out.append(
                f"| {md_cell(finding['id'])} | {md_cell(finding['lens'])} | "
                f"{md_cell(finding.get('model'))} | "
                f"{md_cell(finding['severity'])} | "
                f"{md_cell(finding['rubric_dimension'])} | "
                f"{md_cell(finding['artifact_reference'])} | "
                f"{md_cell(finding.get('agreement'))} | "
                f"{md_cell(disposition.get('disposition'))} | "
                f"{md_cell(disposition_evidence(disposition))} |"
            )
    if record["prior_rounds"]:
        out += [
            "",
            "## Prior rounds",
            "",
            "| loop | decision | findings | dispositioned | path |",
            "| --- | --- | --- | --- | --- |",
        ]
        for prior in record["prior_rounds"]:
            out.append(f"| {prior['loop']} | {prior['decision']} | "
                       f"{prior['findings']} | {prior['dispositioned']} | "
                       f"{md_cell(prior['path'])} |")
    notes = (
        "Findings with source `external` (lens `external`, ids prefixed `EX-`) "
        "originate from legacy review sources outside the lane and are mapped "
        "onto the lane rubric by dimension; internal panel lenses carry id "
        "prefixes RC- (review-contract), RA- (review-adversary), and RS- "
        "(review-security)."
    )
    if not legacy:
        notes += (
            " Finding ids are namespaced `<lens-id>@<model>`; `at` counts the "
            "models that raised a finding at the same artifact and dimension "
            "(a triage grouping, not a merge — every entry carries its own "
            "disposition)."
        )
    out += [
        "",
        "## Notes",
        "",
        notes,
        "",
    ]
    return "\n".join(out)


# -- commands --------------------------------------------------------------

def cmd_render(args, parser: argparse.ArgumentParser) -> int:
    if args.loop < 1:
        parser.error("--loop must be >= 1")
    if args.loop_cap < 1:
        parser.error("--loop-cap must be >= 1")
    if args.loop > args.loop_cap:
        parser.error("--loop exceeds --loop-cap")
    record = compose_record(args, parser)
    out_dir = Path(args.out_dir)
    json_text = json.dumps(record, indent=2, sort_keys=True) + "\n"
    markdown_text = render_markdown(record)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "review.json").write_bytes(json_text.encode("utf-8"))
        (out_dir / f"REVIEW-{record['pr']}.md").write_bytes(
            markdown_text.encode("utf-8"))
    except OSError:
        fail(f"review record: out dir not writable: {os.path.abspath(args.out_dir)}")
    print(f"review record: rendered ({len(record['findings'])} finding(s), "
          f"verdict {record['verdict']['decision']}, "
          f"{os.path.abspath(args.out_dir)})")
    return 0


RECORD_TOP_KEYS = (
    "schema", "story", "pr", "base_sha", "head_sha", "package", "lane",
    "reviewers", "gate_results", "findings", "dispositions", "verdict",
    "prior_rounds", "open_findings", "undispositioned",
)


def validate_record(doc: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(doc, dict):
        return ["record must be a mapping"]
    if doc.get("schema") not in (SCHEMA, LEGACY_SCHEMA):
        errors.append(f"invalid schema {doc.get('schema')!r}")
    legacy = doc.get("schema") == LEGACY_SCHEMA
    for key in RECORD_TOP_KEYS:
        if key not in doc:
            errors.append(f"missing key {key!r}")
    for key in doc:
        if key not in RECORD_TOP_KEYS:
            errors.append(f"unknown key {key!r}")
    if "story" in doc and not isinstance(doc["story"], str):
        errors.append("story must be a string")
    if "pr" in doc and not isinstance(doc["pr"], int):
        errors.append("pr must be an integer")
    for key in ("base_sha", "head_sha"):
        if key in doc and (not isinstance(doc[key], str)
                           or SHA40_RE.fullmatch(doc[key]) is None):
            errors.append(f"{key} must be 40 lowercase hex characters")
    for key in ("package", "lane", "verdict"):
        if key in doc and not isinstance(doc[key], dict):
            errors.append(f"{key} must be a mapping")
    for key in ("reviewers", "gate_results", "findings", "dispositions",
                "prior_rounds", "open_findings", "undispositioned"):
        if key in doc and not isinstance(doc[key], list):
            errors.append(f"{key} must be a list")
    reviewers = doc.get("reviewers")
    if isinstance(reviewers, list):
        for index, entry in enumerate(reviewers):
            if not isinstance(entry, dict):
                errors.append(f"reviewers[{index}] must be a mapping")
                continue
            model = entry.get("model")
            if not (isinstance(model, str) and model.strip()):
                errors.append(
                    f"reviewers[{index}] model must be a non-empty string")
    lane = doc.get("lane")
    if isinstance(lane, dict):
        if lane.get("name") != LANE_NAME:
            errors.append(f"invalid lane name {lane.get('name')!r}")
        expected_version = LEGACY_LANE_VERSION if legacy else LANE_VERSION
        if lane.get("version") != expected_version:
            errors.append(f"invalid lane version {lane.get('version')!r}")
    findings = doc.get("findings")
    if not legacy and isinstance(findings, list):
        for index, entry in enumerate(findings):
            if not isinstance(entry, dict):
                errors.append(f"findings[{index}] must be a mapping")
                continue
            for key in ("model", "location_key"):
                value = entry.get(key)
                if not (isinstance(value, str) and value.strip()):
                    errors.append(
                        f"findings[{index}] {key} must be a non-empty string")
            if not isinstance(entry.get("agreement"), int):
                errors.append(f"findings[{index}] agreement must be an integer")
    return errors


def cmd_check(args) -> int:
    record_path = Path(args.record) / "review.json"
    try:
        data = json.loads(record_path.read_text(encoding="utf-8"))
    except OSError:
        fail(f"review record: record unreadable: {record_path}")
    except json.JSONDecodeError as exc:
        fail(f"review record: record unreadable: {record_path} ({exc.msg})")
    errors = validate_record(data)
    if errors:
        fail("review record: invalid record: " + "; ".join(errors))

    pkg = parse_package(resolve_record_path(data["package"]["path"], args.repo))
    if pkg.sha256 != data["package"]["sha256"]:
        fail(f"review record: package hash mismatch: "
             f"recorded {data['package']['sha256']}, computed {pkg.sha256}")
    if pkg.base_sha != data["base_sha"] or pkg.head_sha != data["head_sha"]:
        fail(f"review record: package head/base mismatch: record names "
             f"{data['base_sha']}..{data['head_sha']}, package records "
             f"{pkg.base_sha}..{pkg.head_sha}")

    withheld_ids = [f["id"] for f in data["findings"]
                    if repo_artifact_path(f["artifact_reference"])
                    in set(pkg.withheld)]
    if withheld_ids:
        fail("review record: withheld artifact: " + ", ".join(withheld_ids))

    # The record is re-derived, not trusted: findings come back out of the
    # reviewer documents themselves, dispositions re-run their rules against
    # those findings, and the verdict is recomputed from severities.
    legacy = data["schema"] == LEGACY_SCHEMA
    qualify = len({reviewer.get("model")
                   for reviewer in data["reviewers"]}) > 1
    derived_findings: list[dict] = []
    seen_slots: set[tuple[str, str]] = set()
    for reviewer in data["reviewers"]:
        role = reviewer.get("role")
        model = reviewer.get("model")
        doc_path = resolve_record_path(reviewer["document"], args.repo)
        raw = read_bytes(doc_path,
                         f"review record: findings doc mismatch: {role}")
        if hashlib.sha256(raw).hexdigest() != reviewer["document_sha256"]:
            fail(f"review record: findings doc mismatch: {role} "
                 f"({reviewer['document']})")
        lens = load_review_lens(doc_path, role, pkg)
        if lens["rubric_version"] != reviewer.get("rubric_version"):
            fail(f"review record: findings doc mismatch: {role} "
                 "(rubric version changed)")
        slot = (role, model)
        if slot in seen_slots:
            fail(f"review record: findings doc mismatch: duplicate "
                 f"role/model {role}@{model}")
        seen_slots.add(slot)
        for finding in lens["findings"]:
            entry = {
                "source": "external" if role == "external" else "internal",
                "lens": role,
                "severity": finding["severity"],
                "rubric_dimension": finding["rubric_dimension"],
                "artifact_reference": finding["artifact_reference"],
                "evidence": finding["evidence"],
                "required_change": finding["required_change"],
                "actionable": finding["actionable"],
            }
            if legacy:
                entry["id"] = finding["id"]
            else:
                entry["id"] = (f"{finding['id']}@{model}" if qualify
                               else finding["id"])
                entry["model"] = model
            derived_findings.append(entry)

    if not legacy:
        check_panel_symmetry(data["reviewers"])
        annotate_agreement(derived_findings)

    FINDING_KEYS = ("id", "source", "lens", "severity", "rubric_dimension",
                    "artifact_reference", "evidence", "required_change",
                    "actionable")
    if not legacy:
        FINDING_KEYS = FINDING_KEYS + ("model", "location_key", "agreement")
    derived_findings.sort(key=lambda f: f["id"])
    stored_findings = sorted(data["findings"], key=lambda f: f.get("id") or "")
    problems: list[str] = []
    if len(stored_findings) != len(derived_findings):
        problems.append(f"{len(stored_findings)} finding(s) stored but "
                        f"documents derive {len(derived_findings)}")
    for stored, derived in zip(stored_findings, derived_findings):
        for key in FINDING_KEYS:
            if stored.get(key) != derived[key]:
                problems.append(f"{derived['id']}: {key} stored "
                                f"{stored.get(key)!r} != document "
                                f"{derived[key]!r}")
                break
    if problems:
        fail("review record: findings mismatch: " + "; ".join(problems))

    for gate in data["gate_results"]:
        try:
            raw = Path(resolve_record_path(gate["evidence_path"], args.repo)).read_bytes()
        except OSError:
            fail(f"review record: gate evidence path missing: {gate['gate']} "
                 f"({gate['evidence_path']})")
        if hashlib.sha256(raw).hexdigest() != gate["evidence_sha256"]:
            fail(f"review record: gate evidence mismatch: {gate['gate']} "
                 f"({gate['evidence_path']})")

    if data["undispositioned"]:
        fail("review record: undispositioned findings: "
             + ", ".join(str(value) for value in data["undispositioned"]))

    by_id = {f["id"]: f for f in stored_findings}
    matched = match_dispositions(data["dispositions"], stored_findings)
    bad_sha = fix_sha_violations(matched, by_id, args.repo)
    if bad_sha:
        fail("review record: bad fixed sha: " + "; ".join(bad_sha))
    validate_disposition_content(matched, stored_findings)

    counts = {severity: 0 for severity in SEVERITIES}
    for finding in stored_findings:
        counts[finding["severity"]] += 1
    decision = ("CHANGES_REQUESTED"
                if counts["blocker"] or counts["major"] else "APPROVED")
    verdict = data["verdict"]
    verdict_problems: list[str] = []
    if verdict.get("decision") != decision:
        verdict_problems.append(f"decision stored {verdict.get('decision')!r} "
                                f"!= computed {decision!r}")
    for key, severity in (("blockers", "blocker"), ("majors", "major"),
                          ("minors", "minor"), ("infos", "info")):
        if verdict.get(key) != counts[severity]:
            verdict_problems.append(f"{key} stored {verdict.get(key)!r} "
                                    f"!= computed {counts[severity]!r}")
    if verdict_problems:
        fail("review record: verdict mismatch: "
             + "; ".join(verdict_problems))

    expected = render_markdown(data)
    markdown_path = Path(args.record) / f"REVIEW-{data['pr']}.md"
    try:
        actual = markdown_path.read_text(encoding="utf-8")
    except OSError:
        fail(f"review record: markdown missing: {markdown_path}")
    if actual != expected:
        fail(f"review record: markdown differs: {markdown_path}")
    print(f"review record: OK ({len(data['findings'])} finding(s), "
          f"{os.path.abspath(args.record)})")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="record.py",
        description="Review record renderer / disposition ledger (tools/review).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    render = subparsers.add_parser(
        "render", help="render out-dir/review.json + out-dir/REVIEW-<pr>.md")
    render.add_argument("--repo", required=True,
                        help="path to the git repository")
    render.add_argument("--story", required=True, help="story identifier")
    render.add_argument("--pr", type=int, required=True, help="PR number")
    render.add_argument("--base", required=True, help="base commit sha")
    render.add_argument("--head", required=True, help="head commit sha")
    render.add_argument("--package", required=True, help="review package file")
    render.add_argument("--findings", action="append", default=[],
                        metavar="ROLE@MODEL=FILE",
                        help="one findings doc per panel slot (repeatable); "
                             "ROLE=FILE with --model is the single-model form")
    render.add_argument("--model", action="append", default=[],
                        metavar="ROLE=MODEL",
                        help="reviewer model per role for the ROLE=FILE form "
                             "(repeatable; not accepted with ROLE@MODEL=FILE)")
    render.add_argument("--dispositions", required=True,
                        help="disposition ledger file")
    render.add_argument("--gate", action="append", default=[],
                        metavar="NAME=EXIT:EVIDENCE",
                        help="gate result and evidence file")
    render.add_argument("--loop", type=int, default=1, help="lane loop number")
    render.add_argument("--loop-cap", type=int, default=2,
                        help="lane loop cap")
    render.add_argument("--prior", action="append", default=[], metavar="DIR",
                        help="directory of a prior round record")
    render.add_argument("--out-dir", default=".", help="output directory")
    check = subparsers.add_parser("check",
                                  help="re-verify a rendered record directory")
    check.add_argument("--record", required=True,
                       help="directory containing review.json")
    check.add_argument("--repo", required=True,
                       help="path to the git repository")
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "render":
            return cmd_render(args, parser)
        return cmd_check(args)
    except Exception as exc:  # no bare tracebacks
        print(f"review record: internal error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())