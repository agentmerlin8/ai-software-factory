#!/usr/bin/env python3
"""Tests for onboarding/forge_probe.py — the forge probe layer.

Every test drives Forge through an in-test recorder transport (the
sync_board FakeTransport pattern): NO network, NO gh, and the default
gh-backed transport is never constructed here. Covers: auth scopes
(full/partial/unparseable), repo view (ok/error), rulesets
(active+bypass / disabled / absent / refused-without-raise), labels
(all/subset), issue form (present/absent), project fields
(resolved/unresolvable/bad-grammar), the pure label-ensure argv builder
(verbatim equality) + the single recorded write call, fixture datasets
(load/unknown-op/malformed/end-to-end), read purity, stdlib-only
imports, and LF endings.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from onboarding import forge_probe as FP

FIXTURES = Path(__file__).parent / "fixtures" / "forge"

AUTH_FULL = {
    "stdout": ("github.com\n"
               "  \u2713 Logged in to github.com account fixture-bot "
               "(GH_TOKEN)\n"
               "  - Token scopes: 'gist', 'project', 'read:org', 'repo'\n"),
    "stderr": "",
}
AUTH_PARTIAL = {
    "stdout": ("github.com\n"
               "  \u2713 Logged in to github.com account fixture-bot\n"
               "  - Token scopes: 'repo', 'project'\n"),
    "stderr": "",
}
AUTH_UNPARSEABLE = {"stdout": "nothing useful here\n", "stderr": ""}

REPO_VIEW_OK = {
    "nameWithOwner": "fixture-org/app",
    "defaultBranchRef": {"name": "main"},
    "visibility": "PRIVATE",
}

RULESET_ACTIVE = {"rulesets": [{
    "id": 1, "name": "protect-main", "target": "branch",
    "enforcement": "active",
    "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}},
    "bypass_actors": [
        {"actor_type": "Integration", "actor_id": 5239072,
         "mode": "always"},
    ],
}]}
RULESET_DISABLED = {"rulesets": [{
    "id": 2, "name": "protect-main", "target": "branch",
    "enforcement": "disabled",
    "conditions": {"ref_name": {"include": ["refs/heads/main"]}},
    "bypass_actors": [],
}]}
RULESET_NONE = {"rulesets": []}

LABELS_SUBSET = {"labels": [
    {"name": "factory:run", "color": "0E8A16",
     "description": "kick off"},
]}

ISSUE_FORM_PRESENT = {"present": True,
                      "path": ".github/ISSUE_TEMPLATE/story.yml"}
ISSUE_FORM_ABSENT = {"present": False, "path": None}

PROJECT_RESOLVED = {
    "node_id": "PVT_kwDON8RfA87FIXTURE",
    "fields": {"nodes": [
        {"__typename": "ProjectV2Field", "id": "F_TITLE",
         "name": "Title"},
        {"__typename": "ProjectV2SingleSelectField", "id": "F_STAGE",
         "name": "Stage",
         "options": [{"id": "OPT_CODING", "name": "CODING"},
                     {"id": "OPT_MERGED", "name": "MERGED"}]},
    ]},
}
PROJECT_UNRESOLVED = {"node_id": None, "fields": {"nodes": []}}


class RecorderTransport:
    """In-memory forge transport recorder — the ONE seam under test.

    Records every (op, params) call; per-op returns and raises are
    configurable. No subprocess, no gh, ever."""

    def __init__(self, responses=None, raises=None):
        self.responses = dict(responses or {})
        self.raises = dict(raises or {})
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, op: str, params: dict) -> dict:
        self.calls.append((op, params))
        if op in self.raises:
            raise self.raises[op]
        if op in self.responses:
            return self.responses[op]
        raise FP.ProbeError(f"fixture has no entry for op: {op}")

    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]


class TestAuthScopes(unittest.TestCase):
    def test_full_scopes_missing_empty(self):
        forge = FP.Forge(RecorderTransport({"auth_scopes": AUTH_FULL}))
        result = forge.auth_scopes()
        self.assertEqual(result["login"], "fixture-bot")
        self.assertEqual(result["missing"], [])
        self.assertIn("repo", result["scopes"])

    def test_partial_scopes_sorted_missing(self):
        forge = FP.Forge(RecorderTransport({"auth_scopes": AUTH_PARTIAL}))
        result = forge.auth_scopes()
        self.assertEqual(result["missing"], ["read:org"])

    def test_unparseable_output_named_refusal(self):
        forge = FP.Forge(RecorderTransport(
            {"auth_scopes": AUTH_UNPARSEABLE}))
        with self.assertRaises(FP.ProbeError) as ctx:
            forge.auth_scopes()
        self.assertIn("cannot determine token scopes", str(ctx.exception))

    def test_required_scopes_constant_frozen(self):
        self.assertEqual(FP.REQUIRED_SCOPES,
                         ("repo", "project", "read:org"))


class TestRepoView(unittest.TestCase):
    def test_ok_payload_parsed(self):
        forge = FP.Forge(RecorderTransport({"repo_view": REPO_VIEW_OK}))
        result = forge.repo_view("fixture-org/app")
        self.assertEqual(result, {"name_with_owner": "fixture-org/app",
                                  "default_branch": "main",
                                  "visibility": "PRIVATE"})

    def test_error_entry_named_probe_error(self):
        forge = FP.Forge(RecorderTransport({
            "repo_view": {"error": "HTTP 404: Not Found"}}))
        with self.assertRaises(FP.ProbeError) as ctx:
            forge.repo_view("fixture-org/app")
        self.assertIn("repo view", str(ctx.exception))
        self.assertIn("HTTP 404: Not Found", str(ctx.exception))


class TestRuleset(unittest.TestCase):
    def test_active_with_app_bypass(self):
        forge = FP.Forge(RecorderTransport({"ruleset": RULESET_ACTIVE}))
        result = forge.ruleset("fixture-org/app", "main")
        self.assertTrue(result["found"])
        self.assertEqual(result["enforcement"], "active")
        self.assertEqual(result["read"], "ok")
        self.assertEqual(result["bypass"], [
            {"actor_type": "Integration", "mode": "always",
             "actor_id": 5239072}])

    def test_disabled_enforcement(self):
        forge = FP.Forge(RecorderTransport({"ruleset": RULESET_DISABLED}))
        result = forge.ruleset("fixture-org/app", "main")
        self.assertTrue(result["found"])
        self.assertEqual(result["enforcement"], "disabled")
        self.assertEqual(result["bypass"], [])

    def test_no_rulesets_found_false(self):
        forge = FP.Forge(RecorderTransport({"ruleset": RULESET_NONE}))
        result = forge.ruleset("fixture-org/app", "main")
        self.assertFalse(result["found"])
        self.assertIsNone(result["enforcement"])
        self.assertEqual(result["read"], "ok")

    def test_refused_read_no_raise(self):
        detail = ("probe failed: ruleset — HTTP 403: plan-gated")
        forge = FP.Forge(RecorderTransport(
            raises={"ruleset": FP.ProbeError(detail)}))
        result = forge.ruleset("fixture-org/app", "main")
        self.assertEqual(result["read"], "refused")
        self.assertEqual(result["error"], detail)
        self.assertFalse(result["found"])


class TestLabels(unittest.TestCase):
    def test_all_present_missing_empty(self):
        responses = {"labels": {"labels": [
            {"name": "factory:run", "color": "0E8A16", "description": ""},
            {"name": "needs-human", "color": "B60205", "description": ""},
        ]}}
        forge = FP.Forge(RecorderTransport(responses))
        result = forge.labels("fixture-org/app",
                              ["factory:run", "needs-human"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["present"]["factory:run"]["color"],
                         "0E8A16")

    def test_subset_missing_sorted(self):
        forge = FP.Forge(RecorderTransport({"labels": LABELS_SUBSET}))
        result = forge.labels("fixture-org/app",
                              ["needs-human", "factory:ready",
                               "factory:run"])
        self.assertEqual(result["missing"],
                         ["factory:ready", "needs-human"])


class TestIssueForm(unittest.TestCase):
    def test_present(self):
        forge = FP.Forge(RecorderTransport(
            {"issue_form": ISSUE_FORM_PRESENT}))
        result = forge.issue_form("fixture-org/app", "story.yml")
        self.assertTrue(result["present"])
        self.assertEqual(result["path"],
                         ".github/ISSUE_TEMPLATE/story.yml")

    def test_absent_not_an_error(self):
        forge = FP.Forge(RecorderTransport(
            {"issue_form": ISSUE_FORM_ABSENT}))
        result = forge.issue_form("fixture-org/app", "story.yml")
        self.assertFalse(result["present"])
        self.assertEqual(result["path"],
                         ".github/ISSUE_TEMPLATE/story.yml")


class TestProjectFields(unittest.TestCase):
    def test_resolved_fields_map_with_options(self):
        forge = FP.Forge(RecorderTransport(
            {"project": PROJECT_RESOLVED}))
        result = forge.project_fields("fixture-org/projects/7")
        self.assertTrue(result["found"])
        self.assertEqual(result["node_id"], "PVT_kwDON8RfA87FIXTURE")
        self.assertEqual(result["fields"]["Stage"]["options"],
                         ["CODING", "MERGED"])
        self.assertEqual(result["fields"]["Title"]["type"],
                         "ProjectV2Field")

    def test_unresolvable_found_false_with_error_key(self):
        forge = FP.Forge(RecorderTransport(
            {"project": PROJECT_UNRESOLVED}))
        result = forge.project_fields("fixture-org/projects/99")
        self.assertFalse(result["found"])
        self.assertIsNone(result["node_id"])
        self.assertIn("project not found: fixture-org/projects/99",
                      result["error"])

    def test_bad_locator_grammar_named_refusal(self):
        recorder = RecorderTransport()
        forge = FP.Forge(recorder)
        with self.assertRaises(FP.ProbeError) as ctx:
            forge.project_fields("not a locator")
        self.assertIn("bad project locator", str(ctx.exception))
        self.assertEqual(recorder.calls, [])  # refused before any wire call

    def test_transport_refusal_becomes_error_key(self):
        detail = "probe failed: project fields — GraphQL errors: forbidden"
        forge = FP.Forge(RecorderTransport(
            raises={"project": FP.ProbeError(detail)}))
        result = forge.project_fields("fixture-org/projects/7")
        self.assertFalse(result["found"])
        self.assertEqual(result["error"], detail)


class TestLabelEnsure(unittest.TestCase):
    def test_builder_argv_verbatim(self):
        self.assertEqual(
            FP.build_label_ensure_argv("fixture-org/app", "factory:run",
                                       "0E8A16", "kick off"),
            ["gh", "label", "create", "factory:run",
             "--color", "0E8A16",
             "--description", "kick off",
             "--repo", "fixture-org/app",
             "--force"])

    def test_records_exactly_one_call_with_that_argv(self):
        recorder = RecorderTransport(
            {"label_ensure": {"ok": True, "stderr": None}})
        forge = FP.Forge(recorder)
        result = forge.label_ensure("fixture-org/app", "factory:run",
                                    "0E8A16", "kick off")
        expected = FP.build_label_ensure_argv(
            "fixture-org/app", "factory:run", "0E8A16", "kick off")
        self.assertEqual(result, {"argv": expected, "ok": True})
        self.assertEqual(len(recorder.calls), 1)
        op, params = recorder.calls[0]
        self.assertEqual(op, "label_ensure")
        self.assertEqual(params["argv"], expected)


class TestReadPurity(unittest.TestCase):
    def test_read_probes_never_write(self):
        recorder = RecorderTransport({
            "auth_scopes": AUTH_FULL,
            "repo_view": REPO_VIEW_OK,
            "ruleset": RULESET_ACTIVE,
            "labels": LABELS_SUBSET,
            "issue_form": ISSUE_FORM_PRESENT,
            "project": PROJECT_RESOLVED,
        })
        forge = FP.Forge(recorder)
        forge.auth_scopes()
        forge.repo_view("fixture-org/app")
        forge.ruleset("fixture-org/app", "main")
        forge.labels("fixture-org/app", ["factory:run"])
        forge.issue_form("fixture-org/app", "story.yml")
        forge.project_fields("fixture-org/projects/7")
        self.assertNotIn("label_ensure", recorder.ops())


class TestFixtureDatasets(unittest.TestCase):
    def test_complete_dataset_end_to_end(self):
        forge = FP.Forge.from_fixture(FIXTURES / "complete.json")
        self.assertEqual(forge.auth_scopes()["missing"], [])
        self.assertEqual(forge.repo_view("fixture-org/app")
                         ["name_with_owner"], "fixture-org/app")
        result = forge.ruleset("fixture-org/app", "main")
        self.assertEqual(result["enforcement"], "active")
        self.assertEqual(result["read"], "ok")
        self.assertEqual(result["bypass"][0]["actor_type"], "Integration")
        labels = forge.labels("fixture-org/app",
                              ["factory:run", "factory:ready"])
        self.assertEqual(labels["missing"], [])
        self.assertTrue(forge.issue_form("fixture-org/app",
                                         "story.yml")["present"])
        project = forge.project_fields("fixture-org/projects/7")
        self.assertTrue(project["found"])
        self.assertEqual(project["fields"]["Stage"]["options"],
                         ["CODING", "MERGED", "BLOCKED"])

    def test_partial_dataset_missing_scope_and_labels(self):
        forge = FP.Forge.from_fixture(FIXTURES / "partial.json")
        self.assertEqual(forge.auth_scopes()["missing"], ["read:org"])
        labels = forge.labels("fixture-org/app",
                              ["factory:run", "factory:ready",
                               "needs-human"])
        self.assertEqual(labels["missing"],
                         ["factory:ready", "needs-human"])
        result = forge.ruleset("fixture-org/app", "main")
        self.assertEqual(result["enforcement"], "disabled")
        self.assertFalse(forge.issue_form("fixture-org/app",
                                          "story.yml")["present"])
        self.assertFalse(forge.project_fields(
            "fixture-org/projects/7")["found"])

    def test_refused_dataset_ruleset_and_repo_view(self):
        forge = FP.Forge.from_fixture(FIXTURES / "refused.json")
        result = forge.ruleset("fixture-org/app", "main")
        self.assertEqual(result["read"], "refused")
        self.assertIn("plan-gated", result["error"])
        with self.assertRaises(FP.ProbeError) as ctx:
            forge.repo_view("fixture-org/app")
        self.assertIn("403", str(ctx.exception))

    def test_unknown_op_named_refusal(self):
        forge = FP.Forge.from_fixture(FIXTURES / "complete.json")
        with self.assertRaises(FP.ProbeError) as ctx:
            forge._call("nonexistent_op", "nonexistent", {})
        self.assertEqual(str(ctx.exception),
                         "fixture has no entry for op: nonexistent_op")

    def test_missing_op_entry_named_refusal(self):
        forge = FP.Forge.from_fixture(FIXTURES / "partial.json")
        with self.assertRaises(FP.ProbeError) as ctx:
            forge._call("label_ensure", "label ensure", {})
        self.assertEqual(str(ctx.exception),
                         "fixture has no entry for op: label_ensure")

    def test_malformed_json_fixture_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            with self.assertRaises(FP.FixtureError) as ctx:
                FP.Forge.from_fixture(bad)
            self.assertIn("malformed", str(ctx.exception))

    def test_non_object_dataset_fixture_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "list.json"
            bad.write_text("[]", encoding="utf-8")
            with self.assertRaises(FP.FixtureError):
                FP.Forge.from_fixture(bad)

    def test_missing_dataset_fixture_error(self):
        with self.assertRaises(FP.FixtureError):
            FP.Forge.from_fixture(FIXTURES / "does-not-exist.json")

    def test_error_entry_detail_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            ds = Path(tmp) / "err.json"
            ds.write_text(json.dumps(
                {"repo_view": {"error": "boom named detail"}}),
                encoding="utf-8")
            forge = FP.Forge.from_fixture(ds)
            with self.assertRaises(FP.ProbeError) as ctx:
                forge.repo_view("fixture-org/app")
            self.assertEqual(str(ctx.exception), "boom named detail")

    def test_fixture_error_is_probe_error(self):
        self.assertTrue(issubclass(FP.FixtureError, FP.ProbeError))
        self.assertTrue(issubclass(FP.ProbeError, ValueError))


class TestHouseLaws(unittest.TestCase):
    def test_module_imports_stdlib_only(self):
        for name, value in vars(FP).items():
            if name.startswith("__") or not hasattr(value, "__name__"):
                continue
            mod = sys.modules.get(getattr(value, "__module__", ""), None)
            if value is sys or hasattr(value, "__file__"):
                continue
            top = getattr(value, "__module__", name)
            root = top.split(".")[0]
            if root in ("onboarding",):
                continue
            self.assertIn(root, sys.stdlib_module_names,
                          f"non-stdlib import leaked: {top}")

    def test_new_files_end_lf_no_crlf(self):
        repo = Path(__file__).resolve().parents[2]
        targets = [
            repo / "onboarding" / "__init__.py",
            repo / "onboarding" / "forge_probe.py",
            repo / "onboarding" / "tests" / "__init__.py",
            repo / "onboarding" / "tests" / "test_forge_probe.py",
        ] + sorted((FIXTURES).glob("*.json"))
        for path in targets:
            raw = path.read_bytes()
            self.assertNotIn(b"\r", raw, f"CRLF in {path}")
            self.assertTrue(raw.endswith(b"\n"), f"no trailing LF: {path}")

    def test_default_transport_never_constructed_in_tests(self):
        # Recorder-only invariant: a Forge built here never shells out.
        recorder = RecorderTransport({"auth_scopes": AUTH_FULL})
        forge = FP.Forge(recorder)
        self.assertIs(forge._t, recorder)


if __name__ == "__main__":
    unittest.main()
