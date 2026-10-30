"""Unit tests for tools.review.bench.score (synthetic + real bench).

Covers exact per-lens metric numbers on a 3-case synthetic bench,
duplicate/combined/match-rule semantics, exit codes (0/1/2), direct
file-path subprocess invocation from an isolated cwd, and the integrity
of the shipped 17-case bench.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.review import findings
from tools.review.bench import score as bench_score
from tools.review.bench.score import BenchError, FindingsError

ROOT = Path(__file__).resolve().parents[3]
SCORE = ROOT / "tools" / "review" / "bench" / "score.py"
REAL_BENCH = ROOT / "tools" / "review" / "bench"

PREFIX = {
    "review-contract": "RC",
    "review-adversary": "RA",
    "review-security": "RS",
    "external": "EX",
}


def finding(reviewer: str, num: int, severity: str, dimension: str, reference: str) -> dict:
    return {
        "id": f"{PREFIX[reviewer]}-{num:03d}",
        "severity": severity,
        "rubric_dimension": dimension,
        "artifact_reference": reference,
        "evidence": "observed in the packaged artifact",
        "required_change": "align with the rubric",
        "actionable": True,
    }


def lens_file(path: Path, reviewer: str, items: list[dict], stop: dict | None = None) -> Path:
    review = {
        "reviewer": reviewer,
        "rubric_version": f"{reviewer}-v1",
        "package_sha256": "a" * 64,
        "base_sha": "b" * 40,
        "head_sha": "c" * 40,
    }
    if stop is not None:
        review["stop"] = stop
    else:
        review["decision"] = "CHANGES_REQUESTED" if items else "APPROVED"
        review["findings"] = items
    path.write_text(findings.to_json({"review": review}), encoding="utf-8")
    return path


def case_file(path: Path, case_id: str, title: str, artifact: str, expected: list[dict]) -> Path:
    doc = {
        "id": case_id,
        "title": title,
        "provenance": "synthetic",
        "artifact": artifact,
        "expected": {"findings": expected},
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def strict(metrics: dict) -> dict:
    """Strict metrics only; the additive location block is asserted separately."""
    return {key: value for key, value in metrics.items() if key != "location"}


class SyntheticBench:
    """A 3-case bench: one expected major found, one missed, one clean."""

    def __init__(self, base: Path):
        self.base = base
        cases = base / "cases"
        arts = cases / "artifacts"
        files = {
            "s1": ("a.md", ["# s1 artifact\n", "defective statement here\n", "tail\n"]),
            "s2": ("b.md", ["# s2\n", "middle\n", "the defect line\n"]),
            "s3": ("c.md", ["# s3\n", "clean body\n", "tail\n"]),
        }
        specs = [
            ("s1", "case s1", "artifacts/s1/a.md",
             [{"id": "E01", "severity": "major", "rubric_dimension": "behavior",
               "artifact_reference": "artifacts/s1/a.md:2"}]),
            ("s2", "case s2", "artifacts/s2/b.md",
             [{"id": "E01", "severity": "major", "rubric_dimension": "test_quality",
               "artifact_reference": "artifacts/s2/b.md:3"}]),
            ("s3", "case s3", "artifacts/s3/c.md", []),
        ]
        for case_id, title, artifact, expected in specs:
            (arts / case_id).mkdir(parents=True, exist_ok=True)
            name, lines = files[case_id]
            (arts / case_id / name).write_text("".join(lines), encoding="utf-8")
            case_file(cases / f"{case_id}.json", case_id, title, artifact, expected)
        self.lens_a = lens_file(
            base / "adversary.json",
            "review-adversary",
            [
                finding("review-adversary", 1, "major", "behavior", "artifacts/s1/a.md:2"),
                finding("review-adversary", 2, "blocker", "operational", "artifacts/s3/c.md:1"),
            ],
        )
        self.lens_s = lens_file(
            base / "security.json",
            "review-security",
            [finding("review-security", 1, "major", "test_quality", "artifacts/s2/b.md:3")],
        )
        self.lens_c = lens_file(
            base / "contract.json",
            "review-contract",
            [],
        )

    def evaluate(self) -> dict:
        cases = bench_score.load_bench(self.base)
        outputs = {
            "review-adversary": bench_score.load_lens_output("review-adversary", self.lens_a),
            "review-security": bench_score.load_lens_output("review-security", self.lens_s),
        }
        return bench_score.evaluate(cases, outputs)


class ScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.bench = SyntheticBench(Path(self._tmp.name) / "synth")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _report(self) -> dict:
        return self.bench.evaluate()

    def test_lens_adversary_metrics_exact(self):
        self.assertEqual(
            strict(self._report()["lenses"]["review-adversary"]),
            {
                "produced": 2,
                "matched": 1,
                "precision": 0.5,
                "recall": 0.5,
                "critical_major_expected": 2,
                "critical_major_matched": 1,
                "critical_major_recall": 0.5,
                "clean_cases": 1,
                "unnecessary_revisions": 1,
                "unnecessary_revision_rate": 1.0,
                "defective_cases": 2,
                "approval_escapes": 1,
                "approval_escape_rate": 0.5,
            },
        )

    def test_lens_security_metrics_exact(self):
        self.assertEqual(
            strict(self._report()["lenses"]["review-security"]),
            {
                "produced": 1,
                "matched": 1,
                "precision": 1.0,
                "recall": 0.5,
                "critical_major_expected": 2,
                "critical_major_matched": 1,
                "critical_major_recall": 0.5,
                "clean_cases": 1,
                "unnecessary_revisions": 0,
                "unnecessary_revision_rate": 0.0,
                "defective_cases": 2,
                "approval_escapes": 1,
                "approval_escape_rate": 0.5,
            },
        )

    def test_combined_union_metrics_exact(self):
        report = self._report()
        self.assertEqual(
            strict(report["combined"]),
            {
                "produced": 3,
                "matched": 2,
                "precision": 0.6667,
                "recall": 1.0,
                "critical_major_expected": 2,
                "critical_major_matched": 2,
                "critical_major_recall": 1.0,
                "clean_cases": 1,
                "unnecessary_revisions": 1,
                "unnecessary_revision_rate": 1.0,
                "defective_cases": 2,
                "approval_escapes": 0,
                "approval_escape_rate": 0.0,
            },
        )
        # s2 is found by only one lens and still counts as found; the
        # escape disappears only because no lens misses it any more.
        self.assertEqual(
            report["decisions"],
            {"s1": "CHANGES_REQUESTED", "s2": "CHANGES_REQUESTED", "s3": "CHANGES_REQUESTED"},
        )
        self.assertEqual(
            report["defective_cases_map"],
            {"s1": True, "s2": True, "s3": False},
        )

    def test_per_lens_escape_and_combined_union(self):
        # With only the adversary lens, s2 escapes; s1's produced major by
        # the adversary protects s1, and s3's false blocker is unnecessary.
        report = self._report()
        self.assertEqual(report["lenses"]["review-adversary"]["approval_escapes"], 1)
        self.assertEqual(report["lenses"]["review-security"]["approval_escapes"], 1)
        # A case escapes only if NO lens raised blocker/major: combined
        # escapes are 0 even though each lens individually missed one case.
        self.assertEqual(report["combined"]["approval_escapes"], 0)

    def test_duplicate_produced_lower_precision(self):
        dup = lens_file(
            Path(self._tmp.name) / "dup.json",
            "review-adversary",
            [
                finding("review-adversary", 1, "major", "behavior", "artifacts/s1/a.md:2"),
                finding("review-adversary", 2, "major", "behavior", "artifacts/s1/a.md:3"),
            ],
        )
        cases = bench_score.load_bench(self.bench.base)
        produced = bench_score.load_lens_output("review-adversary", dup)
        metrics = bench_score.evaluate(cases, {"review-adversary": produced})["lenses"][
            "review-adversary"
        ]
        self.assertEqual(metrics["produced"], 2)
        self.assertEqual(metrics["matched"], 1)
        self.assertEqual(metrics["precision"], 0.5)
        self.assertEqual(metrics["recall"], 0.5)

    def test_matched_info_severity_counts_no_critical_major_credit(self):
        """Strict critical/major credit requires the produced finding that
        made the match to be blocker/major: an info severity match is a
        match, but not a catch, and it protects no defective case."""
        low = lens_file(
            Path(self._tmp.name) / "info.json",
            "review-adversary",
            [finding("review-adversary", 1, "info", "behavior", "artifacts/s1/a.md:2")],
        )
        cases = bench_score.load_bench(self.bench.base)
        produced = bench_score.load_lens_output("review-adversary", low)
        metrics = bench_score.evaluate(cases, {"review-adversary": produced})["lenses"][
            "review-adversary"
        ]
        self.assertEqual(metrics["matched"], 1)          # key match ignores severity
        self.assertEqual(metrics["critical_major_matched"], 0)  # info earns no credit
        self.assertEqual(metrics["critical_major_recall"], 0.0)
        self.assertEqual(metrics["approval_escapes"], 2)  # info protects nobody
        self.assertEqual(metrics["approval_escape_rate"], 1.0)

    def test_unattributable_reference_counts_but_never_matches(self):
        stray = lens_file(
            Path(self._tmp.name) / "stray.json",
            "review-adversary",
            [finding("review-adversary", 1, "major", "behavior", "onboarding/forge_probe.py:3")],
        )
        cases = bench_score.load_bench(self.bench.base)
        produced = bench_score.load_lens_output("review-adversary", stray)
        metrics = bench_score.evaluate(cases, {"review-adversary": produced})["lenses"][
            "review-adversary"
        ]
        self.assertEqual(metrics["produced"], 1)
        self.assertEqual(metrics["matched"], 0)
        self.assertEqual(metrics["precision"], 0.0)
        self.assertEqual(metrics["approval_escapes"], 2)

    def test_stop_shape_refused_by_name(self):
        """F6: a stopped lens is refused by name, not scored as zero metrics
        (a zero-finding score would read as a clean reviewer that never
        ran)."""
        stopped = lens_file(
            Path(self._tmp.name) / "stopped.json",
            "review-security",
            [],
            stop={"rule": "package-hash-mismatch", "detail": "hash does not match the package"},
        )
        with self.assertRaises(BenchError) as ctx:
            bench_score.load_lens_output("review-security", stopped)
        message = str(ctx.exception)
        self.assertIn("review bench: ", message)
        self.assertIn("lens review-security", message)
        self.assertIn("stopped review", message)
        self.assertIn("package-hash-mismatch", message)

    def test_bench_refusals(self):
        base = Path(self._tmp.name)

        def load(dirpath: Path):
            bench_score.load_bench(dirpath)

        # line out of range
        bad_line = base / "bad-line"
        bench_score.load_bench(self.bench.base)  # ensure fixture parses first
        self._write_clone(bad_line, s1_line=99)
        with self.assertRaisesRegex(BenchError, "line 99 out of range"):
            load(bad_line)
        # artifact missing
        missing = base / "missing"
        self._write_clone(missing, s1_artifact="artifacts/s1/nope.md")
        with self.assertRaisesRegex(BenchError, "artifact not found"):
            load(missing)
        # id does not match filename
        renamed = base / "renamed"
        self._write_clone(renamed, s1_id="s9")
        with self.assertRaisesRegex(BenchError, "!= filename"):
            load(renamed)
        # invalid rubric_dimension
        bad_dim = base / "bad-dim"
        self._write_clone(bad_dim, s1_dimension="vibes")
        with self.assertRaisesRegex(BenchError, "rubric_dimension"):
            load(bad_dim)
        # invalid JSON
        bad_json = base / "bad-json"
        (bad_json / "cases").mkdir(parents=True)
        (bad_json / "cases" / "s1.json").write_text("not json", encoding="utf-8")
        with self.assertRaisesRegex(BenchError, "invalid JSON"):
            load(bad_json)
        # missing cases dir
        empty = base / "empty"
        empty.mkdir()
        with self.assertRaisesRegex(BenchError, "missing cases/"):
            load(empty)

    def _write_clone(self, root: Path, s1_line: int = 2, s1_artifact: str | None = None,
                     s1_id: str = "s1", s1_dimension: str = "behavior") -> None:
        cases = root / "cases"
        arts = cases / "artifacts"
        specs = [
            ("s1", "a.md", ["a\n", "b\n", "c\n"],
             [{"id": "E01", "severity": "major", "rubric_dimension": s1_dimension,
               "artifact_reference": f"artifacts/{s1_id}/a.md:{s1_line}"}],
             s1_artifact or f"artifacts/{s1_id}/a.md", s1_id),
            ("s2", "b.md", ["a\n", "b\n", "c\n"], [], "artifacts/s2/b.md", "s2"),
            ("s3", "c.md", ["a\n", "b\n", "c\n"], [], "artifacts/s3/c.md", "s3"),
        ]
        for case_id, name, lines, expected, artifact, doc_id in specs:
            (arts / case_id).mkdir(parents=True, exist_ok=True)
            (arts / case_id / name).write_text("".join(lines), encoding="utf-8")
            case_file(cases / f"{case_id}.json", doc_id, f"case {case_id}", artifact, expected)

    def test_role_mismatch_refused(self):
        # valid adversary doc given as review-security output
        with self.assertRaisesRegex(FindingsError, "written by 'review-adversary'"):
            bench_score.load_lens_output("review-security", self.bench.lens_a)
        # a doc that does not validate at all
        bad = Path(self._tmp.name) / "bad.json"
        bad.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(FindingsError, "review findings: "):
            bench_score.load_lens_output("review-adversary", bad)


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.bench = SyntheticBench(Path(self._tmp.name) / "synth")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_cli(self, extra: list[str], cwd: Path | None = None,
                bench: Path | None = None) -> subprocess.CompletedProcess:
        argv = [
            sys.executable,
            str(SCORE),
            "--bench",
            str(bench or self.bench.base),
            "--outputs",
            f"review-contract={self.bench.lens_c}",
            "--outputs",
            f"review-adversary={self.bench.lens_a}",
            "--outputs",
            f"review-security={self.bench.lens_s}",
            *extra,
        ]
        return subprocess.run(argv, capture_output=True, text=True, cwd=cwd or ROOT)

    def test_cli_json_exit0_schema_and_sorted_keys(self):
        result = self.run_cli(["--json"])
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["schema"], "review.bench.v1")
        self.assertEqual(data["cases"], 3)
        self.assertEqual(
            list(data["lenses"]),
            ["review-adversary", "review-contract", "review-security"],
        )
        self.assertEqual(list(data), sorted(data))
        self.assertEqual(list(data["lenses"]), sorted(data["lenses"]))
        self.assertEqual(list(data["combined"]), sorted(data["combined"]))
        # canonical serialization: indent=2 sort_keys=True + trailing newline
        self.assertEqual(result.stdout, json.dumps(data, indent=2, sort_keys=True) + "\n")

    def test_cli_text_exit0_readable(self):
        result = self.run_cli([])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("review bench:", result.stdout)
        self.assertIn("combined", result.stdout)
        self.assertIn("review-contract", result.stdout)
        self.assertIn("review-adversary", result.stdout)
        self.assertIn("review-security", result.stdout)
        self.assertIn("with expected blocker/major 2, without 1", result.stdout)
        self.assertIn("0.6667", result.stdout)
        self.assertIn("s2  defective  CHANGES_REQUESTED", result.stdout)
        self.assertIn("s3  clean  CHANGES_REQUESTED (unnecessary)", result.stdout)

    def test_cli_invalid_findings_document_exit1(self):
        bad = Path(self._tmp.name) / "bad.json"
        bad.write_text("{}\n", encoding="utf-8")
        argv = [sys.executable, str(SCORE), "--bench", str(self.bench.base),
                "--outputs", f"review-contract={self.bench.lens_c}",
                "--outputs", f"review-adversary={bad}",
                "--outputs", f"review-security={self.bench.lens_s}",
                "--json"]
        result = subprocess.run(argv, capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 1)
        self.assertIn("review findings: ", result.stderr)

    def test_cli_role_mismatch_exit1(self):
        argv = [sys.executable, str(SCORE), "--bench", str(self.bench.base),
                "--outputs", f"review-contract={self.bench.lens_c}",
                "--outputs", f"review-adversary={self.bench.lens_a}",
                "--outputs", f"review-security={self.bench.lens_a}",
                "--json"]
        result = subprocess.run(argv, capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 1)
        self.assertIn("written by 'review-adversary'", result.stderr)

    def test_cli_stopped_lens_exit1(self):
        """F6: the bench CLI refuses a stopped lens by name, exit 1."""
        stopped = lens_file(
            Path(self._tmp.name) / "stopped.json",
            "review-security", [],
            stop={"rule": "package-hash-mismatch",
                  "detail": "package hashes disagree"},
        )
        argv = [sys.executable, str(SCORE), "--bench", str(self.bench.base),
                "--outputs", f"review-contract={self.bench.lens_c}",
                "--outputs", f"review-adversary={self.bench.lens_a}",
                "--outputs", f"review-security={stopped}",
                "--json"]
        result = subprocess.run(argv, capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 1)
        self.assertIn("review bench: ", result.stderr)
        self.assertIn("lens review-security", result.stderr)
        self.assertIn("stopped review", result.stderr)
        self.assertIn("package-hash-mismatch", result.stderr)

    def test_cli_missing_lens_role_exit1(self):
        # A partial panel is a named refusal: the combined block is the
        # deliverable, so any missing lens role is exit 1, not a report.
        argv = [sys.executable, str(SCORE), "--bench", str(self.bench.base),
                "--outputs", f"review-adversary={self.bench.lens_a}",
                "--outputs", f"review-security={self.bench.lens_s}",
                "--json"]
        result = subprocess.run(argv, capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "review bench: combined block requires all three lens roles",
            result.stderr,
        )
        self.assertIn("missing review-contract", result.stderr)

    def test_cli_unreadable_bench_exit1(self):
        result = self.run_cli(["--json"], bench=Path(self._tmp.name) / "nope")
        self.assertEqual(result.returncode, 1)
        self.assertIn("review bench: ", result.stderr)

    def test_cli_invalid_case_file_exit1(self):
        bench = Path(self._tmp.name) / "synth" / "cases"
        (bench / "s2.json").write_text("{broken", encoding="utf-8")
        result = self.run_cli(["--json"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid JSON", result.stderr)

    def test_cli_usage_errors_exit2(self):
        lens_a = str(self.bench.lens_a)

        def run(argv: list[str]) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, str(SCORE), *argv],
                capture_output=True, text=True, cwd=ROOT,
            )

        cases = [
            ["--bench", str(self.bench.base)],                              # no outputs
            ["--bench", str(self.bench.base), "--outputs", "review-adversary"],  # no =
            ["--bench", str(self.bench.base), "--outputs", f"wat={lens_a}"],    # unknown role
            ["--bench", str(self.bench.base), "--outputs", f"review-adversary={lens_a}",
             "--outputs", f"review-adversary={lens_a}"],                    # duplicate role
            ["--outputs", f"review-adversary={lens_a}", "--json"],          # missing --bench
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                result = run(argv)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("usage", result.stderr)

    def test_direct_file_path_subprocess_isolated_cwd(self):
        # Run the module as a plain file path from an unrelated cwd: the
        # sys.path probe must still find tools.review.findings.
        isolated = Path(self._tmp.name) / "elsewhere"
        isolated.mkdir()
        argv = [
            sys.executable,
            str(SCORE),
            "--bench",
            str(self.bench.base),
            "--outputs",
            f"review-contract={self.bench.lens_c}",
            "--outputs",
            f"review-adversary={self.bench.lens_a}",
            "--outputs",
            f"review-security={self.bench.lens_s}",
            "--json",
        ]
        result = subprocess.run(argv, capture_output=True, text=True, cwd=isolated)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["schema"], "review.bench.v1")
        self.assertEqual(data["cases"], 3)


REQUIRED_CASES = [
    "fn18", "fn19", "fn20", "fn21", "fn22", "fn23", "fn24a", "fn24b",
    "fn24c", "fn25", "pr23-01", "pr23-02", "pr23-03", "pr23-04", "pr23-05",
    "clean-01", "clean-02",
]


class RealBenchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cases_dir = REAL_BENCH / "cases"
        self.cases = bench_score.load_bench(REAL_BENCH)

    def test_exactly_17_cases_unique_ids_filenames(self):
        stems = sorted(p.stem for p in self.cases_dir.glob("*.json"))
        self.assertEqual(stems, sorted(REQUIRED_CASES))
        self.assertEqual(len(self.cases), 17)
        self.assertEqual(len(set(self.cases)), 17)

    def test_every_artifact_path_exists(self):
        for case in self.cases.values():
            self.assertTrue(
                (self.cases_dir / case.artifact).is_file(),
                f"{case.id}: missing artifact {case.artifact}",
            )

    def test_one_artifact_dir_per_case(self):
        dirs = sorted(p.name for p in (self.cases_dir / "artifacts").iterdir())
        self.assertEqual(dirs, sorted(REQUIRED_CASES))

    def test_expected_reference_lines_within_file(self):
        for case in self.cases.values():
            for expected in case.expected:
                path = self.cases_dir / expected.artifact_reference.split(":")[0]
                lines = path.read_text(encoding="utf-8").splitlines()
                self.assertGreaterEqual(expected.line, 1, expected.artifact_reference)
                self.assertLessEqual(expected.line, len(lines), expected.artifact_reference)

    def test_dimensions_severities_and_ids_valid(self):
        for case in self.cases.values():
            for expected in case.expected:
                self.assertIn(expected.rubric_dimension, findings.DIMENSIONS)
                self.assertIn(expected.severity, findings.SEVERITIES)
                self.assertRegex(expected.id, r"^E\d+$")

    def test_clean_cases_empty_and_defective_cases_nonempty(self):
        for case_id in ("clean-01", "clean-02"):
            self.assertEqual(self.cases[case_id].expected, (), case_id)
            self.assertFalse(self.cases[case_id].defective)
        for case in self.cases.values():
            self.assertEqual(not case.expected, case.id in ("clean-01", "clean-02"), case.id)
        # Defective = any expected finding with severity blocker or major;
        # the minor-only docs cases are not defective under this rule.
        for case_id in ("fn23", "pr23-05"):
            self.assertFalse(self.cases[case_id].defective, case_id)
        self.assertEqual(sum(case.defective for case in self.cases.values()), 13)

    def test_artifacts_carry_no_lessons_or_notes(self):
        for case in self.cases.values():
            for path in (self.cases_dir / "artifacts" / case.id).iterdir():
                text = path.read_text(encoding="utf-8")
                low = text.lower()
                self.assertNotIn("field note", low, path)
                self.assertNotIn("field-note", low, path)
                self.assertNotIn("expected finding", low, path)
                lines = text.splitlines()
                self.assertGreaterEqual(len(lines), 15, path)
                self.assertLessEqual(len(lines), 45, path)


class LocationMetricsTests(unittest.TestCase):
    """Detection at (case, file) granularity, independent of tag choice."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.bench = SyntheticBench(Path(self._tmp.name) / "synth")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_location_block_adversary(self) -> None:
        loc = self.bench.evaluate()["lenses"]["review-adversary"]["location"]
        self.assertEqual(loc["produced"], 2)
        self.assertEqual(loc["expected"], 2)
        self.assertEqual(loc["detected"], 1)          # s1 found at the right file
        self.assertEqual(loc["detection_recall"], 0.5)
        self.assertEqual(loc["detection_precision"], 0.5)  # the s3 finding is noise
        self.assertEqual(loc["critical_major_detected"], 1)
        self.assertEqual(loc["critical_major_detection_recall"], 0.5)
        self.assertEqual(loc["dimension_agreement"], 1.0)
        self.assertEqual(loc["severity_agreement"], 1.0)

    def test_location_combined_covers_both_defects(self) -> None:
        loc = self.bench.evaluate()["combined"]["location"]
        self.assertEqual(loc["detected"], 2)
        self.assertEqual(loc["detection_recall"], 1.0)
        self.assertEqual(loc["critical_major_detection_recall"], 1.0)
        self.assertEqual(loc["detection_precision"], 0.6667)


class MinorOnlyCaseTests(unittest.TestCase):
    """A minor-only case is approvable and must not count as defective."""

    def test_minor_only_case_excluded_from_escape_denominator(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "bench"
            cases = base / "cases"
            arts = cases / "artifacts"
            for cid, severity in (("maj", "major"), ("min", "minor")):
                (arts / cid).mkdir(parents=True, exist_ok=True)
                (arts / cid / "f.md").write_text("# x\ndefect\n",
                                                 encoding="utf-8")
                case_file(cases / f"{cid}.json", cid, cid,
                          f"artifacts/{cid}/f.md",
                          [{"id": "E01", "severity": severity,
                            "rubric_dimension": "behavior",
                            "artifact_reference": f"artifacts/{cid}/f.md:2"}])
            out = lens_file(
                base / "lens.json", "review-adversary",
                [finding("review-adversary", 1, "minor", "behavior",
                         "artifacts/min/f.md:2")])
            report = bench_score.evaluate(
                bench_score.load_bench(base),
                {"review-adversary": bench_score.load_lens_output(
                    "review-adversary", out)})
            lens = report["lenses"]["review-adversary"]
            self.assertEqual(lens["defective_cases"], 1)
            self.assertEqual(lens["approval_escapes"], 1)
            self.assertEqual(lens["approval_escape_rate"], 1.0)


class EscapeLocationTests(unittest.TestCase):
    """Approval-escape protection is computed per expected finding location."""

    def test_clean_file_in_same_case_does_not_protect_the_defect(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "bench"
            cases = base / "cases"
            arts = cases / "artifacts"
            (arts / "def").mkdir(parents=True, exist_ok=True)
            (arts / "def" / "f.md").write_text("# x\n# y\ndefect\n",
                                                encoding="utf-8")
            (arts / "def" / "clean.md").write_text("# c\nclean\n",
                                                   encoding="utf-8")
            case_file(cases / "def.json", "def", "def", "artifacts/def/f.md",
                      [{"id": "E01", "severity": "major",
                        "rubric_dimension": "behavior",
                        "artifact_reference": "artifacts/def/f.md:3"}])
            out = lens_file(
                base / "lens.json", "review-adversary",
                [finding("review-adversary", 1, "blocker", "operational",
                         "artifacts/def/clean.md:1")])
            report = bench_score.evaluate(
                bench_score.load_bench(base),
                {"review-adversary": bench_score.load_lens_output(
                    "review-adversary", out)})
            lens = report["lenses"]["review-adversary"]
            # The case decision is still CHANGES_REQUESTED (any produced
            # blocker/major requests changes), but nothing protects the
            # defect location: the escape is counted per location.
            self.assertEqual(report["decisions"]["def"], "CHANGES_REQUESTED")
            self.assertEqual(lens["approval_escapes"], 1)
            self.assertEqual(lens["approval_escape_rate"], 1.0)

    def test_produced_at_defect_location_protects(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "bench"
            cases = base / "cases"
            arts = cases / "artifacts"
            (arts / "def").mkdir(parents=True, exist_ok=True)
            (arts / "def" / "f.md").write_text("# x\n# y\ndefect\n",
                                                encoding="utf-8")
            case_file(cases / "def.json", "def", "def", "artifacts/def/f.md",
                      [{"id": "E01", "severity": "major",
                        "rubric_dimension": "behavior",
                        "artifact_reference": "artifacts/def/f.md:3"}])
            out = lens_file(
                base / "lens.json", "review-adversary",
                [finding("review-adversary", 1, "major", "behavior",
                         "artifacts/def/f.md:3")])
            report = bench_score.evaluate(
                bench_score.load_bench(base),
                {"review-adversary": bench_score.load_lens_output(
                    "review-adversary", out)})
            lens = report["lenses"]["review-adversary"]
            self.assertEqual(lens["approval_escapes"], 0)
            self.assertEqual(lens["approval_escape_rate"], 0.0)


class DistinctConsumptionTests(unittest.TestCase):
    """Location-level detection consumes each produced finding once."""

    def test_one_produced_cannot_cover_two_expected_at_one_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "bench"
            cases = base / "cases"
            arts = cases / "artifacts"
            (arts / "multi").mkdir(parents=True, exist_ok=True)
            (arts / "multi" / "f.md").write_text("# h\nd1\nd2\n",
                                                  encoding="utf-8")
            case_file(cases / "multi.json", "multi", "multi",
                      "artifacts/multi/f.md",
                      [{"id": "E01", "severity": "major",
                        "rubric_dimension": "behavior",
                        "artifact_reference": "artifacts/multi/f.md:2"},
                       {"id": "E02", "severity": "major",
                        "rubric_dimension": "test_quality",
                        "artifact_reference": "artifacts/multi/f.md:3"}])
            out = lens_file(
                base / "lens.json", "review-adversary",
                [finding("review-adversary", 1, "major", "behavior",
                         "artifacts/multi/f.md:2")])
            report = bench_score.evaluate(
                bench_score.load_bench(base),
                {"review-adversary": bench_score.load_lens_output(
                    "review-adversary", out)})
            loc = report["lenses"]["review-adversary"]["location"]
            self.assertEqual(loc["detected"], 1)          # only one pairing
            self.assertEqual(loc["detection_recall"], 0.5)
            self.assertEqual(loc["critical_major_detected"], 1)
            self.assertEqual(loc["dimension_agreement"], 1.0)  # paired tag agrees
            # strict matching: one key of two matched
            strict = report["lenses"]["review-adversary"]
            self.assertEqual(strict["matched"], 1)
            self.assertEqual(strict["recall"], 0.5)


class UnnecessaryRevisionDenominatorTests(unittest.TestCase):
    """The rate denominator is every case without an expected blocker/major."""

    def test_minor_only_cases_count_in_the_denominator(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "bench"
            cases = base / "cases"
            arts = cases / "artifacts"
            (arts / "min").mkdir(parents=True, exist_ok=True)
            (arts / "min" / "f.md").write_text("# m\nminor\n", encoding="utf-8")
            case_file(cases / "min.json", "min", "min", "artifacts/min/f.md",
                      [{"id": "E01", "severity": "minor",
                        "rubric_dimension": "docs_process",
                        "artifact_reference": "artifacts/min/f.md:2"}])
            (arts / "clean").mkdir(parents=True, exist_ok=True)
            (arts / "clean" / "g.md").write_text("# g\nclean\n",
                                                 encoding="utf-8")
            case_file(cases / "clean.json", "clean", "clean",
                      "artifacts/clean/g.md", [])
            # A blocker on the minor-only case makes it an unnecessary
            # revision; the denominator is every case without an expected
            # blocker/major (the clean control AND the minor-only case).
            out = lens_file(
                base / "lens.json", "review-adversary",
                [finding("review-adversary", 1, "blocker", "operational",
                         "artifacts/min/f.md:2")])
            report = bench_score.evaluate(
                bench_score.load_bench(base),
                {"review-adversary": bench_score.load_lens_output(
                    "review-adversary", out)})
            lens = report["lenses"]["review-adversary"]
            self.assertEqual(lens["unnecessary_revisions"], 1)
            self.assertEqual(lens["unnecessary_revision_rate"], 0.5)


class LoaderConfinementTests(unittest.TestCase):
    """load_bench confines artifact paths; string prefixes are not enough."""

    def test_artifact_reference_traversal_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "bench"
            cases = base / "cases"
            arts = cases / "artifacts"
            (arts / "s1").mkdir(parents=True, exist_ok=True)
            (arts / "s1" / "a.md").write_text("a\n", encoding="utf-8")
            (base / "outside.txt").write_text("x\ny\n", encoding="utf-8")
            doc = {
                "id": "s1",
                "title": "s1",
                "provenance": "synthetic",
                "artifact": "artifacts/s1/a.md",
                "expected": {"findings": [{
                    "id": "E01",
                    "severity": "major",
                    "rubric_dimension": "behavior",
                    "artifact_reference": "artifacts/s1/../../../outside.txt:1",
                }]},
            }
            (cases / "s1.json").write_text(json.dumps(doc, indent=2),
                                           encoding="utf-8")
            with self.assertRaisesRegex(BenchError,
                                        "escapes the cases directory"):
                bench_score.load_bench(base)

    def test_case_artifact_traversal_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "bench"
            cases = base / "cases"
            arts = cases / "artifacts"
            (arts / "s1").mkdir(parents=True, exist_ok=True)
            (base / "outside.txt").write_text("x\ny\n", encoding="utf-8")
            doc = {
                "id": "s1",
                "title": "s1",
                "provenance": "synthetic",
                "artifact": "artifacts/s1/../../../outside.txt",
                "expected": {"findings": []},
            }
            (cases / "s1.json").write_text(json.dumps(doc, indent=2),
                                           encoding="utf-8")
            with self.assertRaisesRegex(BenchError,
                                        "artifact path .* escapes the cases "
                                        "directory"):
                bench_score.load_bench(base)


if __name__ == "__main__":
    unittest.main()