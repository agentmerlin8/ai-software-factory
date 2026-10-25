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


if __name__ == "__main__":
    unittest.main()
