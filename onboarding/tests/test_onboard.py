#!/usr/bin/env python3
"""Tests for onboarding/onboard.py — the onboarding pipeline CLI.

Coverage map (brief §4):
  * preflight: green fixture rc 0 + PASS lines; non-git dir named FAIL;
    gh absent (PATH-controlled) named FAIL; forge fixture dataset used;
    NEVER writes (hash-walk).
  * inspect: greenfield/brownfield/bundled postures + facts; --json
    parseable + deterministic (two runs byte-equal).
  * propose: drafts written + config_validator-clean; deferred key list;
    refuses over an existing workspace.toml; deterministic regeneration;
    --strict + deferred -> rc 1.
  * apply: the ENTIRE install set (paths + content spot-checks incl.
    KICKOFF's story/<id>-<slug>); idempotency (second run EXISTS-only,
    tree sha256-walk unchanged); never-overwrite (a pre-placed user file
    survives byte-identical); dry-run (zero writes + planned lines +
    exact label argv); posture extras per mood; label ensures recorded
    by a fixture transport (exactly the registry base labels); record
    shape (+ --ui variant) and never re-written by a second apply.
  * integration: apply into a git-init'd temp target, then
    readiness.main verify with an aligned dataset -> GREEN rc 0, zero
    FAIL lines (attests appended via readiness.append_attest).
  * CLI: rc 2 usage; every command prints exactly ONE NEXT: line;
    verify delegation passthrough (rc + stdout verbatim).
  * house laws: stdlib-only imports; LF endings; no __pycache__ created
    by any command; banned-token greps clean.

All runs work on shutil.copytree tmp copies (committed fixtures stay
pristine). Stdlib only.
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from onboarding import forge_probe as FP  # noqa: E402
from onboarding import onboard as OB  # noqa: E402
from onboarding import readiness as RD  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
PIPELINE = FIXTURES / "pipeline"
FORGE_DS = FIXTURES / "forge" / "control_home_good.json"
ORIGIN = "https://github.com/fixture-org/workspace.git"
# G4: dedicated propose refuses a self-binding — the tests' homes carry
# the workspace origin, so plain `propose` (default dedicated) passes a
# DISTINCT binding url.
DISTINCT_REPO_URL = "github.com/fixture-org/app"
DISTINCT_ARGS = ("--repo-url", DISTINCT_REPO_URL)

GIT_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def capture(fn, *args, **kwargs):
    """Run fn capturing stdout/stderr; -> (rc, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*args, **kwargs)
    return rc, out.getvalue(), err.getvalue()


def hash_walk(root: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root))
            digests[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def next_count(out: str) -> int:
    return sum(1 for ln in out.splitlines() if ln.startswith("NEXT:"))


def git_init_target(home: Path, origin: str | None = ORIGIN) -> Path:
    subprocess.run(["git", "init", "-b", "main", str(home)],
                   capture_output=True, check=True,
                   env={**os.environ, **GIT_ENV})
    if origin:
        subprocess.run(["git", "-C", str(home), "remote", "add", "origin",
                        origin], capture_output=True, check=True)
    return home


def dataset_with_label_ensure(tmp: Path, ok: bool = True) -> Path:
    """control_home_good.json + a label_ensure recorder entry."""
    data = json.loads(FORGE_DS.read_text(encoding="utf-8"))
    data["label_ensure"] = {"ok": ok}
    path = tmp / "dataset.json"
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    return path


class RecordingForge:
    """Fixture transport + a label_ensure recorder (argv captured)."""

    def __init__(self, dataset: Path):
        self.calls: list[dict] = []
        base = FP.build_fixture_transport(dataset)

        def transport(op: str, params: dict) -> dict:
            if op == "label_ensure":
                self.calls.append(dict(params))
                return {"ok": True}
            return base(op, params)

        self.forge = FP.Forge(transport)


def make_tmp_target(tmpdir: str, mood: str = "greenfield",
                    origin: str | None = ORIGIN) -> tuple[Path, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="onboard-test.", dir=tmpdir))
    home = tmp / "target"
    shutil.copytree(PIPELINE / mood, home)
    git_init_target(home, origin)
    return tmp, home


# --------------------------------------------------------------- preflight --

class TestPreflight(unittest.TestCase):
    def test_green_fixture_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            rc, out, _err = capture(
                OB.main, ["preflight", str(home),
                          "--forge-fixture", str(FORGE_DS)])
            self.assertEqual(rc, 0)
            for name in ("python3", "git", "gh", "forge-auth",
                         "target-git-worktree"):
                self.assertIn(f"PASS {name}", out)
            self.assertEqual(next_count(out), 1)
            self.assertIn("inspect", out.splitlines()[-1])

    def test_non_git_dir_named_fail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plain = Path(tmpdir) / "plain"
            plain.mkdir()
            rc, out, _err = capture(
                OB.main, ["preflight", str(plain),
                          "--forge-fixture", str(FORGE_DS)])
            self.assertEqual(rc, 1)
            self.assertIn("FAIL target-git-worktree", out)
            self.assertEqual(next_count(out), 1)

    def test_missing_target_named_fail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rc, out, _err = capture(
                OB.main, ["preflight", str(Path(tmpdir) / "nope"),
                          "--forge-fixture", str(FORGE_DS)])
            self.assertEqual(rc, 1)
            self.assertIn("FAIL target-git-worktree", out)
            self.assertIn("target not found", out)

    def test_gh_absent_named_fail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            empty_bin = Path(tmpdir) / "empty-bin"
            empty_bin.mkdir()
            old_path = os.environ["PATH"]
            os.environ["PATH"] = str(empty_bin)
            try:
                rc, out, _err = capture(
                    OB.main, ["preflight", str(home),
                              "--forge-fixture", str(FORGE_DS)])
            finally:
                os.environ["PATH"] = old_path
            self.assertEqual(rc, 1)
            self.assertIn("FAIL gh", out)
            self.assertIn("FAIL git", out)

    def test_forge_refusal_is_named_fail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            refused = Path(tmpdir) / "refused.json"
            refused.write_text(json.dumps(
                {"auth_scopes": {"error": "auth refused by fixture"}}) + "\n",
                encoding="utf-8", newline="\n")
            rc, out, _err = capture(
                OB.main, ["preflight", str(home),
                          "--forge-fixture", str(refused)])
            self.assertEqual(rc, 1)
            self.assertIn("FAIL forge-auth", out)
            self.assertIn("cannot probe: auth refused by fixture", out)

    def test_preflight_never_writes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            before = hash_walk(home)
            capture(OB.main, ["preflight", str(home),
                              "--forge-fixture", str(FORGE_DS)])
            self.assertEqual(hash_walk(home), before)


# ----------------------------------------------------------------- inspect --

class TestInspect(unittest.TestCase):
    def _inspect(self, mood: str):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir, mood)
            rc, out, _err = capture(OB.main, ["inspect", str(home)])
            self.assertEqual(rc, 0)
            return home, out

    def test_postures(self):
        for mood in ("greenfield", "brownfield", "bundled"):
            with self.subTest(mood=mood):
                _home, out = self._inspect(mood)
                self.assertIn(f"posture: {mood}", out)
                self.assertEqual(next_count(out), 1)

    def test_facts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir, "brownfield")
            rc, out, _err = capture(OB.main, ["inspect", str(home)])
            self.assertEqual(rc, 0)
            self.assertIn("origin_url: " + ORIGIN, out)
            self.assertIn("current_branch: main", out)
            self.assertIn("default_branch_guess: main", out)
            self.assertIn("existing.workspace_toml: False", out)
            self.assertIn("source_files: 1", out)
            self.assertIn(f"kit_head_sha: {OB.kit_head_sha()}", out)

    def test_missing_target_refused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rc, out, _err = capture(
                OB.main, ["inspect", str(Path(tmpdir) / "nope")])
            self.assertEqual(rc, 1)
            self.assertIn("refused: target not found", out)
            self.assertEqual(next_count(out), 1)

    def test_json_parseable_and_deterministic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir, "bundled")
            rc1, out1, _ = capture(OB.main, ["inspect", str(home), "--json"])
            rc2, out2, _ = capture(OB.main, ["inspect", str(home), "--json"])
            self.assertEqual((rc1, rc2), (0, 0))
            body1 = out1[:out1.index("NEXT:")]
            body2 = out2[:out2.index("NEXT:")]
            self.assertEqual(body1, body2)  # byte-equal across runs
            data = json.loads(body1)
            self.assertEqual(data["posture"], "bundled")
            self.assertEqual(sorted(data["bundle_docs"]),
                             ["architecture", "prd", "test-strategy"])
            self.assertEqual(next_count(out1), 1)


# ----------------------------------------------------------------- propose --

class TestPropose(unittest.TestCase):
    def test_drafts_written_and_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            rc, out, _err = capture(OB.main, ["propose", str(home), *DISTINCT_ARGS])
            self.assertEqual(rc, 0)
            drafts = home / "onboarding-drafts"
            self.assertTrue((drafts / "workspace.toml").is_file())
            self.assertTrue((drafts / "environments/local.toml").is_file())
            self.assertTrue((drafts / "environments/github.toml").is_file())
            for path in (drafts / "workspace.toml",
                         drafts / "environments/local.toml",
                         drafts / "environments/github.toml"):
                self.assertIn(f"WROTE: {path}", out)
                self.assertIn(f"OK {path}", out)  # config_validator clean
            text = (drafts / "workspace.toml").read_text(encoding="utf-8")
            self.assertIn(f'pin = "{OB.kit_head_sha()}"', text)
            self.assertIn(f'url = "{DISTINCT_REPO_URL}"', text)
            self.assertNotIn("id = \"web\"", text)  # example member dropped
            self.assertEqual(next_count(out), 1)

    def test_deferred_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            rc, out, _err = capture(OB.main, ["propose", str(home), *DISTINCT_ARGS])
            self.assertEqual(rc, 0)
            deferred = {ln[len("DEFERRED: "):] for ln in out.splitlines()
                        if ln.startswith("DEFERRED: ")}
            self.assertEqual(deferred, {
                "workspace.toml:board.project_id",
                "local.toml:state.transcripts",
                "local.toml:models.T1", "local.toml:models.T2",
                "local.toml:models.T3",
                "github.toml:models.T1", "github.toml:models.T2",
                "github.toml:models.T3",
            })

    def test_substitution_flags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            rc, out, _err = capture(OB.main, [
                "propose", str(home), "--workspace-name", "my-space",
                "--control-home", "in-repo", "--repo-id", "svc",
                "--repo-url", "github.com/acme/svc",
                "--pin", OB.kit_head_sha(), "--envs", "local"])
            self.assertEqual(rc, 0)
            text = (home / "onboarding-drafts"
                    / "workspace.toml").read_text(encoding="utf-8")
            self.assertIn('workspace = "my-space"', text)
            self.assertIn('control_home = "in-repo"', text)
            self.assertIn(f'pin = "{OB.kit_head_sha()}"', text)
            self.assertIn('id = "svc"', text)
            self.assertIn('url = "github.com/acme/svc"', text)
            self.assertFalse((home / "onboarding-drafts"
                              / "environments/github.toml").exists())

    def test_refuses_existing_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            (home / "workspace.toml").write_text("workspace = \"x\"\n",
                                                 encoding="utf-8",
                                                 newline="\n")
            rc, out, _err = capture(OB.main, ["propose", str(home), *DISTINCT_ARGS])
            self.assertEqual(rc, 1)
            self.assertIn("already onboarded — propose not applicable; "
                          "edit config directly", out)
            self.assertEqual(next_count(out), 1)

    def test_deterministic_regeneration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            capture(OB.main, ["propose", str(home), *DISTINCT_ARGS])
            first = hash_walk(home / "onboarding-drafts")
            capture(OB.main, ["propose", str(home), *DISTINCT_ARGS])  # re-runs over drafts
            self.assertEqual(hash_walk(home / "onboarding-drafts"), first)

    def test_strict_refuses_deferred(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            rc, out, _err = capture(OB.main, ["propose", str(home),
                                              "--strict", *DISTINCT_ARGS])
            self.assertEqual(rc, 1)
            self.assertIn("refused: --strict with 8 deferred", out)
            self.assertEqual(next_count(out), 1)

    def test_bad_flags_refused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            for flag, value, needle in (
                    ("--workspace-name", "Bad Name", "bad workspace name"),
                    ("--repo-id", "9x", "bad repo id"),
                    ("--pin", "not a pin!!", "bad pin"),
                    ("--envs", "nope", "unknown environment template")):
                with self.subTest(flag=flag):
                    rc, out, _err = capture(
                        OB.main, ["propose", str(home), flag, value])
                    self.assertEqual(rc, 1)
                    self.assertIn(needle, out)
                    self.assertEqual(next_count(out), 1)

    def test_envs_traversal_refused_before_path_use(self):
        # each --envs value must be a single path component — a
        # traversal name is a named refusal (no draft, one NEXT line).
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            for bad in ("../local", "sub/dir", "..", "."):
                with self.subTest(env=bad):
                    rc, out, _err = capture(
                        OB.main, ["propose", str(home),
                                  *DISTINCT_ARGS, "--envs", bad])
                    self.assertEqual(rc, 1, out)
                    self.assertIn("bad environment name", out)
                    self.assertEqual(next_count(out), 1)
                    self.assertFalse((home / "onboarding-drafts").exists())

    def test_bad_repo_url_refused_before_substitution(self):
        # a value containing a quote/newline (or any non-URL shape) is a
        # named refusal — it is never substituted into the TOML draft.
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            bad_values = (
                'github.com/acme/app"\n[[repos]]\nid = "evil"',
                "not a url with spaces",
            )
            for bad in bad_values:
                with self.subTest(value=bad):
                    rc, out, _err = capture(
                        OB.main, ["propose", str(home), "--repo-url", bad])
                    self.assertEqual(rc, 1, out)
                    self.assertIn("bad repo-url", out)
                    self.assertEqual(next_count(out), 1)
                    self.assertFalse((home / "onboarding-drafts").exists())


class TestProposeDedicatedSelfBindingGuard(unittest.TestCase):
    """G4: dedicated mode must never draft a self-binding — propose
    refuses (named, actionable) instead of creating a workspace whose
    primary binding is the control home's own origin (C-3's target
    variant would reject the factory bypass landing on the home)."""

    def test_dedicated_with_inferred_self_binding_refused(self):
        # no --repo-url: the binding is inferred from the target's own
        # origin -> self-binding under the default dedicated mode
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            rc, out, _err = capture(OB.main, ["propose", str(home)])
            self.assertEqual(rc, 1)
            self.assertIn(
                "refused: dedicated control_home with a self-binding "
                "(fixture-org/workspace)", out)
            self.assertIn("pass --repo-url <distinct target>", out)
            self.assertIn("--control-home in-repo", out)
            self.assertEqual(next_count(out), 1)
            self.assertFalse((home / "onboarding-drafts").exists())

    def test_dedicated_with_explicit_self_binding_refused(self):
        # an explicit --repo-url that normalizes to the home slug is
        # refused too (https form + .git suffix normalize identically)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            rc, out, _err = capture(OB.main, [
                "propose", str(home), "--repo-url",
                "https://github.com/fixture-org/workspace.git"])
            self.assertEqual(rc, 1)
            self.assertIn(
                "refused: dedicated control_home with a self-binding "
                "(fixture-org/workspace)", out)
            self.assertEqual(next_count(out), 1)

    def test_case_variant_self_binding_refused(self):
        # slugs compare CASE-INSENSITIVELY — a mixed-case variant of the
        # home's own slug cannot bypass the self-binding refusal.
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            rc, out, _err = capture(OB.main, [
                "propose", str(home), "--repo-url",
                "https://github.com/Fixture-Org/Workspace.git"])
            self.assertEqual(rc, 1, out)
            self.assertIn("self-binding", out)
            self.assertEqual(next_count(out), 1)

    def test_dedicated_with_distinct_url_ok(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            rc, out, _err = capture(OB.main, [
                "propose", str(home), "--repo-url",
                "github.com/fixture-org/app"])
            self.assertEqual(rc, 0, out)
            text = (home / "onboarding-drafts"
                    / "workspace.toml").read_text(encoding="utf-8")
            self.assertIn('control_home = "dedicated"', text)
            self.assertIn('url = "github.com/fixture-org/app"', text)

    def test_in_repo_with_self_binding_stays_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            rc, out, _err = capture(OB.main, [
                "propose", str(home), "--control-home", "in-repo"])
            self.assertEqual(rc, 0, out)
            text = (home / "onboarding-drafts"
                    / "workspace.toml").read_text(encoding="utf-8")
            self.assertIn('control_home = "in-repo"', text)
            self.assertIn('url = "github.com/fixture-org/workspace"', text)

    def test_dedicated_without_origin_ok(self):
        # no origin remote -> no home slug to collide with; the binding
        # stays the template placeholder for the operator to fill in
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir, origin=None)
            rc, out, _err = capture(OB.main, ["propose", str(home)])
            self.assertEqual(rc, 0, out)
            self.assertNotIn("self-binding", out)


# ------------------------------------------------------------------- apply --

APPLY_INSTALL_SET = (
    "workspace.toml", "environments/local.toml", "environments/github.toml",
    "factory/tools/config_validator.py", "factory/tools/ledger_lib.py",
    "factory/tools/state_machine.py", "factory/gates/pre_review_gate.py",
    "factory/ledger/events.jsonl", "factory/locks/OVERRIDE.example",
    "factory/templates/story.md", "factory/templates/story-state.md",
    "factory/templates/story-handoff-issue.md",
    "factory/KICKOFF.md", "factory/SECRETS.md", ".gitignore",
    ".github/ISSUE_TEMPLATE/idea.yml", "stories/README.md",
    "factory/onboarding-registry.json",
)


class TestApply(unittest.TestCase):
    def _applied(self, tmpdir: str, mood: str = "greenfield",
                 apply_args: tuple[str, ...] = (),
                 propose_args: tuple[str, ...] = DISTINCT_ARGS,
                 ) -> tuple[Path, Path, str, Path]:
        tmp, home = make_tmp_target(tmpdir, mood)
        ds = dataset_with_label_ensure(tmp)
        rc, out, _ = capture(OB.main, ["propose", str(home), *propose_args])
        self.assertEqual(rc, 0)
        rc, out, _ = capture(OB.main, ["apply", str(home),
                                       "--forge-fixture", str(ds),
                                       *apply_args])
        return tmp, home, out, ds

    def test_full_install_set(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _tmp, home, out, _ds = self._applied(tmpdir)
            for rel in APPLY_INSTALL_SET:
                with self.subTest(path=rel):
                    self.assertTrue((home / rel).exists(),
                                    f"missing installed path: {rel}")
                    self.assertIn(f"INSTALLED: {rel}", out)
            kickoff = (home / "factory/KICKOFF.md").read_text(encoding="utf-8")
            self.assertIn("story/<id>-<slug>", kickoff)
            self.assertIn("## Human gates", kickoff)
            idea = (home / ".github/ISSUE_TEMPLATE"
                    / "idea.yml").read_text(encoding="utf-8")
            self.assertIn('labels: ["idea"]', idea)
            self.assertEqual((home / "factory/ledger/events.jsonl")
                             .read_bytes(), b"")
            self.assertNotIn("FAIL", out)
            self.assertEqual(next_count(out), 1)
            # never __pycache__ in the installed tree
            self.assertFalse(list(home.rglob("__pycache__")))

    def test_no_pycache_from_tools_copy(self):
        # the kit's tools/ tree carries a live __pycache__ — apply must
        # exclude it from factory/tools and factory/gates.
        with tempfile.TemporaryDirectory() as tmpdir:
            _tmp, home, _out, _ds = self._applied(tmpdir)
            installed = [str(p.relative_to(home)) for p in home.rglob("*")
                         if "__pycache__" in p.parts]
            self.assertEqual(installed, [])

    def test_idempotent_second_apply(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _tmp, home, _out, ds = self._applied(tmpdir)
            first = hash_walk(home)
            rc, out2, _ = capture(OB.main, ["apply", str(home),
                                            "--forge-fixture", str(ds),
                                            "--ui"])
            self.assertEqual(rc, 0)
            self.assertNotIn("INSTALLED:", out2)
            self.assertIn("EXISTS, keeping: factory/KICKOFF.md", out2)
            self.assertEqual(hash_walk(home), first)  # byte-identical tree

    def test_never_overwrites_user_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            ds = dataset_with_label_ensure(tmp)
            capture(OB.main, ["propose", str(home), *DISTINCT_ARGS])
            user_text = "# MY OWN KICKOFF — do not touch\n"
            (home / "factory").mkdir(parents=True)
            (home / "factory/KICKOFF.md").write_text(user_text,
                                                     encoding="utf-8",
                                                     newline="\n")
            before = (home / "factory/KICKOFF.md").read_bytes()
            rc, out, _ = capture(OB.main, ["apply", str(home),
                                           "--forge-fixture", str(ds)])
            self.assertEqual(rc, 0)
            self.assertIn("EXISTS, keeping: factory/KICKOFF.md", out)
            self.assertEqual((home / "factory/KICKOFF.md").read_bytes(),
                             before)

    def test_dry_run_zero_writes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            ds = dataset_with_label_ensure(tmp)
            capture(OB.main, ["propose", str(home), *DISTINCT_ARGS])
            before = hash_walk(home)
            rc, out, _ = capture(OB.main, ["apply", str(home), "--dry-run",
                                           "--forge-fixture", str(ds)])
            self.assertEqual(rc, 0)
            self.assertEqual(hash_walk(home), before)  # zero writes
            self.assertIn("WOULD INSTALL: workspace.toml", out)
            self.assertIn("WOULD INSTALL: factory/KICKOFF.md", out)
            self.assertNotIn("INSTALLED:", out)
            registry = RD.load_registry()
            for lbl in registry["labels"]:
                argv = FP.build_label_ensure_argv(
                    "fixture-org/workspace", lbl["name"],
                    lbl["color"], lbl["description"])
                self.assertIn(f"WOULD: {argv!r}", out)
            self.assertEqual(next_count(out), 1)

    def test_apply_refuses_without_drafts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            rc, out, _ = capture(OB.main, ["apply", str(home)])
            self.assertEqual(rc, 1)
            self.assertIn("drafts missing — run propose first", out)
            self.assertEqual(next_count(out), 1)

    def test_apply_refuses_invalid_drafts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            capture(OB.main, ["propose", str(home), *DISTINCT_ARGS])
            ws = home / "onboarding-drafts/workspace.toml"
            ws.write_text(ws.read_text(encoding="utf-8") + "bogus = 1\n",
                          encoding="utf-8", newline="\n")
            rc, out, _ = capture(OB.main, ["apply", str(home)])
            self.assertEqual(rc, 1)
            self.assertIn("FAIL", out)
            self.assertIn("unknown key: identity.bogus", out)  # verbatim
            self.assertIn("drafts do not validate", out)

    def test_posture_extras(self):
        # greenfield -> docs stubs installed; brownfield -> bootstrap
        # checklist installed; bundled -> no posture extras at all.
        expectations = {
            "greenfield": (
                ("docs/prd.md", "docs/architecture.md", "docs/ui-design.md",
                 "docs/test-strategy.md", "docs/context-bundle-guide.md"),
                ("factory/bootstrap-checklist.md",),
            ),
            "brownfield": (
                ("factory/bootstrap-checklist.md",),
                ("docs/prd.md", "docs/ui-design.md",
                 "docs/context-bundle-guide.md"),
            ),
            "bundled": (
                (),
                ("factory/bootstrap-checklist.md",),
            ),
        }
        for mood, (installed, absent) in expectations.items():
            with self.subTest(mood=mood):
                with tempfile.TemporaryDirectory() as tmpdir:
                    _tmp, home, out, _ds = self._applied(tmpdir, mood)
                    self.assertIn(f"posture: {mood}", out)
                    for rel in installed:
                        self.assertTrue((home / rel).is_file(),
                                        f"{mood}: missing {rel}")
                        self.assertIn(f"INSTALLED: {rel}", out)
                    for rel in absent:
                        self.assertFalse((home / rel).exists(),
                                         f"{mood}: unexpected {rel}")
                        self.assertNotIn(f"INSTALLED: {rel}", out)
                    if mood == "bundled":
                        self.assertNotIn("INSTALLED: docs/", out)

    def test_labels_recorded_via_fixture_transport(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            ds = dataset_with_label_ensure(tmp)
            capture(OB.main, ["propose", str(home), *DISTINCT_ARGS])
            recorder = RecordingForge(ds)
            original = OB.make_forge
            OB.make_forge = lambda fixture: recorder.forge
            try:
                rc, out, _ = capture(OB.main, ["apply", str(home),
                                               "--forge-fixture", str(ds)])
            finally:
                OB.make_forge = original
            self.assertEqual(rc, 0)
            registry = RD.load_registry()
            expected = [(lbl["name"], FP.build_label_ensure_argv(
                "fixture-org/workspace", lbl["name"], lbl["color"],
                lbl["description"])) for lbl in registry["labels"]]
            # the transport builds argv inside; params carry the named
            # fields only — the same argv it would build is reconstructed.
            self.assertEqual(
                [(c["name"], FP.build_label_ensure_argv(
                    c["repo"], c["name"], c["color"], c["description"]))
                 for c in recorder.calls],
                expected)
            self.assertTrue(all("argv" not in c for c in recorder.calls))
            for name, _argv in expected:
                self.assertIn(f"ENSURED: label {name}", out)

    def test_label_failure_is_named_fail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            ds = dataset_with_label_ensure(tmp, ok=False)
            capture(OB.main, ["propose", str(home), *DISTINCT_ARGS])
            rc, out, _ = capture(OB.main, ["apply", str(home),
                                           "--forge-fixture", str(ds)])
            self.assertEqual(rc, 1)
            self.assertIn("FAIL: label ensure factory:planning", out)

    def test_no_origin_label_fail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir, origin=None)
            capture(OB.main, ["propose", str(home), *DISTINCT_ARGS])
            rc, out, _ = capture(OB.main, ["apply", str(home)])
            self.assertEqual(rc, 1)
            self.assertIn("FAIL: label ensure — no origin remote", out)

    def test_record_shape_and_ui_declaration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _tmp, home, _out, _ds = self._applied(tmpdir,
                                                  apply_args=("--ui",))
            record = json.loads((home / "factory/onboarding-registry.json")
                                .read_text(encoding="utf-8"))
            registry = RD.load_registry()
            self.assertEqual(record["registry_version"],
                             registry["registry_version"])
            self.assertEqual(record["kit_version"], OB.kit_head_sha())
            self.assertRegex(record["applied_at"],
                             r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
            self.assertEqual(record["declarations"], {"ui": True})
        with tempfile.TemporaryDirectory() as tmpdir:
            _tmp, home, _out, _ds = self._applied(tmpdir)  # default no-ui
            record = json.loads((home / "factory/onboarding-registry.json")
                                .read_text(encoding="utf-8"))
            self.assertEqual(record["declarations"], {"ui": False})

    def test_record_never_rewritten(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _tmp, home, _out, ds = self._applied(tmpdir)
            rec = home / "factory/onboarding-registry.json"
            before = rec.read_bytes()
            rc, out, _ = capture(OB.main, ["apply", str(home), "--ui",
                                           "--forge-fixture", str(ds)])
            self.assertEqual(rc, 0)
            self.assertIn(
                "EXISTS, keeping: factory/onboarding-registry.json", out)
            self.assertEqual(rec.read_bytes(), before)  # still ui:false


class TestInstallerSymlinkGuard(unittest.TestCase):
    """F-panel: install_bytes NEVER follows a symlink at dest (a
    dangling one would be created through to an outside target; a live
    one would bypass the never-overwrite law)."""

    def test_installer_refuses_dangling_symlink_directly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            home = tmp / "control-home"
            outside = tmp / "outside.txt"
            (home / "factory").mkdir(parents=True)
            (home / "factory" / "KICKOFF.md").symlink_to(outside)
            inst = OB.Installer(home, dry_run=False)
            with self.assertRaises(OB.OnboardError) as ctx:
                inst.install_bytes("factory/KICKOFF.md", b"KICKOFF\n")
            self.assertIn("symlink", str(ctx.exception))
            self.assertFalse(outside.exists())  # nothing written outside

    def test_installer_refuses_symlink_to_existing_file(self):
        # never-overwrite through a link: even a LIVE symlink is refused,
        # the link target's bytes are untouched.
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            home = tmp / "control-home"
            real = home / "user-file.md"
            (home / "factory").mkdir(parents=True)
            real.write_text("user content\n", encoding="utf-8", newline="\n")
            (home / "factory" / "KICKOFF.md").symlink_to(real)
            inst = OB.Installer(home, dry_run=False)
            with self.assertRaises(OB.OnboardError):
                inst.install_bytes("factory/KICKOFF.md", b"KICKOFF\n")
            self.assertEqual(real.read_bytes(), b"user content\n")

    def test_apply_refuses_dangling_symlink_dest(self):
        # the CLI path: apply hits the dangling symlink, prints a named
        # refusal + exactly one NEXT line, rc 1, outside target absent.
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            ds = dataset_with_label_ensure(tmp)
            capture(OB.main, ["propose", str(home), *DISTINCT_ARGS])
            outside = tmp / "escape.txt"
            (home / "factory").mkdir(exist_ok=True)
            (home / "factory" / "KICKOFF.md").symlink_to(outside)
            rc, out, _err = capture(OB.main, ["apply", str(home),
                                              "--forge-fixture", str(ds)])
            self.assertEqual(rc, 1, out)
            self.assertIn("refused:", out)
            self.assertIn("symlink", out)
            self.assertEqual(next_count(out), 1)
            self.assertFalse(outside.exists())


class TestMainCatchAllNextLine(unittest.TestCase):
    """F-panel: a refusal escaping a dispatched command still prints
    exactly ONE NEXT line (the every-command-one-NEXT law)."""

    def test_onboard_error_refusal_prints_next(self):
        original = OB.DISPATCH["preflight"]

        def boom(args):
            raise OB.OnboardError("synthetic refusal")
        OB.DISPATCH["preflight"] = boom
        try:
            rc, out, _err = capture(OB.main, ["preflight", "x"])
        finally:
            OB.DISPATCH["preflight"] = original
        self.assertEqual(rc, 1)
        self.assertIn("refused: synthetic refusal", out)
        self.assertEqual(next_count(out), 1)

    def test_oserror_refusal_prints_next(self):
        original = OB.DISPATCH["preflight"]

        def boom(args):
            raise OSError("synthetic io failure")
        OB.DISPATCH["preflight"] = boom
        try:
            rc, out, _err = capture(OB.main, ["preflight", "x"])
        finally:
            OB.DISPATCH["preflight"] = original
        self.assertEqual(rc, 1)
        self.assertIn("refused: synthetic io failure", out)
        self.assertEqual(next_count(out), 1)


# ------------------------------------------------------------- integration --

class TestIntegrationApplyVerify(unittest.TestCase):
    def test_apply_output_passes_verify_green(self):
        """The apply -> verify link: apply into a git-init'd temp target,
        align the drafts' board key (operator draft review), append the
        attest rows, then readiness verify -> GREEN, zero FAIL lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir, "greenfield")
            ds = dataset_with_label_ensure(tmp)
            rc, _out, _ = capture(OB.main, [
                "propose", str(home),
                "--repo-url", "github.com/fixture-org/app"])
            self.assertEqual(rc, 0)
            ws = home / "onboarding-drafts/workspace.toml"
            ws.write_text(ws.read_text(encoding="utf-8").replace(
                'project_id = "<org>/projects/<n>"',
                'project_id = "fixture-org/projects/7"'),
                encoding="utf-8", newline="\n")
            rc, _out, _ = capture(OB.main, ["apply", str(home), "--ui",
                                            "--forge-fixture", str(ds)])
            self.assertEqual(rc, 0)
            old_key = os.environ.get("LLM_API_KEY")
            os.environ["LLM_API_KEY"] = "presence-only-test-value"
            try:
                for cid in ("C-13", "C-13:app", "U-1", "U-2", "A-2"):
                    RD.append_attest(home, cid, "fixture-human",
                                     f"integration attest {cid}")
                rc, out, _ = capture(
                    RD.main, ["verify", str(home),
                              "--forge-fixture", str(ds)])
            finally:
                if old_key is None:
                    os.environ.pop("LLM_API_KEY", None)
                else:
                    os.environ["LLM_API_KEY"] = old_key
            self.assertEqual(rc, 0)
            self.assertIn("verify status: GREEN", out)
            fails = [ln for ln in out.splitlines()
                     if ln.startswith("FAIL ")]
            self.assertEqual(fails, [])
            self.assertIn("PASS U-1", out)
            self.assertIn("PASS U-2", out)
            self.assertIn("PASS A-2", out)
            self.assertIn("PASS C-13", out)
            # no __pycache__ anywhere after the whole pipeline
            self.assertFalse(list(home.rglob("__pycache__")))


# --------------------------------------------------------------------- CLI --

class TestCli(unittest.TestCase):
    def test_usage_rc2(self):
        for argv in ([], ["bogus"], ["inspect"], ["apply"],
                     ["preflight", "x", "--nope"]):
            with self.subTest(argv=argv):
                rc, _out, _err = capture(OB.main, argv)
                self.assertEqual(rc, 2)

    def test_every_command_one_next_line(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            ds = dataset_with_label_ensure(tmp)
            rc, out, _ = capture(OB.main, ["preflight", str(home),
                                           "--forge-fixture", str(ds)])
            self.assertEqual((rc, next_count(out)), (0, 1))
            rc, out, _ = capture(OB.main, ["inspect", str(home)])
            self.assertEqual((rc, next_count(out)), (0, 1))
            rc, out, _ = capture(OB.main, ["propose", str(home), *DISTINCT_ARGS])
            self.assertEqual((rc, next_count(out)), (0, 1))
            rc, out, _ = capture(OB.main, ["apply", str(home),
                                           "--forge-fixture", str(ds)])
            self.assertEqual((rc, next_count(out)), (0, 1))

    def test_verify_delegation_passthrough(self):
        """onboard verify = readiness verify output + rc, plus exactly
        one wrapper NEXT line."""
        good = FIXTURES / "control_home_good"
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            shutil.copytree(good, home)
            git_init_target(home)
            argv = ["verify", str(home), "--forge-fixture", str(FORGE_DS)]
            rc_rd, out_rd, _ = capture(RD.main, argv)
            rc_ob, out_ob, _ = capture(OB.main, argv)
            self.assertEqual(rc_ob, rc_rd)
            self.assertEqual(next_count(out_ob), 1)
            # readiness output passes through verbatim (its own report
            # NEXT line + the wrapper's pipeline NEXT line)
            self.assertTrue(out_ob.startswith(out_rd))

    def test_attest_delegation_passthrough(self):
        good = FIXTURES / "control_home_good"
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            shutil.copytree(good, home)
            argv = ["attest", str(home), "C-13", "--by", "tester",
                    "--note", "delegation check"]
            rc_rd, out_rd, _ = capture(RD.main, argv)
            rc_ob, out_ob, _ = capture(OB.main, argv)
            self.assertEqual((rc_rd, rc_ob), (0, 0))
            self.assertIn("attest recorded: C-13", out_rd)
            self.assertIn("attest recorded: C-13", out_ob)
            self.assertEqual(next_count(out_ob), 1)


# -------------------------------------------------------------- house laws --

class TestHouseLaws(unittest.TestCase):
    def test_stdlib_only_imports(self):
        src = (REPO_ROOT / "onboarding/onboard.py").read_text(
            encoding="utf-8")
        tree = ast.parse(src)
        allowed = {"tools", "onboarding"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    self.assertTrue(
                        root in sys.stdlib_module_names or root in allowed,
                        f"non-stdlib import: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import (onboarding package)
                    continue
                root = (node.module or "").split(".")[0]
                self.assertTrue(
                    root in sys.stdlib_module_names or root in allowed,
                    f"non-stdlib import: {node.module}")

    def test_lf_endings_on_written_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp, home = make_tmp_target(tmpdir)
            ds = dataset_with_label_ensure(tmp)
            capture(OB.main, ["propose", str(home), *DISTINCT_ARGS])
            capture(OB.main, ["apply", str(home), "--forge-fixture", str(ds),
                              "--ui"])
            for path in hash_walk(home):  # every installed/written file
                p = home / path
                if ".git/" in path.replace(os.sep, "/"):
                    continue
                data = p.read_bytes()
                self.assertNotIn(b"\r\n", data, f"CRLF in {path}")
                if data and path != "factory/ledger/events.jsonl":
                    self.assertTrue(data.endswith(b"\n"),
                                    f"no trailing newline: {path}")

    def test_templates_lf_and_newline(self):
        templates = REPO_ROOT / "onboarding/templates"
        for path in sorted(templates.rglob("*")):
            if not path.is_file():
                continue
            data = path.read_bytes()
            self.assertNotIn(b"\r\n", data, f"CRLF in {path.name}")
            self.assertTrue(data.endswith(b"\n"),
                            f"no trailing newline: {path.name}")

    def test_banned_tokens_absent(self):
        # built by concatenation so THIS file passes the same grep
        tokens = ["po" + "dc" + "ast", "me" + "dia.ing" + "estion",
                  "dap" + "per", "cost" + "entry", "ana" + "lysis.live",
                  "ra" + "ndy", "agent" + "merlin", "dot" + "net",
                  "m" + "tp", "cam" + "paign", "stra" + "tum",
                  "whis" + "per", "epi" + "sode"]
        import re as _re
        pattern = _re.compile("|".join(tokens), _re.IGNORECASE)
        story = _re.compile("STORY-" + "[0-9]")
        touched = [REPO_ROOT / "onboarding/onboard.py",
                   Path(__file__).resolve()]
        touched += sorted((REPO_ROOT / "onboarding/templates").rglob("*"))
        touched += sorted(PIPELINE.rglob("*"))
        scanned = 0
        for path in touched:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(pattern.search(text),
                              f"banned token in {path}")
            self.assertIsNone(story.search(text),
                              f"real story id in {path}")
            scanned += 1
        self.assertGreater(scanned, 15)


if __name__ == "__main__":
    unittest.main()
