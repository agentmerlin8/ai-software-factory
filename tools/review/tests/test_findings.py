"""Unit tests for tools.review.findings (shapes A/B, refusals, normalize, CLI)."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tools.review import findings
from tools.review.findings import FindingsError

SHA256 = "a" * 64
BASE = "b" * 40
HEAD = "c" * 40


def one_finding(**overrides) -> dict:
    base = {
        "id": "RC-001",
        "severity": "major",
        "rubric_dimension": "contract_fidelity",
        "artifact_reference": "onboarding/forge_probe.py:488",
        "evidence": "the tool does not conform",
        "required_change": "make it conform",
        "actionable": True,
    }
    base.update(overrides)
    return base


def doc_a(**review_overrides) -> dict:
    """A valid shape-A document as a dict (dataset, not yet validated)."""
    review = {
        "reviewer": "review-contract",
        "rubric_version": "review-contract-v1",
        "package_sha256": SHA256,
        "base_sha": BASE,
        "head_sha": HEAD,
        "decision": "CHANGES_REQUESTED",
        "findings": [one_finding()],
    }
    review.update(review_overrides)
    return {"schema": findings.SCHEMA, "review": review}


def doc_b(**review_overrides) -> dict:
    review = {
        "reviewer": "review-security",
        "rubric_version": "review-security-v1",
        "package_sha256": SHA256,
        "base_sha": BASE,
        "head_sha": HEAD,
        "stop": {"rule": "package-hash-mismatch", "detail": "hash does not match the package"},
    }
    review.update(review_overrides)
    return {"schema": findings.SCHEMA, "review": review}


class _FileFixtureTest(unittest.TestCase):
    """Base: every test that touches files uses a temporary directory."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write_yaml(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def write_json(self, name: str, doc: dict, prefix: str = "") -> Path:
        path = self.dir / name
        path.write_text(prefix + json.dumps(doc), encoding="utf-8")
        return path

    def yaml_a_file(self, name: str = "review.yaml", **overrides) -> Path:
        doc = doc_a(**overrides)
        return self.write_yaml(name, _YAML_A_TMPL.format(
            reviewer=doc["review"]["reviewer"],
            rubric=doc["review"]["rubric_version"],
            sha=doc["review"]["package_sha256"],
            base=doc["review"]["base_sha"],
            head=doc["review"]["head_sha"],
            decision=doc["review"]["decision"],
            finding_id=doc["review"]["findings"][0]["id"],
            severity=doc["review"]["findings"][0]["severity"],
            dimension=doc["review"]["findings"][0]["rubric_dimension"],
            artifact=doc["review"]["findings"][0]["artifact_reference"],
            evidence=doc["review"]["findings"][0]["evidence"],
            required=doc["review"]["findings"][0]["required_change"],
            actionable=str(doc["review"]["findings"][0]["actionable"]).lower(),
        ))


_YAML_A_TMPL = """\
schema: review.findings.v1
review:
  reviewer: {reviewer}
  rubric_version: {rubric}
  package_sha256: {sha}
  base_sha: {base}
  head_sha: {head}
  decision: {decision}
  findings:
    - id: {finding_id}
      severity: {severity}
      rubric_dimension: {dimension}
      artifact_reference: {artifact}
      evidence: '{evidence}'
      required_change: '{required}'
      actionable: {actionable}
"""


class LoadHappyPathTests(_FileFixtureTest):
    def test_shape_a_yaml(self) -> None:
        path = self.yaml_a_file()
        doc = findings.load(path)
        self.assertEqual(doc["schema"], findings.SCHEMA)
        review = doc["review"]
        self.assertEqual(review["reviewer"], "review-contract")
        self.assertEqual(review["decision"], "CHANGES_REQUESTED")
        self.assertEqual(len(review["findings"]), 1)
        self.assertIs(review["findings"][0]["actionable"], True)
        self.assertEqual(review["findings"][0]["id"], "RC-001")

    def test_shape_a_yaml_without_schema_defaults(self) -> None:
        path = self.dir / "review.yaml"
        path.write_text(
            """review:
  reviewer: review-contract
  rubric_version: review-contract-v1
  package_sha256: {sha}
  base_sha: {base}
  head_sha: {head}
  decision: CHANGES_REQUESTED
  findings:
    - id: RC-001
      severity: major
      rubric_dimension: contract_fidelity
      artifact_reference: onboarding/forge_probe.py:488
      evidence: 'evidence text'
      required_change: 'fix it'
      actionable: true
""".format(sha=SHA256, base=BASE, head=HEAD),
            encoding="utf-8",
        )
        doc = findings.load(path)
        self.assertEqual(doc["schema"], findings.SCHEMA)

    def test_shape_a_json(self) -> None:
        doc = doc_a()
        path = self.write_json("review.json", doc, prefix="\n\n")
        self.assertEqual(findings.load(path)["review"]["decision"], "CHANGES_REQUESTED")

    def test_shape_a_approved_empty_findings(self) -> None:
        doc = doc_a(decision="APPROVED", findings=[])
        path = self.write_json("approved.json", doc)
        loaded = findings.load(path)
        self.assertEqual(loaded["review"]["findings"], [])

    def test_shape_b_stop_yaml(self) -> None:
        path = self.dir / "stop.yaml"
        path.write_text(
            """review:
  reviewer: review-security
  rubric_version: review-security-v1
  package_sha256: {sha}
  base_sha: {base}
  head_sha: {head}
  stop:
    rule: package-hash-mismatch
    detail: 'hash mismatch'
""".format(sha=SHA256, base=BASE, head=HEAD),
            encoding="utf-8",
        )
        doc = findings.load(path)
        self.assertEqual(doc["review"]["stop"]["rule"], "package-hash-mismatch")
        self.assertNotIn("decision", doc["review"])
        self.assertNotIn("findings", doc["review"])

    def test_shape_b_json(self) -> None:
        path = self.write_json("stop.json", doc_b())
        doc = findings.load(path)
        self.assertEqual(doc["review"]["stop"]["detail"], "hash does not match the package")

    def test_yaml_parse_error_is_wrapped_named(self) -> None:
        path = self.write_yaml("bad.yaml", "a: 1.5\n")
        with self.assertRaises(FindingsError) as ctx:
            findings.load(path)
        self.assertTrue(str(ctx.exception).startswith("review findings:"))
        self.assertIn("yamlish:", str(ctx.exception))

    def test_invalid_json_is_named(self) -> None:
        path = self.write_yaml("bad.json", "{not json")
        with self.assertRaises(FindingsError) as ctx:
            findings.load(path)
        self.assertIn("invalid JSON document", str(ctx.exception))

    def test_missing_file_is_named(self) -> None:
        with self.assertRaises(FindingsError) as ctx:
            findings.load(self.dir / "nope.yaml")
        self.assertIn("cannot read", str(ctx.exception))

    def test_empty_file_invalid(self) -> None:
        path = self.write_yaml("empty.yaml", "")
        with self.assertRaises(FindingsError) as ctx:
            findings.load(path)
        self.assertIn("must be a mapping", str(ctx.exception))


class ValidateRefusalTests(unittest.TestCase):
    """One test per refusal class: errors are named, no bare tracebacks."""

    def validate(self, doc) -> list[str]:
        return findings.validate(doc)

    def strip(self, doc: dict, *keys: str, where: str = "review") -> dict:
        if where == "top":
            for k in keys:
                doc.pop(k, None)
            return doc
        review = doc["review"]
        for k in keys:
            review.pop(k, None)
        return doc

    # --- structure ---

    def test_document_must_be_a_mapping(self) -> None:
        self.assertTrue(self.validate([1, 2]))
        self.assertTrue(self.validate("scalar"))
        self.assertTrue(self.validate(None))

    def test_unknown_top_level_key(self) -> None:
        doc = doc_a()
        doc["extras"] = 1
        self.assertIn("unknown key 'extras'", self.validate(doc))

    def test_unknown_review_key(self) -> None:
        self.assertIn("unknown key 'bogus' (review)", self.validate(doc_a(bogus=1)))

    def test_missing_review_key(self) -> None:
        errs = self.validate({})
        self.assertEqual(errs, ["missing key 'review'"])

    def test_missing_decision(self) -> None:
        errs = self.validate(self.strip(doc_a(), "decision"))
        self.assertIn("missing key 'decision' (review)", errs)

    def test_missing_findings(self) -> None:
        errs = self.validate(self.strip(doc_a(), "findings"))
        self.assertIn("missing key 'findings' (review)", errs)

    def test_schema_mismatch(self) -> None:
        doc = doc_a()
        doc["schema"] = "other.v2"
        errs = self.validate(doc)
        self.assertTrue(any("invalid schema 'other.v2'" in e for e in errs), errs)

    def test_schema_wrong_type(self) -> None:
        doc = doc_a()
        doc["schema"] = 7
        self.assertTrue(any("invalid schema" in e for e in self.validate(doc)))

    def test_review_must_be_mapping(self) -> None:
        doc = {"schema": findings.SCHEMA, "review": "nope"}
        self.assertIn("'review' must be a mapping", self.validate(doc))

    # --- identity fields ---

    def test_invalid_reviewer(self) -> None:
        errs = self.validate(doc_a(reviewer="reviewer-zero"))
        self.assertTrue(any("invalid reviewer 'reviewer-zero'" in e for e in errs), errs)

    def test_reviewer_non_string(self) -> None:
        self.assertTrue(any("invalid reviewer" in e for e in self.validate(doc_a(reviewer=3))))

    def test_invalid_rubric_version(self) -> None:
        errs = self.validate(doc_a(rubric_version="ReviewContract-v1"))
        self.assertTrue(any("invalid rubric_version" in e for e in errs), errs)
        errs = self.validate(doc_a(rubric_version="no-version"))
        self.assertTrue(any("invalid rubric_version" in e for e in errs), errs)

    def test_invalid_package_sha256_length(self) -> None:
        errs = self.validate(doc_a(package_sha256="a" * 63))
        self.assertTrue(any("invalid package_sha256" in e for e in errs), errs)

    def test_invalid_package_sha256_uppercase(self) -> None:
        errs = self.validate(doc_a(package_sha256=("a" * 63) + "F"))
        self.assertTrue(any("invalid package_sha256" in e for e in errs), errs)

    def test_invalid_base_sha(self) -> None:
        errs = self.validate(doc_a(base_sha="b" * 39))
        self.assertTrue(any("invalid base_sha" in e for e in errs), errs)

    def test_invalid_head_sha(self) -> None:
        errs = self.validate(doc_a(head_sha=("c" * 39) + "g"))
        self.assertTrue(any("invalid head_sha" in e for e in errs), errs)

    # --- shape A decision/findings ---

    def test_invalid_decision(self) -> None:
        errs = self.validate(doc_a(decision="MAYBE"))
        self.assertTrue(any("invalid decision 'MAYBE'" in e for e in errs), errs)

    def test_approved_requires_empty_findings(self) -> None:
        errs = self.validate(doc_a(decision="APPROVED"))
        self.assertTrue(any("APPROVED requires an empty findings" in e for e in errs), errs)

    def test_changes_required_requires_findings(self) -> None:
        errs = self.validate(doc_a(decision="CHANGES_REQUESTED", findings=[]))
        self.assertTrue(any("CHANGES_REQUESTED requires at least one" in e for e in errs), errs)

    def test_findings_must_be_a_list(self) -> None:
        errs = self.validate(doc_a(findings="RC-001"))
        self.assertIn("'findings' must be a list", errs)

    def test_finding_must_be_a_mapping(self) -> None:
        errs = self.validate(doc_a(findings=["not a mapping"]))
        self.assertIn("finding 0 must be a mapping", errs)

    def test_unknown_finding_key(self) -> None:
        errs = self.validate(doc_a(findings=[one_finding(extra_key=1)]))
        self.assertTrue(any("unknown key 'extra_key' (finding 0)" in e for e in errs), errs)

    def test_missing_finding_key(self) -> None:
        f = one_finding()
        del f["evidence"]
        errs = self.validate(doc_a(findings=[f]))
        self.assertTrue(any("missing key 'evidence' (finding 0)" in e for e in errs), errs)

    def test_invalid_finding_id_format(self) -> None:
        for bad in ("RC-1", "XX-001", "RC-0000", "rc-001", "RC-001x"):
            errs = self.validate(doc_a(findings=[one_finding(id=bad)]))
            self.assertTrue(any("invalid finding id" in e for e in errs), (bad, errs))

    def test_finding_id_prefix_must_match_reviewer(self) -> None:
        errs = self.validate(doc_a(findings=[one_finding(id="RA-001")]))
        self.assertTrue(
            any("finding id 'RA-001' does not match reviewer" in e for e in errs), errs
        )

    def test_duplicate_finding_ids(self) -> None:
        errs = self.validate(doc_a(findings=[one_finding(), one_finding()]))
        self.assertTrue(any("duplicate finding id 'RC-001'" in e for e in errs), errs)

    def test_external_prefix_allowed(self) -> None:
        doc = doc_a(reviewer="external", findings=[one_finding(id="EX-007")])
        self.assertEqual(self.validate(doc), [])

    def test_invalid_severity(self) -> None:
        errs = self.validate(doc_a(findings=[one_finding(severity="fatal")]))
        self.assertTrue(any("invalid severity 'fatal'" in e for e in errs), errs)

    def test_invalid_rubric_dimension(self) -> None:
        errs = self.validate(doc_a(findings=[one_finding(rubric_dimension="style")]))
        self.assertTrue(any("invalid rubric_dimension 'style'" in e for e in errs), errs)

    def test_artifact_reference_valid_forms(self) -> None:
        for ref in (
            "onboarding/forge_probe.py:488",
            "onboarding/forge_probe.py",
            "package:diff@onboarding/forge_probe.py",
            "package:diff@onboarding/forge_probe.py:488",
        ):
            doc = doc_a(findings=[one_finding(artifact_reference=ref)])
            self.assertEqual(self.validate(doc), [], ref)

    def test_artifact_reference_invalid(self) -> None:
        for ref in ("has space.py:3", "a:b:c", "package:diff@a/b.py:3:5", ""):
            errs = self.validate(doc_a(findings=[one_finding(artifact_reference=ref)]))
            self.assertTrue(any("invalid artifact_reference" in e for e in errs), (ref, errs))

    def test_evidence_must_be_nonempty(self) -> None:
        for bad in ("", "   "):
            errs = self.validate(doc_a(findings=[one_finding(evidence=bad)]))
            self.assertTrue(any("evidence must be a non-empty string" in e for e in errs), errs)

    def test_evidence_must_be_string(self) -> None:
        errs = self.validate(doc_a(findings=[one_finding(evidence=42)]))
        self.assertTrue(any("evidence must be a non-empty string" in e for e in errs), errs)

    def test_required_change_must_be_nonempty(self) -> None:
        errs = self.validate(doc_a(findings=[one_finding(required_change="\t")]))
        self.assertTrue(any("required_change must be a non-empty string" in e for e in errs), errs)

    def test_actionable_must_be_bool(self) -> None:
        for bad in ("true", "yes", 1, 0, None):
            errs = self.validate(doc_a(findings=[one_finding(actionable=bad)]))
            self.assertTrue(
                any("actionable must be a true/false boolean" in e for e in errs), (bad, errs)
            )

    # --- shape B ---

    def test_stop_and_decision_together_named(self) -> None:
        doc = doc_a()
        doc["review"]["stop"] = {"rule": "x", "detail": "y"}
        errs = self.validate(doc)
        self.assertIn("review may not contain both 'stop' and 'decision'", errs)

    def test_unknown_stop_key(self) -> None:
        errs = self.validate(doc_b(stop={"rule": "r", "detail": "d", "extra": 1}))
        self.assertTrue(any("unknown key 'extra' (stop)" in e for e in errs), errs)

    def test_missing_stop_key(self) -> None:
        errs = self.validate(doc_b(stop={"rule": "r"}))
        self.assertIn("missing key 'detail' (stop)", errs)

    def test_stop_must_be_mapping(self) -> None:
        errs = self.validate(doc_b(stop="not mapping"))
        self.assertIn("'stop' must be a mapping", errs)

    def test_invalid_stop_rule(self) -> None:
        for bad in ("Bad-Rule", "bad rule", "", "1bad"):
            errs = self.validate(doc_b(stop={"rule": bad, "detail": "d"}))
            self.assertTrue(any("invalid stop rule" in e for e in errs), (bad, errs))

    def test_stop_detail_nonempty(self) -> None:
        errs = self.validate(doc_b(stop={"rule": "ok-rule", "detail": "   "}))
        self.assertIn("stop detail must be a non-empty string", errs)

    def test_shape_b_decision_findings_absent(self) -> None:
        self.assertEqual(self.validate(doc_b()), [])

    def test_shape_a_with_stop_is_unknown_key(self) -> None:
        errs = self.validate(doc_a(stop={"rule": "will-not-pass", "detail": "d"}))
        self.assertTrue(any("unknown key 'stop' (review)" in e for e in errs), errs)

    # --- aggregate behaviour ---

    def test_validate_reports_all_errors_not_first(self) -> None:
        doc = doc_a(
            decision="APPROVED",
            base_sha="too-short",
            findings=[one_finding(severity="fatal", actionable="yes")],
        )
        errs = self.validate(doc)
        self.assertGreaterEqual(len(errs), 4)
        self.assertTrue(any("APPROVED requires" in e for e in errs))
        self.assertTrue(any("invalid base_sha" in e for e in errs))
        self.assertTrue(any("invalid severity" in e for e in errs))
        self.assertTrue(any("actionable" in e for e in errs))

    def test_validate_returns_empty_for_shape_a(self) -> None:
        self.assertEqual(self.validate(doc_a()), [])

    def test_findings_error_string_format(self) -> None:
        exc = FindingsError("boom")
        self.assertEqual(str(exc), "review findings: boom")
        self.assertIsInstance(exc, ValueError)


class NormalizeTests(unittest.TestCase):
    def test_fixed_key_order(self) -> None:
        doc = doc_a()
        normalized = findings.normalize(doc)
        self.assertEqual(
            list(normalized.keys()), ["schema", "review"]
        )
        self.assertEqual(
            list(normalized["review"].keys()),
            [
                "reviewer",
                "rubric_version",
                "package_sha256",
                "base_sha",
                "head_sha",
                "decision",
                "findings",
            ],
        )

    def test_findings_sorted_by_id(self) -> None:
        doc = doc_a(
            reviewer="review-contract",
            findings=[
                one_finding(id="RC-003"),
                one_finding(id="RC-001"),
                one_finding(id="RC-002"),
            ],
        )
        self.assertEqual(findings.validate(doc), [])
        ids = [f["id"] for f in findings.normalize(doc)["review"]["findings"]]
        self.assertEqual(ids, ["RC-001", "RC-002", "RC-003"])

    def test_stop_shape_key_order(self) -> None:
        normalized = findings.normalize(doc_b())
        self.assertEqual(
            list(normalized["review"].keys()),
            ["reviewer", "rubric_version", "package_sha256", "base_sha", "head_sha", "stop"],
        )
        self.assertEqual(list(normalized["review"]["stop"].keys()), ["rule", "detail"])


class ToJsonTests(unittest.TestCase):
    def test_trailing_newline(self) -> None:
        self.assertTrue(findings.to_json(doc_a()).endswith("\n"))

    def test_sorted_keys_and_normalized_round_trip(self) -> None:
        text = findings.to_json(doc_a())
        parsed = json.loads(text)
        self.assertEqual(parsed["review"]["decision"], "CHANGES_REQUESTED")
        # sort_keys=True forces "review" before "schema"
        self.assertLess(text.index('"review"'), text.index('"schema"'))
        # to_json output is itself a valid document (JSON branch of load)
        errors = findings.validate(parsed)
        self.assertEqual(errors, [])

    def test_to_json_is_canonical(self) -> None:
        doc = doc_a(
            findings=[one_finding(id="RC-002"), one_finding(id="RC-001")]
        )
        canonical = doc_a(
            findings=[one_finding(id="RC-001"), one_finding(id="RC-002")]
        )
        self.assertEqual(findings.to_json(doc), findings.to_json(canonical))


class CliTests(_FileFixtureTest):
    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = findings.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_validate_ok(self) -> None:
        path = self.yaml_a_file()
        code, out, err = self.run_cli(["findings", "validate", str(path)])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn(f"review findings: OK (1 finding(s), {path})", out)

    def test_validate_ok_approved_zero_findings(self) -> None:
        path = self.write_json("approved.json", doc_a(decision="APPROVED", findings=[]))
        code, out, _ = self.run_cli(["findings", "validate", str(path)])
        self.assertEqual(code, 0)
        self.assertIn("OK (0 finding(s)", out)

    def test_validate_stop_shape_ok(self) -> None:
        path = self.write_json("stop.json", doc_b())
        code, out, _ = self.run_cli(["findings", "validate", str(path)])
        self.assertEqual(code, 0)
        self.assertIn("OK (0 finding(s)", out)

    def test_validate_errors_exit_1_one_per_line(self) -> None:
        path = self.write_json(
            "bad.json",
            doc_a(decision="APPROVED", base_sha="short", findings=[]),
        )
        code, _, err = self.run_cli(["findings", "validate", str(path)])
        self.assertEqual(code, 1)
        lines = [ln for ln in err.splitlines() if ln.strip()]
        self.assertTrue(lines)
        for line in lines:
            self.assertTrue(line.startswith("  - "), line)

    def test_validate_missing_file_exit_1(self) -> None:
        code, _, err = self.run_cli(["findings", "validate", str(self.dir / "gone.yaml")])
        self.assertEqual(code, 1)
        self.assertTrue(any("cannot read" in ln for ln in err.splitlines()))

    def test_validate_multiple_files_mixed_exit_1(self) -> None:
        good = self.yaml_a_file("good.yaml")
        bad = self.write_json("bad.json", doc_a(decision="MAYBE"))
        code, out, err = self.run_cli(["findings", "validate", str(good), str(bad)])
        self.assertEqual(code, 1)
        self.assertIn(f"OK (1 finding(s), {good})", out)
        self.assertIn(f"  - {bad}: invalid decision", err)

    def test_json_cmd_matches_to_json(self) -> None:
        path = self.yaml_a_file()
        code, out, _ = self.run_cli(["findings", "json", str(path)])
        self.assertEqual(code, 0)
        self.assertEqual(out, findings.to_json(findings.load(path)))

    def test_json_cmd_invalid_exit_1(self) -> None:
        path = self.write_json("bad.json", doc_a(decision="MAYBE"))
        code, _, err = self.run_cli(["findings", "json", str(path)])
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("review findings:"), err)

    def test_usage_exit_2(self) -> None:
        for argv in (
            ["findings"],
            ["findings", "nope", "f"],
            ["findings", "json"],
            ["findings", "json", "a", "b"],
            ["findings", "validate"],
        ):
            code, _, err = self.run_cli(argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("usage", err)


if __name__ == "__main__":
    unittest.main()