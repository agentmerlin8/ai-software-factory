#!/usr/bin/env python3
"""tools/tests/test_make_context_bundle.py — line-counting + assembly
tests for tools/make_context_bundle.py (kit conventions: stdlib only,
throwaway git repos, deterministic assertions).

Covers the F6 fix: an N-line source reports N in the manifest and the
per-source header — with AND without a final newline — and an empty
source reports 0 (the old `count("\\n") + 1` over-counted both). Plus a
bundle-assembles smoke check (rc 0, manifest + sha256 present).
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import make_context_bundle as MCB  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "make_context_bundle.py"


def make_repo(td: str, files: dict[str, str]) -> Path:
    """Throwaway git repo with `files` committed; returns its root."""
    root = Path(td)
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-qm", "fixture"]):
        subprocess.run(["git", "-C", str(root), *args], check=True,
                       capture_output=True)
    return root


def run_bundle(root: Path, out: Path,
               sources: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "STORY-NNN", "--root", str(root),
         "--sha", "HEAD", *sources, "-o", str(out)],
        capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


class LineCountingTests(unittest.TestCase):
    def test_n_lines_with_final_newline_reports_n(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(td, {"src/a.py": "one\ntwo\nthree\n"})
            out = Path(td) / "bundle.md"
            rc, _stdout, stderr = run_bundle(root, out, ["src/a.py"])
            self.assertEqual(rc, 0, stderr)
            text = out.read_text(encoding="utf-8")
            self.assertIn("| `src/a.py` |", text)
            self.assertIn("| 3 |", text)              # manifest cell
            self.assertIn("(3 lines, pinned at", text)  # source header

    def test_n_lines_without_final_newline_reports_n(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(td, {"src/a.py": "one\ntwo\nthree"})
            out = Path(td) / "bundle.md"
            rc, _stdout, stderr = run_bundle(root, out, ["src/a.py"])
            self.assertEqual(rc, 0, stderr)
            text = out.read_text(encoding="utf-8")
            self.assertIn("| 3 |", text)
            self.assertIn("(3 lines, pinned at", text)

    def test_empty_file_reports_zero(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(td, {"src/empty.txt": ""})
            out = Path(td) / "bundle.md"
            rc, _stdout, stderr = run_bundle(root, out, ["src/empty.txt"])
            self.assertEqual(rc, 0, stderr)
            text = out.read_text(encoding="utf-8")
            self.assertIn("| `src/empty.txt` | 0 | 0 |", text)
            self.assertIn("(0 lines, pinned at", text)

    def test_splitlines_is_the_counting_rule(self):
        """Direct unit check of the counting expression's semantics."""
        self.assertEqual(len("a\nb\nc\n".splitlines()), 3)
        self.assertEqual(len("a\nb\nc".splitlines()), 3)
        self.assertEqual(len("".splitlines()), 0)


class BundleAssemblyTests(unittest.TestCase):
    def test_bundle_assembles_rc0(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(td, {"src/a.py": "x = 1\n",
                                  "docs/notes.md": "# notes\n"})
            out = Path(td) / "stories" / "STORY-NNN" / "context-bundle.md"
            rc, stdout, stderr = run_bundle(
                root, out, ["src/a.py", "docs/notes.md"])
            self.assertEqual(rc, 0, stderr)
            text = out.read_text(encoding="utf-8")
            self.assertIn("# Context Bundle — STORY-NNN", text)
            self.assertIn("## Bundle manifest", text)
            self.assertIn("bundle sha256:", text)
            self.assertIn("## Source: `src/a.py`", text)
            self.assertIn("## Source: `docs/notes.md`", text)
            self.assertIn("wrote", stdout)
            # totals line uses the same splitlines rule
            total = len(text.splitlines())
            self.assertIn(f"total lines: {total}", stdout)

    def test_missing_source_refuses_rc2(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(td, {"src/a.py": "x = 1\n"})
            out = Path(td) / "bundle.md"
            rc, _stdout, stderr = run_bundle(root, out, ["src/ghost.py"])
            self.assertEqual(rc, 2)
            self.assertIn("not present at the pinned SHA", stderr)


class VerbatimSourceTests(unittest.TestCase):
    """Sources are inserted VERBATIM — no rstrip: trailing whitespace and
    blank lines survive, a whitespace-only source stays visible, and the
    bundle bytes stay deterministic."""

    def test_trailing_whitespace_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(td, {"src/a.py": "x = 1   \n\nkeep me   \n"})
            out = Path(td) / "bundle.md"
            rc, _stdout, stderr = run_bundle(root, out, ["src/a.py"])
            self.assertEqual(rc, 0, stderr)
            text = out.read_text(encoding="utf-8")
            # the exact source bytes appear untouched in the bundle
            self.assertIn("x = 1   \n\nkeep me   \n", text)

    def test_whitespace_only_source_visible(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(td, {"src/ws.txt": "   \n\n  \t\n"})
            out = Path(td) / "bundle.md"
            rc, _stdout, stderr = run_bundle(root, out, ["src/ws.txt"])
            self.assertEqual(rc, 0, stderr)
            text = out.read_text(encoding="utf-8")
            self.assertIn("## Source: `src/ws.txt`", text)
            # the whitespace-only bytes survive between header and next part
            self.assertIn("## Source: `src/ws.txt`\n\n   \n\n  \t\n", text)

    def test_counts_match_untrimmed_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            src = "one   \ntwo\n\n"
            root = make_repo(td, {"src/a.py": src})
            out = Path(td) / "bundle.md"
            rc, _stdout, stderr = run_bundle(root, out, ["src/a.py"])
            self.assertEqual(rc, 0, stderr)
            text = out.read_text(encoding="utf-8")
            # manifest counts the UNTRIMMED content (3 lines, all bytes)
            self.assertIn(f"| `src/a.py` | {len(src.encode()):,} | 3 |", text)

    def test_bundle_sha_deterministic_across_runs(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(td, {"src/a.py": "x = 1   \n",
                                  "src/ws.txt": "  \n"})
            out1 = Path(td) / "b1.md"
            out2 = Path(td) / "b2.md"
            rc1, _o1, e1 = run_bundle(root, out1, ["src/a.py", "src/ws.txt"])
            rc2, _o2, e2 = run_bundle(root, out2, ["src/a.py", "src/ws.txt"])
            self.assertEqual((rc1, rc2), (0, 0), (e1, e2))
            h1 = hashlib.sha256(out1.read_bytes()).hexdigest()
            h2 = hashlib.sha256(out2.read_bytes()).hexdigest()
            self.assertEqual(h1, h2)
            # source without trailing newline still renders cleanly
            root2 = make_repo(str(Path(td) / "r2"), {"src/b.py": "y = 2"})
            out3 = Path(td) / "b3.md"
            rc3, _o3, e3 = run_bundle(root2, out3, ["src/b.py"])
            self.assertEqual(rc3, 0, e3)
            text3 = out3.read_text(encoding="utf-8")
            self.assertIn("## Source: `src/b.py`\n\ny = 2\n", text3)


class RawBytesTests(unittest.TestCase):
    """R3-D5: show_at must capture stdout as RAW BYTES — text=True
    applied universal-newline translation, so a committed CRLF file was
    silently LF-normalized and the manifest byte count lied. The bundle
    must carry CRLF verbatim and the manifest bytes must equal the git
    blob size."""

    def _repo_bytes(self, td: str, files: dict[str, bytes]) -> Path:
        root = Path(td)
        root.mkdir(parents=True, exist_ok=True)
        for rel, data in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        for args in (["init", "-q"], ["add", "-A"],
                     ["-c", "user.email=t@t", "-c", "user.name=t",
                      "commit", "-qm", "fixture"]):
            subprocess.run(["git", "-C", str(root), *args], check=True,
                           capture_output=True)
        return root

    def _blob_size(self, root: Path, rel: str) -> int:
        oid = subprocess.run(
            ["git", "-C", str(root), "rev-parse", f"HEAD:{rel}"],
            capture_output=True, text=True, check=True).stdout.strip()
        return int(subprocess.run(
            ["git", "-C", str(root), "cat-file", "-s", oid],
            capture_output=True, text=True, check=True).stdout.strip())

    def test_crlf_file_verbatim_and_manifest_matches_blob(self):
        crlf = b"one\r\ntwo\r\nthree\r\n"
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_bytes(td, {"src/crlf.txt": crlf})
            out = Path(td) / "bundle.md"
            rc, _stdout, stderr = run_bundle(root, out, ["src/crlf.txt"])
            self.assertEqual(rc, 0, stderr)
            raw = out.read_bytes()
            # CRLF survives verbatim — no newline translation
            self.assertIn(b"one\r\ntwo\r\nthree\r\n", raw)
            self.assertNotIn(b"one\ntwo\nthree", raw.replace(b"\r\n", b""))
            # manifest byte count == git blob size (18, not the
            # LF-normalized 15)
            size = self._blob_size(root, "src/crlf.txt")
            self.assertEqual(size, len(crlf))
            self.assertIn(f"| `src/crlf.txt` | {size:,} | 3 |",
                          raw.decode("utf-8"))

    def test_lf_file_unchanged(self):
        lf = b"one\ntwo\n"
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_bytes(td, {"src/lf.txt": lf})
            out = Path(td) / "bundle.md"
            rc, _stdout, stderr = run_bundle(root, out, ["src/lf.txt"])
            self.assertEqual(rc, 0, stderr)
            text = out.read_text(encoding="utf-8")
            self.assertIn(f"| `src/lf.txt` | {len(lf):,} | 2 |", text)
            self.assertIn("## Source: `src/lf.txt`\n\none\ntwo\n", text)

    def test_crlf_bundle_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_bytes(
                td, {"src/crlf.txt": b"a\r\nb\r\n", "src/lf.txt": b"c\n"})
            out1, out2 = Path(td) / "b1.md", Path(td) / "b2.md"
            rc1, _o1, e1 = run_bundle(
                root, out1, ["src/crlf.txt", "src/lf.txt"])
            rc2, _o2, e2 = run_bundle(
                root, out2, ["src/crlf.txt", "src/lf.txt"])
            self.assertEqual((rc1, rc2), (0, 0), (e1, e2))
            self.assertEqual(
                hashlib.sha256(out1.read_bytes()).hexdigest(),
                hashlib.sha256(out2.read_bytes()).hexdigest())

    def test_show_at_returns_raw_byte_count(self):
        """Direct unit check: show_at returns (text, RAW byte length) —
        the byte count includes every \\r of a CRLF blob."""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_bytes(td, {"src/crlf.txt": b"x\r\ny\r\n"})
            content, nbytes = MCB.show_at(root, "HEAD", "src/crlf.txt")
            self.assertEqual(content, "x\r\ny\r\n")
            self.assertEqual(nbytes, 6)
            self.assertEqual(nbytes, self._blob_size(root, "src/crlf.txt"))


class DestinationConfinementTests(unittest.TestCase):
    """Round 25 Z1: story_id becomes a path component of the default
    destination — a traversal id (`../../outside`) must refuse with a
    named error and write NOTHING outside <root>/stories."""

    def _run_default(self, root: Path, story_id: str,
                     sources: list[str]) -> tuple[int, str, str]:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), story_id, "--root", str(root),
             "--sha", "HEAD", *sources],
            capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    def test_traversal_id_refused_nothing_written_outside(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(str(Path(td) / "repo"), {"src/a.py": "x = 1\n"})
            rc, _stdout, stderr = self._run_default(
                root, "../../outside", ["src/a.py"])
            self.assertEqual(rc, 2)
            self.assertIn("refusing story id '../../outside'", stderr)
            self.assertIn("escapes the stories directory", stderr)
            # canonical-shape guidance in the same message
            self.assertIn("STORY-NNN", stderr)
            # nothing created outside the repo (nor inside it)
            self.assertFalse((Path(td) / "outside").exists())
            self.assertFalse((root / "stories").exists())

    def test_canonical_id_default_dest_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(str(Path(td) / "repo"), {"src/a.py": "x = 1\n"})
            dest = root / "stories" / "STORY-NNN" / "context-bundle.md"
            rc, stdout, stderr = self._run_default(
                root, "STORY-NNN", ["src/a.py"])
            self.assertEqual(rc, 0, stderr)
            self.assertIn(f"wrote {dest}", stdout)
            self.assertTrue(dest.exists())

    def test_noncanonical_but_confined_id_still_writes_under_stories(self):
        """A non-canonical id that resolves strictly under <root>/stories
        (no separators, no traversal) keeps working — confinement, not a
        canonical-only gate, is the safety property."""
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(str(Path(td) / "repo"), {"src/a.py": "x = 1\n"})
            rc, _stdout, stderr = self._run_default(
                root, "draft-notes", ["src/a.py"])
            self.assertEqual(rc, 0, stderr)
            dest = root / "stories" / "draft-notes" / "context-bundle.md"
            self.assertTrue(dest.exists())

    # ---- round 26 AA1: the resolved-path check covers CANONICAL ids too
    def test_symlinked_canonical_story_dir_refused_nothing_written_outside(
            self):
        """A canonical STORY-NNN spelling proves nothing about the
        destination: with stories/STORY-001 symlinked to an unrelated
        directory, the default-output invocation must refuse (rc 2) and
        write NOTHING through the symlink — the old spelling-only fast
        path exited 0 and wrote outside."""
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(str(Path(td) / "repo"), {"src/a.py": "x = 1\n"})
            outside = Path(td) / "outside"
            outside.mkdir()
            (root / "stories").mkdir()
            (root / "stories" / "STORY-001").symlink_to(outside)
            rc, _stdout, stderr = self._run_default(
                root, "STORY-001", ["src/a.py"])
            self.assertEqual(rc, 2)
            self.assertIn("refusing story id 'STORY-001'", stderr)
            self.assertIn("escapes the stories directory", stderr)
            # nothing written through the symlink
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse((outside / "context-bundle.md").exists())

    def test_symlinked_noncanonical_story_dir_refused_too(self):
        """Same confinement for a non-canonical id whose story directory
        is a symlink out of <root>/stories."""
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(str(Path(td) / "repo"), {"src/a.py": "x = 1\n"})
            outside = Path(td) / "outside"
            outside.mkdir()
            (root / "stories").mkdir()
            (root / "stories" / "draft-notes").symlink_to(outside)
            rc, _stdout, stderr = self._run_default(
                root, "draft-notes", ["src/a.py"])
            self.assertEqual(rc, 2)
            self.assertIn("escapes the stories directory", stderr)
            self.assertEqual(list(outside.iterdir()), [])

    def test_symlink_inside_stories_still_writes(self):
        """A story dir symlinked to ANOTHER location inside <root>/stories
        resolves under the stories root — confined, so it writes."""
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(str(Path(td) / "repo"), {"src/a.py": "x = 1\n"})
            (root / "stories").mkdir()
            real = root / "stories" / "real-dir"
            real.mkdir()
            (root / "stories" / "STORY-002").symlink_to(real)
            rc, _stdout, stderr = self._run_default(
                root, "STORY-002", ["src/a.py"])
            self.assertEqual(rc, 0, stderr)
            self.assertTrue((real / "context-bundle.md").exists())

    def test_explicit_out_override_is_the_callers_own_path(self):
        """--out is an explicit caller-chosen destination, not id-derived:
        it is taken as given (documented override), including paths
        outside <root>/stories — the same file the caller could write."""
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(str(Path(td) / "repo"), {"src/a.py": "x = 1\n"})
            out = Path(td) / "explicit.md"
            rc, _stdout, stderr = run_bundle(root, out, ["src/a.py"])
            self.assertEqual(rc, 0, stderr)
            self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
