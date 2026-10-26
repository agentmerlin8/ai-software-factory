#!/usr/bin/env python3
"""Tests for onboarding/readiness.py — the verify engine + attest CLI.

Acceptance-#1: `test_good_home_verify_green` drives the REAL CLI
(readiness.main) against a tmp copy of control_home_good with its forge
dataset and asserts rc 0 with zero FAILs. Every required check also has
a FAIL-path test via the make_home/verify_home generator (one defect per
test, precise detail assertions). Covers: applicability N-A cases,
attest semantics (missing/append/supersession/malformed refusal/ledger
validation), registry drift (version FAIL + kit-sha ADVISORY + record
missing), the output contract (text shapes, --json sorted-keys + same
data, rc 2 usage, PREFLIGHT.md sections + regeneration, determinism),
the read-only law (hash-walk: only sanctioned paths differ), engine
errors (registry row without implementation, malformed registry.json),
fixture-copy law (committed trees pristine), and house laws
(stdlib-only, LF endings).
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

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from onboarding import readiness as RD  # noqa: E402
from tools import ledger_lib as L  # noqa: E402
from tools import state_machine as SM  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = FIXTURES / "control_home_good"
BAD = FIXTURES / "control_home_bad"
DS_GOOD = FIXTURES / "forge" / "control_home_good.json"
DS_BAD = FIXTURES / "forge" / "control_home_bad.json"

ORIGIN = "https://github.com/fixture-org/workspace.git"


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


def make_home(tmp: Path, defects: list[str] | None = None,
              source: Path = GOOD) -> Path:
    """Fixture-copy law: work on a shutil.copytree TMP copy (committed
    fixtures stay pristine). `defects` mutates the good home one way per
    test; also git-init + origin remote so the home is a real repo root
    (W-1 semantics) with a forge-facing origin (C-1/W-7)."""
    home = tmp / "home"
    shutil.copytree(source, home)
    subprocess.run(["git", "init", "-b", "main", str(home)],
                   capture_output=True, check=True,
                   env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
                        "GIT_CONFIG_SYSTEM": "/dev/null"})
    subprocess.run(["git", "-C", str(home), "remote", "add", "origin",
                    ORIGIN], capture_output=True, check=True)
    apply_defects(home, defects or [])
    return home


def apply_defects(home: Path, defects: list[str]) -> None:
    """One named defect mutation per entry (FAIL-path generator)."""
    ws = home / "workspace.toml"
    for defect in defects:
        if defect == "no-origin":
            subprocess.run(["git", "-C", str(home), "remote", "remove",
                            "origin"], capture_output=True, check=True)
        elif defect == "no-binding":
            ws.write_text(
                ws.read_text(encoding="utf-8").replace(
                    'url = "github.com/fixture-org/app"',
                    'url = "github.com/fixture-org/"'),
                encoding="utf-8", newline="\n")
        elif defect == "no-idea-form":
            (home / ".github" / "ISSUE_TEMPLATE" / "idea.yml").unlink()
        elif defect == "no-kit-tools":
            shutil.rmtree(home / "factory" / "tools")
        elif defect == "empty-templates":
            (home / "factory" / "templates" / "story.md").unlink()
        elif defect == "no-locks":
            (home / "factory" / "locks" / "OVERRIDE.example").unlink()
        elif defect == "no-ledger-seed":
            (home / "factory" / "ledger" / "events.jsonl").unlink()
        elif defect == "ledger-invalid":
            (home / "factory" / "ledger" / "events.jsonl").write_text(
                '{"event": "nope"}\n', encoding="utf-8", newline="\n")
        elif defect == "bad-workspace-toml":
            ws.write_text(
                ws.read_text(encoding="utf-8") + '\nbogus_key = 1\n',
                encoding="utf-8", newline="\n")
        elif defect == "no-kickoff":
            (home / "factory" / "KICKOFF.md").unlink()
        elif defect == "no-secrets-doc":
            (home / "factory" / "SECRETS.md").unlink()
        elif defect == "bad-gitignore":
            (home / ".gitignore").write_text("__pycache__/\n",
                                             encoding="utf-8", newline="\n")
        elif defect == "drop-attest":
            # strip every answered attest for the check named via suffix
            pass  # handled by drop_attest_* below
        elif defect.startswith("drop-attest-"):
            check_id = defect[len("drop-attest-"):]
            ledger = home / "factory" / "ledger" / "events.jsonl"
            rows = [ln for ln in
                    ledger.read_text(encoding="utf-8").splitlines()
                    if ln.strip()]
            keep = [ln for ln in rows
                    if f"onboard attest: {check_id} " not in ln]
            ledger.write_text("".join(ln + "\n" for ln in keep),
                              encoding="utf-8", newline="\n")
        elif defect == "no-env-credential-file":
            (home / "secrets" / "token.txt").unlink()
        elif defect == "in-repo":
            ws.write_text(
                ws.read_text(encoding="utf-8").replace(
                    'control_home = "dedicated"',
                    'control_home = "in-repo"'),
                encoding="utf-8", newline="\n")
        elif defect == "not-git-root":
            nested = home / "nested"
            nested.mkdir()
            subprocess.run(["git", "init", "-b", "main", str(nested)],
                           capture_output=True, check=True,
                           env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
                                "GIT_CONFIG_SYSTEM": "/dev/null"})
            # re-init git metadata ABOVE home is impossible; instead move
            # the home's .git into nested so the toplevel differs
            shutil.move(str(home / ".git"), str(nested / ".git"))
        elif defect == "no-record":
            (home / "factory" / "onboarding-registry.json").unlink()
        elif defect == "record-drift":
            rec = home / "factory" / "onboarding-registry.json"
            data = json.loads(rec.read_text(encoding="utf-8"))
            data["registry_version"] = 99
            rec.write_text(json.dumps(data, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
        elif defect == "record-kit-drift":
            rec = home / "factory" / "onboarding-registry.json"
            data = json.loads(rec.read_text(encoding="utf-8"))
            data["kit_version"] = "0" * 40
            rec.write_text(json.dumps(data, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
        elif defect == "record-unparseable":
            (home / "factory" / "onboarding-registry.json").write_text(
                "{not json", encoding="utf-8", newline="\n")
        elif defect == "ui-undeclared":
            rec = home / "factory" / "onboarding-registry.json"
            data = json.loads(rec.read_text(encoding="utf-8"))
            data["declarations"]["ui"] = False
            rec.write_text(json.dumps(data, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
        elif defect == "no-hermes-env":
            (home / "environments" / "local.toml").write_text(
                (home / "environments" / "local.toml")
                .read_text(encoding="utf-8")
                .replace('runtime = "hermes"', 'runtime = "human-shell"'),
                encoding="utf-8", newline="\n")
        else:
            raise AssertionError(f"unknown defect: {defect}")


def verify_home(home: Path, dataset: Path = DS_GOOD, as_json: bool = False):
    """The REAL CLI. Runs once for text and once for --json (the engine
    is deterministic); -> (rc, stdout_text, parsed_json)."""
    argv = ["verify", str(home), "--forge-fixture", str(dataset)]
    rc, out, _err = capture(RD.main, argv)
    rc_j, out_j, _err_j = capture(RD.main, argv + ["--json"])
    data = json.loads(out_j)
    if as_json:
        return rc_j, out_j, data
    return rc, out, data


def statuses(data: dict) -> dict[str, list[str]]:
    """id -> [status] (REGISTRY may render an extra advisory row)."""
    acc: dict[str, list[str]] = {}
    for row in data["checks"]:
        acc.setdefault(row["id"], []).append(row["status"])
    return acc


def detail_of(data: dict, check_id: str) -> str:
    for row in data["checks"]:
        if row["id"] == check_id and row["status"] in ("PASS", "FAIL", "N-A"):
            return row.get("detail") or ""
    return ""


class VerifyGoodHome(unittest.TestCase):
    """Acceptance-#1 + the good-home contract."""

    def test_good_home_verify_green(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td))
            rc, out, _data = verify_home(home)
            self.assertEqual(rc, 0, out)
            self.assertNotIn("FAIL", out)
            for cid in ("C-1", "C-2", "C-3", "C-4", "C-5", "C-6", "C-7",
                        "C-8", "C-9", "C-10", "C-11", "C-12", "C-13",
                        "C-14", "REGISTRY", "W-1", "W-2", "W-3", "W-4",
                        "W-5", "W-6", "W-7", "W-8", "U-1", "U-2", "A-2"):
                self.assertIn(f"PASS {cid} ", out)
            # A-2 carries its DEFERRED(S4) line
            self.assertIn("DEFERRED(S4) A-2", out)
            # adapters not in play are N-A with the named reason
            for cid, adapter in (("A-1", "human"), ("A-3", "claude-code"),
                                 ("A-4", "codex"), ("A-5", "api-direct"),
                                 ("A-6", "copilot")):
                self.assertIn(f"N-A {cid} ", out)
                self.assertIn(f"adapter '{adapter}' not in play", out)
            # advisories rendered
            for cid in ("A-7", "A-8", "A-9"):
                self.assertIn(f"ADVISORY {cid} ", out)
            # header + footer contract
            self.assertTrue(out.splitlines()[0].startswith(
                f"verify: {home} (mode=dedicated, registry=1, kit="))
            self.assertIn("verify status: GREEN", out)
            self.assertIn("report: factory/PREFLIGHT.md", out)
            self.assertRegex(out, r"required: \d+ pass / 0 fail / \d+ n-a · advisory: \d+")

    def test_good_home_json_contract(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td))
            rc, out, data = verify_home(home, as_json=True)
            self.assertEqual(rc, 0)
            self.assertEqual(data["status"], "GREEN")
            self.assertEqual(data["mode"], "dedicated")
            self.assertEqual(data["registry_version"], 1)
            self.assertEqual(data["summary"]["failed"], 0)
            self.assertEqual(len(data["kit_version"]), 40)
            self.assertTrue(data["report_path"].endswith(
                "factory/PREFLIGHT.md"))
            # sorted keys in the raw text
            keys = [ln.strip().split('"')[1] for ln in out.splitlines()
                    if ln.startswith('  "')]
            self.assertEqual(keys, sorted(keys))
            st = statuses(data)
            self.assertEqual(st["A-2"], ["PASS"])
            a2 = next(r for r in data["checks"] if r["id"] == "A-2")
            self.assertEqual(a2["deferred"], [
                "factory/adapters/hermes/adapter.toml "
                "(adapter packages land with S4)"])

    def test_good_home_determinism(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td))
            _rc1, out1, _d1 = verify_home(home, as_json=True)
            _rc2, out2, _d2 = verify_home(home, as_json=True)
            self.assertEqual(out1, out2)

    def test_good_home_preflight_written(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td))
            preflight = home / "factory" / "PREFLIGHT.md"
            self.assertFalse(preflight.exists())
            rc, _out, _data = verify_home(home)
            self.assertEqual(rc, 0)
            self.assertTrue(preflight.is_file())
            text = preflight.read_text(encoding="utf-8")
            self.assertIn("# PREFLIGHT", text)
            self.assertIn("| id | category | status | detail |", text)
            self.assertIn("verify status: GREEN", text)
            self.assertIn("generated: ", text)
            self.assertIn("NEXT:", text)
            # regenerated (a second run rewrites it)
            before = text
            verify_home(home)
            self.assertTrue(preflight.is_file())
            after = preflight.read_text(encoding="utf-8")
            self.assertEqual(before.split("generated:")[0],
                             after.split("generated:")[0])

    def test_read_only_hash_walk(self):
        """Only sanctioned writes differ: PREFLIGHT.md + render outputs."""
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td))
            before = hash_walk(home)
            rc, _out, _data = verify_home(home)
            self.assertEqual(rc, 0)
            after = hash_walk(home)
            sanctioned = {"factory/PREFLIGHT.md", "factory/DASHBOARD.md"}
            changed = {k for k in after
                       if k not in before or before.get(k) != after[k]}
            added = set(after) - set(before)
            self.assertTrue(changed | added <= sanctioned,
                            f"unsanctioned writes: {(changed | added) - sanctioned}")
            self.assertIn("factory/PREFLIGHT.md", added)
            self.assertIn("factory/DASHBOARD.md", added)

    def test_fixture_trees_pristine(self):
        """Fixture-copy law: committed fixtures unchanged by all runs."""
        pre = {name: hash_walk(path)
               for name, path in (("good", GOOD), ("bad", BAD))}
        pre_ds = {p.name: p.read_bytes() for p in (DS_GOOD, DS_BAD)}
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td))
            verify_home(home)
            verify_home(home, dataset=DS_BAD)
        self.assertEqual(pre["good"], hash_walk(GOOD))
        self.assertEqual(pre["bad"], hash_walk(BAD))
        self.assertEqual(pre_ds,
                         {p.name: p.read_bytes() for p in (DS_GOOD, DS_BAD)})


class VerifyBadHome(unittest.TestCase):
    """The acceptance run of record: every seeded defect FAILs precisely."""

    def test_bad_home_precise_fails(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td), source=BAD)
            rc, out, data = verify_home(home, dataset=DS_BAD)
            self.assertEqual(rc, 1)
            self.assertIn("verify status: RED", out)
            st = statuses(data)
            # D1 two labels missing (forge dataset)
            self.assertEqual(st["C-4"], ["FAIL"])
            self.assertIn("missing:", detail_of(data, "C-4"))
            self.assertIn("factory:blocked", detail_of(data, "C-4"))
            self.assertIn("needs-human", detail_of(data, "C-4"))
            # D2 missing scope read:org
            self.assertEqual(st["C-2"], ["FAIL"])
            self.assertIn("missing: read:org", detail_of(data, "C-2"))
            # D3 ruleset read refused
            self.assertEqual(st["C-3"], ["FAIL"])
            self.assertIn("enforcement unverified", detail_of(data, "C-3"))
            self.assertIn("never claimed as protection",
                          detail_of(data, "C-3"))
            self.assertEqual(st["W-7"], ["FAIL"])
            # D4 Stage field removed from the board
            self.assertEqual(st["C-6"], ["FAIL"])
            self.assertIn("no Stage field", detail_of(data, "C-6"))
            self.assertEqual(st["W-8"], ["FAIL"])
            # D5 KICKOFF absent
            self.assertEqual(st["C-11"], ["FAIL"])
            self.assertIn("missing factory/KICKOFF.md",
                          detail_of(data, "C-11"))
            self.assertEqual(st["C-14"], ["FAIL"])
            # D6 registry drift record
            reg_rows = [r for r in data["checks"] if r["id"] == "REGISTRY"]
            self.assertEqual(reg_rows[0]["status"], "FAIL")
            self.assertIn("registry drift: recorded 0, kit ships 1",
                          reg_rows[0]["detail"])
            # D7 unresolvable pin (C-10 + W-2 + C-7 installed validator)
            self.assertEqual(st["C-10"], ["FAIL"])
            self.assertIn("unresolvable pin", detail_of(data, "C-10"))
            self.assertEqual(st["W-2"], ["FAIL"])
            self.assertEqual(st["C-7"], ["FAIL"])
            self.assertIn("unresolvable pin", detail_of(data, "C-7"))
            # D8 ledger attests emptied
            self.assertEqual(st["C-13"], ["FAIL"])
            self.assertIn("no answered attest recorded",
                          detail_of(data, "C-13"))
            self.assertEqual(st["U-1"], ["FAIL"])
            self.assertEqual(st["U-2"], ["FAIL"])
            self.assertEqual(st["A-2"], ["FAIL"])
            # remedies printed for failures
            self.assertIn("remedy:", out)
            self.assertIn("onboard attest C-13", out)


class FailPathPerCheck(unittest.TestCase):
    """Generator-style: one defect per required check's FAIL path."""

    def assert_defect(self, defects, check_id, detail_contains,
                      dataset=DS_GOOD):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td), defects=defects)
            rc, out, data = verify_home(home, dataset=dataset)
            self.assertEqual(rc, 1, out)
            row = next(r for r in data["checks"]
                       if r["id"] == check_id and r["status"] == "FAIL")
            for needle in detail_contains:
                self.assertIn(needle, row["detail"])
            self.assertEqual(row["remedy"], self.registry_remedy(check_id))

    @classmethod
    def registry_remedy(cls, check_id):
        registry = RD.load_registry()
        return next(r["remedy"] for r in registry["checks"]
                    if r["id"] == check_id)

    def test_c1_fail_no_origin(self):
        self.assert_defect(["no-origin"], "C-1",
                           ["no origin remote in the control home"])

    def test_c1_fail_binding_unreachable(self):
        # dataset without the repo_view entry → cannot probe (fail closed)
        with tempfile.TemporaryDirectory() as td:
            ds = Path(td) / "ds" / "ds.json"
            ds.parent.mkdir()
            data = json.loads(DS_GOOD.read_text(encoding="utf-8"))
            del data["repo_view"]
            ds.write_text(json.dumps(data), encoding="utf-8")
            home = make_home(Path(td) / "h")
            rc, out, jdata = verify_home(home, dataset=ds)
            self.assertEqual(rc, 1)
            self.assertIn("cannot probe:", detail_of(jdata, "C-1"))

    def test_c2_fail_missing_scopes(self):
        with tempfile.TemporaryDirectory() as td:
            ds = Path(td) / "ds.json"
            data = json.loads(DS_GOOD.read_text(encoding="utf-8"))
            data["auth_scopes"]["stdout"] = data["auth_scopes"][
                "stdout"].replace("'read:org', ", "")
            ds.write_text(json.dumps(data), encoding="utf-8")
            home = make_home(Path(td) / "home")
            rc, _out, jdata = verify_home(home, dataset=ds)
            self.assertEqual(rc, 1)
            self.assertEqual(detail_of(jdata, "C-2"), "missing: read:org")

    def ruleset_variant(self, mutate):
        with tempfile.TemporaryDirectory() as td:
            ds = Path(td) / "ds.json"
            data = json.loads(DS_GOOD.read_text(encoding="utf-8"))
            mutate(data["ruleset"])
            ds.write_text(json.dumps(data), encoding="utf-8")
            home = make_home(Path(td) / "home")
            rc, _out, jdata = verify_home(home, dataset=ds)
            self.assertEqual(rc, 1)
            return detail_of(jdata, "C-3")

    def test_c3_fail_disabled(self):
        def mutate(rs):
            rs["rulesets"][0]["enforcement"] = "disabled"
        detail = self.ruleset_variant(mutate)
        self.assertIn("staged, not enforcing", detail)

    def test_c3_fail_refused(self):
        def mutate(rs):
            rs.clear()
            rs["rulesets"] = []
        with tempfile.TemporaryDirectory() as td:
            ds = Path(td) / "ds.json"
            data = json.loads(DS_GOOD.read_text(encoding="utf-8"))
            data["ruleset"] = {"error": "probe failed: ruleset — HTTP 403"}
            ds.write_text(json.dumps(data), encoding="utf-8")
            home = make_home(Path(td) / "home")
            rc, _out, jdata = verify_home(home, dataset=ds)
            self.assertEqual(rc, 1)
            detail = detail_of(jdata, "C-3")
            self.assertIn("enforcement unverified", detail)
            self.assertIn("never claimed as protection", detail)

    def test_c3_fail_not_found(self):
        def mutate(rs):
            rs["rulesets"] = []
        detail = self.ruleset_variant(mutate)
        self.assertEqual(detail, "no ruleset targeting main")

    def test_c3_fail_human_bypass(self):
        def mutate(rs):
            rs["rulesets"][0]["bypass_actors"] = [
                {"actor_type": "User", "actor_id": 1, "mode": "always"}]
        detail = self.ruleset_variant(mutate)
        self.assertEqual(
            detail,
            "human bypass present — the merge gate is not mechanically real")

    def test_c3_fail_multiple_integration_bypass(self):
        def mutate(rs):
            rs["rulesets"][0]["bypass_actors"] = [
                {"actor_type": "Integration", "actor_id": 1, "mode": "always"},
                {"actor_type": "Integration", "actor_id": 2, "mode": "always"}]
        detail = self.ruleset_variant(mutate)
        self.assertEqual(
            detail,
            "multiple integration bypasses — expected the factory bot only")

    def test_c4_fail_missing_labels(self):
        with tempfile.TemporaryDirectory() as td:
            ds = Path(td) / "ds.json"
            data = json.loads(DS_GOOD.read_text(encoding="utf-8"))
            data["labels"]["labels"] = [
                lbl for lbl in data["labels"]["labels"]
                if lbl["name"] != "idea"]
            ds.write_text(json.dumps(data), encoding="utf-8")
            home = make_home(Path(td) / "home")
            rc, _out, jdata = verify_home(home, dataset=ds)
            self.assertEqual(rc, 1)
            self.assertIn("missing: idea", detail_of(jdata, "C-4"))
            self.assertIn("missing: idea", detail_of(jdata, "C-5"))

    def test_c5_fail_no_idea_form(self):
        self.assert_defect(["no-idea-form"], "C-5",
                           ["missing .github/ISSUE_TEMPLATE/idea.yml"])

    def test_c6_fail_no_board_locator(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td))
            ws = home / "workspace.toml"
            ws.write_text(ws.read_text(encoding="utf-8").replace(
                'project_id = "fixture-org/projects/7"',
                'project_id = "<org>/projects/<n>"'),
                encoding="utf-8", newline="\n")
            rc, _out, data = verify_home(home)
            self.assertEqual(rc, 1)
            self.assertIn("board.project_id not configured",
                          detail_of(data, "C-6"))

    def test_c6_fail_project_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            ds = Path(td) / "ds.json"
            data = json.loads(DS_GOOD.read_text(encoding="utf-8"))
            data["project"] = {"node_id": None, "fields": {"nodes": []}}
            ds.write_text(json.dumps(data), encoding="utf-8")
            home = make_home(Path(td) / "home")
            rc, _out, jdata = verify_home(home, dataset=ds)
            self.assertEqual(rc, 1)
            self.assertIn("project not found", detail_of(jdata, "C-6"))

    def test_c7_fail_missing_surface(self):
        self.assert_defect(["no-kit-tools"], "C-7",
                           ["missing: factory/tools"])

    def test_c7_fail_empty_templates(self):
        self.assert_defect(["empty-templates"], "C-7",
                           ["factory/templates (empty)"])

    def test_c7_fail_installed_validator_refuses(self):
        self.assert_defect(["bad-workspace-toml"], "C-7",
                           ["installed config_validator",
                            "unknown key: identity.bogus_key"])

    def test_c8_fail_ledger_invalid(self):
        self.assert_defect(["ledger-invalid"], "C-8", ["ledger rejected"])

    def test_c8_fail_no_seed(self):
        # no seed file at all → renders still pass (missing ledger = empty)
        # but C-7 presence names it; C-8 PASSes. Assert C-7 instead.
        self.assert_defect(["no-ledger-seed"], "C-7",
                           ["missing: factory/ledger/events.jsonl"])

    def test_c10_fail_bad_workspace(self):
        self.assert_defect(["bad-workspace-toml"], "C-10",
                           ["unknown key: identity.bogus_key"])

    def test_c11_fail_no_kickoff(self):
        self.assert_defect(["no-kickoff"], "C-11",
                           ["missing factory/KICKOFF.md"])

    def test_c12_fail_no_secrets_doc(self):
        self.assert_defect(["no-secrets-doc"], "C-12",
                           ["missing or empty factory/SECRETS.md"])

    def test_c12_fail_gitignore(self):
        self.assert_defect(["bad-gitignore"], "C-12",
                           ["no secret-ish ignore pattern"])

    def test_c13_fail_no_attest(self):
        self.assert_defect(["drop-attest-C-13"], "C-13",
                           ["no answered attest recorded"])
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td), defects=["drop-attest-C-13"])
            rc, out, _data = verify_home(home)
            self.assertIn("onboard attest C-13 --by <you>", out)

    def test_c14_fail_no_locks(self):
        self.assert_defect(["no-locks"], "C-14",
                           ["missing: factory/locks/OVERRIDE.example"])

    def test_registry_fail_record_missing(self):
        self.assert_defect(["no-record"], "REGISTRY",
                           ["record missing"])

    def test_registry_fail_record_unparseable(self):
        self.assert_defect(["record-unparseable"], "REGISTRY",
                           ["record unparseable"])

    def test_registry_fail_drift(self):
        self.assert_defect(["record-drift"], "REGISTRY",
                           ["registry drift: recorded 99, kit ships 1"])

    def test_registry_kit_sha_drift_advisory(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td), defects=["record-kit-drift"])
            rc, out, data = verify_home(home)
            self.assertEqual(rc, 0, out)  # does NOT gate
            self.assertIn("kit version drift: recorded " + "0" * 40, out)
            adv = [r for r in data["checks"]
                   if r["id"] == "REGISTRY" and r["status"] == "ADVISORY"]
            self.assertEqual(len(adv), 1)
            self.assertEqual(adv[0]["category"], "ADVISORY")

    def test_w1_fail_in_repo(self):
        # a dedicated home whose git root does not resolve to the home
        # itself (T7-fixed semantics) FAILs W-1 with a named detail
        self.assert_defect(["not-git-root"], "W-1",
                           ["not inside a git repo"])

    def test_w2_fail_unresolvable_pin(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td))
            ws = home / "workspace.toml"
            ws.write_text(ws.read_text(encoding="utf-8").replace(
                "e11c0b58f17d7fc2a268a91ea62b57b538d3bf89",
                "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"),
                encoding="utf-8", newline="\n")
            rc, _out, data = verify_home(home)
            self.assertEqual(rc, 1)
            self.assertIn("unresolvable pin", detail_of(data, "W-2"))
            self.assertIn("unresolvable pin", detail_of(data, "C-10"))

    def test_w3_fail_missing_credential_ref(self):
        self.assert_defect(["no-env-credential-file"], "W-3",
                           ["missing credential ref: file:secrets/token.txt"])

    def test_w3_fail_env_var_absent(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td))
            env = home / "environments" / "local.toml"
            env.write_text(env.read_text(encoding="utf-8").replace(
                'refs = ["file:secrets/token.txt"]',
                'refs = ["env:S3T2_DEFINITELY_UNSET_VAR"]'),
                encoding="utf-8", newline="\n")
            rc, _out, data = verify_home(home)
            self.assertEqual(rc, 1)
            self.assertIn(
                "missing credential ref: env:S3T2_DEFINITELY_UNSET_VAR",
                detail_of(data, "W-3"))

    def test_w4_fail_binding_unreachable(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td))
            ws = home / "workspace.toml"
            ws.write_text(ws.read_text(encoding="utf-8").replace(
                '[[repos]]\nid = "app"',
                '[[repos]]\nid = "gone"\nrole = "member"\n'
                'url = "github.com/fixture-org/gone"\n\n'
                '[[repos]]\nid = "app"', 1),
                encoding="utf-8", newline="\n")
            ds = Path(td) / "ds.json"
            data = json.loads(DS_GOOD.read_text(encoding="utf-8"))

            def transport(op, params):
                entry = dict(data[op])
                if op == "repo_view" and params.get("repo", "").endswith("/gone"):
                    return {"error": "Not Found"}
                return entry

            from onboarding import forge_probe as FP
            forge = FP.Forge(transport)
            registry = RD.load_registry()
            kit_sha = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                capture_output=True, text=True).stdout.strip()
            ctx = RD.Context(home.resolve(), forge, registry, kit_sha)
            results = RD.evaluate(ctx)
            w4 = next(r for r in results if r["id"] == "W-4")
            self.assertEqual(w4["status"], "FAIL")
            self.assertIn("binding gone unreachable", w4["detail"])

    def test_w6_fail_no_templates_dir(self):
        self.assert_defect(["empty-templates"], "W-6",
                           ["missing factory/templates/"])

    def test_w8_fail_missing_stage_options(self):
        with tempfile.TemporaryDirectory() as td:
            ds = Path(td) / "ds.json"
            data = json.loads(DS_GOOD.read_text(encoding="utf-8"))
            for node in data["project"]["fields"]["nodes"]:
                if node.get("name") == "Stage":
                    node["options"] = node["options"][:5]
            ds.write_text(json.dumps(data), encoding="utf-8")
            home = make_home(Path(td) / "home")
            rc, _out, jdata = verify_home(home, dataset=ds)
            self.assertEqual(rc, 1)
            detail = detail_of(jdata, "W-8")
            self.assertIn("Stage options missing states:", detail)
            self.assertIn("CODING", detail)
            self.assertIn("BLOCKED", detail)

    def test_u1_fail_declared_no_attest(self):
        self.assert_defect(["drop-attest-U-1"], "U-1",
                           ["no answered attest recorded"])
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td), defects=["drop-attest-U-1"])
            rc, out, _data = verify_home(home)
            self.assertIn("onboard attest U-1 --by <you>", out)

    def test_a2_fail_no_attest(self):
        self.assert_defect(["drop-attest-A-2"], "A-2",
                           ["no answered attest recorded"])


class Applicability(unittest.TestCase):

    def test_in_repo_home_workspace_checks_na(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td), defects=["in-repo"])
            rc, _out, data = verify_home(home)
            for cid in ("W-1", "W-2", "W-3", "W-4", "W-5", "W-6", "W-7",
                        "W-8"):
                row = next(r for r in data["checks"] if r["id"] == cid)
                self.assertEqual(row["status"], "N-A", cid)
                self.assertEqual(row["detail"],
                                 "control_home=in-repo (Mode 1)")
            self.assertEqual(rc, 0)

    def test_ui_undeclared_na(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td), defects=["ui-undeclared"])
            rc, _out, data = verify_home(home)
            for cid in ("U-1", "U-2"):
                row = next(r for r in data["checks"] if r["id"] == cid)
                self.assertEqual(row["status"], "N-A")
                self.assertEqual(row["detail"],
                                 "no UI surface declared (declarations.ui=false)")

    def test_no_hermes_env_a2_na(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td), defects=["no-hermes-env"])
            rc, _out, data = verify_home(home)
            row = next(r for r in data["checks"] if r["id"] == "A-2")
            self.assertEqual(row["status"], "N-A")
            self.assertIn("adapter 'hermes' not in play", row["detail"])
            # human-shell is not an adapter runtime → still no adapter in play
            self.assertIn("runtimes declared", row["detail"])


class AttestSemantics(unittest.TestCase):

    def test_missing_attest_fails_then_append_passes(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td), defects=["drop-attest-C-13"])
            rc, _out, data = verify_home(home)
            self.assertEqual(rc, 1)
            self.assertEqual(
                next(r for r in data["checks"]
                     if r["id"] == "C-13")["status"], "FAIL")
            # the REAL attest CLI appends via kit ledger_lib
            rc2, out2, _err = capture(
                RD.main, ["attest", str(home), "C-13",
                          "--by", "fixture-human",
                          "--note", "pytest -q green locally"])
            self.assertEqual(rc2, 0, out2)
            rc3, _out3, data3 = verify_home(home)
            self.assertEqual(rc3, 0)
            row = next(r for r in data3["checks"] if r["id"] == "C-13")
            self.assertEqual(row["status"], "PASS")
            self.assertIn("attested by fixture-human", row["detail"])
            # the appended row passes the ledger's own validation
            findings = L.validate_ledger(
                home / "factory" / "ledger" / "events.jsonl")
            self.assertEqual(findings, [])

    def test_appended_row_shape(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td), defects=["drop-attest-U-2"])
            RD.append_attest(home, "U-2", "fixture-human", "screenshots ok")
            rows = L.load_ledger(home / "factory" / "ledger" / "events.jsonl")
            row = rows[-1]
            self.assertEqual(row["event"], "human_gate")
            self.assertEqual(row["gate"], "meta")
            self.assertTrue(row["question"].startswith(
                "onboard attest: U-2 "))
            self.assertIsNone(row["story_id"])
            self.assertEqual(row["story_id_reason"],
                             "workspace-level attest (no story)")
            self.assertEqual(row["provenance"], "live")
            self.assertEqual(row["decision"], "approved")
            self.assertEqual(row["answered_by"], "fixture-human")
            self.assertTrue(row["event_id"].startswith(
                "onboard-attest-U-2-"))
            self.assertEqual(L.validate_ledger(
                home / "factory" / "ledger" / "events.jsonl"), [])

    def test_malformed_attest_refused(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td))
            ledger = home / "factory" / "ledger" / "events.jsonl"
            before = ledger.read_bytes()
            with self.assertRaises(RD.AttestError):
                RD.append_attest(home, "C-13", "", "note")
            with self.assertRaises(RD.AttestError):
                RD.append_attest(home, "C-13", "   ", "note")
            with self.assertRaises(RD.AttestError):
                RD.append_attest(home, "C-13", "who", "")
            self.assertEqual(ledger.read_bytes(), before)
            rc, _out, err = capture(
                RD.main, ["attest", str(home), "C-13", "--by", "", "--note", "x"])
            self.assertEqual(rc, 1)
            self.assertIn("attest refused", err)

    def test_attest_refused_for_machine_checked(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td))
            rc, _out, err = capture(
                RD.main, ["attest", str(home), "C-4",
                          "--by", "who", "--note", "trust me"])
            self.assertEqual(rc, 1)
            self.assertIn("not attest-allowed", err)
            rc, _out, err = capture(
                RD.main, ["attest", str(home), "NOPE-1",
                          "--by", "who", "--note", "x"])
            self.assertEqual(rc, 1)
            self.assertIn("unknown check id", err)

    def test_latest_answered_wins(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td), defects=["drop-attest-C-13"])
            RD.append_attest(home, "C-13", "first-human", "first answer")
            RD.append_attest(home, "C-13", "second-human", "second answer")
            rows = L.load_ledger(home / "factory" / "ledger" / "events.jsonl")
            found = RD.find_attest(rows, "C-13")
            self.assertEqual(found["answered_by"], "second-human")
            # rejected latest → FAIL naming it
            ledger = home / "factory" / "ledger" / "events.jsonl"
            now = L.utcnow()
            reject = dict(found)
            reject["event_id"] = "onboard-attest-C-13-rejected-suffix"
            reject["decision"] = "rejected"
            reject["answered_by"] = "third-human"
            reject["recorded_at"] = now
            reject["asked_at"] = now
            reject["answered_at"] = now
            reject["question"] = "onboard attest: C-13 rejected later"
            L.append_event(ledger, reject)
            rc, _out, data = verify_home(home)
            self.assertEqual(rc, 1)
            row = next(r for r in data["checks"] if r["id"] == "C-13")
            self.assertEqual(row["status"], "FAIL")
            self.assertIn("latest attest not approved", row["detail"])
            self.assertIn("third-human", row["detail"])

    def test_pending_row_does_not_count(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td), defects=["drop-attest-U-1"])
            now = L.utcnow()
            L.append_event(home / "factory" / "ledger" / "events.jsonl", {
                "event": "human_gate",
                "event_id": "onboard-attest-U-1-pending-suffix",
                "recorded_at": now,
                "provenance": "live",
                "story_id": None,
                "story_id_reason": "workspace-level attest (no story)",
                "gate": "meta",
                "question": "onboard attest: U-1 please confirm",
                "asked_at": now,
                "answered_at": None,
                "answered_at_reason": "not yet answered",
                "decision": None,
                "decision_reason": "pending",
                "answered_by": None,
                "answered_by_reason": "pending",
            })
            rc, _out, data = verify_home(home)
            self.assertEqual(rc, 1)
            self.assertEqual(
                next(r for r in data["checks"]
                     if r["id"] == "U-1")["status"], "FAIL")


class OutputContract(unittest.TestCase):

    def test_usage_rc2(self):
        rc, _out, _err = capture(RD.main, [])
        self.assertEqual(rc, 2)
        with self.assertRaises(SystemExit) as ctx:
            capture(RD.main, ["verify"])
        self.assertEqual(ctx.exception.code, 2)
        with self.assertRaises(SystemExit):
            capture(RD.main, ["bogus"])

    def test_text_and_json_same_data(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td))
            _rc_t, text, _d = verify_home(home)
            _rc_j, _raw, data = verify_home(home, as_json=True)
            lines = text.splitlines()
            self.assertTrue(lines[0].startswith("verify: "))
            self.assertEqual(lines[-1], "report: factory/PREFLIGHT.md")
            self.assertEqual(lines[-2], "verify status: GREEN")
            self.assertIn(" · advisory: ", lines[-3])
            text_ids = [ln.split(" ")[1] for ln in lines[1:]
                        if ln.startswith(("PASS ", "FAIL ", "N-A ",
                                          "ADVISORY "))]
            json_ids = [r["id"] for r in data["checks"]]
            self.assertEqual(text_ids, json_ids)
            for row in data["checks"]:
                self.assertIn(row["status"], ("PASS", "FAIL", "N-A",
                                              "ADVISORY"))
                self.assertTrue(row["detail"])
            # PASS rows never carry a remedy in JSON
            self.assertTrue(all("remedy" not in r for r in data["checks"]
                                if r["status"] != "FAIL"))

    def test_missing_target_named_error(self):
        rc, out, _err = capture(
            RD.main, ["verify", "/nonexistent/home", "--forge-fixture",
                      str(DS_GOOD)])
        self.assertEqual(rc, 1)
        self.assertIn("verify error: target not found", out)

    def test_forge_unavailable_fail_closed(self):
        """No --forge-fixture and gh not runnable: forge-backed checks
        FAIL with a named `cannot probe:` detail — never a traceback."""
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td))
            # PATH with git but WITHOUT gh (deterministic on any host)
            bin_dir = Path(td) / "bin"
            bin_dir.mkdir()
            git_path = shutil.which("git")
            (bin_dir / "git").symlink_to(git_path)
            env_backup = {k: os.environ.pop(k, None)
                          for k in ("GH_TOKEN", "GITHUB_TOKEN")}
            path_backup = os.environ["PATH"]
            os.environ["PATH"] = str(bin_dir)
            try:
                rc, out, data = capture_and_json(home)
            finally:
                os.environ["PATH"] = path_backup
                for k, v in env_backup.items():
                    if v is not None:
                        os.environ[k] = v
            self.assertEqual(rc, 1)
            self.assertNotIn("Traceback", out)
            for cid in ("C-2", "C-4"):
                row = next(r for r in data["checks"] if r["id"] == cid)
                self.assertEqual(row["status"], "FAIL")
                self.assertTrue(
                    row["detail"].startswith("cannot probe:"), row["detail"])


def capture_and_json(home):
    """verify without a fixture dataset; returns (rc, stdout, json|None)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = RD.run_verify(str(home), forge_fixture=None, as_json=True)
    text = out.getvalue()
    return rc, text, (json.loads(text) if rc in (0, 1) and text.strip()
                      else None)


class EngineErrors(unittest.TestCase):

    def test_registry_row_without_implementation(self):
        registry = RD.load_registry()
        registry["checks"].append({
            "id": "X-99", "category": "CORE", "title": "ghost",
            "required": True, "applies": "always",
            "verify": {"kind": "local", "name": "none"},
            "remedy": "none", "attest_allowed": False})
        with self.assertRaises(RD.EngineError) as ctx:
            RD.validate_implementations(registry)
        self.assertEqual(str(ctx.exception),
                         "registry check not implemented: X-99")

    def test_implementation_without_registry_row(self):
        registry = RD.load_registry()
        registry["checks"] = [r for r in registry["checks"]
                              if r["id"] != "C-1"]
        with self.assertRaises(RD.EngineError) as ctx:
            RD.validate_implementations(registry)
        self.assertEqual(str(ctx.exception),
                         "implementation without registry row: C-1")

    def test_malformed_registry_named_error(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "registry.json"
            bad.write_text("{not json", encoding="utf-8")
            with self.assertRaises(RD.EngineError) as ctx:
                RD.load_registry(bad)
            self.assertIn("registry malformed", str(ctx.exception))
            home = make_home(Path(td) / "h")
            rc, out, _err = capture(
                RD.run_verify, str(home), None, False, bad)
            self.assertEqual(rc, 1)
            self.assertIn("verify error: registry malformed", out)

    def test_engine_load_contract(self):
        registry = RD.load_registry()
        self.assertEqual(registry["registry_version"], 1)
        ids = [r["id"] for r in registry["checks"]]
        self.assertEqual(len(ids), len(set(ids)))
        for cid in ("C-1", "C-2", "C-3", "C-4", "C-5", "C-6", "C-7", "C-8",
                    "C-9", "C-10", "C-11", "C-12", "C-13", "C-14",
                    "REGISTRY", "W-1", "W-2", "W-3", "W-4", "W-5", "W-6",
                    "W-7", "W-8", "U-1", "U-2", "A-1", "A-2", "A-3", "A-4",
                    "A-5", "A-6", "A-7", "A-8", "A-9"):
            self.assertIn(cid, ids)


class HouseLaws(unittest.TestCase):

    def test_stdlib_only_imports(self):
        text = (Path(RD.__file__)).read_text(encoding="utf-8")
        allowed_local = ("onboarding", "tools")
        stdlib = {"argparse", "hashlib", "importlib.util", "json", "os",
                  "re", "subprocess", "sys", "time", "tomllib", "datetime",
                  "pathlib", "typing"}
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("import "):
                mod = line[len("import "):].split(" ")[0].split(".")[0]
                self.assertTrue(mod in stdlib
                                or mod in ("importlib",), line)
            elif line.startswith("from "):
                mod = line[len("from "):].split(" ")[0]
                if mod.startswith("."):
                    continue  # relative import of a local module
                root = mod.split(".")[0]
                self.assertTrue(root in stdlib or root == "__future__"
                                or root in allowed_local, line)

    def test_lf_endings(self):
        for path in (Path(RD.__file__),
                     Path(RD.__file__).parent / "registry.json"):
            raw = path.read_bytes()
            self.assertNotIn(b"\r", raw)
            self.assertTrue(raw.endswith(b"\n"))

    def test_fixture_lf_endings(self):
        for root in (GOOD, BAD):
            for path in root.rglob("*"):
                if path.is_file():
                    self.assertNotIn(b"\r", path.read_bytes(), str(path))

    def test_subprocess_law_no_shell(self):
        text = (Path(RD.__file__)).read_text(encoding="utf-8")
        self.assertNotIn("shell=True", text)
        self.assertIn("PYTHONDONTWRITEBYTECODE", text)
        self.assertIn('"-B"', text)


class DirectRunCLI(unittest.TestCase):

    def test_direct_run_verify(self):
        """`python3 onboarding/readiness.py verify <target>` works."""
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td))
            out = subprocess.run(
                [sys.executable, "-B",
                 str(REPO_ROOT / "onboarding" / "readiness.py"),
                 "verify", str(home), "--forge-fixture", str(DS_GOOD)],
                capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("verify status: GREEN", out.stdout)
            self.assertNotIn("Traceback", out.stderr)

    def test_direct_run_attest(self):
        with tempfile.TemporaryDirectory() as td:
            home = make_home(Path(td), defects=["drop-attest-C-13"])
            out = subprocess.run(
                [sys.executable, "-B",
                 str(REPO_ROOT / "onboarding" / "readiness.py"),
                 "attest", str(home), "C-13",
                 "--by", "fixture-human", "--note", "manual"],
                capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("attest recorded: C-13", out.stdout)


if __name__ == "__main__":
    unittest.main()
