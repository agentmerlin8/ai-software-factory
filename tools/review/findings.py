"""findings — the `review.findings.v1` review findings schema for the kit.

Contract
--------
A review findings document is a mapping produced by an LLM reviewer.  Two
shapes are valid:

- Shape A (findings): ``review`` holds reviewer identity, rubric version,
  package/base/head commit hashes, a decision, and a ``findings`` list.
- Shape B (stopped review): ``review`` holds the same identity fields plus a
  ``stop`` mapping (``rule`` + ``detail``) instead of a decision.

An optional top-level ``schema: review.findings.v1`` key is accepted; if
present it must equal `SCHEMA`, and it is added by `normalize()` when absent.

- `validate(doc)` returns ``[]`` for a valid document and otherwise ALL
  violations as named strings in a deterministic (top-down) order.
- `normalize(doc)` returns the document with a fixed key order and findings
  sorted by id.
- `load(path)` parses (JSON if the text strips to start with ``{`` or ``[``,
  otherwise the yamlish YAML subset), validates, and returns the normalized
  dictionary.
- `to_json(doc)` is the canonical form: ``json.dumps(normalize(doc),
  indent=2, sort_keys=True) + "\\n"``.

Named refusals are raised as `FindingsError` (``str(exc) ==
"review findings: <message>"``); parse or validation failures never leak a
bare traceback.

CLI (``python3 -m tools.review.findings <cmd> ...``)
-------------
- ``validate <file>...``: exit 0 prints ``review findings: OK (<n>
  finding(s), <file>)`` per file; exit 1 prints every error on its own line,
  each prefixed ``  - ``; exit 2 on usage errors.
- ``json <file>``: prints `to_json(load(file))`; exit 0/1/2 as above.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from tools.review.yamlish import YamlishError, loads as yamlish_loads

SCHEMA = "review.findings.v1"
ROLES = ("review-contract", "review-adversary", "review-security", "external")
DIMENSIONS = (
    "contract_fidelity",
    "behavior",
    "test_quality",
    "security",
    "scope",
    "architecture",
    "docs_process",
    "operational",
)
SEVERITIES = ("blocker", "major", "minor", "info")
DECISIONS = ("APPROVED", "CHANGES_REQUESTED")

_ROLE_PREFIX = {
    "review-contract": "RC",
    "review-adversary": "RA",
    "review-security": "RS",
    "external": "EX",
}

_RUBRIC_RE = re.compile(r"^[a-z0-9-]+-v[0-9]+$")
_PACKAGE_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_FINDING_ID_RE = re.compile(r"^(RC|RA|RS|EX)-[0-9]{3}$")
_ARTIFACT_RE = re.compile(r"^(package:diff@[^\s:]+(:[0-9]+)?|[^\s:]+(:[0-9]+)?)$")
_STOP_RULE_RE = re.compile(r"^[a-z][a-z0-9-]*$")

_TOP_KEYS = ("schema", "review")
_REVIEW_KEYS_BASE = (
    "reviewer",
    "rubric_version",
    "package_sha256",
    "base_sha",
    "head_sha",
)
_REVIEW_KEYS_A = _REVIEW_KEYS_BASE + ("decision", "findings")
_REVIEW_KEYS_B = _REVIEW_KEYS_BASE + ("stop",)
_FINDING_KEYS = (
    "id",
    "severity",
    "rubric_dimension",
    "artifact_reference",
    "evidence",
    "required_change",
    "actionable",
)
_STOP_KEYS = ("rule", "detail")


class FindingsError(ValueError):
    """A named refusal from the findings module.

    ``str(exc)`` is always ``"review findings: <message>"``.  Validation
    failures also carry the individual violations in ``errors``.
    """

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        self.message = message
        self.errors = errors
        super().__init__(message)

    def __str__(self) -> str:
        return f"review findings: {self.message}"


# -- validation -----------------------------------------------------------

def _name(value: object) -> str:
    if isinstance(value, str):
        return repr(value)
    return f"{type(value).__name__} {value!r}"


def validate(doc: object) -> list[str]:
    """Return [] if `doc` is a valid review.findings.v1 document, otherwise
    ALL violations as named strings in deterministic order."""
    errors: list[str] = []
    if not isinstance(doc, dict):
        errors.append("document must be a mapping (review/... got a different type)")
        return errors

    # schema
    if "schema" in doc and doc.get("schema") != SCHEMA:
        errors.append(f"invalid schema {doc['schema']!r} (expected {SCHEMA!r})")

    # top-level keys
    for key in doc:
        if key not in _TOP_KEYS:
            errors.append(f"unknown key {key!r}")
    if "review" not in doc:
        errors.append("missing key 'review'")
        return errors

    review = doc.get("review")
    if not isinstance(review, dict):
        errors.append("'review' must be a mapping")
        return errors

    has_stop = "stop" in review
    has_decision = "decision" in review
    shape_b = has_stop and not has_decision
    allowed_review = _REVIEW_KEYS_B if shape_b else _REVIEW_KEYS_A

    if has_stop and has_decision:
        errors.append("review may not contain both 'stop' and 'decision'")

    for key in review:
        if key not in allowed_review:
            errors.append(f"unknown key {key!r} (review)")
    for key in allowed_review:
        if key not in review:
            errors.append(f"missing key {key!r} (review)")

    # identity fields
    reviewer = review.get("reviewer")
    reviewer_ok = isinstance(reviewer, str) and reviewer in ROLES
    if "reviewer" in review and not reviewer_ok:
        errors.append(f"invalid reviewer {_name(reviewer)} (expected one of {ROLES})")

    rubric_version = review.get("rubric_version")
    if "rubric_version" in review:
        if not isinstance(rubric_version, str) or not _RUBRIC_RE.fullmatch(rubric_version):
            errors.append(
                f"invalid rubric_version {_name(rubric_version)} "
                "(expected a lowercase name like 'review-contract-v1')"
            )

    for field, pattern, expected in (
        ("package_sha256", _PACKAGE_SHA_RE, "64 lowercase hex characters"),
        ("base_sha", _GIT_SHA_RE, "40 lowercase hex characters"),
        ("head_sha", _GIT_SHA_RE, "40 lowercase hex characters"),
    ):
        value = review.get(field)
        if field in review and (not isinstance(value, str) or not pattern.fullmatch(value)):
            errors.append(f"invalid {field} {_name(value)} (expects {expected})")

    if shape_b:
        stop = review.get("stop")
        if not isinstance(stop, dict):
            errors.append("'stop' must be a mapping")
        else:
            for key in stop:
                if key not in _STOP_KEYS:
                    errors.append(f"unknown key {key!r} (stop)")
            for key in _STOP_KEYS:
                if key not in stop:
                    errors.append(f"missing key {key!r} (stop)")
            rule = stop.get("rule")
            if "rule" in stop and (
                not isinstance(rule, str) or not _STOP_RULE_RE.fullmatch(rule)
            ):
                errors.append(
                    f"invalid stop rule {_name(rule)} (expected a lowercase "
                    "name like 'package-hash-mismatch')"
                )
            detail = stop.get("detail")
            if "detail" in stop and (
                not isinstance(detail, str) or not detail.strip()
            ):
                errors.append("stop detail must be a non-empty string")
        return errors

    # shape A
    decision = review.get("decision")
    if "decision" in review and (not isinstance(decision, str) or decision not in DECISIONS):
        errors.append(
            f"invalid decision {_name(decision)} (expected APPROVED or CHANGES_REQUESTED)"
        )

    findings = review.get("findings")
    if "findings" in review and not isinstance(findings, list):
        errors.append("'findings' must be a list")
        return errors

    if isinstance(findings, list) and isinstance(decision, str) and decision in DECISIONS:
        if decision == "APPROVED" and findings:
            errors.append(
                f"decision APPROVED requires an empty findings list (found {len(findings)})"
            )
        if decision == "CHANGES_REQUESTED" and not findings:
            errors.append("decision CHANGES_REQUESTED requires at least one finding")

    if not isinstance(findings, list):
        return errors

    seen_ids: set[str] = set()
    for idx, finding in enumerate(findings):
        label = f"finding {idx}"
        if not isinstance(finding, dict):
            errors.append(f"{label} must be a mapping")
            continue
        for key in finding:
            if key not in _FINDING_KEYS:
                errors.append(f"unknown key {key!r} ({label})")
        for key in _FINDING_KEYS:
            if key not in finding:
                errors.append(f"missing key {key!r} ({label})")

        fid = finding.get("id")
        if not isinstance(fid, str) or not _FINDING_ID_RE.fullmatch(fid):
            if "id" in finding:
                errors.append(
                    f"invalid finding id {_name(fid)} (expected (RC|RA|RS|EX)-NNN)"
                )
        else:
            if fid in seen_ids:
                errors.append(f"duplicate finding id {fid!r} ({label})")
            seen_ids.add(fid)
            if reviewer_ok and fid.split("-", 1)[0] != _ROLE_PREFIX[reviewer]:
                errors.append(
                    f"finding id {fid!r} does not match reviewer {reviewer!r} "
                    f"(expected prefix {_ROLE_PREFIX[reviewer]!r}) ({label})"
                )

        severity = finding.get("severity")
        if "severity" in finding and (
            not isinstance(severity, str) or severity not in SEVERITIES
        ):
            errors.append(f"invalid severity {_name(severity)} (expected one of {SEVERITIES})")

        dimension = finding.get("rubric_dimension")
        if "rubric_dimension" in finding and (
            not isinstance(dimension, str) or dimension not in DIMENSIONS
        ):
            errors.append(
                f"invalid rubric_dimension {_name(dimension)} (expected one of {DIMENSIONS})"
            )

        reference = finding.get("artifact_reference")
        if "artifact_reference" in finding and (
            not isinstance(reference, str) or not _ARTIFACT_RE.fullmatch(reference)
        ):
            errors.append(
                f"invalid artifact_reference {_name(reference)} "
                "(expected 'package:diff@path[:line]' or 'path[:line]')"
            )

        for field in ("evidence", "required_change"):
            value = finding.get(field)
            if field in finding and (
                not isinstance(value, str) or not value.strip()
            ):
                errors.append(f"{field} must be a non-empty string ({label})")

        actionable = finding.get("actionable")
        if "actionable" in finding and not isinstance(actionable, bool):
            errors.append(
                f"actionable must be a true/false boolean, got {_name(actionable)} "
                f"({label})"
            )
    return errors


# -- normalization and IO --------------------------------------------------

def normalize(doc: dict) -> dict:
    """Return a copy with fixed key order and findings sorted by id.

    The top-level ``schema`` key is always included (= SCHEMA).
    """
    review = doc.get("review")
    review = review if isinstance(review, dict) else {}
    out_review: dict = {}
    for key in ("reviewer", "rubric_version", "package_sha256", "base_sha", "head_sha"):
        if key in review:
            out_review[key] = review[key]
    if "decision" in review:
        out_review["decision"] = review["decision"]
    findings = review.get("findings")
    if isinstance(findings, list):
        sorted_findings = sorted(
            findings,
            key=lambda f: str(f.get("id", ""))
            if isinstance(f, dict)
            else "",  # id-sorted; non-mappings trail)
        )
        out_review["findings"] = sorted_findings
    elif "findings" in review:
        out_review["findings"] = findings
    if "stop" in review:
        out_review["stop"] = review["stop"] if isinstance(review["stop"], dict) else review["stop"]
    return {"schema": SCHEMA, "review": out_review}


def to_json(doc: dict) -> str:
    """Canonical JSON form (normalized, sorted keys, trailing newline)."""
    return json.dumps(normalize(doc), indent=2, sort_keys=True) + "\n"


def load(path: str | Path) -> dict:
    """Parse (JSON or yamlish), validate, and return the normalized document."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise FindingsError(f"cannot read {p}: {exc.strerror or exc}") from exc
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FindingsError(
                f"invalid JSON document: {exc.msg} (line {exc.lineno}, column {exc.colno})"
            ) from exc
    else:
        try:
            doc = yamlish_loads(text)
        except YamlishError as exc:
            raise FindingsError(f"invalid YAML document: {exc}") from exc
    errors = validate(doc)
    if errors:
        raise FindingsError(
            f"invalid review findings document ({len(errors)} error(s)):\n"
            + "\n".join(f"  - {err}" for err in errors),
            errors=errors,
        )
    return normalize(doc)


_USAGE = (
    "usage: review-findings validate <file> [<file> ...]\n"
    "       review-findings json <file>"
)


def main(argv: list[str]) -> int:
    """validate/json CLI. Exit codes: 0 ok, 1 refusal/findings, 2 usage."""
    if len(argv) < 3 or argv[1] not in ("validate", "json"):
        print(_USAGE, file=sys.stderr)
        return 2
    cmd, paths = argv[1], argv[2:]
    if cmd == "json":
        if len(paths) != 1:
            print(_USAGE, file=sys.stderr)
            return 2
        try:
            print(to_json(load(paths[0])), end="")
        except FindingsError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    ok = True
    for path in paths:
        try:
            doc = load(path)
        except FindingsError as exc:
            ok = False
            errs = exc.errors if exc.errors else [exc.message]
            for err in errs:
                print(f"  - {path}: {err}", file=sys.stderr)
            continue
        findings = doc.get("review", {}).get("findings", [])
        count = len(findings) if isinstance(findings, list) else 0
        print(f"review findings: OK ({count} finding(s), {path})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))