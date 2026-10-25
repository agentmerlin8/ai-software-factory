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
        mm = re.match(r"^\s*-?\s*`?([^`:\s]+\.\w+):(\d+)`?\s*—?\s*([^—]*)",
                      line)
        self.assertIsNotNone(mm)
        self.assertEqual(mm.group(1), "src/app/widget.py")
        self.assertEqual(int(mm.group(2)), 12)
        self.assertIn("compute_total", mm.group(3))

    def test_non_anchor_lines_ignored(self):
        mm = re.match(r"^\s*-?\s*`?([^`:\s]+\.\w+):(\d+)`?\s*—?\s*([^—]*)",
                      "a prose sentence without anchors")
        self.assertIsNone(mm)


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
                          "(looked at stories/STORY-904/story.md, "
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


class StoryFileCandidatesTests(unittest.TestCase):
    def test_kit_layout_first(self):
        root = Path("/tmp/x")
        cands = CD.story_file_candidates(root, "STORY-901")
        self.assertEqual(
            cands,
            [root / "stories" / "STORY-901" / "story.md",
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


if __name__ == "__main__":
    unittest.main()
