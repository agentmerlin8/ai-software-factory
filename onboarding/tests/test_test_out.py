#!/usr/bin/env python3
"""Tests for onboarding/test_out.py — the fresh-repo test-out driver.

Coverage map (brief §5):
  * smoke on a tiny synthetic seed (temp-only): rc 0; every expected
    step PASS; verify GREEN; the idempotency assertion observed; the
    kit repo untouched (hash-walk of onboarding/ + benchmark-app/
    before/after).
  * failure propagation: a dropped dataset -> named `FAIL: preflight`
    + rc 1 with clean stderr discipline; a seed carrying a nested
    .git -> named refusal at the seed step + rc 1.
  * --full without --confirm-full-run: rc 1, the plan printed, ZERO
    subprocess calls (stubbed runner records nothing).
  * --seed-dir override honored; --keep prints the temp path (and the
    dir survives for inspection).
  * CLI conventions: rc 2 usage (bad flag; both modes at once);
    exactly ONE `NEXT:` line per run.
  * house laws: stdlib-only imports; LF endings on the driver, this
    file and the smoke dataset; banned-token greps clean.

All smoke runs work on temp dirs with a tiny synthetic seed (the real
benchmark-app seed run is the orchestrator's acceptance, not a unit
test). Stdlib only.
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from onboarding import test_out as TO  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
SMOKE_DS = FIXTURES / "test_out" / "smoke_forge.json"

EXPECTED_STEPS = ("seed", "preflight", "inspect", "propose",
                  "operator-review", "apply", "apply-idempotent",
                  "attests", "verify", "smoke")


def capture(fn, *args, **kwargs):
    """Run fn capturing stdout/stderr; -> (rc, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*args, **kwargs)
    return rc, out.getvalue(), err.getvalue()


def next_count(out: str) -> int:
    return sum(1 for ln in out.splitlines() if ln.startswith("NEXT:"))


def pass_steps(out: str) -> list[str]:
    return [ln[len("PASS: "):].split(" — ", 1)[0].strip()
            for ln in out.splitlines() if ln.startswith("PASS: ")]


def tiny_seed(root: Path) -> Path:
    """The tiny synthetic brownfield seed (one source file + README)."""
    seed = root / "seed"
    (seed / "src").mkdir(parents=True)
    (seed / "src" / "app.py").write_text("print('hello')\n",
                                         encoding="utf-8", newline="\n")
    (seed / "README.md").write_text("# tiny seed\n",
                                    encoding="utf-8", newline="\n")
    return seed


def hash_walk(root: Path) -> dict[str, str]:
    """rel path -> sha256 for every regular file (skip __pycache__)."""
    digests: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digests[str(path.relative_to(root))] = \
                hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


class RecordingRunner:
    """Stubbed subprocess runner: records argv, never executes."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")


class PostureRunner:
    """Stubbed runner: `inspect` prints a fixed posture, every other
    onboard call returns rc 1 so run_pipeline stops right after the
    posture check (no repo writes)."""

    def __init__(self, posture: str):
        self.posture = posture

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess:
        if "inspect" in argv:
            return subprocess.CompletedProcess(
                argv, 0, f"posture: {self.posture}\n", "")
        if "preflight" in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 1, "", "stub: stop after "
                                                       "inspect")


class TestPostureExpectationModeAware(unittest.TestCase):
    """FIX: --full clones a fresh GitHub repo (greenfield), so the
    posture expectation is a parameter: smoke requires brownfield;
    full tolerates greenfield (and brownfield once seeded)."""

    def _run(self, posture: str, postures: tuple[str, ...] | None):
        with tempfile.TemporaryDirectory() as td:
            kwargs = {} if postures is None else {"postures": postures}
            with self.assertRaises(TO.StepFailure) as ctx:
                TO.run_pipeline(Path(td), None, "org/projects/1",
                                PostureRunner(posture),
                                lambda step, detail: None, **kwargs)
            return ctx.exception

    def test_full_tolerates_greenfield(self):
        failure = self._run("greenfield", ("greenfield", "brownfield"))
        # passed the posture gate; stopped at the NEXT stubbed step
        self.assertEqual(failure.step, "propose")

    def test_full_tolerates_brownfield(self):
        failure = self._run("brownfield", ("greenfield", "brownfield"))
        self.assertEqual(failure.step, "propose")

    def test_full_still_rejects_unknown_posture(self):
        failure = self._run("weird", ("greenfield", "brownfield"))
        self.assertEqual(failure.step, "inspect")
        self.assertIn("'weird'", failure.detail)

    def test_smoke_default_still_requires_brownfield(self):
        failure = self._run("greenfield", None)  # default ("brownfield",)
        self.assertEqual(failure.step, "inspect")
        self.assertIn("'greenfield'", failure.detail)
        self.assertIn("'brownfield'", failure.detail)


# ------------------------------------------------------------------- smoke --

class TestSmokeTinySeed(unittest.TestCase):
    def test_smoke_green_end_to_end(self):
        """The full smoke on a tiny synthetic seed: rc 0, every step
        PASS, verify GREEN, idempotency observed, kit repo untouched."""
        before = {p: hash_walk(REPO_ROOT / p)
                  for p in ("onboarding", "benchmark-app", "templates",
                            "tools")}
        with tempfile.TemporaryDirectory() as tmpdir:
            seed = tiny_seed(Path(tmpdir))
            rc, out, err = capture(
                TO.main, ["--smoke", "--seed-dir", str(seed)])
            self.assertEqual(rc, 0, out + err)
            self.assertEqual(err, "")
            self.assertEqual(pass_steps(out), list(EXPECTED_STEPS))
            self.assertIn("verify status: GREEN".replace("verify ", ""),
                          out)  # the verify PASS detail carries GREEN
            self.assertIn("PASS: inspect — rc 0 — posture brownfield", out)
            # idempotency assertion observed (re-apply: EXISTS-only, rc 0)
            self.assertIn("PASS: apply-idempotent", out)
            self.assertRegex(out, r"PASS: apply-idempotent — rc 0 .*EXISTS")
            self.assertEqual(next_count(out), 1)
            self.assertTrue(out.splitlines()[-1].startswith("NEXT:"))
        after = {p: hash_walk(REPO_ROOT / p) for p in before}
        self.assertEqual(before, after)  # temp-only law: the kit is read-only


# ------------------------------------------------------- failure propagation --

class TestFailurePropagation(unittest.TestCase):
    def test_missing_dataset_fails_preflight(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            seed = tiny_seed(Path(tmpdir))
            rc, out, err = capture(TO.main, [
                "--smoke", "--seed-dir", str(seed),
                "--dataset", str(Path(tmpdir) / "no-such-dataset.json")])
            self.assertEqual(rc, 1)
            self.assertEqual(err, "")
            self.assertIn("PASS: seed", out)
            self.assertRegex(out, r"FAIL: preflight — rc=1")
            self.assertEqual(next_count(out), 1)
            # the run STOPS at the first failure
            self.assertNotIn("PASS: inspect", out)

    def test_nested_git_seed_refused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            seed = tiny_seed(Path(tmpdir))
            (seed / ".git").mkdir()
            (seed / ".git" / "HEAD").write_text("ref: refs/heads/main\n",
                                                encoding="utf-8")
            rc, out, err = capture(TO.main, ["--smoke", "--seed-dir",
                                             str(seed)])
            self.assertEqual(rc, 1)
            self.assertEqual(err, "")
            self.assertIn("FAIL: seed — refused: seed carries a nested .git",
                          out)
            self.assertEqual(next_count(out), 1)

    def test_missing_seed_dir_refused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rc, out, _ = capture(TO.main, ["--smoke", "--seed-dir",
                                           str(Path(tmpdir) / "absent")])
            self.assertEqual(rc, 1)
            self.assertIn("FAIL: seed — seed directory not found", out)


# ---------------------------------------------------------------- full guard --

class TestFullGuard(unittest.TestCase):
    def test_full_without_confirm_prints_plan_no_subprocess(self):
        recorder = RecordingRunner()
        original = TO.run_argv
        TO.run_argv = recorder
        try:
            rc, out, err = capture(TO.main, ["--full"])
        finally:
            TO.run_argv = original
        self.assertEqual(rc, 1)
        self.assertEqual(err, "")
        self.assertEqual(recorder.calls, [])  # ZERO subprocess calls
        self.assertIn("refused: --full requires --confirm-full-run", out)
        self.assertIn("unexercised in v0.1-S3", out)
        # the plan is printed (gh repo create appears ONLY as plan text)
        self.assertIn("gh repo create", out)
        self.assertIn("onboard.py verify", out)
        self.assertEqual(next_count(out), 1)


# ------------------------------------------------------- full live stub run --

class FullStubRunner:
    """Stubbed runner for the --full live path: never executes. `git
    clone` materializes the repo dir (with the draft workspace.toml the
    operator-review edit needs); `apply` prints INSTALLED:/EXISTS lines;
    `inspect` reports greenfield. Everything else returns rc 0."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.applied = False

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(list(argv))
        text = " ".join(str(a) for a in argv)
        if "clone" in argv:
            repo = Path(argv[3])
            (repo / "onboarding-drafts").mkdir(parents=True)
            (repo / "onboarding-drafts" / "workspace.toml").write_text(
                'project_id = "<org>/projects/<n>"\n',
                encoding="utf-8", newline="\n")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "inspect" in text:
            return subprocess.CompletedProcess(
                argv, 0, "posture: greenfield\n", "")
        if " apply " in f" {text} ":
            # python_argv form: [python, -B, onboard.py, apply, <repo>, ...]
            repo = Path(argv[4])
            if not self.applied:
                self.applied = True
                out = "INSTALLED: workspace.toml\nINSTALLED: factory/KICKOFF.md\n"
                # materialize the handoff the retention must keep
                (repo / "factory").mkdir(parents=True, exist_ok=True)
                (repo / "factory" / "KICKOFF.md").write_text(
                    "# KICKOFF\n", encoding="utf-8", newline="\n")
                return subprocess.CompletedProcess(argv, 0, out, "")
            return subprocess.CompletedProcess(
                argv, 0,
                "EXISTS, keeping: workspace.toml\n"
                "EXISTS, keeping: factory/KICKOFF.md\n", "")
        if "auth" in argv and "status" in argv:
            return subprocess.CompletedProcess(
                argv, 0, "",
                "✓ Logged in to github.com account fixture-bot\n")
        return subprocess.CompletedProcess(argv, 0, "", "")


def run_full_stubbed(extra_argv: list[str] | None = None):
    """Run --full --confirm-full-run with the stubbed runner + recorded
    append_attest. -> (rc, out, err, runner, attest_calls, tmp_path)."""
    runner = FullStubRunner()
    attest_calls: list[tuple] = []
    original_run, original_attest = TO.run_argv, TO.RD.append_attest
    TO.run_argv = runner
    TO.RD.append_attest = lambda *a, **k: attest_calls.append((a, k))
    try:
        argv = ["--full", "--confirm-full-run", "--repo-name", "fresh-app",
                "--project-id", "fixture-org/projects/7"]
        argv += extra_argv or []
        rc, out, err = capture(TO.main, argv)
    finally:
        TO.run_argv, TO.RD.append_attest = original_run, original_attest
    kept = [ln for ln in out.splitlines() if ln.startswith("kept: ")]
    tmp_path = Path(kept[0].split(": ", 1)[1]) if kept else None
    return rc, out, err, runner, attest_calls, tmp_path


class TestFullLivePathDiscipline(unittest.TestCase):
    """F3 + F8: the LIVE path never appends synthetic attestations and
    RETAINS the checkout with the durable handoff."""

    def test_full_run_never_appends_attestations(self):
        rc, out, _err, _runner, attest_calls, tmp_path = run_full_stubbed()
        try:
            self.assertEqual(rc, 0, out)
            self.assertEqual(attest_calls, [])  # ZERO synthetic rows
            required = [ln for ln in out.splitlines()
                        if ln.startswith("ATTEST REQUIRED: ")]
            self.assertEqual(len(required), len(TO.ATTEST_IDS))
            for cid in TO.ATTEST_IDS:
                self.assertTrue(any(f"attest " in ln and f" {cid} " in ln
                                    for ln in required), cid)
                self.assertTrue(any("--by <you>" in ln and cid in ln
                                    for ln in required), cid)
            self.assertIn("verify", out)  # the operator handoff command
            self.assertEqual(next_count(out), 1)
        finally:
            if tmp_path:
                import shutil
                shutil.rmtree(tmp_path, ignore_errors=True)

    def test_full_run_retains_checkout_and_handoff(self):
        rc, out, _err, _runner, _attests, tmp_path = run_full_stubbed()
        self.assertEqual(rc, 0, out)
        try:
            self.assertIsNotNone(tmp_path)
            self.assertTrue(tmp_path.is_dir())  # NOT deleted on success
            checkout = tmp_path / "fresh-app"
            kept = [ln for ln in out.splitlines()
                    if ln.startswith("kept: ") and "checkout" in ln]
            self.assertEqual(len(kept), 1)
            self.assertIn(str(checkout), kept[0])
            self.assertTrue(checkout.is_dir())
            self.assertTrue((checkout / "factory" / "KICKOFF.md").is_file())
            self.assertIn("the checkout stays; remove it when done", out)
        finally:
            if tmp_path:
                import shutil
                shutil.rmtree(tmp_path, ignore_errors=True)

    def test_full_run_retains_checkout_on_failure(self):
        # a failing pipeline step still keeps the checkout for debugging
        runner_cls = FullStubRunner

        class FailPreflight(runner_cls):
            def __call__(self, argv):
                if "preflight" in " ".join(str(a) for a in argv):
                    self.calls.append(list(argv))
                    return subprocess.CompletedProcess(
                        argv, 1, "", "FAIL forge-auth — refused")
                return super().__call__(argv)

        runner = FailPreflight()
        original_run = TO.run_argv
        TO.run_argv = runner
        try:
            rc, out, _err = capture(TO.main, [
                "--full", "--confirm-full-run", "--repo-name", "fresh-app",
                "--project-id", "fixture-org/projects/7"])
        finally:
            TO.run_argv = original_run
        self.assertEqual(rc, 1)
        kept = [ln for ln in out.splitlines() if ln.startswith("kept: ")]
        self.assertTrue(kept, out)
        tmp_path = Path(kept[0].split(": ", 1)[1])
        self.assertIsNotNone(tmp_path)
        try:
            self.assertTrue(tmp_path.is_dir())  # retained after failure
        finally:
            import shutil
            shutil.rmtree(tmp_path, ignore_errors=True)


# ------------------------------------------------------------ name confinement --

class TestNameConfinement(unittest.TestCase):
    """F4: --name / --repo-name must be a single relative dir name."""

    BAD = ("/abs/path", "../escape", "a/b", "a\\b", ".", "..", "", "x\x00y")

    def test_bad_names_refused_with_named_message(self):
        for bad in self.BAD:
            with self.subTest(name=bad):
                with self.assertRaises(TO.TestOutError) as ctx:
                    TO.validate_dir_name(bad)
                self.assertEqual(
                    str(ctx.exception),
                    f"refused: bad --name '{bad}' — must be a single "
                    "relative directory name")

    def test_good_names_accepted(self):
        for good in ("benchmark-smoke", "fresh-app", "a-b_c.1"):
            TO.validate_dir_name(good)  # no raise

    def test_smoke_bad_name_refused_nothing_created(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            seed = tiny_seed(Path(tmpdir))
            for bad in ("/abs/path", "../escape", "a/b", ".", ".."):
                rc, out, _err = capture(TO.main, [
                    "--smoke", "--name", bad, "--seed-dir", str(seed),
                    "--dataset", str(Path(tmpdir) / "absent.json")])
                self.assertEqual(rc, 1, bad)
                self.assertIn("refused: bad --name", out)
                self.assertEqual(next_count(out), 1)
            # nothing escaped the (never created) temp dir
            self.assertEqual([p.name for p in Path(tmpdir).iterdir()],
                             ["seed"])
            self.assertFalse(Path("../escape").exists())

    def test_full_bad_repo_name_refused_no_subprocess(self):
        recorder = RecordingRunner()
        original = TO.run_argv
        TO.run_argv = recorder
        try:
            rc, out, _err = capture(TO.main, [
                "--full", "--confirm-full-run", "--repo-name", "../escape",
                "--project-id", "fixture-org/projects/7"])
        finally:
            TO.run_argv = original
        self.assertEqual(rc, 1)
        self.assertIn("refused: bad --name '../escape'", out)
        self.assertEqual(recorder.calls, [])  # refused before any subprocess


# --------------------------------------------------------------- seed guards --

class TestSeedSymlinkGuard(unittest.TestCase):
    """F2: a seed symlink is a named refusal BEFORE any copy — the seed
    must be symlink-free (a preserved symlink could point outside the
    temp dir and later writes would mutate the original target)."""

    def test_file_symlink_seed_refused_nothing_copied(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir)
            seed = tiny_seed(td)
            outside = td / "outside.txt"
            outside.write_text("original\n", encoding="utf-8")
            (seed / ".gitignore").symlink_to(outside)
            dest = td / "dest"
            with self.assertRaises(TO.TestOutError) as ctx:
                TO.seed_repo(seed, dest)
            self.assertIn("refused: seed carries a symlink:",
                          str(ctx.exception))
            self.assertIn("(the seed must be symlink-free)",
                          str(ctx.exception))
            self.assertFalse(dest.exists())  # nothing copied
            self.assertEqual(outside.read_text(encoding="utf-8"),
                             "original\n")  # outside untouched

    def test_dir_symlink_seed_refused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir)
            seed = tiny_seed(td)
            outside = td / "outside-dir"
            outside.mkdir()
            (seed / "linked").symlink_to(outside)
            dest = td / "dest"
            with self.assertRaises(TO.TestOutError) as ctx:
                TO.seed_repo(seed, dest)
            self.assertIn("refused: seed carries a symlink:",
                          str(ctx.exception))
            self.assertFalse(dest.exists())

    def test_symlink_free_seed_copies_without_symlinks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir)
            seed = tiny_seed(td)
            dest = td / "dest"
            TO.seed_repo(seed, dest)
            self.assertTrue(dest.is_dir())
            self.assertTrue((dest / "src" / "app.py").is_file())
            survivors = [p for p in dest.rglob("*") if p.is_symlink()]
            self.assertEqual(survivors, [])  # symlink-free copy

    def test_smoke_symlink_seed_fails_at_seed_step(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir)
            seed = tiny_seed(td)
            (seed / "README.md").unlink()
            (seed / "README.md").symlink_to(td / "seed" / "src" / "app.py")
            rc, out, _err = capture(TO.main, ["--smoke", "--seed-dir",
                                              str(seed)])
            self.assertEqual(rc, 1)
            self.assertIn("FAIL: seed — refused: seed carries a symlink",
                          out)
            self.assertEqual(next_count(out), 1)



# ------------------------------------------------------------- seed-dir/keep --

class TestSeedDirAndKeep(unittest.TestCase):
    def test_seed_dir_override_honored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            seed = tiny_seed(Path(tmpdir))
            rc, out, _ = capture(TO.main, [
                "--smoke", "--seed-dir", str(seed),
                "--dataset", str(Path(tmpdir) / "absent.json")])
            self.assertEqual(rc, 1)  # stops early, but the override is proven
            self.assertIn(f"smoke: seed {seed}", out)

    def test_keep_prints_and_retains_the_temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            seed = tiny_seed(Path(tmpdir))
            rc, out, _ = capture(TO.main, [
                "--smoke", "--seed-dir", str(seed), "--keep",
                "--dataset", str(Path(tmpdir) / "absent.json")])
            self.assertEqual(rc, 1)
            kept = [ln for ln in out.splitlines()
                    if ln.startswith("temp dir kept: ")]
            self.assertEqual(len(kept), 1)
            kept_dir = Path(kept[0].split(": ", 1)[1])
            try:
                self.assertTrue(kept_dir.is_dir())
                self.assertTrue((kept_dir / "benchmark-smoke"
                                 / "src" / "app.py").is_file())
            finally:
                import shutil
                shutil.rmtree(kept_dir, ignore_errors=True)


# ----------------------------------------------------------------- CLI shape --

class TestCli(unittest.TestCase):
    def test_usage_rc2(self):
        for argv in (["--bogus"], ["--smoke", "--full"]):
            with self.subTest(argv=argv):
                rc, _out, _err = capture(TO.main, argv)
                self.assertEqual(rc, 2)

    def test_one_next_line_per_run(self):
        # smoke green run + full refusal + seed failure all end with
        # exactly ONE NEXT: line (the green case is covered above).
        rc, out, _ = capture(TO.main, ["--full"])
        self.assertEqual((rc, next_count(out)), (1, 1))
        with tempfile.TemporaryDirectory() as tmpdir:
            rc, out, _ = capture(TO.main, ["--smoke", "--seed-dir",
                                           str(Path(tmpdir) / "absent")])
            self.assertEqual((rc, next_count(out)), (1, 1))


# -------------------------------------------------------------- house laws --

class TestHouseLaws(unittest.TestCase):
    TOUCHED = (REPO_ROOT / "onboarding" / "test_out.py",
               Path(__file__).resolve(),
               SMOKE_DS)

    def test_stdlib_only_imports(self):
        src = (REPO_ROOT / "onboarding" / "test_out.py").read_text(
            encoding="utf-8")
        allowed = {"onboarding", "tools"}
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    self.assertTrue(
                        root in sys.stdlib_module_names or root in allowed,
                        f"non-stdlib import: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                root = (node.module or "").split(".")[0]
                self.assertTrue(
                    root in sys.stdlib_module_names or root in allowed,
                    f"non-stdlib import: {node.module}")

    def test_lf_endings(self):
        for path in self.TOUCHED:
            data = path.read_bytes()
            self.assertNotIn(b"\r\n", data, f"CRLF in {path}")
            self.assertTrue(data.endswith(b"\n"), f"no trailing newline: {path}")

    def test_banned_tokens_absent(self):
        # built by concatenation so THIS file passes the same grep
        tokens = ["po" + "dc" + "ast", "me" + "dia.ing" + "estion",
                  "dap" + "per", "cost" + "entry", "ana" + "lysis.live",
                  "ra" + "ndy", "agent" + "merlin", "dot" + "net",
                  "m" + "tp", "cam" + "paign", "stra" + "tum",
                  "whis" + "per", "epi" + "sode"]
        pattern = re.compile("|".join(tokens), re.IGNORECASE)
        story = re.compile("STORY-" + "[0-9]")
        for path in self.TOUCHED:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(pattern.search(text), f"banned token in {path}")
            self.assertIsNone(story.search(text), f"real story id in {path}")

    def test_smoke_dataset_shape(self):
        import json
        data = json.loads(SMOKE_DS.read_text(encoding="utf-8"))
        self.assertEqual(data["label_ensure"], {"ok": True})
        rs = data["ruleset@fixture-org/benchmark-app"]["rulesets"]
        self.assertEqual(len(rs), 1)
        self.assertEqual(rs[0]["enforcement"], "active")
        self.assertEqual(len(rs[0]["bypass_actors"]), 1)
        self.assertEqual(rs[0]["bypass_actors"][0]["actor_type"],
                         "Integration")
        self.assertIn("ruleset", data)  # the generic entry is kept


if __name__ == "__main__":
    unittest.main()
