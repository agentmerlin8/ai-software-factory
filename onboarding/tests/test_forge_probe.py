#!/usr/bin/env python3
"""Tests for onboarding/forge_probe.py — the forge probe layer.

Every Forge-level test drives an in-test recorder transport (the
sync_board FakeTransport pattern): NO network, NO gh. The real
gh-backed transport is covered separately with subprocess.run stubbed
via unittest.mock — still no network, no gh, no shell. Covers: auth
scopes (full/partial/unparseable), repo view (ok/error), rulesets
(active+bypass / disabled / absent / refused-without-raise),
ref-pattern selector matching (fullmatch glob semantics,
`~ALL`/`~DEFAULT_BRANCH`, unqualified branch patterns, exclude wins),
branch-target filtering (a tag ruleset never shadows a branch one),
ruleset listing pagination (page to exhaustion, then per-id details),
labels (all/subset), issue form (present/absent), project fields
(resolved/unresolvable/bad-grammar), the pure label-ensure argv builder
(verbatim equality) + the single recorded write call, fixture datasets
(load/unknown-op/malformed/end-to-end), parse_project_locator grammar,
the real transport's project branch (resolution -> fields, null
resolution, bad locator refuses before any subprocess call), read
purity, stdlib-only imports, and LF endings.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        self.assertEqual(result["rules"], [])  # default when absent

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
        self.assertEqual(result["rules"], [])

    def test_refused_read_no_raise(self):
        detail = ("probe failed: ruleset — HTTP 403: plan-gated")
        forge = FP.Forge(RecorderTransport(
            raises={"ruleset": FP.ProbeError(detail)}))
        result = forge.ruleset("fixture-org/app", "main")
        self.assertEqual(result["read"], "refused")
        self.assertEqual(result["error"], detail)
        self.assertFalse(result["found"])
        self.assertEqual(result["rules"], [])


class TestRulesetExcludeMatching(unittest.TestCase):
    """F1: a ruleset matches iff include matches AND exclude does NOT
    (same two selector forms on both lists)."""

    def _ruleset(self, include, exclude, branch="main"):
        data = {"rulesets": [{
            "id": 1, "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": include,
                                        "exclude": exclude}},
            "bypass_actors": []}]}
        forge = FP.Forge(RecorderTransport({"ruleset": data}))
        return forge.ruleset("fixture-org/app", branch)

    def test_include_default_branch_exclude_main_not_selected(self):
        result = self._ruleset(["~DEFAULT_BRANCH"], ["refs/heads/main"])
        self.assertFalse(result["found"])
        self.assertEqual(result["read"], "ok")

    def test_include_main_exclude_default_branch_not_selected(self):
        result = self._ruleset(["refs/heads/main"], ["~DEFAULT_BRANCH"])
        self.assertFalse(result["found"])

    def test_exclude_unrelated_branch_still_selected(self):
        result = self._ruleset(["~DEFAULT_BRANCH"], ["refs/heads/release/*"])
        self.assertTrue(result["found"])
        self.assertEqual(result["enforcement"], "active")

    def test_excluded_ruleset_skipped_next_one_considered(self):
        data = {"rulesets": [
            {"id": 1, "target": "branch", "enforcement": "active",
             "conditions": {"ref_name": {
                 "include": ["~DEFAULT_BRANCH"],
                 "exclude": ["refs/heads/main"]}},
             "bypass_actors": []},
            {"id": 2, "target": "branch", "enforcement": "disabled",
             "conditions": {"ref_name": {
                 "include": ["refs/heads/main"]}},
             "bypass_actors": []}]}
        forge = FP.Forge(RecorderTransport({"ruleset": data}))
        result = forge.ruleset("fixture-org/app", "main")
        self.assertTrue(result["found"])
        self.assertEqual(result["enforcement"], "disabled")  # id=2 chosen

    def test_exclude_reads_detail_merged_conditions(self):
        """F1 x F9: on the LIVE path the conditions arrive via the
        per-id detail merge — exclude matching must read the MERGED
        conditions (list summary carries none)."""
        list_json = json.dumps([{"id": 7, "target": "branch",
                                 "enforcement": "active"}])
        detail_json = json.dumps({
            "id": 7, "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"],
                                        "exclude": ["refs/heads/main"]}},
            "bypass_actors": []})
        stub = _stub_run(list_json, detail_json)
        with mock.patch.object(FP.subprocess, "run", stub):
            transport_result = FP.gh_probe_transport(
                "ruleset", {"repo": "fixture-org/app", "branch": "main"})
        forge = FP.Forge(RecorderTransport({"ruleset": transport_result}))
        result = forge.ruleset("fixture-org/app", "main")
        self.assertFalse(result["found"])


class TestRulesetAllSelectorAndActivePreference(unittest.TestCase):
    """G2: `~ALL` matches ANY branch in include/exclude; G3: among
    matching rulesets the first ACTIVE one is selected — non-active
    only as fallback (deterministic, order-independent)."""

    def _forge(self, rulesets):
        return FP.Forge(RecorderTransport({"rulesets": None,
                                           "ruleset": {"rulesets": rulesets}}))

    def _rs(self, rid, enforcement, include, exclude=None):
        cond = {"include": include}
        if exclude is not None:
            cond["exclude"] = exclude
        return {"id": rid, "target": "branch", "enforcement": enforcement,
                "conditions": {"ref_name": cond}, "bypass_actors": []}

    # -- G2: ~ALL selector ---------------------------------------------------

    def test_all_include_selects_any_branch(self):
        forge = self._forge([self._rs(1, "active", ["~ALL"])])
        result = forge.ruleset("o/r", "main")
        self.assertTrue(result["found"])
        result = forge.ruleset("o/r", "some-feature")
        self.assertTrue(result["found"])

    def test_all_exclude_never_selected(self):
        forge = self._forge([self._rs(1, "active", ["~ALL"], ["~ALL"])])
        result = forge.ruleset("o/r", "main")
        self.assertFalse(result["found"])

    def test_all_include_with_literal_exclude(self):
        # ~ALL include + refs/heads/main exclude -> main not selected
        forge = self._forge([self._rs(1, "active", ["~ALL"],
                                      ["refs/heads/main"])])
        self.assertFalse(forge.ruleset("o/r", "main")["found"])
        self.assertTrue(forge.ruleset("o/r", "dev")["found"])

    def test_all_combined_with_default_branch_semantics(self):
        # ~DEFAULT_BRANCH exclude still beats ~ALL include on main
        forge = self._forge([self._rs(1, "active", ["~ALL"],
                                      ["~DEFAULT_BRANCH"])])
        self.assertFalse(forge.ruleset("o/r", "main")["found"])

    # -- G3: active preference ------------------------------------------------

    def test_disabled_then_active_selects_active(self):
        forge = self._forge([self._rs(1, "disabled", ["refs/heads/main"]),
                             self._rs(2, "active", ["refs/heads/main"])])
        result = forge.ruleset("o/r", "main")
        self.assertTrue(result["found"])
        self.assertEqual(result["enforcement"], "active")

    def test_active_then_disabled_selects_active(self):
        forge = self._forge([self._rs(1, "active", ["refs/heads/main"]),
                             self._rs(2, "disabled", ["refs/heads/main"])])
        result = forge.ruleset("o/r", "main")
        self.assertEqual(result["enforcement"], "active")

    def test_disabled_only_falls_back_to_disabled(self):
        # staged, not enforcing — the diagnosis must still surface
        forge = self._forge([self._rs(1, "disabled", ["refs/heads/main"])])
        result = forge.ruleset("o/r", "main")
        self.assertTrue(result["found"])
        self.assertEqual(result["enforcement"], "disabled")

    def test_first_active_wins_among_multiple_active(self):
        forge = self._forge([self._rs(1, "disabled", ["refs/heads/main"]),
                             self._rs(2, "active", ["refs/heads/main"]),
                             self._rs(3, "active", ["refs/heads/main"])])
        result = forge.ruleset("o/r", "main")
        self.assertEqual(result["enforcement"], "active")


class TestRulesetRefPatternMatching(unittest.TestCase):
    """F1 (EX-001): GitHub-compatible ref-pattern selectors — FULLMATCH
    semantics over `refs/heads/<branch>` for include AND exclude:
    `**` crosses `/`, `*` does not, everything else is literal, a
    pattern without a `refs/` prefix is a branch pattern, `~ALL` matches
    any ref, and exclude wins over include. fnmatch semantics (whose `*`
    crosses `/`) never apply."""

    def _result(self, include, exclude, branch):
        rs = {"id": 1, "target": "branch", "enforcement": "active",
              "conditions": {"ref_name": {"include": include}},
              "bypass_actors": [], "rules": [{"type": "deletion"}]}
        if exclude is not None:
            rs["conditions"]["ref_name"]["exclude"] = exclude
        forge = FP.Forge(RecorderTransport({"ruleset": {"rulesets": [rs]}}))
        return forge.ruleset("fixture-org/app", branch)

    def test_wildcard_include_matches_one_segment_not_across_slash(self):
        self.assertTrue(self._result(
            ["refs/heads/release/*"], None, "release/1")["found"])
        self.assertFalse(self._result(
            ["refs/heads/release/*"], None, "release/1/x")["found"])

    def test_double_star_crosses_slash(self):
        self.assertTrue(self._result(
            ["refs/heads/release/**"], None, "release/1/x")["found"])
        self.assertFalse(self._result(
            ["refs/heads/release/**"], None, "release")["found"])

    def test_exact_literal_fullmatch_unchanged(self):
        # a literal pattern still matches its exact ref — and only it
        # (fullmatch: `refs/heads/main` never prefix-matches `mainline`)
        self.assertTrue(self._result(
            ["refs/heads/main"], None, "main")["found"])
        self.assertFalse(self._result(
            ["refs/heads/main"], None, "mainline")["found"])

    def test_unqualified_pattern_is_a_branch_pattern(self):
        self.assertTrue(self._result(
            ["release/*"], None, "release/2")["found"])
        self.assertFalse(self._result(
            ["release/*"], None, "release/2/x")["found"])

    def test_all_selector_matches_every_ref(self):
        self.assertTrue(self._result(
            ["~ALL"], None, "anything/at/all")["found"])

    def test_exclude_wins_over_include(self):
        # `**` include + a refs/heads/release/* exclude -> release/9
        # excluded, everything else covered (exclude lists are ORs)
        self.assertFalse(self._result(
            ["**"], ["refs/heads/release/*"], "release/9")["found"])
        self.assertTrue(self._result(
            ["**"], ["refs/heads/release/*"], "main")["found"])

    def test_exclude_glob_blocks_default_branch(self):
        # EX-001 repro verbatim: include ~DEFAULT_BRANCH, exclude
        # refs/heads/m* — main must NOT be selected (fnmatch's `*`
        # would have crossed the `/` here, but it never applies).
        self.assertFalse(self._result(
            ["~DEFAULT_BRANCH"], ["refs/heads/m*"], "main")["found"])
        self.assertTrue(self._result(
            ["~DEFAULT_BRANCH"], ["refs/heads/m*"], "dev")["found"])

    def test_non_matching_pattern_does_not_match(self):
        self.assertFalse(self._result(
            ["refs/heads/release/*"], None, "main")["found"])
        self.assertFalse(self._result(
            ["refs/tags/v1*"], None, "main")["found"])

    def test_empty_include_never_matches_empty_exclude_never_blocks(self):
        self.assertFalse(self._result([], None, "main")["found"])
        self.assertTrue(self._result(
            ["~DEFAULT_BRANCH"], [], "main")["found"])


class TestRulesetBranchTargetFilter(unittest.TestCase):
    """F3 (EX-003): only rulesets whose `target` is absent or exactly
    'branch' participate in branch-protection selection — an active TAG
    ruleset using ~ALL, even when listed FIRST, must never be chosen."""

    def test_active_tag_ruleset_never_shadows_branch_ruleset(self):
        tag = {"id": 1, "target": "tag", "enforcement": "active",
               "conditions": {"ref_name": {"include": ["~ALL"]}},
               "bypass_actors": [],
               "rules": [{"type": "update"}]}
        branch = {"id": 2, "target": "branch", "enforcement": "active",
                  "conditions": {"ref_name": {
                      "include": ["~DEFAULT_BRANCH"]}},
                  "bypass_actors": [],
                  "rules": [{"type": "deletion"},
                            {"type": "non_fast_forward"}]}
        forge = FP.Forge(RecorderTransport(
            {"ruleset": {"rulesets": [tag, branch]}}))
        result = forge.ruleset("fixture-org/app", "main")
        self.assertTrue(result["found"])
        self.assertEqual(result["target"], "branch")
        self.assertEqual(result["rules"], [{"type": "deletion"},
                                           {"type": "non_fast_forward"}])

    def test_tag_only_ruleset_selected_never(self):
        tag = {"id": 3, "target": "tag", "enforcement": "active",
               "conditions": {"ref_name": {"include": ["~ALL"]}},
               "bypass_actors": []}
        forge = FP.Forge(RecorderTransport(
            {"ruleset": {"rulesets": [tag]}}))
        result = forge.ruleset("fixture-org/app", "main")
        self.assertFalse(result["found"])
        self.assertEqual(result["read"], "ok")

    def test_ruleset_without_target_still_participates(self):
        rs = {"id": 4, "enforcement": "active",
              "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}},
              "bypass_actors": []}
        forge = FP.Forge(RecorderTransport(
            {"ruleset": {"rulesets": [rs]}}))
        result = forge.ruleset("fixture-org/app", "main")
        self.assertTrue(result["found"])
        self.assertEqual(result["target"], "branch")  # absent -> branch


class TestRealTransportRulesetPagination(unittest.TestCase):
    """F2 (EX-002): the ruleset op pages
    `repos/<repo>/rulesets?per_page=100&page=N` to exhaustion (a short
    page ends the loop — the labels law), THEN fetches each detail by
    id — ids on LATER pages must be fetched too with the normalized
    repo path."""

    def test_two_page_stub_fetches_details_for_ids_on_both_pages(self):
        page1 = [{"id": i, "target": "branch"} for i in range(1, 101)]
        page2 = [{"id": 101, "target": "branch"},
                 {"id": 102, "target": "branch"}]
        outputs = [json.dumps(page1), json.dumps(page2)]
        # one detail payload per listed id, in listing order
        outputs += [json.dumps({"id": i, "target": "branch"})
                    for i in range(1, 103)]
        stub = _stub_run(*outputs)
        with mock.patch.object(FP.subprocess, "run", stub):
            result = FP.gh_probe_transport(
                "ruleset", {"repo": "github.com/fixture-org/app",
                            "branch": "main"})
        argvs = [call.args[0] for call in stub.call_args_list]
        self.assertEqual(len(argvs), 2 + 102)  # two pages + 102 details
        self.assertEqual(
            argvs[0], ["gh", "api",
                       "repos/fixture-org/app/rulesets"
                       "?per_page=100&page=1"])
        self.assertEqual(
            argvs[1], ["gh", "api",
                       "repos/fixture-org/app/rulesets"
                       "?per_page=100&page=2"])
        detail_ids = [int(argv[2].rsplit("/", 1)[1]) for argv in argvs[2:]]
        self.assertEqual(detail_ids, list(range(1, 103)))
        # a page-1 id and BOTH page-2 ids got their detail fetch
        self.assertEqual(
            argvs[2], ["gh", "api", "repos/fixture-org/app/rulesets/1"])
        self.assertEqual(
            argvs[-1], ["gh", "api", "repos/fixture-org/app/rulesets/102"])
        ids = [entry["id"] for entry in result["rulesets"]]
        self.assertEqual(ids, list(range(1, 103)))

    def test_single_short_page_stops_after_one_page_call(self):
        page = [{"id": 7, "target": "branch"}]
        stub = _stub_run(json.dumps(page),
                         json.dumps({"id": 7, "target": "branch"}))
        with mock.patch.object(FP.subprocess, "run", stub):
            result = FP.gh_probe_transport(
                "ruleset", {"repo": "fixture-org/app", "branch": "main"})
        argvs = [call.args[0] for call in stub.call_args_list]
        self.assertEqual(len(argvs), 2)  # one list call + one detail
        self.assertIn("?per_page=100&page=1", argvs[0][2])
        self.assertEqual([e["id"] for e in result["rulesets"]], [7])

    def test_non_array_page_named_refusal(self):
        stub = _stub_run(json.dumps({"message": "API rate limit exceeded"}))
        with mock.patch.object(FP.subprocess, "run", stub):
            with self.assertRaises(FP.ProbeError) as ctx:
                FP.gh_probe_transport("ruleset", {"repo": "o/r",
                                                  "branch": "main"})
        self.assertIn("ruleset", str(ctx.exception))
        self.assertIn("expected an array", str(ctx.exception))


class TestRealTransportLabelsPagination(unittest.TestCase):
    """G1: the labels op pages `gh api repos/<repo>/labels` to
    exhaustion — a required label that only appears on page 2 must be
    present (the old --limit 200 single call truncated large repos)."""

    def test_two_page_stub_flattens_labels(self):
        page1 = [{"name": f"filler-{i}", "color": "ededed",
                  "description": ""} for i in range(100)]
        page2 = [{"name": "factory:run", "color": "0E8A16",
                  "description": "kick off"}]
        stub = _stub_run(json.dumps(page1), json.dumps(page2))
        with mock.patch.object(FP.subprocess, "run", stub):
            result = FP.gh_probe_transport(
                "labels", {"repo": "github.com/fixture-org/app"})
        argvs = [call.args[0] for call in stub.call_args_list]
        self.assertEqual(len(argvs), 2)  # paged to exhaustion
        self.assertIn("page=1", argvs[0][2])
        self.assertIn("page=2", argvs[1][2])
        self.assertEqual(len(result["labels"]), 101)
        names = {lbl["name"] for lbl in result["labels"]}
        self.assertIn("factory:run", names)
        # present/missing correct through the Forge probe
        forge = FP.Forge(RecorderTransport({"labels": result}))
        probe = forge.labels("fixture-org/app", ["factory:run", "idea"])
        self.assertEqual(probe["missing"], ["idea"])
        self.assertEqual(probe["present"]["factory:run"]["color"], "0E8A16")

    def test_single_short_page_stops_after_one_call(self):
        stub = _stub_run(json.dumps([{"name": "a", "color": "000000",
                                      "description": None}]))
        with mock.patch.object(FP.subprocess, "run", stub):
            result = FP.gh_probe_transport(
                "labels", {"repo": "fixture-org/app"})
        self.assertEqual(len(stub.call_args_list), 1)
        self.assertEqual(result["labels"],
                         [{"name": "a", "color": "000000",
                           "description": None}])

    def test_non_array_page_named_refusal(self):
        stub = _stub_run(json.dumps({"message": "API rate limit exceeded"}))
        with mock.patch.object(FP.subprocess, "run", stub):
            with self.assertRaises(FP.ProbeError) as ctx:
                FP.gh_probe_transport("labels", {"repo": "o/r"})
        self.assertIn("labels", str(ctx.exception))
        self.assertIn("expected an array", str(ctx.exception))


class TestRulesetCarriesRules(unittest.TestCase):
    """F10: the probe preserves the protection `rules` array."""

    def test_rules_preserved_from_dataset_entry(self):
        rules = [{"type": "deletion"},
                 {"type": "non_fast_forward"},
                 {"type": "pull_request",
                  "parameters": {"required_approving_review_count": 1}},
                 {"type": "required_status_checks",
                  "parameters": {"required_status_checks": []}}]
        data = {"rulesets": [{
            "id": 1, "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}},
            "bypass_actors": [], "rules": rules}]}
        forge = FP.Forge(RecorderTransport({"ruleset": data}))
        result = forge.ruleset("fixture-org/app", "main")
        self.assertTrue(result["found"])
        self.assertEqual(result["rules"], rules)


class TestRealTransportRepoNormalization(unittest.TestCase):
    """F5: `gh api repos/...` endpoints get the owner/name form — the
    workspace canonical `github.com/owner/name` is normalized (stubbed
    subprocess; exact argv asserted). No network, no gh."""

    LIST_EMPTY = json.dumps([])

    def _argvs(self, stub):
        return [call.args[0] for call in stub.call_args_list]

    def test_ruleset_list_endpoint_normalized(self):
        stub = _stub_run(self.LIST_EMPTY)
        with mock.patch.object(FP.subprocess, "run", stub):
            FP.gh_probe_transport(
                "ruleset", {"repo": "github.com/fixture-org/app",
                            "branch": "main"})
        argv = self._argvs(stub)[0]
        self.assertEqual(
            argv, ["gh", "api",
                   "repos/fixture-org/app/rulesets?per_page=100&page=1"])

    def test_ruleset_per_id_endpoint_normalized(self):
        list_json = json.dumps([{"id": 42, "target": "branch"}])
        detail_json = json.dumps({"id": 42, "target": "branch"})
        stub = _stub_run(list_json, detail_json)
        with mock.patch.object(FP.subprocess, "run", stub):
            FP.gh_probe_transport(
                "ruleset", {"repo": "github.com/fixture-org/app",
                            "branch": "main"})
        argvs = self._argvs(stub)
        self.assertEqual(
            argvs[1], ["gh", "api", "repos/fixture-org/app/rulesets/42"])

    def test_issue_form_contents_endpoint_normalized(self):
        stub = _stub_run(json.dumps({"path": ".github/ISSUE_TEMPLATE/x.yml"}))
        with mock.patch.object(FP.subprocess, "run", stub):
            FP.gh_probe_transport(
                "issue_form", {"repo": "github.com/fixture-org/app",
                               "filename": "x.yml"})
        argv = self._argvs(stub)[0]
        self.assertEqual(
            argv, ["gh", "api",
                   "repos/fixture-org/app/contents/"
                   ".github/ISSUE_TEMPLATE/x.yml"])

    def test_plain_slug_unchanged(self):
        stub = _stub_run(self.LIST_EMPTY)
        with mock.patch.object(FP.subprocess, "run", stub):
            FP.gh_probe_transport(
                "ruleset", {"repo": "fixture-org/app", "branch": "main"})
        argv = self._argvs(stub)[0]
        self.assertEqual(
            argv, ["gh", "api",
                   "repos/fixture-org/app/rulesets?per_page=100&page=1"])

    def test_rest_repo_helper_shapes(self):
        self.assertEqual(FP.rest_repo("github.com/fixture-org/app"),
                         "fixture-org/app")
        self.assertEqual(FP.rest_repo("fixture-org/app"),
                         "fixture-org/app")
        self.assertEqual(
            FP.rest_repo("https://github.com/fixture-org/app.git"),
            "fixture-org/app")

    def test_repo_view_keeps_host_prefixed_form(self):
        # `gh repo view` accepts github.com/owner/name (left untouched)
        stub = _stub_run(json.dumps({"nameWithOwner": "fixture-org/app",
                                     "defaultBranchRef": {"name": "main"},
                                     "visibility": "PRIVATE"}))
        with mock.patch.object(FP.subprocess, "run", stub):
            FP.gh_probe_transport(
                "repo_view", {"repo": "github.com/fixture-org/app"})
        self.assertEqual(
            self._argvs(stub)[0][:3],
            ["gh", "repo", "view"])
        self.assertEqual(self._argvs(stub)[0][3], "github.com/fixture-org/app")

    def test_labels_op_normalizes_and_pages_to_exhaustion(self):
        # G1: the labels op now hits the REST endpoint (normalized to
        # owner/name) and pages to exhaustion — the host-prefixed form
        # is NOT kept (it would break the REST path segment).
        stub = _stub_run(json.dumps([]))
        with mock.patch.object(FP.subprocess, "run", stub):
            FP.gh_probe_transport(
                "labels", {"repo": "github.com/fixture-org/app"})
        argv = self._argvs(stub)[0]
        self.assertEqual(argv[:2], ["gh", "api"])
        self.assertTrue(argv[2].startswith("repos/fixture-org/app/labels?"))
        self.assertIn("per_page=100", argv[2])
        self.assertIn("page=1", argv[2])


class TestRealTransportRulesetDetailMerge(unittest.TestCase):
    """F9: the per-id detail payload is merged COMPLETELY — conditions
    and rules come from the detail when the list summary lacks them."""

    def test_detail_conditions_and_rules_win_over_summary(self):
        list_json = json.dumps([{"id": 3, "target": "branch",
                                 "enforcement": "active"}])
        detail_json = json.dumps({
            "id": 3, "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}},
            "rules": [{"type": "deletion"}],
            "bypass_actors": [{"actor_type": "Integration",
                               "actor_id": 9, "mode": "always"}]})
        stub = _stub_run(list_json, detail_json)
        with mock.patch.object(FP.subprocess, "run", stub):
            result = FP.gh_probe_transport(
                "ruleset", {"repo": "fixture-org/app", "branch": "main"})
        entry = result["rulesets"][0]
        self.assertEqual(entry["conditions"],
                         {"ref_name": {"include": ["~DEFAULT_BRANCH"]}})
        self.assertEqual(entry["rules"], [{"type": "deletion"}])
        self.assertEqual(entry["bypass_actors"],
                         [{"actor_type": "Integration",
                           "actor_id": 9, "mode": "always"}])

    def test_probe_selects_ruleset_from_merged_detail(self):
        """The regression the review names: list WITHOUT conditions +
        detail WITH them -> found=True on the live path."""
        list_json = json.dumps([{"id": 3, "target": "branch",
                                 "enforcement": "active"}])
        detail_json = json.dumps({
            "id": 3, "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}},
            "rules": [], "bypass_actors": []})
        stub = _stub_run(list_json, detail_json)
        with mock.patch.object(FP.subprocess, "run", stub):
            transport_result = FP.gh_probe_transport(
                "ruleset", {"repo": "fixture-org/app", "branch": "main"})
        forge = FP.Forge(RecorderTransport({"ruleset": transport_result}))
        result = forge.ruleset("fixture-org/app", "main")
        self.assertTrue(result["found"])
        self.assertEqual(result["enforcement"], "active")

    def test_summary_fallback_when_detail_lacks_key(self):
        list_json = json.dumps([{"id": 3, "target": "branch",
                                 "enforcement": "active",
                                 "name": "from-summary"}])
        detail_json = json.dumps({"id": 3, "target": "branch"})
        stub = _stub_run(list_json, detail_json)
        with mock.patch.object(FP.subprocess, "run", stub):
            result = FP.gh_probe_transport(
                "ruleset", {"repo": "fixture-org/app", "branch": "main"})
        entry = result["rulesets"][0]
        self.assertEqual(entry["name"], "from-summary")  # summary kept
        self.assertEqual(entry["enforcement"], "active")


class TestRulesetTransportBodyShape(unittest.TestCase):
    """F-panel: a non-array listing body (e.g. a rate-limit object at
    rc 0) is a named ProbeError naming the label AND the observed type
    — never a silent 'no rulesets'."""

    def test_object_body_named_refusal(self):
        stub = _stub_run(json.dumps({"message": "API rate limit exceeded"}))
        with mock.patch.object(FP.subprocess, "run", stub):
            with self.assertRaises(FP.ProbeError) as ctx:
                FP.gh_probe_transport(
                    "ruleset", {"repo": "fixture-org/app", "branch": "main"})
        msg = str(ctx.exception)
        self.assertIn("ruleset", msg)
        self.assertIn("expected an array of rulesets", msg)
        self.assertIn("dict", msg)


class TestIssueFormTransport404Classification(unittest.TestCase):
    """F-panel: absence requires 404 AND a (case-insensitive) 'not
    found' — an unrelated error merely mentioning 404 is a named
    ProbeError, never silent absence."""

    @staticmethod
    def _stub_gh_error(stderr: str):
        return mock.Mock(side_effect=lambda argv, **kw:
                         subprocess.CompletedProcess(argv, 1, "", stderr))

    def test_true_404_not_found_is_absence(self):
        stub = self._stub_gh_error("HTTP 404: Not Found (GH006)")
        with mock.patch.object(FP.subprocess, "run", stub):
            result = FP.gh_probe_transport(
                "issue_form", {"repo": "fixture-org/app",
                               "filename": "story.yml"})
        self.assertEqual(result, {"present": False, "path": None})

    def test_case_insensitive_not_found_is_absence(self):
        stub = self._stub_gh_error("error 404: TEMPLATE NOT FOUND")
        with mock.patch.object(FP.subprocess, "run", stub):
            result = FP.gh_probe_transport(
                "issue_form", {"repo": "fixture-org/app",
                               "filename": "story.yml"})
        self.assertEqual(result, {"present": False, "path": None})

    def test_unrelated_404_error_is_named_refusal(self):
        stub = self._stub_gh_error("pipeline 4041 failed: deploy error")
        with mock.patch.object(FP.subprocess, "run", stub):
            with self.assertRaises(FP.ProbeError) as ctx:
                FP.gh_probe_transport(
                    "issue_form", {"repo": "fixture-org/app",
                                   "filename": "story.yml"})
        self.assertIn("4041", str(ctx.exception))

    def test_404_without_not_found_is_named_refusal(self):
        stub = self._stub_gh_error("HTTP 404: API rate limit exceeded")
        with mock.patch.object(FP.subprocess, "run", stub):
            with self.assertRaises(FP.ProbeError) as ctx:
                FP.gh_probe_transport(
                    "issue_form", {"repo": "fixture-org/app",
                                   "filename": "story.yml"})
        self.assertIn("rate limit", str(ctx.exception))


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

    def test_records_exactly_one_call_with_named_params(self):
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
        # the caller-supplied argv is NOT forwarded — the transport
        # builds it from the named params only.
        self.assertNotIn("argv", params)
        self.assertEqual(params, {
            "repo": "fixture-org/app", "name": "factory:run",
            "color": "0E8A16", "description": "kick off"})


class TestGhTransportLabelEnsureBuildsArgv(unittest.TestCase):
    """F-panel: the transport builds the label-ensure argv INSIDE from
    the named params — a caller-supplied `argv` key is refused."""

    def test_transport_builds_argv_internally(self):
        stub = _stub_run("")
        with mock.patch.object(FP.subprocess, "run", stub):
            result = FP.gh_probe_transport(
                "label_ensure", {"repo": "fixture-org/app",
                                 "name": "factory:run", "color": "0E8A16",
                                 "description": "kick off"})
        self.assertEqual(result, {"ok": True, "stderr": None})
        executed = stub.call_args_list[0].args[0]
        self.assertEqual(
            executed, FP.build_label_ensure_argv(
                "fixture-org/app", "factory:run", "0E8A16", "kick off"))

    def test_caller_supplied_argv_refused_nothing_runs(self):
        stalker = list()  # noqa: F823 (deliberate name — assertion target)

        def noop_run(argv, **kwargs):
            stalker.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        hostile = ["gh", "label", "create", "pwned", "--repo", "evil/x"]
        with mock.patch.object(FP.subprocess, "run", side_effect=noop_run):
            with self.assertRaises(FP.ProbeError) as ctx:
                FP.gh_probe_transport(
                    "label_ensure", {"repo": "fixture-org/app",
                                     "name": "factory:run",
                                     "color": "0E8A16",
                                     "description": "x",
                                     "argv": hostile})
        self.assertIn("caller-supplied argv", str(ctx.exception))
        self.assertEqual(stalker, [])  # the hostile argv never executed


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

    def test_non_utf8_dataset_fixture_error_not_unicode_error(self):
        # F6: a non-UTF-8 dataset is a named FixtureError, never a raw
        # UnicodeDecodeError traceback
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "latin1.json"
            bad.write_bytes(b'{"repo_view": {"\xff\xfe\x80": 1}}')
            with self.assertRaises(FP.FixtureError) as ctx:
                FP.Forge.from_fixture(bad)
            self.assertIn("unreadable", str(ctx.exception))

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


class TestFixturePerRepoOverrides(unittest.TestCase):
    """Per-repo override keys "<op>@<slug>" in fixture datasets."""

    DATASET = {
        "ruleset": {"rulesets": [{
            "id": 1, "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}},
            "bypass_actors": [{"actor_type": "Integration",
                               "actor_id": 9, "mode": "always"}]}]},
        "ruleset@fixture-org/app": {"rulesets": [{
            "id": 2, "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}},
            "bypass_actors": []}]},
    }

    def _forge(self, data, tmp):
        ds = Path(tmp) / "ds.json"
        ds.write_text(json.dumps(data), encoding="utf-8")
        return FP.Forge.from_fixture(ds)

    def test_override_wins_for_matching_repo_host_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge = self._forge(self.DATASET, tmp)
            result = forge.ruleset("github.com/fixture-org/app", "main")
        self.assertEqual(result["bypass"], [])  # override (no bypass)

    def test_override_wins_for_matching_repo_slug_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge = self._forge(self.DATASET, tmp)
            result = forge.ruleset("fixture-org/app", "main")
        self.assertEqual(result["bypass"], [])

    def test_default_used_when_no_override_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge = self._forge(self.DATASET, tmp)
            result = forge.ruleset("fixture-org/workspace", "main")
        self.assertTrue(result["found"])
        self.assertEqual(result["bypass"][0]["actor_type"], "Integration")

    def test_override_with_git_suffix_stripped(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge = self._forge(self.DATASET, tmp)
            result = forge.ruleset(
                "https://github.com/fixture-org/app.git", "main")
        self.assertEqual(result["bypass"], [])

    def test_unknown_base_op_in_override_refused_at_load(self):
        data = {"bogus_op@fixture-org/app": {"rulesets": []}}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FP.FixtureError) as ctx:
                self._forge(data, tmp)
            self.assertIn("unknown base op", str(ctx.exception))

    def test_existing_datasets_behavior_unchanged(self):
        # no @-keys in the pre-existing datasets → same lookups as before
        for name in ("complete.json", "partial.json", "refused.json"):
            data = json.loads(
                (FIXTURES / name).read_text(encoding="utf-8"))
            self.assertFalse(any("@" in k for k in data), name)
        forge = FP.Forge.from_fixture(FIXTURES / "complete.json")
        result = forge.ruleset("fixture-org/app", "main")
        self.assertEqual(result["enforcement"], "active")


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


def _stub_run(*outputs: str):
    """A subprocess.run stub returning rc=0 with the given stdouts in
    call order (capture_output/text kwargs shape)."""
    results = [subprocess.CompletedProcess(args=["gh"], returncode=0,
                                           stdout=text, stderr="")
               for text in outputs]
    return mock.Mock(side_effect=results)


GH_JSON_RESOLVE = json.dumps(
    {"data": {"organization": {"projectV2": {"id": "PVT_X"}}}})
GH_JSON_FIELDS = json.dumps(
    {"data": {"node": {"fields": {"nodes": [
        {"__typename": "ProjectV2SingleSelectField", "id": "F_STAGE",
         "name": "Stage",
         "options": [{"id": "OPT_CODING", "name": "CODING"},
                     {"id": "OPT_MERGED", "name": "MERGED"}]},
    ]}}}})
GH_JSON_NULL_RESOLVE = json.dumps(
    {"data": {"organization": {"projectV2": None}}})


class TestParseProjectLocator(unittest.TestCase):
    def test_valid_locator_parses(self):
        self.assertEqual(FP.parse_project_locator("fixture-org/projects/7"),
                         ("fixture-org", 7))

    def test_bad_locators_refused_with_named_message(self):
        for bad in ("fixture-org/projects/x", "nope", "org/projects/7/x",
                    ""):
            with self.assertRaises(FP.ProbeError) as ctx:
                FP.parse_project_locator(bad)
            self.assertEqual(
                str(ctx.exception),
                f"refused: bad project locator '{bad}' — must match "
                "<org>/projects/<n>")


class TestRealTransportProject(unittest.TestCase):
    """The REAL gh_probe_transport with subprocess.run stubbed — closes
    the fixture-masked transport gap. No network, no gh, no shell."""

    def test_resolve_then_fields_two_argv_list_calls(self):
        stub = _stub_run(GH_JSON_RESOLVE, GH_JSON_FIELDS)
        with mock.patch.object(FP.subprocess, "run", stub):
            result = FP.gh_probe_transport(
                "project", {"locator": "fixture-org/projects/7"})
        self.assertEqual(result["node_id"], "PVT_X")
        self.assertEqual(result["fields"],
                         json.loads(GH_JSON_FIELDS)["data"]["node"]["fields"])
        self.assertEqual(stub.call_count, 2)
        for call in stub.call_args_list:
            argv = call.args[0]
            self.assertIsInstance(argv, list)  # argv LIST, never a shell str
            self.assertEqual(argv[:4], ["gh", "api", "graphql", "-f"])
            self.assertTrue(argv[4].startswith("query="))
            self.assertNotIn("shell", call.kwargs)
        self.assertIn('organization(login: "fixture-org")',
                      stub.call_args_list[0].args[0][4])
        self.assertIn('node(id: "PVT_X")',
                      stub.call_args_list[1].args[0][4])

    def test_null_resolution_returns_empty_no_raise(self):
        stub = _stub_run(GH_JSON_NULL_RESOLVE)
        with mock.patch.object(FP.subprocess, "run", stub):
            result = FP.gh_probe_transport(
                "project", {"locator": "fixture-org/projects/7"})
        self.assertEqual(result, {"node_id": None, "fields": {"nodes": []}})
        self.assertEqual(stub.call_count, 1)  # never reads fields

    def test_bad_locator_refused_before_any_subprocess(self):
        stub = _stub_run()
        with mock.patch.object(FP.subprocess, "run", stub):
            with self.assertRaises(FP.ProbeError) as ctx:
                FP.gh_probe_transport(
                    "project", {"locator": "fixture-org/projects/x"})
        self.assertEqual(
            str(ctx.exception),
            "refused: bad project locator 'fixture-org/projects/x' — "
            "must match <org>/projects/<n>")
        stub.assert_not_called()


if __name__ == "__main__":
    unittest.main()
