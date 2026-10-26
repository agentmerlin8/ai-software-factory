#!/usr/bin/env python3
"""tools/tests/test_check_digest.py — digest-gate deterministic tests
(adapted port).

Covers section presence, the line cap, risk-marker validity, the AC-count
honesty pattern, manifest first-cell parsing, and the anchor regex.
End-to-end behavior (temp git repos, CLI exit codes) lives in the gate
acceptance harness (tools/gates/test_gate.py — DigestGateFixtures).
Stdlib unittest.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_digest as CD  # noqa: E402

FXROOT = Path(__file__).resolve().parent / "fixtures" / "check_digest"


def build_repo(td: str, fixture: str, sid: str) -> Path:
    """Throwaway git repo from a check_digest fixture (same shape as the
    gate harness's DigestGateFixtures._build_repo). A fixture without a
    story.md builds a repo without one (the no-ground-truth case)."""
    root = Path(td)
    fx = FXROOT / fixture
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text(
        (fx / "tree" / "src" / "app.py").read_text(encoding="utf-8"))
    sdir = root / "stories" / sid
    sdir.mkdir(parents=True)
    if (fx / "story.md").exists():
        (sdir / "story.md").write_text(
            (fx / "story.md").read_text(encoding="utf-8"))
    (sdir / "context-digest.md").write_text(
        (fx / "context-digest.md").read_text(encoding="utf-8"))
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-qm", "fixture"]):
        subprocess.run(["git", "-C", str(root), *args], check=True,
                       capture_output=True)
    return root


class SectionPresenceTests(unittest.TestCase):
    def test_missing_section_fails(self):
        text = "## 1. File manifest\nstuff\n## 2. Story contract\nstuff"
        errors = [s for s in CD.SECTIONS if s not in text]
        self.assertGreaterEqual(len(errors), 6)  # most sections missing

    def test_all_sections_present(self):
        text = "\n".join(CD.SECTIONS)
        missing = [s for s in CD.SECTIONS if s not in text]
        self.assertEqual(missing, [])

    def test_section_count_is_eight(self):
        self.assertEqual(len(CD.SECTIONS), 8)


class LineCapTests(unittest.TestCase):
    def test_over_cap_detected(self):
        lines = "\n".join(f"line {i}" for i in range(CD.LINE_CAP + 1))
        self.assertGreater(len(lines.splitlines()), CD.LINE_CAP)


class RiskMarkerTests(unittest.TestCase):
    def test_valid_markers(self):
        for m in "LMH":
            self.assertIn(m, "LMH")
        self.assertNotIn("X", "LMH")


class RiskMarkerBoundaryTests(unittest.TestCase):
    """R4-E1: the marker regex requires a LEFT token boundary (not
    preceded by a letter) AND a marker-ish right context (end of line or
    a dash) — the last letter of an uppercase word ("…API") is not a
    risk marker, while a genuinely bad standalone marker still flags."""

    def _repo(self, td: str, risk_lines: str) -> Path:
        root = Path(td)
        (root / "src").mkdir(parents=True)
        (root / "src" / "app.py").write_text(
            "# a\ndef f(x):\n    return x\n", encoding="utf-8")
        sdir = root / "stories" / "STORY-908"
        sdir.mkdir(parents=True)
        (sdir / "story.md").write_text("# s\n\n- AC-1: one.\n",
                                       encoding="utf-8")
        (sdir / "context-digest.md").write_text(
            "# digest — STORY-908\n\n"
            "## 1. File manifest\n\n"
            "| path | role |\n|---|---|\n"
            "| `src/app.py` | subject |\n\n"
            "## 2. Story contract\n\nAcceptance criteria: exactly 1.\n\n"
            "## 3. Frozen decisions\n\n- none.\n\n"
            "## 4. Code anchors\n\n- `src/app.py:2` — def f\n\n"
            "## 5. Conventions\n\n- stdlib only.\n\n"
            "## 6. Open questions\n\n- none.\n\n"
            f"## 7. Risk list\n\n{risk_lines}\n\n"
            "## 8. Verification block\n\nBASE_SHA: HEAD\n",
            encoding="utf-8")
        _commit(root)
        return root

    def test_uppercase_word_tail_and_valid_marker_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "- stale API response — M\n"
                                  "- API drift\n")
            errors = CD.run_gate(root, "STORY-908", "HEAD")
            self.assertEqual(errors, [])

    def test_bad_standalone_marker_still_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "- drift — X\n")
            errors = CD.run_gate(root, "STORY-908", "HEAD")
            self.assertIn("bad risk marker: X", errors)

    def test_multichar_severity_word_flagged(self):
        """R19-T1: `- stale cache — CRITICAL` used to produce no
        single-letter candidate, so the membership check never ran and
        the invalid severity passed. The COMPLETE token after the
        delimiter must be validated and rejected by name."""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "- stale cache — CRITICAL\n")
            errors = CD.run_gate(root, "STORY-908", "HEAD")
            self.assertIn("bad risk marker: CRITICAL", errors)
            # en-dash and spaced-hyphen delimiters behave the same
            for line in ("- stale cache – CRITICAL\n",
                         "- stale cache - CRITICAL\n"):
                root2 = self._repo(
                    tempfile.mkdtemp(dir=td), line)
                errors2 = CD.run_gate(root2, "STORY-908", "HEAD")
                self.assertIn("bad risk marker: CRITICAL", errors2)

    def test_valid_single_letter_severities_still_pass(self):
        """R19-T1: L/M/H after the delimiter remain accepted; a missing
        severity (no delimiter) stays unchanged (no finding)."""
        with tempfile.TemporaryDirectory() as td:
            for sev in ("L", "M", "H"):
                root = self._repo(tempfile.mkdtemp(dir=td),
                                  f"- drift — {sev}\n")
                errors = CD.run_gate(root, "STORY-908", "HEAD")
                self.assertEqual(errors, [], sev)
            root = self._repo(tempfile.mkdtemp(dir=td), "- drift\n")
            self.assertEqual(CD.run_gate(root, "STORY-908", "HEAD"), [])


class ACGrepTests(unittest.TestCase):
    def test_ac_pattern_matches_typical(self):
        sample = (
            "## Acceptance Criteria\n"
            "- AC-1: thing\n- AC-2: other\n- AC-3: third\n"
            "AC-4: fourth\n* AC-5: fifth\n"
        )
        actual = sum(
            1 for l in sample.splitlines()
            if re.match(r"^\s*(?:[-*] )?AC-\d+[:.)]", l))
        self.assertGreaterEqual(actual, 4)


class ManifestParseTests(unittest.TestCase):
    """The gate must only treat the FIRST table cell as a file path,
    not every cell (the 'role'/'facts extracted' columns are not paths)."""

    def test_first_cell_only(self):
        text = (
            "## 1. File manifest\n"
            "| path | role | facts extracted |\n"
            "|---|---|---|\n"
            "| `stories/T-A.md` | provider precedent | queue consumer |\n"
            "| `docs/architecture.md` | layers | module map |\n"
            "\n## 2. Story contract\n"
        )
        m = re.search(r"## 1\. File manifest\n(.*?)(?=\n## 2\.)", text, re.S)
        self.assertIsNotNone(m)
        paths = []
        for line in m.group(1).splitlines():
            mm = re.match(r"^\s*\|\s*`?([^`|\n]+?)`?\s*\|", line)
            if mm:
                p = mm.group(1).strip()
                p = p[2:] if p.startswith("./") else p
                if p and not p.startswith(("path", "---")):
                    paths.append(p)
        self.assertEqual(paths, ["stories/T-A.md", "docs/architecture.md"])

    def test_leading_dot_prefix_only_and_hidden_paths_survive(self):
        """The manifest strip must remove a LITERAL `./` prefix, not the
        character set {.,/} — hidden paths like `.github/workflows/ci.yml`
        keep their leading dot."""
        for raw, want in (("./src/app.py", "src/app.py"),
                          ("src/app.py", "src/app.py"),
                          (".github/workflows/ci.yml",
                           ".github/workflows/ci.yml"),
                          ("./.hidden/x.md", ".hidden/x.md")):
            p = raw.strip()
            p = p[2:] if p.startswith("./") else p
            self.assertEqual(p, want)


class HiddenPathGateTests(unittest.TestCase):
    """End-to-end: a manifest path with a leading dot (.github/…) that
    EXISTS at the sha must pass the existence check — the old
    lstrip("./") mangled it to `github/…` and failed valid digests."""

    def _hidden_repo(self, td: str, sid: str) -> Path:
        root = Path(td)
        wf = root / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text("name: ci\non: push\n", encoding="utf-8")
        (root / "src").mkdir(parents=True)
        (root / "src" / "app.py").write_text(
            "# line1\n# line2\n# line3\ndef compute_total(items):\n"
            "    return sum(items)\n", encoding="utf-8")
        sdir = root / "stories" / sid
        sdir.mkdir(parents=True)
        (sdir / "story.md").write_text(
            "# story\n\n- AC-1: hidden paths resolve.\n", encoding="utf-8")
        (sdir / "context-digest.md").write_text(
            f"# digest — {sid}\n\n"
            "## 1. File manifest\n\n"
            "| path | role |\n|---|---|\n"
            "| `.github/workflows/ci.yml` | workflow |\n"
            "| `./src/app.py` | subject |\n\n"
            "## 2. Story contract\n\nAcceptance criteria: exactly 1.\n\n"
            "## 3. Frozen decisions\n\n- none.\n\n"
            "## 4. Code anchors\n\n"
            "- `.github/workflows/ci.yml:1` — name ci\n\n"
            "## 5. Conventions\n\n- stdlib only.\n\n"
            "## 6. Open questions\n\n- none.\n\n"
            "## 7. Risk list\n\n- none — L\n\n"
            "## 8. Verification block\n\nBASE_SHA: HEAD\n",
            encoding="utf-8")
        for args in (["init", "-q"], ["add", "-A"],
                     ["-c", "user.email=t@t", "-c", "user.name=t",
                      "commit", "-qm", "fixture"]):
            subprocess.run(["git", "-C", str(root), *args], check=True,
                           capture_output=True)
        return root

    def test_hidden_manifest_path_and_anchor_pass(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._hidden_repo(td, "STORY-NNN")
            errors = CD.run_gate(root, "STORY-NNN", "HEAD")
            self.assertEqual(errors, [])


class AnchorParseTests(unittest.TestCase):
    def test_anchor_line_grammar(self):
        line = "- `src/app/widget.py:12` — def compute_total"
        mm = re.match(r"^\s*-?\s*`?([^`:\s]+):(\d+)`?\s*—?\s*([^—]*)",
                      line)
        self.assertIsNotNone(mm)
        self.assertEqual(mm.group(1), "src/app/widget.py")
        self.assertEqual(int(mm.group(2)), 12)
        self.assertIn("compute_total", mm.group(3))

    def test_non_anchor_lines_ignored(self):
        mm = re.match(r"^\s*-?\s*`?([^`:\s]+):(\d+)`?\s*—?\s*([^—]*)",
                      "a prose sentence without anchors")
        self.assertIsNone(mm)

    def test_extensionless_anchor_grammar(self):
        """R6-G3: the path token no longer requires a `.ext` suffix —
        extensionless anchors parse with the same grammar."""
        for line, path, lineno in (
                ("- `Dockerfile:12` — FROM base", "Dockerfile", 12),
                ("- `bin/build:3` — set -e", "bin/build", 3),
                ("- Dockerfile:999999", "Dockerfile", 999999)):
            mm = re.match(r"^\s*-?\s*`?([^`:\s]+):(\d+)`?\s*—?\s*([^—]*)",
                          line)
            self.assertIsNotNone(mm, line)
            self.assertEqual(mm.group(1), path)
            self.assertEqual(int(mm.group(2)), lineno)


class FailClosedACInputsTests(unittest.TestCase):
    """F3: the AC-count honesty check fails closed when either input is
    missing — the declaration phrase is REQUIRED, and without a story file
    there is no ground truth."""

    def test_missing_ac_count_declaration_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = build_repo(td, "no-ac-count", "STORY-903")
            errors = CD.run_gate(root, "STORY-903", "HEAD")
            self.assertIn('missing AC-count declaration '
                          '("Acceptance criteria: exactly N")', errors)

    def test_missing_story_file_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = build_repo(td, "no-story-file", "STORY-904")
            errors = CD.run_gate(root, "STORY-904", "HEAD")
            self.assertIn("story file not found for AC-count ground truth "
                          "(looked at stories/STORY-904/spec.md, "
                          "stories/STORY-904/story.md, "
                          "stories/STORY-904.md)", errors)

    def test_both_inputs_present_no_ac_errors(self):
        """The zero-anchor fixture has BOTH AC inputs — only the anchor
        error must appear (no fail-closed false positives)."""
        with tempfile.TemporaryDirectory() as td:
            root = build_repo(td, "zero-anchor", "STORY-905")
            errors = CD.run_gate(root, "STORY-905", "HEAD")
            self.assertNotIn('missing AC-count declaration '
                             '("Acceptance criteria: exactly N")', errors)
            self.assertFalse(any("story file not found" in e
                                 for e in errors), errors)


class ZeroAnchorTests(unittest.TestCase):
    """F4: a `:0` anchor must refuse — the old code indexed src_lines[-1]
    and could false-pass a symbol on the last line."""

    def test_zero_anchor_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = build_repo(td, "zero-anchor", "STORY-905")
            errors = CD.run_gate(root, "STORY-905", "HEAD")
            self.assertIn("anchor line must be >= 1: src/app.py:0", errors)


class NegativeAnchorTests(unittest.TestCase):
    """Round 28 AC2: `- src/ghost.py:-1 — def compute_total` used to
    MISS the unsigned line-number regex and be silently skipped — invalid
    evidence passed. The signed capture feeds the same `lineno < 1`
    refusal, so `:0` and `:-1` are both rejected by name."""

    def _repo(self, td: str, anchor_line: str, sid: str) -> Path:
        root = Path(td)
        (root / "src").mkdir(parents=True)
        (root / "src" / "app.py").write_text(
            "# a\ndef f(x):\n    return x\n", encoding="utf-8")
        sdir = root / "stories" / sid
        sdir.mkdir(parents=True)
        (sdir / "story.md").write_text("# s\n\n- AC-1: one.\n",
                                       encoding="utf-8")
        (sdir / "context-digest.md").write_text(
            f"# digest — {sid}\n\n"
            "## 1. File manifest\n\n"
            "| path | role |\n|---|---|\n"
            "| `src/app.py` | subject |\n\n"
            "## 2. Story contract\n\nAcceptance criteria: exactly 1.\n\n"
            "## 3. Frozen decisions\n\n- none.\n\n"
            f"## 4. Code anchors\n\n{anchor_line}\n\n"
            "## 5. Conventions\n\n- stdlib only.\n\n"
            "## 6. Open questions\n\n- none.\n\n"
            "## 7. Risk list\n\n- drift — L\n\n"
            "## 8. Verification block\n\nBASE_SHA: HEAD\n",
            encoding="utf-8")
        _commit(root)
        return root

    def test_negative_anchor_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "- `src/ghost.py:-1` — def compute_total",
                              "STORY-909")
            errors = CD.run_gate(root, "STORY-909", "HEAD")
            self.assertIn("anchor line must be >= 1: src/ghost.py:-1",
                          errors)

    def test_negative_anchor_bare_form_refused(self):
        """No backticks, no em-dash description — still captured."""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "- src/ghost.py:-3", "STORY-910")
            errors = CD.run_gate(root, "STORY-910", "HEAD")
            self.assertIn("anchor line must be >= 1: src/ghost.py:-3",
                          errors)

    def test_signed_regex_grammar(self):
        """The signed pattern captures negatives AND keeps normal
        anchors parsing unchanged."""
        for line, path, lineno in (
                ("- `src/app.py:12` — def f", "src/app.py", 12),
                ("- `src/ghost.py:-1` — def compute_total",
                 "src/ghost.py", -1),
                ("- `src/app.py:0`", "src/app.py", 0),
                ("1. `bin/build:3` — set -e", "bin/build", 3)):
            mm = re.match(r"^\s*(?:[-*+]|\d+\.)?\s*"
                          r"`?([^`:\s]+):(-?\d+)`?\s*—?\s*([^—]*)",
                          line)
            self.assertIsNotNone(mm, line)
            self.assertEqual(mm.group(1), path)
            self.assertEqual(int(mm.group(2)), lineno)

    def test_normal_anchor_unchanged(self):
        """A valid positive anchor still passes end-to-end."""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "- `src/app.py:2` — def f", "STORY-911")
            errors = CD.run_gate(root, "STORY-911", "HEAD")
            self.assertEqual(errors, [])


class StoryFileCandidatesTests(unittest.TestCase):
    def test_canonical_spec_path_first(self):
        """R8-I2: `stories/<sid>/spec.md` leads (architecture.md §Installed
        control home; same order as the plan gate's `resolve_spec()`), then
        the kit story.md, then the reference layout."""
        root = Path("/tmp/x")
        cands = CD.story_file_candidates(root, "STORY-901")
        self.assertEqual(
            cands,
            [root / "stories" / "STORY-901" / "spec.md",
             root / "stories" / "STORY-901" / "story.md",
             root / "stories" / "STORY-901.md"])


def _commit(root: Path) -> None:
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-qm", "fixture"]):
        subprocess.run(["git", "-C", str(root), *args], check=True,
                       capture_output=True)


class LiteralHeaderSkipTests(unittest.TestCase):
    """R3-D2: the manifest skip must exempt ONLY the literal `path`
    header cell and separator rows — the old `startswith(("path",
    "---"))` let `paths/config.json` / `pathlib.py` bypass the existence
    check entirely."""

    def _repo(self, td: str, manifest_rows: str) -> Path:
        root = Path(td)
        (root / "src").mkdir(parents=True)
        (root / "src" / "app.py").write_text(
            "# a\ndef f(x):\n    return x\n", encoding="utf-8")
        sdir = root / "stories" / "STORY-906"
        sdir.mkdir(parents=True)
        (sdir / "story.md").write_text("# s\n\n- AC-1: one.\n",
                                       encoding="utf-8")
        (sdir / "context-digest.md").write_text(
            "# digest — STORY-906\n\n"
            "## 1. File manifest\n\n"
            "| path | role |\n|---|---|\n"
            f"{manifest_rows}\n"
            "| `src/app.py` | subject |\n\n"
            "## 2. Story contract\n\nAcceptance criteria: exactly 1.\n\n"
            "## 3. Frozen decisions\n\n- none.\n\n"
            "## 4. Code anchors\n\n- `src/app.py:2` — def f\n\n"
            "## 5. Conventions\n\n- stdlib only.\n\n"
            "## 6. Open questions\n\n- none.\n\n"
            "## 7. Risk list\n\n- none — L\n\n"
            "## 8. Verification block\n\nBASE_SHA: HEAD\n",
            encoding="utf-8")
        _commit(root)
        return root

    def test_pathlike_ghost_entries_flagged(self):
        rows = ("| `paths/config.json` | ghost |\n"
                "| `pathlib.py` | ghost |")
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, rows)
            errors = CD.run_gate(root, "STORY-906", "HEAD")
            self.assertIn("manifest path missing at HEAD: "
                          "paths/config.json", errors)
            self.assertIn("manifest path missing at HEAD: pathlib.py",
                          errors)

    def test_good_manifest_still_passes(self):
        """The literal header cell (`path`) and separator rows stay
        exempt — a fully valid manifest reports no errors."""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "")
            self.assertEqual(CD.run_gate(root, "STORY-906", "HEAD"), [])


class ACHeadingCountTests(unittest.TestCase):
    """R3-D3: the AC counter must accept the heading form
    (`### AC-1 — …`) that the controller's _AC_RE and the good-story
    fixture use — the old `[-*] ` + `[:.)]` pattern counted such a
    valid story as ZERO ACs, so a truthful digest failed."""

    def _heading_repo(self, td: str, declared: int) -> Path:
        root = Path(td)
        sdir = root / "stories" / "STORY-907"
        sdir.mkdir(parents=True)
        good = (Path(__file__).resolve().parent / "fixtures" /
                "controller" / "story_good.md")
        (sdir / "story.md").write_text(good.read_text(encoding="utf-8"),
                                       encoding="utf-8")
        (sdir / "context-digest.md").write_text(
            "# digest — STORY-907\n\n"
            "## 1. File manifest\n\n"
            "| path | role |\n|---|---|\n"
            "| `stories/STORY-907/story.md` | contract |\n\n"
            "## 2. Story contract\n\n"
            f"Acceptance criteria: exactly {declared}.\n\n"
            "## 3. Frozen decisions\n\n- none.\n\n"
            "## 4. Code anchors\n\n- none.\n\n"
            "## 5. Conventions\n\n- stdlib only.\n\n"
            "## 6. Open questions\n\n- none.\n\n"
            "## 7. Risk list\n\n- none — L\n\n"
            "## 8. Verification block\n\nBASE_SHA: HEAD\n",
            encoding="utf-8")
        _commit(root)
        return root

    def test_heading_form_declaring_two_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._heading_repo(td, 2)
            self.assertEqual(CD.run_gate(root, "STORY-907", "HEAD"), [])

    def test_heading_form_declaring_zero_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._heading_repo(td, 0)
            errors = CD.run_gate(root, "STORY-907", "HEAD")
            self.assertIn("AC count mismatch: digest says 0, "
                          "story file has ~2", errors)

    def test_existing_bullet_and_plain_forms_still_counted(self):
        sample = ("- AC-1: thing\n* AC-2) other\nAC-3. third\n"
                  "### AC-4 — heading form\n"
                  "prose mentioning AC-5 mid-line\n")
        pat = r"^\s*(?:#{1,6}\s+|[-*]\s+\*{0,2})?AC-\d+\b"
        actual = sum(1 for l in sample.splitlines() if re.match(pat, l))
        self.assertEqual(actual, 4)  # mid-prose AC-5 does not match
        # the old pattern matched the bullet/plain subset (no heading)
        old = sum(1 for l in sample.splitlines()
                  if re.match(r"^\s*(?:[-*] )?AC-\d+[:.)]", l))
        self.assertEqual(old, 3)


class HeadingWhitespaceFailClosedTests(unittest.TestCase):
    """R6-G2: a section heading with trailing whitespace must still
    parse (its validation RUNS — a ghost manifest entry is flagged), and
    a present-but-unparseable required section fails closed with a named
    error instead of silently skipping its check."""

    def _repo(self, td: str, sid: str, heading: str,
              manifest_rows: str = "", anchor_lines: str = "") -> Path:
        root = Path(td)
        (root / "src").mkdir(parents=True)
        (root / "src" / "app.py").write_text(
            "# a\ndef f(x):\n    return x\n", encoding="utf-8")
        sdir = root / "stories" / sid
        sdir.mkdir(parents=True)
        (sdir / "story.md").write_text("# s\n\n- AC-1: one.\n",
                                       encoding="utf-8")
        (sdir / "context-digest.md").write_text(
            f"# digest — {sid}\n\n"
            f"{heading}\n\n"
            "| path | role |\n|---|---|\n"
            f"{manifest_rows}"
            "| `src/app.py` | subject |\n\n"
            "## 2. Story contract\n\nAcceptance criteria: exactly 1.\n\n"
            "## 3. Frozen decisions\n\n- none.\n\n"
            f"## 4. Code anchors\n\n{anchor_lines or '- `src/app.py:2` — def f'}\n\n"
            "## 5. Conventions\n\n- stdlib only.\n\n"
            "## 6. Open questions\n\n- none.\n\n"
            "## 7. Risk list\n\n- none — L\n\n"
            "## 8. Verification block\n\nBASE_SHA: HEAD\n",
            encoding="utf-8")
        _commit(root)
        return root

    def test_trailing_space_heading_ghost_entry_flagged(self):
        """The old `\\n`-anchored regex returned None on a trailing-space
        heading, silently skipping manifest validation — the ghost entry
        must now be reported."""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-909", "## 1. File manifest  ",
                              manifest_rows="| `src/ghost.py` | ghost |\n")
            errors = CD.run_gate(root, "STORY-909", "HEAD")
            self.assertIn("manifest path missing at HEAD: src/ghost.py",
                          errors)

    def test_trailing_space_heading_valid_digest_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-909", "## 1. File manifest\t ")
            self.assertEqual(CD.run_gate(root, "STORY-909", "HEAD"), [])

    def test_unparseable_present_section_named(self):
        """A required section present in the text but unparseable (no
        newline after the heading — body fused into the heading line)
        must produce the named fail-closed error."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir(parents=True)
            (root / "src" / "app.py").write_text(
                "# a\ndef f(x):\n    return x\n", encoding="utf-8")
            sdir = root / "stories" / "STORY-910"
            sdir.mkdir(parents=True)
            (sdir / "story.md").write_text("# s\n\n- AC-1: one.\n",
                                           encoding="utf-8")
            (sdir / "context-digest.md").write_text(
                "# digest — STORY-910\n\n"
                "## 1. File manifest | `src/ghost.py` | ghost |\n\n"
                "## 2. Story contract\n\nAcceptance criteria: exactly 1.\n\n"
                "## 3. Frozen decisions\n\n- none.\n\n"
                "## 4. Code anchors\n\n- `src/app.py:2` — def f\n\n"
                "## 5. Conventions\n\n- stdlib only.\n\n"
                "## 6. Open questions\n\n- none.\n\n"
                "## 7. Risk list\n\n- none — L\n\n"
                "## 8. Verification block\n\nBASE_SHA: HEAD\n",
                encoding="utf-8")
            _commit(root)
            errors = CD.run_gate(root, "STORY-910", "HEAD")
            self.assertIn("unparseable section: ## 1. File manifest",
                          errors)


class ExtensionlessAnchorGateTests(unittest.TestCase):
    """R6-G3: extensionless anchors (Dockerfile:999999, bin/build:1) get
    the same existence / line-bound / symbol checks — the old regex's
    mandatory `.\\w+` skipped them entirely."""

    def _repo(self, td: str, sid: str, anchors: str,
              with_extras: bool = True) -> Path:
        root = Path(td)
        (root / "src").mkdir(parents=True)
        (root / "src" / "app.py").write_text(
            "# a\ndef f(x):\n    return x\n", encoding="utf-8")
        if with_extras:
            (root / "Dockerfile").write_text(
                "FROM base\nRUN true\n", encoding="utf-8")
            (root / "bin").mkdir()
            (root / "bin" / "build").write_text(
                "#!/bin/sh\nset -e\necho build\n", encoding="utf-8")
        sdir = root / "stories" / sid
        sdir.mkdir(parents=True)
        (sdir / "story.md").write_text("# s\n\n- AC-1: one.\n",
                                       encoding="utf-8")
        (sdir / "context-digest.md").write_text(
            f"# digest — {sid}\n\n"
            "## 1. File manifest\n\n"
            "| path | role |\n|---|---|\n"
            "| `src/app.py` | subject |\n\n"
            "## 2. Story contract\n\nAcceptance criteria: exactly 1.\n\n"
            "## 3. Frozen decisions\n\n- none.\n\n"
            f"## 4. Code anchors\n\n{anchors}\n\n"
            "## 5. Conventions\n\n- stdlib only.\n\n"
            "## 6. Open questions\n\n- none.\n\n"
            "## 7. Risk list\n\n- none — L\n\n"
            "## 8. Verification block\n\nBASE_SHA: HEAD\n",
            encoding="utf-8")
        _commit(root)
        return root

    def test_missing_extensionless_path_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-911",
                              "- `Dockerfile:999999` — FROM base\n",
                              with_extras=False)
            errors = CD.run_gate(root, "STORY-911", "HEAD")
            self.assertIn("anchor path missing at HEAD: Dockerfile", errors)

    def test_extensionless_line_beyond_eof_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-912",
                              "- `Dockerfile:999999` — FROM base\n")
            errors = CD.run_gate(root, "STORY-912", "HEAD")
            self.assertIn("anchor line 999999 beyond file Dockerfile "
                          "(2 lines)", errors)

    def test_valid_extensionless_anchors_resolve(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(
                td, "STORY-913",
                "- `Dockerfile:1` — FROM base\n"
                "- `bin/build:2` — set -e\n")
            self.assertEqual(CD.run_gate(root, "STORY-913", "HEAD"), [])

    def test_extensionless_symbol_mismatch_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-914",
                              "- `bin/build:1` — set -e\n")
            errors = CD.run_gate(root, "STORY-914", "HEAD")
            self.assertIn("anchor symbol not on line 1 of bin/build: "
                          "['set']", errors)


class AlignedSeparatorSkipTests(unittest.TestCase):
    """R7-H1: aligned Markdown table separators (`|:---|---|`,
    `|---:|---|`, `|:---:|---|`) yield first cells like `:---` /
    `---:` — the old `set(p) == {"-"}` skip missed them, so the gate
    looked for the separator as a Git path and rejected a valid digest.
    Separator cells containing only `-`/`:` characters are skipped;
    ghost entries and real paths are unaffected."""

    def _repo(self, td: str, sid: str, separator: str,
              extra_rows: str = "") -> Path:
        root = Path(td)
        (root / "src").mkdir(parents=True)
        (root / "src" / "app.py").write_text(
            "# a\ndef f(x):\n    return x\n", encoding="utf-8")
        sdir = root / "stories" / sid
        sdir.mkdir(parents=True)
        (sdir / "story.md").write_text("# s\n\n- AC-1: one.\n",
                                       encoding="utf-8")
        (sdir / "context-digest.md").write_text(
            f"# digest — {sid}\n\n"
            "## 1. File manifest\n\n"
            f"| path | role |\n{separator}\n"
            f"{extra_rows}"
            "| `src/app.py` | subject |\n\n"
            "## 2. Story contract\n\nAcceptance criteria: exactly 1.\n\n"
            "## 3. Frozen decisions\n\n- none.\n\n"
            "## 4. Code anchors\n\n- `src/app.py:2` — def f\n\n"
            "## 5. Conventions\n\n- stdlib only.\n\n"
            "## 6. Open questions\n\n- none.\n\n"
            "## 7. Risk list\n\n- none — L\n\n"
            "## 8. Verification block\n\nBASE_SHA: HEAD\n",
            encoding="utf-8")
        _commit(root)
        return root

    def test_left_aligned_separator_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-915", "|:---|---|")
            self.assertEqual(CD.run_gate(root, "STORY-915", "HEAD"), [])

    def test_right_aligned_separator_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-916", "|---:|---|")
            self.assertEqual(CD.run_gate(root, "STORY-916", "HEAD"), [])

    def test_center_and_plain_separators_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-917", "|:---:|---|")
            self.assertEqual(CD.run_gate(root, "STORY-917", "HEAD"), [])
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-918", "|---|---|")
            self.assertEqual(CD.run_gate(root, "STORY-918", "HEAD"), [])

    def test_ghost_entries_still_flagged_with_aligned_separator(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-919", "|:---|---|",
                              extra_rows="| `src/ghost.py` | ghost |\n")
            errors = CD.run_gate(root, "STORY-919", "HEAD")
            self.assertIn("manifest path missing at HEAD: src/ghost.py",
                          errors)

    def test_colon_path_cells_are_not_separator_exemptions(self):
        """A cell that is not purely `-`/`:` characters still hits the
        existence check — `:` alone or path-like tokens are not exempt."""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-920", "|:---|---|",
                              extra_rows="| `x:y` | odd |\n")
            errors = CD.run_gate(root, "STORY-920", "HEAD")
            self.assertIn("manifest path missing at HEAD: x:y", errors)


def _r8_repo(root: Path, sid: str, *, src_lines: str, story_text: str | None,
             anchors: str, spec_only: bool = False) -> Path:
    """R8 inline fixture: minimal repo for the round-8 digest-gate tests.
    `src/app.py` content is `src_lines`; the story ground truth is written
    as story.md, spec.md (spec_only), or omitted (None)."""
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "app.py").write_text(src_lines, encoding="utf-8")
    sdir = root / "stories" / sid
    sdir.mkdir(parents=True, exist_ok=True)
    if story_text is not None:
        name = "spec.md" if spec_only else "story.md"
        (sdir / name).write_text(story_text, encoding="utf-8")
    (sdir / "context-digest.md").write_text(
        f"# digest — {sid}\n\n"
        "## 1. File manifest\n\n"
        "| path | role |\n|---|---|\n"
        "| `src/app.py` | subject |\n\n"
        "## 2. Story contract\n\nAcceptance criteria: exactly 1.\n\n"
        "## 3. Frozen decisions\n\n- none.\n\n"
        f"## 4. Code anchors\n\n{anchors}\n\n"
        "## 5. Conventions\n\n- stdlib only.\n\n"
        "## 6. Open questions\n\n- none.\n\n"
        "## 7. Risk list\n\n- none — L\n\n"
        "## 8. Verification block\n\nBASE_SHA: HEAD\n",
        encoding="utf-8")
    _commit(root)
    return root


R8_SRC = ("# app\n"
          "class Widget:\n"
          "    async def existing(self):\n"
          "        return 1\n"
          "\n"
          "// public static Foo Bar() { return new Foo(); }\n")


class SpecPathPreferenceTests(unittest.TestCase):
    """R8-I2: ground truth prefers the canonical `stories/<sid>/spec.md`
    (architecture.md §Installed control home; plan-gate resolve_spec
    order) — a spec-only story resolves instead of failing the digest
    gate, and both-files repos count the spec, not a stale draft."""

    def test_spec_only_story_resolves(self):
        with tempfile.TemporaryDirectory() as td:
            root = _r8_repo(Path(td), "STORY-921", src_lines=R8_SRC,
                            story_text="# s\n\n- AC-1: one.\n",
                            anchors="- none.", spec_only=True)
            self.assertEqual(CD.run_gate(root, "STORY-921", "HEAD"), [])

    def test_spec_preferred_over_story_md_draft(self):
        """Both files present: the spec's AC count is the ground truth —
        the story.md draft with a DIFFERENT count must not be counted."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _r8_repo(root, "STORY-922", src_lines=R8_SRC,
                     story_text="# s\n\n- AC-1: one.\n",
                     anchors="- none.")
            (root / "stories" / "STORY-922" / "spec.md").write_text(
                "# spec\n\n- AC-1: one.\n- AC-2: two.\n", encoding="utf-8")
            _commit(root)
            # digest declares 1 — matches story.md but NOT the preferred
            # spec.md (2 ACs) → the mismatch proves the spec was used
            errors = CD.run_gate(root, "STORY-922", "HEAD")
            self.assertIn("AC count mismatch: digest says 1, "
                          "story file has ~2", errors)


class BaseShaValidationTests(unittest.TestCase):
    """R8-I3: the base revision is validated at gate entry — a
    NONEXISTENT base sha is a named failure even when the manifest and
    anchors perform no git reads whose failure would surface."""

    def _quiet_repo(self, td: str, sid: str) -> Path:
        # `- none` anchors: without the entry check the only git read
        # (story lookup) falls back to the working tree and a bogus sha
        # would pass every check
        return _r8_repo(Path(td), sid, src_lines=R8_SRC,
                        story_text="# s\n\n- AC-1: one.\n",
                        anchors="- none.")

    def test_bogus_sha_named_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._quiet_repo(td, "STORY-923")
            bogus = "0" * 40
            errors = CD.run_gate(root, "STORY-923", bogus)
            self.assertEqual(errors, [f"unresolvable base sha: {bogus}"])

    def test_bogus_ref_named_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._quiet_repo(td, "STORY-924")
            errors = CD.run_gate(root, "STORY-924", "no-such-ref")
            self.assertEqual(errors,
                             ["unresolvable base sha: no-such-ref"])

    def test_valid_sha_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._quiet_repo(td, "STORY-925")
            self.assertEqual(CD.run_gate(root, "STORY-925", "HEAD"), [])

    def test_story_working_tree_fallback_still_applies(self):
        """An UNCOMMITTED story file still counts via the working-tree
        fallback — the entry check only rejects unreadable revisions."""
        with tempfile.TemporaryDirectory() as td:
            root = _r8_repo(Path(td), "STORY-926", src_lines=R8_SRC,
                            story_text=None, anchors="- none.")
            (root / "stories" / "STORY-926" / "story.md").write_text(
                "# s\n\n- AC-1: one.\n", encoding="utf-8")
            # not committed — exists only in the working tree
            self.assertEqual(CD.run_gate(root, "STORY-926", "HEAD"), [])


class AnchorKeywordSkipTests(unittest.TestCase):
    """R8-I4: declaration keywords/modifiers are skipped when checking
    anchor description tokens — the first remaining SYMBOL token must
    appear on the line (`async def not_present_anywhere` no longer passes
    against `async def existing():`)."""

    def _repo(self, td: str, sid: str, anchors: str) -> Path:
        return _r8_repo(Path(td), sid, src_lines=R8_SRC,
                        story_text="# s\n\n- AC-1: one.\n",
                        anchors=anchors)

    def test_async_def_missing_symbol_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-927",
                              "- `src/app.py:3` — async def "
                              "not_present_anywhere\n")
            errors = CD.run_gate(root, "STORY-927", "HEAD")
            self.assertIn("anchor symbol not on line 3 of src/app.py: "
                          "['not_present_anywhere']", errors)

    def test_async_def_existing_symbol_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-928",
                              "- `src/app.py:3` — async def existing\n")
            self.assertEqual(CD.run_gate(root, "STORY-928", "HEAD"), [])

    def test_public_static_missing_symbol_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-929",
                              "- `src/app.py:6` — public static Missing "
                              "Bar\n")
            errors = CD.run_gate(root, "STORY-929", "HEAD")
            self.assertIn("anchor symbol not on line 6 of src/app.py: "
                          "['Missing']", errors)

    def test_public_static_existing_symbols_pass(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-930",
                              "- `src/app.py:6` — public static Foo Bar\n")
            self.assertEqual(CD.run_gate(root, "STORY-930", "HEAD"), [])

    def test_all_keyword_description_keeps_old_behavior(self):
        """When every token is a declaration keyword the check degrades
        to the old first-two-tokens behavior."""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-931",
                              "- `src/app.py:2` — class def\n")
            # line 2 is `class Widget:` — contains `class` but not `def`
            errors = CD.run_gate(root, "STORY-931", "HEAD")
            self.assertIn("anchor symbol not on line 2 of src/app.py: "
                          "['class', 'def']", errors)


J2_SRC = ("# module\n"
          "def compute_total(items):\n"
          "    return sum(items)\n")


S1_SRC = ("// mod\n"
          "export interface ExistingShape {}\n"
          "const existingConst = 1;\n"
          "struct ExistingStruct { }\n"
          "enum ExistingEnum { }\n")


class AnchorDeclKeywordFilterTests(unittest.TestCase):
    """R18-S1: declaration keywords `export`/`interface`/`const`/
    `struct`/`enum` are filtered before the symbol check — the
    DECLARED NAME is the first checked token, so a mismatched declared
    name (e.g. `export interface MissingShape` vs a line declaring
    `ExistingShape`) is an anchor-symbol failure, never a pass on the
    filtered keyword tokens alone."""

    def _repo(self, td: str, sid: str, anchors: str) -> Path:
        return _r8_repo(Path(td), sid, src_lines=S1_SRC,
                        story_text="# s\n\n- AC-1: one.\n",
                        anchors=anchors)

    def test_export_interface_mismatch_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-964",
                              "- `src/app.py:2` — export interface "
                              "MissingShape\n")
            errors = CD.run_gate(root, "STORY-964", "HEAD")
            self.assertIn("anchor symbol not on line 2 of src/app.py: "
                          "['MissingShape']", errors)

    def test_export_interface_match_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-965",
                              "- `src/app.py:2` — export interface "
                              "ExistingShape\n")
            self.assertEqual(CD.run_gate(root, "STORY-965", "HEAD"), [])

    def test_const_mismatch_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-966",
                              "- `src/app.py:3` — const MissingConst\n")
            errors = CD.run_gate(root, "STORY-966", "HEAD")
            self.assertIn("anchor symbol not on line 3 of src/app.py: "
                          "['MissingConst']", errors)

    def test_struct_mismatch_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-967",
                              "- `src/app.py:4` — struct MissingStruct\n")
            errors = CD.run_gate(root, "STORY-967", "HEAD")
            self.assertIn("anchor symbol not on line 4 of src/app.py: "
                          "['MissingStruct']", errors)

    def test_enum_mismatch_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-968",
                              "- `src/app.py:5` — enum MissingEnum\n")
            errors = CD.run_gate(root, "STORY-968", "HEAD")
            self.assertIn("anchor symbol not on line 5 of src/app.py: "
                          "['MissingEnum']", errors)

    def test_matching_names_all_pass(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-969",
                              "- `src/app.py:3` — const existingConst\n"
                              "- `src/app.py:4` — struct ExistingStruct\n"
                              "- `src/app.py:5` — enum ExistingEnum\n")
            self.assertEqual(CD.run_gate(root, "STORY-969", "HEAD"), [])


class AnchorSymbolTokenMatchTests(unittest.TestCase):
    """R9-J2: the anchor symbol check compares COMPLETE tokens, not
    substrings — anchor `def total` must NOT pass against
    `def compute_total(items):` (an incorrect anchor must never
    authorize planner dispatch); `compute_total` still passes."""

    def _repo(self, td: str, sid: str, anchors: str) -> Path:
        return _r8_repo(Path(td), sid, src_lines=J2_SRC,
                        story_text="# s\n\n- AC-1: one.\n",
                        anchors=anchors)

    def test_substring_anchor_symbol_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-932",
                              "- `src/app.py:2` — def total\n")
            errors = CD.run_gate(root, "STORY-932", "HEAD")
            self.assertIn("anchor symbol not on line 2 of src/app.py: "
                          "['total']", errors)

    def test_complete_token_anchor_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-933",
                              "- `src/app.py:2` — def compute_total\n")
            self.assertEqual(CD.run_gate(root, "STORY-933", "HEAD"), [])

    def test_prefix_of_token_anchor_flagged(self):
        """`compute` is a prefix of the line's `compute_total` token —
        a prefix substring is not a token match either."""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-934",
                              "- `src/app.py:2` — compute\n")
            errors = CD.run_gate(root, "STORY-934", "HEAD")
            self.assertIn("anchor symbol not on line 2 of src/app.py: "
                          "['compute']", errors)

    def test_token_inside_punctuation_passes(self):
        """Tokenization splits on non-\\w characters — `items` on
        `def compute_total(items):` is a complete token and passes."""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-935",
                              "- `src/app.py:2` — items\n")
            self.assertEqual(CD.run_gate(root, "STORY-935", "HEAD"), [])


class DeclaredBaseMismatchTests(unittest.TestCase):
    """R11-L2: a digest declaring `BASE_SHA: <B>` must NOT pass
    validation against supplied commit A — plan_checks.resolve_base_sha()
    would then consume a DIFFERENT snapshot than this gate validated.
    Declared and supplied both resolve to commit ids, so a matching
    full/abbrev pair passes; a digest with NO declared base (the
    uppercase `BASE_SHA: HEAD` fixtures) is unchanged."""

    def _two_commit_repo(self, td: str, sid: str, decl_block: str) -> Path:
        root = Path(td)
        (root / "src").mkdir(parents=True)
        (root / "src" / "app.py").write_text(
            "# a\ndef f(x):\n    return x\n", encoding="utf-8")
        sdir = root / "stories" / sid
        sdir.mkdir(parents=True)
        (sdir / "story.md").write_text("# s\n\n- AC-1: one.\n",
                                       encoding="utf-8")
        (sdir / "context-digest.md").write_text(
            f"# digest — {sid}\n\n"
            "## 1. File manifest\n\n"
            "| path | role |\n|---|---|\n"
            "| `src/app.py` | subject |\n\n"
            "## 2. Story contract\n\nAcceptance criteria: exactly 1.\n\n"
            "## 3. Frozen decisions\n\n- none.\n\n"
            "## 4. Code anchors\n\n- `src/app.py:2` — def f\n\n"
            "## 5. Conventions\n\n- stdlib only.\n\n"
            "## 6. Open questions\n\n- none.\n\n"
            "## 7. Risk list\n\n- none — L\n\n"
            f"## 8. Verification block\n\n{decl_block}\n",
            encoding="utf-8")
        _commit(root)
        return root

    def _sha(self, root: Path, rev: str) -> str:
        r = subprocess.run(
            ["git", "-C", str(root), "rev-parse", rev],
            check=True, capture_output=True, text=True)
        return r.stdout.strip()

    def _second_commit(self, root: Path) -> None:
        (root / "src" / "app.py").write_text(
            "# a2\ndef f(x):\n    return x\n", encoding="utf-8")
        for args in (["add", "-A"],
                     ["-c", "user.email=t@t", "-c", "user.name=t",
                      "commit", "-qm", "second"]):
            subprocess.run(["git", "-C", str(root), *args], check=True,
                           capture_output=True)

    def test_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._two_commit_repo(td, "STORY-936", "BASE_SHA: HEAD")
            first = self._sha(root, "HEAD")
            (root / "stories" / "STORY-936" / "context-digest.md").write_text(
                (root / "stories" / "STORY-936"
                 / "context-digest.md").read_text(encoding="utf-8")
                .replace("BASE_SHA: HEAD", f"BASE_SHA: {first}"),
                encoding="utf-8")
            _commit(root)
            self._second_commit(root)
            second = self._sha(root, "HEAD")
            errors = CD.run_gate(root, "STORY-936", second)
            self.assertEqual(errors, [
                f"base sha mismatch: digest declares {first}, "
                f"invocation supplied {second}"])

    def test_matching_full_abbrev_pair_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._two_commit_repo(td, "STORY-937", "BASE_SHA: HEAD")
            first = self._sha(root, "HEAD")
            (root / "stories" / "STORY-937" / "context-digest.md").write_text(
                (root / "stories" / "STORY-937"
                 / "context-digest.md").read_text(encoding="utf-8")
                .replace("BASE_SHA: HEAD", f"BASE_SHA: {first}"),
                encoding="utf-8")
            _commit(root)
            self._second_commit(root)
            # declared FULL sha, supplied its 7-char abbreviation — same
            # commit id, so no mismatch (and the story/anchors still
            # resolve at the supplied rev)
            errors = CD.run_gate(root, "STORY-937", first[:7])
            self.assertFalse(any("base sha mismatch" in e for e in errors),
                             errors)

    def test_no_declaration_unchanged(self):
        """The uppercase `BASE_SHA: HEAD` fixture form declares no
        lowercase-hex sha (identical parse to plan_checks) — validation
        proceeds against the supplied sha exactly as before."""
        with tempfile.TemporaryDirectory() as td:
            root = self._two_commit_repo(td, "STORY-938", "BASE_SHA: HEAD")
            self._second_commit(root)
            self.assertEqual(CD.run_gate(root, "STORY-938", "HEAD"), [])

    def test_declared_parser_matches_plan_checks_ladder(self):
        """declared_base_sha uses the digest-side rules of
        plan_checks.resolve_base_sha(): BASE_SHA token first, then the
        §8 `git rev-parse <sha>` / `pinned … <sha>` forms."""
        sha = "0123456789abcdef0123456789abcdef01234567"
        self.assertEqual(
            CD.declared_base_sha(f"x\nBASE_SHA: {sha}\n"), sha)
        self.assertEqual(
            CD.declared_base_sha(
                "## 8. Verification block\nrun: git rev-parse "
                f"{sha}\n"), sha)
        self.assertEqual(
            CD.declared_base_sha(
                f"## 8. Verification block\npinned at {sha}\n"), sha)
        # BASE_SHA token wins over a §8 form
        self.assertEqual(
            CD.declared_base_sha(
                f"BASE_SHA: {sha}\n## 8. Verification block\n"
                f"git rev-parse {'f' * 40}\n"), sha)
        self.assertIsNone(CD.declared_base_sha("no base anywhere\n"))


class AnchorBulletFormsTests(unittest.TestCase):
    """R11-L3: the §4 anchor grammar accepts ALL Markdown unordered-list
    bullets (`-`, `*`, `+`) — a `*`-bulleted ghost anchor is no longer
    silently skipped, and valid `*`/`+` anchors resolve. The existing
    `-` form is unchanged."""

    def _repo(self, td: str, sid: str, anchors: str) -> Path:
        return _r8_repo(Path(td), sid, src_lines=J2_SRC,
                        story_text="# s\n\n- AC-1: one.\n",
                        anchors=anchors)

    def test_star_bullet_ghost_anchor_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-939",
                              "* src/ghost.py:999 — def nonexistent\n")
            errors = CD.run_gate(root, "STORY-939", "HEAD")
            self.assertIn("anchor path missing at HEAD: src/ghost.py",
                          errors)

    def test_plus_bullet_ghost_anchor_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-940",
                              "+ `src/ghost.py:999` — def nonexistent\n")
            errors = CD.run_gate(root, "STORY-940", "HEAD")
            self.assertIn("anchor path missing at HEAD: src/ghost.py",
                          errors)

    def test_valid_star_and_plus_anchors_resolve(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(
                td, "STORY-941",
                "* `src/app.py:2` — def compute_total\n"
                "+ `src/app.py:3` — return\n")
            self.assertEqual(CD.run_gate(root, "STORY-941", "HEAD"), [])

    def test_existing_dash_form_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-942",
                              "- `src/app.py:2` — def compute_total\n")
            self.assertEqual(CD.run_gate(root, "STORY-942", "HEAD"), [])

    def test_grammar_accepts_all_bullet_markers(self):
        pat = r"^\s*(?:[-*+]|\d+\.)?\s*`?([^`:\s]+):(\d+)`?\s*—?\s*([^—]*)"
        for line in ("- `src/app.py:12` — def f",
                     "* `src/app.py:12` — def f",
                     "+ `src/app.py:12` — def f",
                     "  * src/app.py:12 — def f"):
            mm = re.match(pat, line)
            self.assertIsNotNone(mm, line)
            self.assertEqual(mm.group(1), "src/app.py")
            self.assertEqual(int(mm.group(2)), 12)


def _r14_repo(root: Path, sid: str, *, src_lines: str, story_text: str,
              manifest_rows: str, anchors: str) -> Path:
    """R14 inline fixture: like _r8_repo but with a caller-supplied
    manifest-rows block (leading pipes optional per row)."""
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "app.py").write_text(src_lines, encoding="utf-8")
    sdir = root / "stories" / sid
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "story.md").write_text(story_text, encoding="utf-8")
    (sdir / "context-digest.md").write_text(
        f"# digest — {sid}\n\n"
        "## 1. File manifest\n\n"
        "| path | role |\n|---|---|\n"
        f"{manifest_rows}\n"
        "## 2. Story contract\n\nAcceptance criteria: exactly 1.\n\n"
        "## 3. Frozen decisions\n\n- none.\n\n"
        f"## 4. Code anchors\n\n{anchors}\n\n"
        "## 5. Conventions\n\n- stdlib only.\n\n"
        "## 6. Open questions\n\n- none.\n\n"
        "## 7. Risk list\n\n- none — L\n\n"
        "## 8. Verification block\n\nBASE_SHA: HEAD\n",
        encoding="utf-8")
    _commit(root)
    return root


class ManifestPipelessRowTests(unittest.TestCase):
    """R14-O1: manifest table rows may omit the leading pipe — a
    pipe-less ghost row must still hit the existence check, and the
    leading-pipe form is unchanged."""

    def test_pipeless_ghost_row_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = _r14_repo(
                Path(td), "STORY-950", src_lines=J2_SRC,
                story_text="# s\n\n- AC-1: one.\n",
                manifest_rows="| `src/app.py` | subject |\n"
                              "`src/ghost.py` | subject\n",
                anchors="- `src/app.py:2` — def compute_total\n")
            errors = CD.run_gate(root, "STORY-950", "HEAD")
            self.assertIn("manifest path missing at HEAD: src/ghost.py",
                          errors)

    def test_pipeless_bare_ghost_row_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = _r14_repo(
                Path(td), "STORY-951", src_lines=J2_SRC,
                story_text="# s\n\n- AC-1: one.\n",
                manifest_rows="src/ghost.py | subject\n",
                anchors="- `src/app.py:2` — def compute_total\n")
            errors = CD.run_gate(root, "STORY-951", "HEAD")
            self.assertIn("manifest path missing at HEAD: src/ghost.py",
                          errors)

    def test_leading_pipe_form_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = _r14_repo(
                Path(td), "STORY-952", src_lines=J2_SRC,
                story_text="# s\n\n- AC-1: one.\n",
                manifest_rows="| `src/app.py` | subject |\n",
                anchors="- `src/app.py:2` — def compute_total\n")
            self.assertEqual(CD.run_gate(root, "STORY-952", "HEAD"), [])

    def test_pipeless_valid_row_resolves(self):
        with tempfile.TemporaryDirectory() as td:
            root = _r14_repo(
                Path(td), "STORY-953", src_lines=J2_SRC,
                story_text="# s\n\n- AC-1: one.\n",
                manifest_rows="`src/app.py` | subject\n",
                anchors="- `src/app.py:2` — def compute_total\n")
            self.assertEqual(CD.run_gate(root, "STORY-953", "HEAD"), [])


class NumberedAnchorTests(unittest.TestCase):
    """R14-O2: the §4 anchor grammar accepts ORDERED-list markers
    (`1.`) — a numbered ghost anchor is flagged instead of silently
    skipped, numbered valid anchors resolve, and `-`/`*`/`+`/bare forms
    are unchanged."""

    def _repo(self, td: str, sid: str, anchors: str) -> Path:
        return _r8_repo(Path(td), sid, src_lines=J2_SRC,
                        story_text="# s\n\n- AC-1: one.\n",
                        anchors=anchors)

    def test_numbered_ghost_anchor_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(
                td, "STORY-954",
                "1. src/ghost.py:99 — def missing_symbol\n")
            errors = CD.run_gate(root, "STORY-954", "HEAD")
            self.assertIn("anchor path missing at HEAD: src/ghost.py",
                          errors)

    def test_numbered_backtick_ghost_anchor_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(
                td, "STORY-955",
                "12. `src/ghost.py:999` — def nonexistent\n")
            errors = CD.run_gate(root, "STORY-955", "HEAD")
            self.assertIn("anchor path missing at HEAD: src/ghost.py",
                          errors)

    def test_numbered_valid_anchor_resolves(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(
                td, "STORY-956",
                "1. `src/app.py:2` — def compute_total\n"
                "2. `src/app.py:3` — return\n")
            self.assertEqual(CD.run_gate(root, "STORY-956", "HEAD"), [])

    def test_previous_forms_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(
                td, "STORY-957",
                "- `src/app.py:2` — def compute_total\n"
                "* `src/app.py:3` — return\n")
            self.assertEqual(CD.run_gate(root, "STORY-957", "HEAD"), [])

    def test_grammar_accepts_ordered_markers(self):
        pat = r"^\s*(?:[-*+]|\d+\.)?\s*`?([^`:\s]+):(\d+)`?\s*—?\s*([^—]*)"
        for line in ("1. src/ghost.py:99 — def missing_symbol",
                     "12. `src/app.py:12` — def f",
                     "  3. src/app.py:12 — def f",
                     "- `src/app.py:12` — def f",
                     "src/app.py:12 — def f"):
            mm = re.match(pat, line)
            self.assertIsNotNone(mm, line)
            self.assertEqual(mm.group(1).strip("`"), "src/ghost.py"
                             if "ghost" in line else "src/app.py")


def _p1_repo(root: Path, sid: str, spec_acs: int | None,
             digest_acs: int) -> None:
    """Repo builder for the R15-P1 pinned-tree regression tests: commits
    src/app.py, the digest (declaring `digest_acs`) and — when spec_acs is
    not None — stories/<sid>/spec.md with that many ACs."""
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text(
        "# a\ndef f(x):\n    return x\n", encoding="utf-8")
    sdir = root / "stories" / sid
    sdir.mkdir(parents=True)
    if spec_acs is not None:
        body = "".join(f"- AC-{i}: crit {i}.\n" for i in range(1, spec_acs + 1))
        (sdir / "spec.md").write_text(f"# s\n\n{body}", encoding="utf-8")
    (sdir / "context-digest.md").write_text(
        f"# digest — {sid}\n\n"
        "## 1. File manifest\n\n"
        "| path | role |\n|---|---|\n"
        "| `src/app.py` | subject |\n\n"
        f"## 2. Story contract\n\nAcceptance criteria: exactly {digest_acs}.\n\n"
        "## 3. Frozen decisions\n\n- none.\n\n"
        "## 4. Code anchors\n\n- `src/app.py:2` — def f\n\n"
        "## 5. Conventions\n\n- stdlib only.\n\n"
        "## 6. Open questions\n\n- none.\n\n"
        "## 7. Risk list\n\n- drift — M\n\n"
        "## 8. Verification block\n\nBASE_SHA: HEAD\n",
        encoding="utf-8")
    _commit(root)


class PinnedTreeGroundTruthTests(unittest.TestCase):
    """R15-P1: canonical story-file candidates are probed at the PINNED
    sha first — a working-tree layout change must not hide the spec that
    exists at the supplied SHA."""

    def test_local_swap_cannot_bypass_pinned_spec(self):
        """Bypass regression: two-AC spec.md committed at the pin; spec.md
        deleted locally and a one-AC story.md added in the working tree; a
        digest declaring one AC must FAIL (count comes from the pinned
        spec.md = 2)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _p1_repo(root, "STORY-960", spec_acs=2, digest_acs=1)
            (root / "stories" / "STORY-960" / "spec.md").unlink()
            (root / "stories" / "STORY-960" / "story.md").write_text(
                "# s\n\n- AC-1: only one.\n", encoding="utf-8")
            errors = CD.run_gate(root, "STORY-960", "HEAD")
            self.assertIn("AC count mismatch: digest says 1, "
                          "story file has ~2", errors)

    def test_uncommitted_story_working_tree_fallback_still_passes(self):
        """No candidate at the sha → the INTENTIONAL uncommitted-story
        fallback reads the working tree (existing behavior)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _p1_repo(root, "STORY-961", spec_acs=None, digest_acs=1)
            (root / "stories" / "STORY-961" / "story.md").write_text(
                "# s\n\n- AC-1: only one.\n", encoding="utf-8")
            self.assertEqual(CD.run_gate(root, "STORY-961", "HEAD"), [])

    def test_committed_spec_at_pin_wins_over_committed_story(self):
        """Normal committed case: spec.md leads the candidate order AT the
        pin even when story.md also exists (two ACs from spec.md pass;
        the one-AC story.md is never consulted)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _p1_repo(root, "STORY-962", spec_acs=2, digest_acs=2)
            (root / "stories" / "STORY-962" / "story.md").write_text(
                "# s\n\n- AC-1: only one.\n", encoding="utf-8")
            _commit(root)
            self.assertEqual(CD.run_gate(root, "STORY-962", "HEAD"), [])

    def test_pinned_probe_uses_supplied_sha_not_head(self):
        """The probe keys off the SUPPLIED sha: HEAD adds a one-AC
        story.md, but at the older pin only the two-AC spec.md exists —
        a digest declaring one AC still fails against the pin."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _p1_repo(root, "STORY-963", spec_acs=2, digest_acs=1)
            pin = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True).stdout.strip()
            (root / "stories" / "STORY-963" / "story.md").write_text(
                "# s\n\n- AC-1: only one.\n", encoding="utf-8")
            _commit(root)
            errors = CD.run_gate(root, "STORY-963", pin)
            self.assertIn("AC count mismatch: digest says 1, "
                          "story file has ~2", errors)


class AnchorSignatureMemberTests(unittest.TestCase):
    """R24-Y1: a call-signature anchor description checks the MEMBER
    IDENTIFIER immediately before the `(` — `public static Foo Missing()`
    must NOT pass against `public static Foo Bar()` on the shared return
    type alone."""

    def _repo(self, td: str, sid: str, anchors: str) -> Path:
        return _r8_repo(Path(td), sid, src_lines=R8_SRC,
                        story_text="# s\n\n- AC-1: one.\n",
                        anchors=anchors)

    def test_signature_wrong_member_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-970",
                              "- `src/app.py:6` — public static Foo "
                              "Missing()\n")
            errors = CD.run_gate(root, "STORY-970", "HEAD")
            self.assertIn("anchor symbol not on line 6 of src/app.py: "
                          "['Missing']", errors)

    def test_signature_correct_member_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-971",
                              "- `src/app.py:6` — public static Foo Bar()\n")
            self.assertEqual(CD.run_gate(root, "STORY-971", "HEAD"), [])

    def test_signature_with_args_wrong_member_flagged(self):
        """Parenthesized arguments do not change the checked symbol —
        the identifier before `(` is the member."""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-972",
                              "- `src/app.py:6` — public static Foo "
                              "Missing(int x)\n")
            errors = CD.run_gate(root, "STORY-972", "HEAD")
            self.assertIn("anchor symbol not on line 6 of src/app.py: "
                          "['Missing']", errors)

    def test_python_signature_with_colon(self):
        """A `name(...):` form (Python def anchored with the trailing
        colon) checks the member identifier too."""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-973",
                              "- `src/app.py:3` — async def missing():\n")
            errors = CD.run_gate(root, "STORY-973", "HEAD")
            self.assertIn("anchor symbol not on line 3 of src/app.py: "
                          "['missing']", errors)
            root2 = self._repo(td + "b", "STORY-974",
                               "- `src/app.py:3` — async def "
                               "existing(self):\n")
            self.assertEqual(CD.run_gate(root2, "STORY-974", "HEAD"), [])

    def test_non_signature_description_keeps_keyword_skip(self):
        """No call signature in the description → unchanged R8-I4
        behavior: keyword-skip, first symbol token checked."""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-975",
                              "- `src/app.py:3` — async def existing\n")
            self.assertEqual(CD.run_gate(root, "STORY-975", "HEAD"), [])
            root2 = self._repo(td + "b", "STORY-976",
                               "- `src/app.py:3` — async def "
                               "not_present_anywhere\n")
            errors = CD.run_gate(root2, "STORY-976", "HEAD")
            self.assertIn("anchor symbol not on line 3 of src/app.py: "
                          "['not_present_anywhere']", errors)


class ParenOrderedAnchorTests(unittest.TestCase):
    """Round 29 AD2: `1) src/ghost.py:99 — …` used to miss the ordered
    marker regex (only `1.` was accepted) and be silently skipped —
    invalid evidence passed. Both ordered delimiter forms parse."""

    def _repo(self, td: str, sid: str, anchors: str) -> Path:
        return _r8_repo(Path(td), sid, src_lines=R8_SRC,
                        story_text="# s\n\n- AC-1: one.\n",
                        anchors=anchors)

    def test_paren_ghost_anchor_rejected_by_name(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-980",
                              "1) `src/ghost.py:99` — def compute_total\n")
            errors = CD.run_gate(root, "STORY-980", "HEAD")
            self.assertIn("anchor path missing at HEAD: src/ghost.py",
                          errors)

    def test_paren_valid_anchor_resolves(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-981",
                              "1) `src/app.py:3` — async def existing\n")
            self.assertEqual(CD.run_gate(root, "STORY-981", "HEAD"), [])

    def test_paren_symbol_mismatch_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-982",
                              "2) `src/app.py:3` — async def missing\n")
            errors = CD.run_gate(root, "STORY-982", "HEAD")
            self.assertIn("anchor symbol not on line 3 of src/app.py: "
                          "['missing']", errors)

    def test_ordered_marker_regex_grammar(self):
        """Both `1.` and `1)` delimiters parse; other forms unchanged."""
        for line, path, lineno in (
                ("1. `bin/build:3` — set -e", "bin/build", 3),
                ("1) `bin/build:3` — set -e", "bin/build", 3),
                ("12) src/ghost.py:99 — def x", "src/ghost.py", 99),
                ("- `src/app.py:12` — def f", "src/app.py", 12)):
            mm = re.match(r"^\s*(?:[-*+]|\d+[.)])?\s*"
                          r"`?([^`:\s]+):(-?\d+)`?\s*—?\s*([^—]*)",
                          line)
            self.assertIsNotNone(mm, line)
            self.assertEqual(mm.group(1), path)
            self.assertEqual(int(mm.group(2)), lineno)


DECL_ASSIGN_SRC = (
    "// app\n"
    "export const Existing = makeWidget()\n"
    "let total = 0\n"
    "if (a == b) { run(); }\n"
    "function checkSum(a == b) { }\n")


class AnchorDeclaredNameTests(unittest.TestCase):
    """Round 29 AD3: `export const Missing = makeWidget()` used to PASS
    against `export const Existing = makeWidget()` — only the trailing
    initializer call was checked. The assignment form recognizes the
    DECLARED NAME (first non-keyword token before `=`) first."""

    def _repo(self, td: str, sid: str, anchors: str) -> Path:
        return _r8_repo(Path(td), sid, src_lines=DECL_ASSIGN_SRC,
                        story_text="# s\n\n- AC-1: one.\n",
                        anchors=anchors)

    def test_assignment_wrong_declared_name_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-983",
                              "- `src/app.py:2` — export const Missing = "
                              "makeWidget()\n")
            errors = CD.run_gate(root, "STORY-983", "HEAD")
            self.assertIn("anchor symbol not on line 2 of src/app.py: "
                          "['Missing']", errors)

    def test_assignment_matching_declared_name_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-984",
                              "- `src/app.py:2` — export const Existing = "
                              "makeWidget()\n")
            self.assertEqual(CD.run_gate(root, "STORY-984", "HEAD"), [])

    def test_const_assignment_declared_name(self):
        """A keyword (`const`) before `=` is skipped — the DECLARED NAME
        is the checked symbol, not the initializer."""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-985",
                              "- `src/app.py:3` — const subtotal = 0\n")
            errors = CD.run_gate(root, "STORY-985", "HEAD")
            self.assertIn("anchor symbol not on line 3 of src/app.py: "
                          "['subtotal']", errors)

    def test_comparison_operators_not_treated_as_assignment(self):
        """`==` is not an assignment — the description keeps the
        call-signature branch. `total == checkSum()` checks the member
        `checkSum` (on line 5); had `==` matched as an assignment, the
        spurious declared name `total` (NOT on the line) would flag."""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td, "STORY-986",
                              "- `src/app.py:5` — total == checkSum()\n")
            self.assertEqual(CD.run_gate(root, "STORY-986", "HEAD"), [])
            root2 = self._repo(td + "b", "STORY-987",
                               "- `src/app.py:5` — total == "
                               "missingMember()\n")
            errors = CD.run_gate(root2, "STORY-987", "HEAD")
            self.assertIn("anchor symbol not on line 5 of src/app.py: "
                          "['missingMember']", errors)

    def test_pure_signature_branch_unchanged(self):
        """No `=` in the description → the call-signature check applies
        exactly as before (R24-Y1)."""
        with tempfile.TemporaryDirectory() as td:
            root = _r8_repo(Path(td), "STORY-988", src_lines=R8_SRC,
                            story_text="# s\n\n- AC-1: one.\n",
                            anchors="- `src/app.py:6` — public static Foo "
                                    "Missing()\n")
            errors = CD.run_gate(root, "STORY-988", "HEAD")
            self.assertIn("anchor symbol not on line 6 of src/app.py: "
                          "['Missing']", errors)


if __name__ == "__main__":
    unittest.main()
