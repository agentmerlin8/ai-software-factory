"""Review record renderer and disposition ledger (S3.5a T3).

``tools/review/record.py`` closes a review lane iteration: it verifies a
review package, one findings document per reviewer lens, the disposition
ledger, and each gate evidence file, then writes the two deterministic
render outputs:

- ``<out-dir>/review.json``   schema ``review.record.v1`` — the canonical
  machine-readable record: identity, lane, reviewers, gate results, findings,
  dispositions, verdict, prior rounds, and the open/undispositioned id lists.
- ``<out-dir>/REVIEW-<pr>.md`` — the human-readable rendering.

The verdict is *computed* from finding severities (any blocker or major
forces ``CHANGES_REQUESTED``); it is never accepted from input.  All inputs
are re-verified: the package against its own ``package_sha256`` header and
its Identity base/head, each findings document against the package, fixed
dispositions against the repository (the fix commit must resolve and must
touch the artifact), and gate evidence files are hashed into the record.

CLI::

    record.py render --repo PATH --story ID --pr N --base SHA --head SHA
        --package FILE --findings ROLE=FILE... --dispositions FILE
        --gate NAME=EXIT:EVIDENCE... [--loop N] [--loop-cap 2]
        [--prior DIR]... [--out-dir DIR]

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

SCHEMA = "review.record.v1"
LANE_NAME = "review-lane-v1"
LANE_VERSION = 1
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
    """Resolve a stored record path for reading (relative -> repo-joined)."""
    return path if os.path.isabs(path) else os.path.join(repo, path)


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

def repo_artifact_path(reference: str) -> str | None:
    """The bare repo path of a repo-path reference (line suffix stripped);
    None for ``package:diff@`` references, which are exempt."""
    if reference.startswith(PACKAGE_REF_PREFIX):
        return None
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


def collect_reviewers(specs: list[tuple[str, str]], pkg: PackageInfo,
                      repo: str):
    """Load every --findings ROLE=FILE pair; returns (reviewers, findings)."""
    reviewers: list[dict] = []
    findings: list[dict] = []
    seen: set[str] = set()
    for role, path in specs:
        if role not in ROLES:
            fail(f"review record: unknown reviewer role: {role}")
        if role in seen:
            fail(f"review record: duplicate reviewer role: {role}")
        seen.add(role)
        lens = load_review_lens(path, role, pkg)
        reviewers.append({
            "role": role,
            "rubric_version": lens["rubric_version"],
            "document": record_path(path, repo),
            "document_sha256": lens["document_sha256"],
        })
        for finding in lens["findings"]:
            findings.append({
                "id": finding["id"],
                "source": "external" if role == "external" else "internal",
                "lens": role,
                "severity": finding["severity"],
                "rubric_dimension": finding["rubric_dimension"],
                "artifact_reference": finding["artifact_reference"],
                "evidence": finding["evidence"],
                "required_change": finding["required_change"],
                "actionable": finding["actionable"],
            })
    reviewers.sort(key=lambda r: r["role"])
    findings.sort(key=lambda f: f["id"])
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
                       "evidence_path": record_path(path, repo)})
    missing: list[str] = []
    for item in parsed:
        try:
            data = Path(item["evidence_path"]).read_bytes()
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
        artifact = repo_artifact_path(by_id[entry["id"]]["artifact_reference"])
        if artifact is not None and not commit_touches_path(repo, full, artifact):
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

def load_prior_rounds(dirs: list[str]) -> list[dict]:
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
            "path": os.path.abspath(directory),
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

    specs: list[tuple[str, str]] = []
    for spec in args.findings:
        role, sep, path = spec.partition("=")
        if not sep or not role or not path:
            parser.error(f"bad --findings spec (expected ROLE=FILE): {spec!r}")
        specs.append((role, path))
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

    prior_rounds = load_prior_rounds(args.prior)

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
        "| role | rubric | document |",
        "| --- | --- | --- |",
    ]
    for reviewer in record["reviewers"]:
        out.append(f"| {md_cell(reviewer['role'])} | "
                   f"{md_cell(reviewer['rubric_version'])} | "
                   f"{md_cell(reviewer['document'])} |")
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
        "| id | lens | severity | dimension | artifact | disposition | evidence |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    dispositions = {d["id"]: d for d in record["dispositions"]}
    for finding in record["findings"]:
        disposition = dispositions.get(finding["id"], {})
        out.append(
            f"| {md_cell(finding['id'])} | {md_cell(finding['lens'])} | "
            f"{md_cell(finding['severity'])} | "
            f"{md_cell(finding['rubric_dimension'])} | "
            f"{md_cell(finding['artifact_reference'])} | "
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
    out += [
        "",
        "## Notes",
        "",
        "Findings with source `external` (lens `external`, ids prefixed `EX-`) "
        "originate from legacy review sources outside the lane and are mapped "
        "onto the lane rubric by dimension; internal panel lenses carry id "
        "prefixes RC- (review-contract), RA- (review-adversary), and RS- "
        "(review-security).",
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
    if doc.get("schema") != SCHEMA:
        errors.append(f"invalid schema {doc.get('schema')!r}")
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
    lane = doc.get("lane")
    if isinstance(lane, dict):
        if lane.get("name") != LANE_NAME:
            errors.append(f"invalid lane name {lane.get('name')!r}")
        if lane.get("version") != LANE_VERSION:
            errors.append(f"invalid lane version {lane.get('version')!r}")
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

    for reviewer in data["reviewers"]:
        raw = read_bytes(resolve_record_path(reviewer["document"], args.repo),
                         "review record: findings doc mismatch: "
                         f"{reviewer['role']}")
        if hashlib.sha256(raw).hexdigest() != reviewer["document_sha256"]:
            fail(f"review record: findings doc mismatch: {reviewer['role']} "
                 f"({reviewer['document']})")

    for gate in data["gate_results"]:
        try:
            raw = Path(resolve_record_path(gate["evidence_path"], args.repo)).read_bytes()
        except OSError:
            fail(f"review record: gate evidence path missing: {gate['gate']} "
                 f"({gate['evidence_path']})")
        if hashlib.sha256(raw).hexdigest() != gate["evidence_sha256"]:
            fail(f"review record: gate evidence mismatch: {gate['gate']} "
                 f"({gate['evidence_path']})")

    by_id = {f["id"]: f for f in data["findings"]}
    bad_sha = fix_sha_violations(data["dispositions"], by_id, args.repo)
    if bad_sha:
        fail("review record: bad fixed sha: " + "; ".join(bad_sha))

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
                        metavar="ROLE=FILE", help="findings doc per role")
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