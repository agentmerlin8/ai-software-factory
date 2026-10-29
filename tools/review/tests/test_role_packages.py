"""Tests for the runtime-neutral review role packages (S3.5a T5).

Each role package (agents/review-*.agent.md) is a plain Markdown file with
YAML frontmatter and a fixed section order, intended to be handed to a review
subagent by a dispatcher on any agent runtime.  These tests parse the files
with a simple line parser (no YAML library) and check:

- frontmatter: exactly the six required keys, in the canonical order, with
  kind/version/id/rubric_version matching tools/review/findings;
- body: the eight required sections present, in order, as the only level-2
  headings, each with content;
- Output contract: the fenced YAML blocks extract cleanly and validate as
  review.findings.v1 documents (findings shape A and stop-block shape B) for
  the lens's own reviewer id and id prefix;
- runtime neutrality: no banned runtime-specific keys anywhere, no absolute
  paths, and each file names only its own finding-id prefix.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from typing import cast

from tools.review import findings
from tools.review.yamlish import loads as yamlish_loads

ROOT = Path(__file__).resolve().parents[3]
AGENTS_DIR = ROOT / "agents"

ROLES = {
    "review-contract.agent.md": (
        "review-contract", "review-contract-v1", "RC",
        ("contract_fidelity", "scope", "docs_process", "architecture"),
    ),
    "review-adversary.agent.md": (
        "review-adversary", "review-adversary-v1", "RA",
        ("behavior", "test_quality", "operational"),
    ),
    "review-security.agent.md": (
        "review-security", "review-security-v1", "RS",
        ("security", "behavior", "operational"),
    ),
}

FRONTMATTER_KEYS = (
    ("name", "description", "id", "kind", "version", "rubric_version"),
)

SECTIONS = (
    "## Role",
    "## Inputs",
    "## Stay-out boundary",
    "## Stop rules",
    "## Procedure",
    "## Output contract",
    "## Severity",
    "## Budget",
)

FINDING_KEYS = (
    "id", "severity", "rubric_dimension", "artifact_reference",
    "evidence", "required_change", "actionable",
)

BANNED_TOKENS = (
    "tools:", "user-invocable", "disable-model-invocation",
    "model:", "copilot", "claude", "codex",
)

# An absolute-path-looking token: a slash at the start of a line or right
# after whitespace, followed by a letter.  Relative paths (src/x.py) and
# mid-word slashes (and/or) are fine; the packages must not hard-code any
# absolute location, or they stop being runtime-neutral.
ABS_PATH_RE = re.compile(r"(^|\s)/[A-Za-z][A-Za-z0-9_.-]*")

YAML_FENCE_RE = re.compile(r"```yaml\n(.*?)```", re.DOTALL)

# The example finding ids must match the findings schema's id grammar.
ID_RE = re.compile(r"^(RC|RA|RS|EX)-[0-9]{3}$")


def parse_frontmatter(text: str) -> dict:
    """Simple line parser for the frontmatter: keys up to the closing fence."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise AssertionError("frontmatter must open with a '---' line")
    frontmatter: dict = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return frontmatter
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        frontmatter[key.strip()] = value.strip()
    raise AssertionError("frontmatter is not closed by a '---' line")


def section_bodies(text: str) -> tuple[list, dict]:
    """([heading, ...] in file order, {heading: body}) for '#!' headings."""
    heads: list = []
    for line in text.splitlines():
        if line.startswith("## "):
            heads.append(line)
            break
    order: list = []
    bodies: dict = {}
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("## ")]
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        heading = lines[i]
        order.append(heading)
        bodies[heading] = "\n".join(lines[i + 1:end]).strip()
    return order, bodies


def yaml_blocks(text: str) -> list[str]:
    """Fenced yaml blocks under the Output contract section, in order."""
    _, bodies = section_bodies(text)
    return YAML_FENCE_RE.findall(bodies["## Output contract"])


class RolePackageTest(unittest.TestCase):
    """Structural + validation checks for every role package file."""

    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.texts = {}
        cls.scan = {}
        for filename in ROLES:
            path = AGENTS_DIR / filename
            if not path.is_file():
                raise AssertionError(f"role package missing: {path}")
            text = path.read_text(encoding="utf-8")
            cls.texts[filename] = text
            cls.scan[filename] = {
                "frontmatter": parse_frontmatter(text),
                "order": None,
                "bodies": {},
            }
            order, bodies = section_bodies(text)
            cls.scan[filename]["order"] = order
            cls.scan[filename]["bodies"] = bodies

    def for_each(self):
        for filename in ROLES:
            with self.subTest(file=filename):
                yield filename

    # -- frontmatter -------------------------------------------------------

    def test_frontmatter_has_exactly_six_keys_in_canonical_order(self):
        expected = FRONTMATTER_KEYS[0]
        for filename in self.for_each():
            keys = tuple(self.scan[filename]["frontmatter"])
            self.assertEqual(keys, expected)
            for value in self.scan[filename]["frontmatter"].values():
                self.assertTrue(value, f"empty frontmatter value in {filename}")

    def test_kind_and_version(self):
        for filename in self.for_each():
            fm = self.scan[filename]["frontmatter"]
            self.assertEqual(fm["kind"], "role-package")
            self.assertEqual(fm["version"], "1")

    def test_id_and_rubric_version_match_findings(self):
        for filename in self.for_each():
            role_id, rubric_version, prefix, _ = ROLES[filename]
            fm = self.scan[filename]["frontmatter"]
            self.assertEqual(fm["id"], role_id)
            self.assertEqual(fm["rubric_version"], rubric_version)
            self.assertIn(fm["id"], findings.ROLES)
            self.assertTrue(
                re.fullmatch(r"^[a-z0-9-]+-v[0-9]+$", fm["rubric_version"]),
                f"malformed rubric_version in {filename}",
            )

    # -- sections ----------------------------------------------------------

    def test_required_sections_present_in_order_and_only_those(self):
        for filename in self.for_each():
            order = self.scan[filename]["order"]
            self.assertEqual(order, list(SECTIONS))

    def test_every_section_has_content(self):
        for filename in self.for_each():
            for heading, body in self.scan[filename]["bodies"].items():
                self.assertTrue(
                    body, f"{heading} is empty in {filename}"
                )

    # -- output contract yaml ---------------------------------------------

    def test_output_contract_has_two_valid_yaml_blocks(self):
        for filename in self.for_each():
            blocks = yaml_blocks(self.texts[filename])
            self.assertEqual(len(blocks), 2)
            body = self.scan[filename]["bodies"]["## Output contract"]
            self.assertIn("review.findings.v1", body)

    def test_findings_block_validates_for_this_lens(self):
        for filename in self.for_each():
            role_id, rubric_version, prefix, owned = ROLES[filename]
            block = yaml_blocks(self.texts[filename])[0]
            doc = cast(dict, yamlish_loads(block))
            self.assertEqual(findings.validate(doc), [])
            review = doc["review"]
            self.assertEqual(review["reviewer"], role_id)
            self.assertEqual(review["rubric_version"], rubric_version)
            self.assertIn(review["decision"], findings.DECISIONS)
            self.assertIsInstance(review["findings"], list)
            self.assertTrue(review["findings"])
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "findings.yaml"
                path.write_text(block, encoding="utf-8")
                loaded = findings.load(path)
            self.assertEqual(loaded["review"]["reviewer"], role_id)

    def test_stop_block_validates(self):
        for filename in self.for_each():
            role_id, rubric_version, _, _ = ROLES[filename]
            block = yaml_blocks(self.texts[filename])[1]
            doc = cast(dict, yamlish_loads(block))
            self.assertEqual(findings.validate(doc), [])
            review = doc["review"]
            self.assertEqual(review["reviewer"], role_id)
            self.assertEqual(review["rubric_version"], rubric_version)
            self.assertIn("stop", review)
            self.assertNotIn("decision", review)
            self.assertIn(review["stop"]["rule"], (
                "package-hash-mismatch", "missing-package-section",
                "head-sha-mismatch", "truncated-diff", "withheld-artifact",
            ))

    def test_example_finding_carries_all_seven_keys(self):
        for filename in self.for_each():
            block = yaml_blocks(self.texts[filename])[0]
            finding = cast(dict, yamlish_loads(block))["review"]["findings"][0]
            self.assertEqual(tuple(finding), FINDING_KEYS)
            self.assertTrue(ID_RE.fullmatch(finding["id"]))
            role_id, _, prefix, owned = ROLES[filename]
            self.assertEqual(finding["id"].split("-", 1)[0], prefix)
            self.assertIn(finding["rubric_dimension"], owned)
            self.assertIn(finding["severity"], findings.SEVERITIES)
            self.assertTrue(finding["actionable"] is True)

    # -- stop rules / procedure / inputs / budget --------------------------

    def test_stop_rules_numbered_with_action(self):
        for filename in self.for_each():
            body = self.scan[filename]["bodies"]["## Stop rules"]
            numbered = re.findall(r"^\d+\.", body, re.M)
            self.assertGreaterEqual(len(numbered), 5)
            self.assertIn("stop block", body)

    def test_procedure_has_mutation_pass_and_incremental_write(self):
        for filename in self.for_each():
            body = self.scan[filename]["bodies"]["## Procedure"]
            self.assertIn("mutation", body.lower())
            self.assertIn("incrementally", body.lower())
            self.assertIn("FIRST", body)
            self.assertIn("how-this-fails", body.lower())

    def test_inputs_name_all_dispatched_values(self):
        for filename in self.for_each():
            body = self.scan[filename]["bodies"]["## Inputs"]
            self.assertIn("package_sha256", body)
            self.assertIn("base", body)
            self.assertIn("head", body)
            self.assertIn("output path", body)
            self.assertIn("sole evidence surface", body)

    def test_budget_caps_at_the_package(self):
        for filename in self.for_each():
            body = self.scan[filename]["bodies"]["## Budget"]
            self.assertIn("capped at the package", body)
            self.assertIn("no finding may cite a file outside", body)

    # -- runtime neutrality ------------------------------------------------

    def test_no_banned_runtime_keys_anywhere(self):
        for filename in self.for_each():
            lowered = self.texts[filename].lower()
            for token in BANNED_TOKENS:
                self.assertNotIn(
                    token, lowered, f"banned token {token!r} in {filename}"
                )

    def test_no_absolute_paths(self):
        for filename in self.for_each():
            for lineno, line in enumerate(self.texts[filename].splitlines(), 1):
                self.assertIsNone(
                    ABS_PATH_RE.search(line),
                    f"absolute path in {filename} line {lineno}: {line!r}",
                )

    def test_each_file_names_its_own_id_prefix_only(self):
        for filename in self.for_each():
            _, _, prefix, _ = ROLES[filename]
            text = self.texts[filename]
            self.assertIn(f"{prefix}-", text)
            for other in ("RC", "RA", "RS"):
                if other != prefix:
                    self.assertNotIn(
                        f"{other}-", text,
                        f"foreign id prefix {other}- in {filename}",
                    )


if __name__ == "__main__":
    unittest.main()