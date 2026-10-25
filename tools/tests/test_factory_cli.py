#!/usr/bin/env python3
"""tools/tests/test_factory_cli.py — workspace CLI read path (S2-T7).

Covers: workspace show (text + JSON + derived states — pin
deferred/resolved/not-checked, repo local present/missing/not-git, ledger
summary incl. stories map), validator-message passthrough on an invalid
workspace.toml, env show, env resolve (all-present, missing env-var ref,
missing file ref, deferred under --strict), the READ-ONLY proof
(sha256-walk of the fixture tree before/after all three commands in
text + JSON), and usage rc 2.

Fixtures: tools/tests/fixtures/cli_ws/ (fictional ids only).
"""
from __future__ import annotations

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
from unittest import mock

from tools import factory

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cli_ws"
GOOD = FIXTURES / "good"
CONCRETE = FIXTURES / "concrete"
BADREF = FIXTURES / "badref"
INVALID = FIXTURES / "invalid"


def run(argv: list[str]) -> tuple[int, str]:
    """Run factory.main capturing stdout; return (rc, output)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = factory.main(argv)
    return rc, buf.getvalue()


def ws_run(ws: Path, *rest: str) -> tuple[int, str]:
    return run(["--workspace", str(ws), *rest])


CLI = Path(factory.__file__).resolve()


def sub_run(argv: list[str], env: dict[str, str] | None = None,
            ) -> tuple[int, str, str]:
    """Run the CLI in a FRESH subprocess; return (rc, stdout, stderr)."""
    e = {k: v for k, v in (env if env is not None else os.environ).items()
         if k != "PYTHONPYCACHEPREFIX"}
    res = subprocess.run([sys.executable, str(CLI), *argv],
                         capture_output=True, text=True, env=e)
    return res.returncode, res.stdout, res.stderr


def sha256_walk(root: Path) -> dict[str, str]:
    """Digest every file under root (relpath -> sha256), plus each
    directory's relpath so structural writes are caught too."""
    digests: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        for name in sorted(dirnames):
            digests[os.path.join(rel_dir, name) + "/"] = "<dir>"
        for name in filenames:
            p = Path(dirpath) / name
            digests[os.path.relpath(p, root)] = hashlib.sha256(
                p.read_bytes()).hexdigest()
    return digests


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True)


def make_factory_repo(tmp: Path) -> Path:
    """A REAL temp git repo tagged v0.1.0 (pin-resolution target)."""
    repo = tmp / "factory-repo"
    repo.mkdir()
    git("init", "-q", cwd=repo)
    git("config", "user.email", "fixture@example.invalid", cwd=repo)
    git("config", "user.name", "fixture", cwd=repo)
    (repo / "README.md").write_text("fixture factory repo\n", encoding="utf-8")
    git("add", ".", cwd=repo)
    git("commit", "-q", "-m", "fixture", cwd=repo)
    git("tag", "v0.1.0", cwd=repo)
    return repo


class WorkspaceShowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="t7-cli-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws = self.tmp / "ws"
        shutil.copytree(GOOD, self.ws)

    def test_show_text_deferred_pin_and_ledger(self) -> None:
        rc, out = ws_run(self.ws, "workspace", "show")
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], "workspace: cli-fixture")
        self.assertEqual(lines[1], "control_home: dedicated")
        self.assertEqual(lines[2], "factory_pin: <kit tag/sha> deferred")
        # seeded mini-ledger: repo-less transition states per story
        self.assertIn("ledger: events=4 latest=2026-10-25T02:00:00Z "
                      "stories=STORY-AAA:PLANNED, STORY-BBB:CODING", lines)
        self.assertEqual(lines[-1], "environments: local, template")

    def test_show_json_same_data(self) -> None:
        rc, out = ws_run(self.ws, "workspace", "show", "--json")
        self.assertEqual(rc, 0)
        self.assertTrue(out.endswith("\n"))
        data = json.loads(out)
        self.assertEqual(data["workspace"], "cli-fixture")
        self.assertEqual(data["control_home"], "dedicated")
        self.assertEqual(data["factory_pin"]["status"], "deferred")
        self.assertEqual(data["ledger"]["events"], 4)
        self.assertEqual(data["ledger"]["latest"], "2026-10-25T02:00:00Z")
        self.assertEqual(data["ledger"]["stories"],
                         {"STORY-AAA": "PLANNED", "STORY-BBB": "CODING"})
        self.assertEqual(data["environments"], ["local", "template"])
        # sorted keys, trailing newline (contract)
        self.assertEqual(out, json.dumps(data, sort_keys=True, indent=2) + "\n")

    def test_repo_local_status_present_missing_not_git(self) -> None:
        # app: a REAL git checkout at the DEFAULT binding-id path
        # (<ws>/app — architecture.md §Configuration layout, NOT a
        # repos/ prefix); web: absent; tools: plain dir override
        git("init", "-q", cwd=self.ws / "app")
        rc, out = ws_run(self.ws, "workspace", "show")
        self.assertEqual(rc, 0)
        self.assertIn("  - id=app role=primary url=github.com/ORG/app "
                      "local=present", out.splitlines())
        self.assertIn("  - id=web role=member url=github.com/ORG/web "
                      "local=missing", out.splitlines())
        self.assertIn("  - id=tools role=member url=github.com/ORG/tools "
                      "local=not-git path=checkout/tools", out.splitlines())

    def test_default_path_is_binding_id_not_repos_prefix(self) -> None:
        # round 21 V1: a checkout at the LEGACY <ws>/repos/<id> path must
        # NOT be picked up — the default is <ws>/<binding-id> only
        legacy = self.ws / "repos" / "web"
        legacy.mkdir(parents=True)
        git("init", "-q", cwd=legacy)
        rc, out = ws_run(self.ws, "workspace", "show")
        self.assertEqual(rc, 0)
        self.assertIn("  - id=web role=member url=github.com/ORG/web "
                      "local=missing", out.splitlines())
        # and a checkout at <ws>/web IS present
        (self.ws / "web").mkdir()
        git("init", "-q", cwd=self.ws / "web")
        rc, out = ws_run(self.ws, "workspace", "show")
        self.assertEqual(rc, 0)
        self.assertIn("  - id=web role=member url=github.com/ORG/web "
                      "local=present", out.splitlines())

    def test_repo_local_status_json(self) -> None:
        git("init", "-q", cwd=self.ws / "app")
        rc, out = ws_run(self.ws, "workspace", "show", "--json")
        data = json.loads(out)
        by_id = {r["id"]: r for r in data["repos"]}
        self.assertEqual(by_id["app"]["local"], "present")
        self.assertEqual(by_id["web"]["local"], "missing")
        self.assertEqual(by_id["tools"]["local"], "not-git")
        self.assertEqual(by_id["tools"]["path"], "checkout/tools")
        self.assertIsNone(by_id["app"]["path"])

    def test_plain_dir_nested_in_parent_repo_is_not_git(self) -> None:
        # P1: `present` must mean the dir IS ITSELF a checkout root — a
        # plain dir merely nested inside a parent repo is not-git, even
        # though `rev-parse` parent-walks and succeeds from inside it.
        parent = Path(tempfile.mkdtemp(prefix="nested-parent-"))
        try:
            git("init", "-q", cwd=parent)
            nested = parent / "plain"
            nested.mkdir()
            (nested / "README.md").write_text("not a checkout\n")
            status = factory._repo_local_status(parent, "plain")
            self.assertEqual(status, "not-git")
            # sanity: the parent root itself IS a checkout root
            self.assertEqual(factory._repo_local_status(parent, "."),
                             "present")
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_missing_git_binary_means_not_git(self) -> None:
        git("init", "-q", cwd=self.ws / "app")
        with mock.patch.object(factory, "_git", return_value=None):
            rc, out = ws_run(self.ws, "workspace", "show")
        self.assertEqual(rc, 0)
        self.assertIn("local=not-git", out)

    def test_concrete_pin_not_checked_without_repo(self) -> None:
        rc, out = ws_run(CONCRETE, "workspace", "show")
        self.assertEqual(rc, 0)
        self.assertIn("factory_pin: v0.1.0 not-checked (pass --repo)",
                      out.splitlines())

    def test_concrete_pin_resolved_against_real_temp_repo(self) -> None:
        repo = make_factory_repo(self.tmp)
        rc, out = ws_run(CONCRETE, "workspace", "show", "--repo", str(repo))
        self.assertEqual(rc, 0)
        tag_sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "v0.1.0^{commit}"],
            capture_output=True, text=True, check=True).stdout.strip()
        self.assertIn(f"factory_pin: v0.1.0 resolved {tag_sha}",
                      out.splitlines())

    def test_concrete_pin_unresolvable_is_refusal(self) -> None:
        repo = make_factory_repo(self.tmp)  # no v0.1.0? it has the tag —
        git("tag", "-d", "v0.1.0", cwd=repo)  # drop it: pin cannot resolve
        rc, out = ws_run(CONCRETE, "workspace", "show", "--repo", str(repo))
        self.assertEqual(rc, 1)
        self.assertIn(f"factory_pin: v0.1.0 unresolvable — not found in "
                      f"{repo}", out.splitlines())

    def test_unresolvable_pin_json_mode_is_refusal_rc1(self) -> None:
        # K2: --json must carry the SAME status-dependent exit code as
        # text — rc 0 while factory_pin.status says "unresolvable" would
        # read as success to automation.
        repo = make_factory_repo(self.tmp)
        git("tag", "-d", "v0.1.0", cwd=repo)  # pin cannot resolve
        rc, out, err = sub_run(["--workspace", str(CONCRETE),
                                "workspace", "show", "--repo", str(repo),
                                "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)  # JSON is still emitted in full
        self.assertEqual(data["factory_pin"]["status"], "unresolvable")
        self.assertEqual(err, "")

    def test_resolvable_and_deferred_pin_json_mode_rc0(self) -> None:
        # K2 counterpart: resolvable pin → rc 0 ...
        repo = make_factory_repo(self.tmp)
        rc, out, _ = sub_run(["--workspace", str(CONCRETE),
                              "workspace", "show", "--repo", str(repo),
                              "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["factory_pin"]["status"], "resolved")
        # ... deferred pin (no --repo) → rc 0
        rc, out, _ = sub_run(["--workspace", str(self.ws),
                              "workspace", "show", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["factory_pin"]["status"], "deferred")

    def test_ledger_none_when_absent(self) -> None:
        rc, out = ws_run(CONCRETE, "workspace", "show")
        self.assertIn("ledger: none", out.splitlines())
        rc, out = ws_run(CONCRETE, "workspace", "show", "--json")
        self.assertIsNone(json.loads(out)["ledger"])

    def test_invalid_workspace_refuses_with_validator_messages(self) -> None:
        rc, out = ws_run(INVALID, "workspace", "show")
        self.assertEqual(rc, 1)
        # exact config_validator message, verbatim; no partial output
        self.assertIn("unknown key: unknown_section_key", out.splitlines())
        self.assertNotIn("workspace: cli-fixture", out)

    def test_rejected_ledger_is_named_refusal(self) -> None:
        bad = self.ws / "factory" / "ledger" / "events.jsonl"
        bad.write_text('{"event": "loop"\n', encoding="utf-8")
        rc, out = ws_run(self.ws, "workspace", "show")
        self.assertEqual(rc, 1)
        self.assertIn("invalid JSON", out)


class EnvShowTests(unittest.TestCase):
    def test_show_local_text(self) -> None:
        rc, out = ws_run(GOOD, "env", "show", "local")
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertEqual(lines[1], "runtime: hermes")
        self.assertIn("dispatch: mechanism=delegate_task "
                      "isolation=per-invocation", lines)
        self.assertIn("state: agent_home=~/.agent usage=harvest", lines)
        self.assertIn("models: T1=model-a T2=model-b T3=model-c", lines)
        self.assertIn("credentials: env:CLI_WS_TEST_KEY, "
                      "file:secrets/token.txt", lines)
        self.assertIn("limits: wall=2h runs=10 budget=50M", lines)

    def test_show_local_json(self) -> None:
        rc, out = ws_run(GOOD, "env", "show", "local", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["runtime"], "hermes")
        self.assertEqual(data["dispatch"],
                         {"mechanism": "delegate_task",
                          "isolation": "per-invocation"})
        self.assertEqual(data["models"],
                         {"T1": "model-a", "T2": "model-b", "T3": "model-c"})
        self.assertEqual(data["credentials"]["refs"],
                         ["env:CLI_WS_TEST_KEY", "file:secrets/token.txt"])
        self.assertEqual(data["limits"],
                         {"wall": "2h", "runs": 10, "budget": "50M"})
        self.assertEqual(out, json.dumps(data, sort_keys=True, indent=2) + "\n")

    def test_show_by_relative_path_form(self) -> None:
        # a value containing '/' or ending '.toml' is a path
        rc, out = ws_run(GOOD, "env", "show", "environments/local.toml")
        self.assertEqual(rc, 0)
        self.assertIn("runtime: hermes", out)

    def test_show_deferred_env_as_declared(self) -> None:
        rc, out = ws_run(GOOD, "env", "show", "template")
        self.assertEqual(rc, 0)
        self.assertIn("models: T1=<model-id>", out.splitlines())

    def test_show_missing_env_named_refusal(self) -> None:
        rc, out = ws_run(GOOD, "env", "show", "nope")
        self.assertEqual(rc, 1)
        self.assertIn("environment not found: nope", out)

    def test_show_invalid_env_surfaces_validator_messages(self) -> None:
        with tempfile.TemporaryDirectory(prefix="t7-env-") as tmp:
            ws = Path(tmp)
            (ws / "environments").mkdir()
            (ws / "environments" / "bad.toml").write_text(
                'runtime = "hermes"\nunknown_key = 1\n', encoding="utf-8")
            rc, out = ws_run(ws, "env", "show", "bad")
        self.assertEqual(rc, 1)
        self.assertIn("unknown key: unknown_key", out.splitlines())

    def test_env_named_workspace_toml_uses_environment_schema(self) -> None:
        """R13-N3: the env commands select the schema EXPLICITLY — a
        VALID environment file named `workspace.toml` is accepted by the
        environment schema (basename selection would have validated it
        as a workspace doc and rejected it)."""
        with tempfile.TemporaryDirectory(prefix="t7-env-") as tmp:
            ws = Path(tmp)
            shutil.copyfile(GOOD / "environments" / "local.toml",
                            ws / "workspace.toml")
            rc, out = ws_run(ws, "env", "show", "workspace.toml")
            self.assertEqual(rc, 0, out)
            self.assertIn("runtime: hermes", out.splitlines())
            rc, _ = ws_run(ws, "env", "resolve", "workspace.toml")
            self.assertIn(rc, (0, 1))  # 1 only for missing credential refs
            rc, out = ws_run(ws, "env", "resolve", "workspace.toml",
                             "--json")
            data = json.loads(out) if out.strip().startswith("{") else {}
            self.assertEqual(data.get("models", {}).get("T1"), "model-a")

    def test_env_command_rejects_workspace_doc(self) -> None:
        """R13-N3: a WORKSPACE doc handed to an env command gets the
        ENVIRONMENT-schema rejection (never basename-selected), so an
        empty doc named `workspace.toml` cannot pass as an environment."""
        with tempfile.TemporaryDirectory(prefix="t7-env-") as tmp:
            ws = Path(tmp)
            shutil.copyfile(GOOD / "workspace.toml", ws / "workspace.toml")
            rc, out = ws_run(ws, "env", "show", "workspace.toml")
            self.assertEqual(rc, 1)
            lines = out.splitlines()
            self.assertIn("missing required key: runtime", lines)
            self.assertIn("missing required table: dispatch", lines)
            self.assertIn("missing required table: models", lines)
            # an empty doc under the environment schema also rejects
            (ws / "empty.toml").write_text("", encoding="utf-8")
            rc, out = ws_run(ws, "env", "show", "empty.toml")
            self.assertEqual(rc, 1)
            self.assertIn("missing required key: runtime", out.splitlines())
            rc, out = ws_run(ws, "env", "resolve", "workspace.toml")
            self.assertEqual(rc, 1)
            self.assertIn("missing required key: runtime", out.splitlines())

    def test_env_named_environment_doc_unchanged(self) -> None:
        """R13-N3 pin: a normally-named environment doc behaves exactly
        as before under both env commands."""
        rc, out = ws_run(GOOD, "env", "show", "local")
        self.assertEqual(rc, 0)
        self.assertIn("runtime: hermes", out.splitlines())
        with mock.patch.dict(os.environ, {"CLI_WS_TEST_KEY": "***"},
                             clear=False):
            rc, _ = ws_run(GOOD, "env", "resolve", "local")
        self.assertEqual(rc, 0)


class EnvResolveTests(unittest.TestCase):
    def test_resolve_all_present(self) -> None:
        with mock.patch.dict(os.environ, {"CLI_WS_TEST_KEY": "***"},
                             clear=False):
            rc, out = ws_run(GOOD, "env", "resolve", "local")
        self.assertEqual(rc, 0)
        self.assertIn("  env:CLI_WS_TEST_KEY present", out.splitlines())
        self.assertIn("  file:secrets/token.txt exists", out.splitlines())
        self.assertNotIn("missing credential reference", out)

    def test_resolve_never_reads_env_value(self) -> None:
        # presence check only: the value must never appear in output
        with mock.patch.dict(os.environ, {"CLI_WS_TEST_KEY": "***"},
                             clear=False):
            rc, out = ws_run(GOOD, "env", "resolve", "local")
        self.assertEqual(rc, 0)
        self.assertNotIn("SUPER-SECRET-VALUE", out)

    def test_resolve_all_present_json(self) -> None:
        with mock.patch.dict(os.environ, {"CLI_WS_TEST_KEY": "***"},
                             clear=False):
            rc, out = ws_run(GOOD, "env", "resolve", "local", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["missing"], [])
        self.assertIn({"ref": "env:CLI_WS_TEST_KEY", "status": "present"},
                      data["credentials"])
        self.assertIn({"ref": "file:secrets/token.txt", "status": "exists"},
                      data["credentials"])
        self.assertEqual(out, json.dumps(data, sort_keys=True, indent=2) + "\n")

    def test_resolve_missing_env_var_ref(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "CLI_WS_TEST_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            rc, out = ws_run(GOOD, "env", "resolve", "local")
        self.assertEqual(rc, 1)
        self.assertIn("missing credential reference: env:CLI_WS_TEST_KEY",
                      out.splitlines())
        self.assertNotIn("missing credential reference: file:secrets/token.txt",
                         out)

    def test_resolve_missing_file_ref_lists_all_missing(self) -> None:
        with mock.patch.dict(os.environ, {"CLI_WS_ABSENT_KEY": "***"},
                             clear=False):
            rc, out = ws_run(BADREF, "env", "resolve", "local")
        self.assertEqual(rc, 1)
        lines = out.splitlines()
        # env var now present; only the absent file ref is missing
        self.assertIn("  env:CLI_WS_ABSENT_KEY present", lines)
        self.assertIn("missing credential reference: file:secrets/absent.txt",
                      lines)
        # badref with NEITHER ref satisfied lists ALL missing, then rc 1
        env = {k: v for k, v in os.environ.items() if k != "CLI_WS_ABSENT_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            rc, out = ws_run(BADREF, "env", "resolve", "local")
        self.assertEqual(rc, 1)
        self.assertIn("missing credential reference: env:CLI_WS_ABSENT_KEY",
                      out.splitlines())
        self.assertIn("missing credential reference: file:secrets/absent.txt",
                      out.splitlines())

    def test_resolve_deferred_rendered_without_strict(self) -> None:
        rc, out = ws_run(GOOD, "env", "resolve", "template")
        self.assertEqual(rc, 0)
        self.assertIn("models: T1=deferred: <model-id>", out.splitlines())
        self.assertIn("  <token-ref> deferred", out.splitlines())

    def test_resolve_strict_refuses_deferred(self) -> None:
        rc, out = ws_run(GOOD, "env", "resolve", "template", "--strict")
        self.assertEqual(rc, 1)
        lines = out.splitlines()
        self.assertIn("deferred placeholder not resolved: models.T1", lines)
        self.assertIn("deferred placeholder not resolved: credentials.refs[0]",
                      lines)

    def test_resolve_strict_json(self) -> None:
        rc, out = ws_run(GOOD, "env", "resolve", "template", "--strict",
                         "--json")
        self.assertEqual(rc, 1)
        # findings printed as lines (rc 1), no JSON body on refusal
        self.assertNotIn("{", out)

    def test_resolve_missing_env_named_refusal(self) -> None:
        rc, out = ws_run(GOOD, "env", "resolve", "nope")
        self.assertEqual(rc, 1)
        self.assertIn("environment not found: nope", out)

    def test_missing_credential_diagnostics_json_mode_go_to_stderr(self) -> None:
        # K3: in JSON mode stdout stays pure JSON (parseable — no
        # "Extra data" from appended plain-text diagnostics); the
        # missing-reference lines move to stderr; rc stays 1.
        env = {k: v for k, v in os.environ.items() if k != "CLI_WS_TEST_KEY"}
        rc, out, err = sub_run(["--workspace", str(GOOD),
                                "env", "resolve", "local", "--json"], env=env)
        self.assertEqual(rc, 1)
        data = json.loads(out)  # stdout is PURE JSON
        self.assertEqual(data["missing"], ["env:CLI_WS_TEST_KEY"])
        self.assertIn("missing credential reference: env:CLI_WS_TEST_KEY",
                      err.splitlines())
        self.assertNotIn("missing credential reference", out)

    def test_missing_credential_diagnostics_text_mode_stay_on_stdout(self) -> None:
        # K3 counterpart: text mode unchanged — diagnostics on stdout.
        env = {k: v for k, v in os.environ.items() if k != "CLI_WS_TEST_KEY"}
        rc, out, err = sub_run(["--workspace", str(GOOD),
                                "env", "resolve", "local"], env=env)
        self.assertEqual(rc, 1)
        self.assertIn("missing credential reference: env:CLI_WS_TEST_KEY",
                      out.splitlines())
        self.assertEqual(err, "")


class ReadOnlyProofTests(unittest.TestCase):
    def test_all_commands_write_nothing_under_the_workspace(self) -> None:
        before = sha256_walk(GOOD)
        env = {"CLI_WS_TEST_KEY": "***"}
        with mock.patch.dict(os.environ, env, clear=False):
            for argv in (
                ["workspace", "show"],
                ["workspace", "show", "--json"],
                ["env", "show", "local"],
                ["env", "show", "local", "--json"],
                ["env", "resolve", "local"],
                ["env", "resolve", "local", "--json"],
            ):
                rc, _ = ws_run(GOOD, *argv)
                self.assertIn(rc, (0, 1))
        self.assertEqual(sha256_walk(GOOD), before)

    def test_badref_tree_untouched_by_refusals(self) -> None:
        before = sha256_walk(BADREF)
        ws_run(BADREF, "workspace", "show")
        ws_run(BADREF, "env", "show", "local")
        ws_run(BADREF, "env", "resolve", "local")
        self.assertEqual(sha256_walk(BADREF), before)


class NoBytecodeWritesTests(unittest.TestCase):
    """K1: a FRESH installed-like tree must gain no __pycache__/*.pyc when
    the documented `python3 tools/factory.py …` invocation runs — the
    read-only guarantee holds on disk too (sys.dont_write_bytecode is set
    before any local import)."""

    MODULES = ("__init__.py", "factory.py", "config_validator.py",
               "ledger_lib.py", "ledger_schema.py")

    def test_cli_subprocess_writes_no_bytecode_under_installed_tree(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="t7-pyc-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        install = tmp / "install"
        (install / "tools").mkdir(parents=True)
        src_dir = CLI.parent
        for name in self.MODULES:
            shutil.copyfile(src_dir / name, install / "tools" / name)
        ws = tmp / "ws"
        shutil.copytree(GOOD, ws)

        self.assertEqual([], list(install.rglob("*.pyc")))  # clean slate
        res = subprocess.run(
            [sys.executable, str(install / "tools" / "factory.py"),
             "--workspace", str(ws), "workspace", "show"],
            capture_output=True, text=True,
            env={k: v for k, v in os.environ.items()
                 if k != "PYTHONPYCACHEPREFIX"})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual([], list(install.rglob("__pycache__")))
        self.assertEqual([], list(install.rglob("*.pyc")))
        # sanity: the CLI actually ran (output contract intact)
        self.assertIn("workspace: cli-fixture", res.stdout.splitlines())


class UsageTests(unittest.TestCase):
    def test_no_subcommand_is_usage_rc2(self) -> None:
        rc, _ = run([])
        self.assertEqual(rc, 2)

    def test_partial_subcommand_is_usage_rc2(self) -> None:
        rc, _ = run(["workspace"])
        self.assertEqual(rc, 2)
        rc, _ = run(["env"])
        self.assertEqual(rc, 2)

    def test_env_show_without_name_is_usage_rc2(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            run(["env", "show"])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
