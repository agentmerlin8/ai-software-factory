"""score — the review calibration bench scorer for the kit.

The bench holds ground-truth review cases under ``cases/``: each case is
a self-contained artifact (the review input) plus the findings a correct
review would produce (the answer key).  One lens role writes one findings
document (``review.findings.v1``); the scorer validates every input,
attributes each produced finding to a case via the path component right
after ``artifacts/`` in its artifact_reference, matches on case + artifact
file + rubric_dimension, and reports per-lens and combined metrics.

CLI (``python3 tools/review/bench/score.py ...``)
- ``--bench DIR`` — the bench directory (must contain ``cases/``).
- ``--outputs ROLE=FILE [--outputs ROLE=FILE ...]`` — one findings
  document per role; ROLE must be one of findings.ROLES.  The combined
  block is the deliverable the lane is calibrated against, so all three
  lens roles (``review-contract``, ``review-adversary``,
  ``review-security``) are required and a partial panel is a named
  refusal (exit 1).
- ``--json`` — print a single ``review.bench.v1`` object (sorted keys).

Exit codes: 0 = inputs parsed and metrics printed (metrics are data, so
0 regardless of how few findings matched); 1 = unreadable or invalid
input (bench, case file, or findings document) or a partial panel;
2 = usage errors.

Matching rule
-------------
A produced finding names a case because the path component right after
``artifacts/`` in its artifact_reference is the case id; a reference like
``tools/review/bench/cases/artifacts/fn18/x.md:3`` attributes to the same
case as ``artifacts/fn18/x.md:3``.  It matches one expected finding when
case id, artifact tail, and rubric_dimension all agree.  Line numbers do
not participate in the match.  Severity does not participate in the
strict match, but it gates every protection count: strict critical/major
recall requires the matched produced finding itself to be blocker/major,
approval-escape protection is computed per expected finding location (a
produced blocker/major at a clean or wrong file protects nothing), and
unnecessary revisions are counted over every case without an expected
blocker/major finding.  Location-level detection pairs produced and
expected findings one-to-one per artifact file: one produced finding
cannot cover two expected findings at the same file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):  # direct CLI: python3 tools/review/bench/score.py
    # bench/ sits one level below its sibling review modules (whose probe
    # uses parents[2] from tools/review/); the import root is the kit
    # root, which is four parents up from this file.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.review.findings import DIMENSIONS, ROLES, SEVERITIES, FindingsError
from tools.review.findings import load as load_findings

SCHEMA = "review.bench.v1"
CRITICAL_MAJOR = ("blocker", "major")
LENS_ROLES = ("review-contract", "review-adversary", "review-security")
_EXPECTED_ID_RE = re.compile(r"^E[0-9]+$")
_REFERENCE_RE = re.compile(r"^(?P<path>.+):(?P<line>[0-9]+)$")
_LINE_SUFFIX_RE = re.compile(r"(:\d+)$")
_PACKAGE_PREFIX = "package:diff@"

METRIC_KEYS = (
    "produced",
    "matched",
    "precision",
    "recall",
    "critical_major_expected",
    "critical_major_matched",
    "critical_major_recall",
    "clean_cases",
    "unnecessary_revisions",
    "unnecessary_revision_rate",
    "defective_cases",
    "approval_escapes",
    "approval_escape_rate",
)


@dataclass(frozen=True)
class Expected:
    id: str
    severity: str
    rubric_dimension: str
    artifact_reference: str
    line: int
    tail: str


@dataclass(frozen=True)
class BenchCase:
    id: str
    title: str
    provenance: str
    artifact: str
    expected: tuple[Expected, ...]

    @property
    def defective(self) -> bool:
        return any(e.severity in CRITICAL_MAJOR for e in self.expected)


@dataclass(frozen=True)
class Produced:
    lens: str
    severity: str
    rubric_dimension: str
    artifact_reference: str
    case_id: str | None
    tail: str | None


class BenchError(ValueError):
    """A named refusal from the bench scorer."""

    def __str__(self) -> str:
        return f"review bench: {self.args[0]}"


# -- bench loading ---------------------------------------------------------

def load_bench(bench_dir: str | Path) -> dict[str, BenchCase]:
    """Load and validate every case under ``<bench_dir>/cases/``.

    Refusals (BenchError) name the failing case file: unreadable JSON, a
    case id that disagrees with its filename, an artifact that does not
    resolve, a malformed expected finding, or a duplicate match key.
    """
    root = Path(bench_dir)
    cases_dir = root / "cases"
    if not cases_dir.is_dir():
        raise BenchError(f"cannot read bench {root}: missing cases/ directory")
    cases_root = cases_dir.resolve()
    cases: dict[str, BenchCase] = {}
    expected_keys: set[tuple[str, str, str]] = set()
    for case_path in sorted(cases_dir.glob("*.json")):
        case_id = case_path.stem
        try:
            doc = json.loads(case_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise BenchError(
                f"cannot read case file {case_path}: {exc.strerror or exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise BenchError(
                f"invalid case file {case_path}: invalid JSON "
                f"(line {exc.lineno}, column {exc.colno})"
            ) from exc
        if not isinstance(doc, dict):
            raise BenchError(f"invalid case file {case_path}: document must be a mapping")
        missing = [key for key in ("id", "title", "provenance", "artifact", "expected")
                   if key not in doc]
        if missing:
            raise BenchError(f"invalid case file {case_path}: missing key(s) {missing}")
        if doc["id"] != case_id:
            raise BenchError(f"invalid case file {case_path}: id {doc['id']!r} != filename {case_id!r}")
        if not isinstance(doc["title"], str) or not isinstance(doc["provenance"], str):
            raise BenchError(f"invalid case {case_id}: title and provenance must be strings")
        artifact = doc["artifact"]
        if not isinstance(artifact, str) or not artifact.startswith("artifacts/"):
            raise BenchError(f"invalid case {case_id}: artifact must be an artifacts/... path")
        parts = artifact.split("/")
        if len(parts) < 3 or parts[1] != case_id:
            raise BenchError(
                f"invalid case {case_id}: artifact {artifact!r} must live in artifacts/{case_id}/"
            )
        artifact_target = (cases_dir / artifact).resolve()
        if not artifact_target.is_relative_to(cases_root):
            raise BenchError(
                f"invalid case {case_id}: artifact path {artifact!r} escapes the cases directory"
            )
        expected_doc = doc["expected"]
        if not isinstance(expected_doc, dict):
            raise BenchError(f"invalid case {case_id}: 'expected' must be a mapping")
        findings_raw = expected_doc.get("findings")
        if not isinstance(findings_raw, list):
            raise BenchError(f"invalid case {case_id}: expected findings must be a list")
        expected: list[Expected] = []
        seen_ids: set[str] = set()
        for idx, raw in enumerate(findings_raw):
            label = f"case {case_id} expected finding {idx}"
            if not isinstance(raw, dict):
                raise BenchError(f"invalid {label}: must be a mapping")
            for key in ("id", "severity", "rubric_dimension", "artifact_reference"):
                if key not in raw:
                    raise BenchError(f"invalid {label}: missing key {key!r}")
            fid = raw["id"]
            if not isinstance(fid, str) or not _EXPECTED_ID_RE.fullmatch(fid):
                raise BenchError(f"invalid {label}: id {fid!r} (expected E<number>)")
            if fid in seen_ids:
                raise BenchError(f"invalid {label}: duplicate id {fid!r}")
            seen_ids.add(fid)
            if raw["severity"] not in SEVERITIES:
                raise BenchError(
                    f"invalid {label}: severity {raw['severity']!r} (expected one of {SEVERITIES})"
                )
            dimension = raw["rubric_dimension"]
            if dimension not in DIMENSIONS:
                raise BenchError(
                    f"invalid {label}: rubric_dimension {dimension!r} (expected one of {DIMENSIONS})"
                )
            reference = raw["artifact_reference"]
            m = _REFERENCE_RE.fullmatch(reference) if isinstance(reference, str) else None
            if m is None:
                raise BenchError(
                    f"invalid {label}: artifact_reference {reference!r} (expected '<path>:<line>')"
                )
            ref_path, line = m.group("path"), int(m.group("line"))
            if not ref_path.startswith(f"artifacts/{case_id}/"):
                raise BenchError(
                    f"invalid {label}: artifact_reference path {ref_path!r} "
                    f"must live in artifacts/{case_id}/"
                )
            # The string-prefix check is not a confinement: resolve the
            # path and refuse traversal outside the cases directory before
            # anything is read (a ``../`` segment passes the prefix).
            target = (cases_dir / ref_path).resolve()
            if not target.is_relative_to(cases_root):
                raise BenchError(
                    f"invalid {label}: artifact_reference path {ref_path!r} "
                    "escapes the cases directory"
                )
            if not target.is_file():
                raise BenchError(f"invalid {label}: artifact_reference file not found: {ref_path}")
            if line < 1 or line > len(target.read_text(encoding="utf-8").splitlines()):
                raise BenchError(
                    f"invalid {label}: artifact_reference line {line} out of range for {ref_path}"
                )
            tail = ref_path[len("artifacts/"):]
            key = (case_id, tail, dimension)
            if key in expected_keys:
                raise BenchError(
                    f"invalid {label}: duplicate match key (case, artifact file, rubric_dimension)"
                )
            expected_keys.add(key)
            expected.append(Expected(fid, raw["severity"], dimension, reference, line, tail))
        if not artifact_target.is_file():
            raise BenchError(f"invalid case {case_id}: artifact not found: {artifact}")
        cases[case_id] = BenchCase(
            case_id, doc["title"], doc["provenance"], artifact, tuple(expected)
        )
    if not cases:
        raise BenchError(f"cannot read bench {root}: no case files in {cases_dir}")
    return cases


# -- produced findings -----------------------------------------------------

def _split_reference(reference: str) -> tuple[str, int | None]:
    """Split a findings artifact_reference into (path, line|None)."""
    path = reference
    if path.startswith(_PACKAGE_PREFIX):
        path = path[len(_PACKAGE_PREFIX):]
    m = _LINE_SUFFIX_RE.search(path)
    if m:
        return path[: m.start()], int(m.group(1)[1:])
    return path, None


def _case_of(path: str) -> tuple[str, str] | None:
    """(case id, artifact tail) from a path, via the component right
    after the ``artifacts/`` segment; None when unattributable."""
    segments = path.split("/")
    for idx, segment in enumerate(segments):
        if segment == "artifacts" and idx + 1 < len(segments):
            return segments[idx + 1], "/".join(segments[idx + 1:])
    return None


def load_lens_output(role: str, path: str | Path) -> list[Produced]:
    """Load and validate one lens findings document; attribute findings.

    Raises FindingsError when the file is unreadable, fails
    ``review.findings.v1`` validation, or was written by a reviewer
    other than `role`.  A stopped review (shape B) is refused by
    BenchError: scoring it as an empty findings list would count a
    reviewer that never ran as a clean zero.
    """
    doc = load_findings(path)
    review = doc["review"]
    if "stop" in review:
        stop = review.get("stop")
        rule = stop.get("rule") if isinstance(stop, dict) else None
        raise BenchError(
            f"lens {role}: findings document {path} is a stopped review "
            f"({rule})"
        )
    reviewer = review.get("reviewer")
    if reviewer != role:
        raise FindingsError(
            f"lens {role}: findings document {path} was written by {reviewer!r}"
        )
    produced: list[Produced] = []
    findings = doc["review"].get("findings")
    if isinstance(findings, list):
        for finding in findings:
            reference = finding["artifact_reference"]
            file_path, _line = _split_reference(reference)
            case_key = _case_of(file_path)
            case_id = tail = None
            if case_key is not None:
                case_id, tail = case_key
            produced.append(
                Produced(
                    lens=role,
                    severity=finding["severity"],
                    rubric_dimension=finding["rubric_dimension"],
                    artifact_reference=reference,
                    case_id=case_id,
                    tail=tail,
                )
            )
    return produced


# -- metrics ---------------------------------------------------------------

def _critical_ids(produced: list[Produced]) -> set[str]:
    """Case ids touched by any produced blocker/major (any tail)."""
    return {
        finding.case_id
        for finding in produced
        if finding.case_id is not None and finding.severity in CRITICAL_MAJOR
    }


def _covered_locations(produced: list[Produced]) -> dict[str, set[str]]:
    """case id -> artifact tails carrying a produced blocker/major."""
    covered: dict[str, set[str]] = {}
    for finding in produced:
        if (finding.case_id is not None and finding.tail is not None
                and finding.severity in CRITICAL_MAJOR):
            covered.setdefault(finding.case_id, set()).add(finding.tail)
    return covered


def _escape_cases(cases: dict[str, BenchCase],
                  covered: dict[str, set[str]]) -> int:
    """Approval escapes, computed per expected finding location.

    A defective case escapes when no produced blocker/major sits at any
    of the case's expected blocker/major artifact locations: a produced
    blocker/major at a clean file (or any other file of the case)
    protects nothing.
    """
    escapes = 0
    for case in cases.values():
        if not case.defective:
            continue
        expected_locations = {item.tail for item in case.expected
                              if item.severity in CRITICAL_MAJOR}
        if not (expected_locations & covered.get(case.id, set())):
            escapes += 1
    return escapes


def _metrics(
    cases: dict[str, BenchCase],
    expected_index: dict[tuple[str, str, str], Expected],
    produced: list[Produced],
    matched: set[tuple[str, str, str]],
    matched_cm: set[tuple[str, str, str]],
    critical: set[str],
) -> dict:
    produced_n = len(produced)
    matched_n = len(matched)
    expected_total = sum(len(case.expected) for case in cases.values())
    cm_expected = sum(
        1
        for case in cases.values()
        for item in case.expected
        if item.severity in CRITICAL_MAJOR
    )
    # Strict critical/major recall counts a match only when the produced
    # finding that made it was itself blocker/major: matching an expected
    # major with a produced info/minor is a match, but not a catch.
    cm_matched = sum(
        1
        for key in matched
        if key in matched_cm and expected_index[key].severity in CRITICAL_MAJOR
    )
    clean_cases = sum(1 for case in cases.values() if not case.expected)
    defective_cases = sum(1 for case in cases.values() if case.defective)
    # The unnecessary-revision denominator is every case WITHOUT an
    # expected blocker/major: clean cases AND minor-only cases.  A
    # minor-only case is approvable under the verdict policy, but
    # requesting changes on one is still an unnecessary revision.
    no_cm_cases = sum(1 for case in cases.values() if not case.defective)
    unnecessary = sum(
        1 for case in cases.values() if not case.defective and case.id in critical
    )
    escapes = _escape_cases(cases, _covered_locations(produced))
    return {
        "produced": produced_n,
        "matched": matched_n,
        "precision": round(matched_n / produced_n, 4) if produced_n else 0.0,
        "recall": round(matched_n / expected_total, 4) if expected_total else 0.0,
        "critical_major_expected": cm_expected,
        "critical_major_matched": cm_matched,
        "critical_major_recall": round(cm_matched / cm_expected, 4) if cm_expected else 0.0,
        "clean_cases": clean_cases,
        "unnecessary_revisions": unnecessary,
        "unnecessary_revision_rate": (round(unnecessary / no_cm_cases, 4)
                                      if no_cm_cases else 0.0),
        "defective_cases": defective_cases,
        "approval_escapes": escapes,
        "approval_escape_rate": (round(escapes / defective_cases, 4)
                                 if defective_cases else 0.0),
    }


def evaluate(
    cases: dict[str, BenchCase], outputs: dict[str, list[Produced]]
) -> dict:
    """Score every lens and the combined view.

    Combined matching and critical coverage are the unions of the lens
    values; strict critical/major recall counts a match only when the
    produced finding that made it was blocker/major, an approval escape
    is computed per expected finding location, and the combined block
    assumes the caller provided all three lens roles (the CLI enforces
    it with a named refusal).  ``report['lenses']`` and
    ``report['combined']`` carry the metric dicts; decisions are derived
    from the combined critical set — any produced blocker/major in a case
    requests changes, the same rule the record verdict uses.
    """
    expected_index: dict[tuple[str, str, str], Expected] = {}
    for case in cases.values():
        for item in case.expected:
            expected_index[(case.id, item.tail, item.rubric_dimension)] = item
    lens_order = sorted(outputs, key=lambda role: ROLES.index(role))
    lens_metrics: dict[str, dict] = {}
    lens_matched: dict[str, set] = {}
    lens_matched_cm: dict[str, set] = {}
    lens_critical: dict[str, set[str]] = {}
    all_produced: list[Produced] = []
    for role in lens_order:
        produced = outputs[role]
        matched: set = set()
        matched_cm: set = set()
        for finding in produced:
            if finding.case_id is None or finding.tail is None:
                continue
            key = (finding.case_id, finding.tail, finding.rubric_dimension)
            if key in expected_index and key not in matched:
                matched.add(key)
                if finding.severity in CRITICAL_MAJOR:
                    matched_cm.add(key)
        lens_metrics[role] = _metrics(
            cases, expected_index, produced, matched, matched_cm,
            _critical_ids(produced)
        )
        lens_metrics[role]["location"] = _location_metrics(cases, produced)
        lens_matched[role] = matched
        lens_matched_cm[role] = matched_cm
        lens_critical[role] = _critical_ids(produced)
        all_produced.extend(produced)
    matched_union: set = set()
    matched_cm_union: set = set()
    critical_union: set[str] = set()
    for role in lens_order:
        matched_union |= lens_matched[role]
        matched_cm_union |= lens_matched_cm[role]
        critical_union |= lens_critical[role]
    combined = _metrics(cases, expected_index, all_produced, matched_union,
                        matched_cm_union, critical_union)
    combined["location"] = _location_metrics(cases, all_produced)
    decisions = {
        case_id: ("CHANGES_REQUESTED" if case_id in critical_union else "APPROVED")
        for case_id in sorted(cases)
    }
    return {
        "schema": SCHEMA,
        "cases": len(cases),
        "lenses": lens_metrics,
        "combined": combined,
        "lens_order": lens_order,
        "decisions": decisions,
        "defective_cases_map": {case_id: case.defective for case_id, case in cases.items()},
    }


_SEVERITY_ORDER = ("info", "minor", "major", "blocker")


def _location_metrics(cases: dict[str, BenchCase],
                      produced: list[Produced]) -> dict:
    """Detection metrics at (case, artifact-file) granularity.

    The strict metrics above require the rubric dimension to agree, which
    measures tag choice as much as detection: the same defect reported under
    a neighbouring lens's dimension counts as a miss there. This block asks
    the narrower question the lane actually owes — did some lens raise a
    finding at the right artifact? — and reports dimension/severity
    agreement separately.

    Produced findings are consumed one-to-one per location: each expected
    finding pairs with a DISTINCT produced finding (dimension-agreeing ones
    first, so tag agreement is preserved when it exists), and one produced
    finding can never cover two expected findings at the same file.
    """
    expected_at: dict[tuple[str, str], list[Expected]] = {}
    for case in cases.values():
        for item in case.expected:
            expected_at.setdefault((case.id, item.tail), []).append(item)
    produced_at: dict[tuple[str, str], list[Produced]] = {}
    for finding in produced:
        if finding.case_id is None or finding.tail is None:
            continue
        produced_at.setdefault((finding.case_id, finding.tail), []).append(finding)

    expected_total = sum(len(items) for items in expected_at.values())
    cm_expected = sum(
        1 for items in expected_at.values() for item in items
        if item.severity in CRITICAL_MAJOR
    )
    detected = cm_detected = dim_ok = sev_ok = 0
    for key, items in expected_at.items():
        unused = produced_at.get(key, [])
        for expected in items:
            if not unused:
                break
            index = next(
                (i for i, finding in enumerate(unused)
                 if finding.rubric_dimension == expected.rubric_dimension),
                0,
            )
            match = unused.pop(index)
            detected += 1
            if (expected.severity in CRITICAL_MAJOR
                    and match.severity in CRITICAL_MAJOR):
                cm_detected += 1
            if match.rubric_dimension == expected.rubric_dimension:
                dim_ok += 1
            idx = _SEVERITY_ORDER.index(expected.severity)
            adjacent = {_SEVERITY_ORDER[i] for i in (idx - 1, idx, idx + 1)
                        if 0 <= i < len(_SEVERITY_ORDER)}
            if match.severity in adjacent:
                sev_ok += 1
    return {
        "produced": len(produced),
        "expected": expected_total,
        "detected": detected,
        "detection_recall": round(detected / expected_total, 4) if expected_total else 0.0,
        "detection_precision": (round(detected / len(produced), 4)
                                if produced else 0.0),
        "critical_major_expected": cm_expected,
        "critical_major_detected": cm_detected,
        "critical_major_detection_recall": (round(cm_detected / cm_expected, 4)
                                            if cm_expected else 0.0),
        "dimension_agreement": round(dim_ok / detected, 4) if detected else 0.0,
        "severity_agreement": round(sev_ok / detected, 4) if detected else 0.0,
    }

# -- rendering -------------------------------------------------------------

def render_text(bench_label: str, report: dict) -> str:
    """Human-readable per-lens table plus the combined block and the
    derived per-case decisions."""

    def cell(value: object) -> str:
        return f"{value:.4f}" if isinstance(value, float) else str(value)

    header = ["lens", *METRIC_KEYS]
    rows: list[list[str]] = []
    for role in report["lens_order"]:
        rows.append([role, *[cell(report["lenses"][role][key]) for key in METRIC_KEYS]])
    rows.append(["combined", *[cell(report["combined"][key]) for key in METRIC_KEYS]])
    widths = [
        max(len(str(row[i])) for row in [header, *rows]) for i in range(len(header))
    ]
    lines = [
        f"review bench: {bench_label}",
        (
            f"cases: {report['cases']} "
            f"(with expected blocker/major {report['combined']['defective_cases']}, "
            f"without {report['cases'] - report['combined']['defective_cases']})"
        ),
        "",
    ]
    for row in [header, *rows]:
        lines.append("  ".join(str(row[i]).rjust(widths[i]) for i in range(len(row))))
    lines.append("")
    lines.append("case decisions (combined)")
    for case_id in sorted(report["decisions"]):
        decision = report["decisions"][case_id]
        kind = "defective" if report["defective_cases_map"][case_id] else "clean"
        suffix = ""
        if kind == "defective" and decision == "APPROVED":
            suffix = " (escaped)"
        elif kind == "clean" and decision == "CHANGES_REQUESTED":
            suffix = " (unnecessary)"
        lines.append(f"{case_id}  {kind}  {decision}{suffix}")
    return "\n".join(lines)


# -- CLI -------------------------------------------------------------------

def _usage_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review-bench-score",
        description="Score review lens outputs against the calibration bench.",
    )
    parser.add_argument("--bench", required=True, metavar="DIR",
                        help="bench directory (must contain cases/)")
    parser.add_argument("--outputs", action="append", default=[], metavar="ROLE=FILE",
                        help="one findings document per lens role (repeatable)")
    parser.add_argument("--json", action="store_true",
                        help="print a single review.bench.v1 JSON object")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Bench scoring CLI. Exit codes: 0 ok, 1 invalid input, 2 usage."""
    parser = _usage_parser()
    args = parser.parse_args(argv)
    outputs: dict[str, str] = {}
    for pair in args.outputs:
        role, sep, path = pair.partition("=")
        if not sep or not role or not path:
            parser.error(f"invalid --outputs {pair!r} (expected ROLE=FILE)")
        if role not in ROLES:
            parser.error(f"unknown role {role!r} (expected one of {', '.join(ROLES)})")
        if role in outputs:
            parser.error(f"duplicate role {role!r}")
        outputs[role] = path
    if not outputs:
        parser.error("at least one --outputs ROLE=FILE is required")
    # The combined block is the point of the calibration: it refuses a
    # partial panel, so no run can silently under-report the union.
    missing = [role for role in LENS_ROLES if role not in outputs]
    if missing:
        print(
            "review bench: combined block requires all three lens roles "
            f"({', '.join(LENS_ROLES)}): missing {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1
    try:
        cases = load_bench(args.bench)
        produced_by_role = {
            role: load_lens_output(role, path) for role, path in outputs.items()
        }
    except (BenchError, FindingsError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    report = evaluate(cases, produced_by_role)
    if args.json:
        payload = {key: report[key] for key in ("schema", "cases", "lenses", "combined")}
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_text(args.bench, report))
    return 0


if __name__ == "__main__":
    sys.exit(main())