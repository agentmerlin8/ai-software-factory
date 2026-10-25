#!/usr/bin/env python3
"""Tests for tools/sync_board.py — the board bridge: config-driven
project-id resolution (workspace.toml / --project, deferred = named
refusal), the single injectable GraphQL transport (in-memory recorder —
NO network, NO gh calls), exact payload construction for add/set, the
fail-closed Stage refusals naming the missing field/option (board schema
is read-only), status parsing, and CLI exit codes.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from tools import state_machine as SM
from tools import sync_board as SB

SCHEMA_RESPONSE = {"node": {"fields": {"nodes": [
    {"__typename": "ProjectV2Field", "id": "F_TITLE", "name": "Title"},
    {"__typename": "ProjectV2SingleSelectField", "id": "F_STAGE",
     "name": "Stage",
     "options": [{"id": "OPT_CODING", "name": "CODING"},
                 {"id": "OPT_MERGED", "name": "MERGED"},
                 {"id": "OPT_BLOCKED", "name": "BLOCKED"}]},
]}}}

ITEMS_RESPONSE = {"node": {
    "title": "Fixture Board", "url": "https://board.test",
    "items": {"nodes": [
        {"id": "PVTI_1",
         "content": {"number": 42, "title": "fixture story one"},
         "fieldValues": {"nodes": [
             {"field": {"name": "Stage"}, "name": "CODING"},
             {"field": {"name": "Title"}, "name": "fixture story one"},
         ]}},
        {"id": "PVTI_2",
         "content": {"number": 43, "title": "fixture story two"},
         "fieldValues": {"nodes": []}},
        {"id": "PVTI_3", "content": None,
         "fieldValues": {"nodes": []}},
    ]}}}

ISSUE_RESPONSE = {"repository": {"issue": {"id": "I_42"}}}


class FakeTransport:
    """In-memory GraphQL recorder — the ONE seam Board uses."""

    def __init__(self, schema=None, items=None, issue=None):
        self.schema = schema if schema is not None else SCHEMA_RESPONSE
        self.items = items if items is not None else ITEMS_RESPONSE
        self.issue = issue if issue is not None else ISSUE_RESPONSE
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, query: str, **variables) -> dict:
        self.calls.append((query, variables))
        if "fields(first: 100)" in query:
            return self.schema
        if "items(first: 100)" in query:
            return self.items
        if "repository(owner:" in query:
            return self.issue
        if query.startswith("mutation"):
            if "addProjectV2ItemById" in query:
                return {"addProjectV2ItemById": {"item": {"id": "PVTI_NEW"}}}
            return {"updateProjectV2ItemFieldValue":
                    {"projectV2Item": {"id": "PVTI_1"}}}
        raise AssertionError(f"unexpected query: {query[:80]}")

    def mutations(self) -> list[str]:
        return [q for q, _ in self.calls if q.startswith("mutation")]


def board_with(transport: FakeTransport,
               project_id: str = "fixture-org/projects/7") -> SB.Board:
    return SB.Board(project_id, gql=transport)


class StageVocabularyTests(unittest.TestCase):
    def test_stages_are_the_state_machine_states(self):
        # imported authority — never re-typed
        self.assertIs(SB.STAGES, SM.STATES)

    def test_normalize_case_insensitive(self):
        self.assertEqual(SB.normalize_stage("coding"), "CODING")
        self.assertEqual(SB.normalize_stage(" Ready_For_Review "),
                         "READY_FOR_REVIEW")

    def test_invalid_stage_error_lists_every_state(self):
        with self.assertRaises(ValueError) as ctx:
            SB.normalize_stage("SHIPPED")
        msg = str(ctx.exception)
        for s in SM.STATES:
            self.assertIn(s, msg)


class ConfigResolutionTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def write_ws(self, text: str) -> None:
        (self.root / "workspace.toml").write_text(text, encoding="utf-8")

    def test_cli_project_wins(self):
        self.write_ws('board = { project_id = "ws-org/projects/1" }\n')
        self.assertEqual(
            SB.load_project_id(self.root, "cli-org/projects/9"),
            "cli-org/projects/9")

    def test_workspace_toml_resolution(self):
        self.write_ws('board = { project_id = "ws-org/projects/3" }\n')
        self.assertEqual(SB.load_project_id(self.root), "ws-org/projects/3")

    def test_deferred_project_id_is_a_named_refusal(self):
        self.write_ws('board = { project_id = "<org>/projects/<n>" }\n')
        with self.assertRaises(SB.ConfigError) as ctx:
            SB.load_project_id(self.root)
        self.assertEqual(
            str(ctx.exception),
            "REFUSED: board.project_id is deferred ('<org>/projects/<n>') "
            "— set a concrete <org>/projects/<n> in "
            f"{self.root / 'workspace.toml'} or pass --project")
        self.assertEqual(ctx.exception.rc, 2)

    def test_absent_project_id_is_a_named_refusal(self):
        self.write_ws('workspace = "x"\n')
        with self.assertRaises(SB.ConfigError) as ctx:
            SB.load_project_id(self.root)
        self.assertIn("REFUSED: board project id not configured",
                      str(ctx.exception))

    def test_malformed_project_id_refused(self):
        self.write_ws('board = { project_id = "projects/3" }\n')
        with self.assertRaises(SB.ConfigError) as ctx:
            SB.load_project_id(self.root)
        self.assertIn("REFUSED: bad board.project_id", str(ctx.exception))

    def test_cli_deferred_project_refused(self):
        with self.assertRaises(SB.ConfigError) as ctx:
            SB.load_project_id(self.root, "<org>/projects/<n>")
        self.assertIn("REFUSED: --project is deferred", str(ctx.exception))

    def test_repo_from_first_binding(self):
        self.write_ws('[[repos]]\nid = "app"\nrole = "primary"\n'
                      'url = "github.test/fixture-org/app"\n')
        self.assertEqual(SB.load_repo(self.root),
                         ("fixture-org", "app"))

    def test_repo_cli_override_and_refusal(self):
        self.write_ws("")
        self.assertEqual(SB.load_repo(self.root, "o/n"), ("o", "n"))
        with self.assertRaises(SB.ConfigError) as ctx:
            SB.load_repo(self.root)
        self.assertIn("REFUSED: repo not configured", str(ctx.exception))
        with self.assertRaises(SB.ConfigError):
            SB.load_repo(self.root, "bad")


class SchemaReadOnlyRefusalTests(unittest.TestCase):
    """Board schema is READ only — missing Stage field/option = named
    fail-closed refusal, never creation, never inference."""

    def test_missing_stage_field_names_itself(self):
        schema = {"node": {"fields": {"nodes": [
            {"__typename": "ProjectV2Field", "id": "F_TITLE",
             "name": "Title"}]}}}
        board = board_with(FakeTransport(schema=schema))
        with self.assertRaises(SB.BoardError) as ctx:
            board.stage_field()
        self.assertIn("REFUSED: project fixture-org/projects/7 has no "
                      "'Stage' single-select field", str(ctx.exception))
        self.assertIn("board-schema changes are excluded",
                      str(ctx.exception))

    def test_missing_option_refusal_names_the_option(self):
        board = board_with(FakeTransport())
        with self.assertRaises(SB.BoardError) as ctx:
            board.stage_option_id("CLOSED")
        self.assertEqual(
            str(ctx.exception),
            "REFUSED: Stage option 'CLOSED' missing on project "
            "fixture-org/projects/7 — board-schema changes are excluded; "
            "options present: ['BLOCKED', 'CODING', 'MERGED']")
        self.assertEqual(ctx.exception.rc, 1)

    def test_existing_option_resolves(self):
        board = board_with(FakeTransport())
        self.assertEqual(board.stage_option_id("CODING"), "OPT_CODING")


class PayloadConstructionTests(unittest.TestCase):
    def test_set_stage_payload_exact(self):
        payload = SB.build_set_stage_payload("P1", "ITEM_1", "F_STAGE",
                                             "OPT_CODING")
        self.assertEqual(payload, """mutation {
  updateProjectV2ItemFieldValue(input: {
    projectId: "P1", itemId: "ITEM_1",
    fieldId: "F_STAGE",
    value: {singleSelectOptionId: "OPT_CODING"}
  }) { projectV2Item { id } }
}""")

    def test_add_item_payload_exact(self):
        payload = SB.build_add_item_payload("P1", "I_42")
        self.assertEqual(payload, """mutation {
  addProjectV2ItemById(input: {
    projectId: "P1", contentId: "I_42"
  }) { item { id } }
}""")

    def test_issue_query_exact(self):
        q = SB.build_issue_id_query("fixture-org", "app", 42)
        self.assertEqual(q, """query {
  repository(owner: "fixture-org", name: "app") {
    issue(number: 42) { id }
  }
}""")


class CmdSetTests(unittest.TestCase):
    def setUp(self):
        self.t = FakeTransport()
        self.board = board_with(self.t)

    def test_set_writes_the_resolved_payload(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = SB.cmd_set(self.board, 42, "coding")
        self.assertEqual(rc, 0)
        self.assertEqual(self.t.mutations(), [
            SB.build_set_stage_payload("fixture-org/projects/7", "PVTI_1",
                                       "F_STAGE", "OPT_CODING")])
        self.assertIn("updated #42: Stage=CODING", out.getvalue())

    def test_dry_run_prints_payload_and_writes_nothing(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = SB.cmd_set(self.board, 42, "MERGED", dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.t.mutations(), [])
        printed = out.getvalue()
        self.assertIn(SB.build_set_stage_payload(
            "fixture-org/projects/7", "PVTI_1", "F_STAGE", "OPT_MERGED"),
            printed)
        self.assertIn("dry-run: would set issue #42 Stage=MERGED", printed)

    def test_option_missing_refuses_before_any_mutation(self):
        with self.assertRaises(SB.BoardError) as ctx:
            SB.cmd_set(self.board, 42, "LIVE_PROVEN")
        self.assertIn("REFUSED: Stage option 'LIVE_PROVEN' missing",
                      str(ctx.exception))
        self.assertEqual(self.t.mutations(), [])

    def test_item_not_on_board_refuses(self):
        with self.assertRaises(SB.BoardError) as ctx:
            SB.cmd_set(self.board, 99, "CODING")
        self.assertEqual(
            str(ctx.exception),
            "REFUSED: issue #99 is not on the board — run "
            "`add --issue 99` first")
        self.assertEqual(self.t.mutations(), [])


class CmdAddTests(unittest.TestCase):
    def setUp(self):
        self.t = FakeTransport()
        self.board = board_with(self.t)

    def test_add_writes_the_resolved_payload(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = SB.cmd_add(self.board, 42, ("fixture-org", "app"))
        self.assertEqual(rc, 0)
        self.assertEqual(self.t.mutations(), [
            SB.build_add_item_payload("fixture-org/projects/7", "I_42")])
        self.assertIn("added #42 to board (item PVTI_NEW)", out.getvalue())

    def test_dry_run_prints_payload_and_writes_nothing(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = SB.cmd_add(self.board, 42, ("fixture-org", "app"),
                            dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.t.mutations(), [])
        self.assertIn(SB.build_add_item_payload("fixture-org/projects/7",
                                                "I_42"), out.getvalue())
        self.assertIn("no write performed", out.getvalue())

    def test_missing_issue_refused(self):
        t = FakeTransport(issue={"repository": {"issue": None}})
        board = board_with(t)
        with self.assertRaises(SB.BoardError) as ctx:
            SB.cmd_add(board, 42, ("fixture-org", "app"))
        self.assertEqual(
            str(ctx.exception),
            "REFUSED: issue #42 not found in fixture-org/app")


class CmdStatusTests(unittest.TestCase):
    def test_status_parses_items_and_stage(self):
        t = FakeTransport()
        board = board_with(t)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = SB.cmd_status(board)
        self.assertEqual(rc, 0)
        printed = out.getvalue()
        self.assertIn("Board: Fixture Board (https://board.test)", printed)
        self.assertIn("42  fixture story one", printed)
        self.assertIn("Stage=CODING", printed)
        self.assertIn("43  fixture story two", printed)
        self.assertIn("Stage=(unset)", printed)
        self.assertIn("-  (draft)", printed)  # content-less item

    def test_status_items_pure_parse(self):
        board = board_with(FakeTransport())
        proj, items = board.status_items()
        self.assertEqual(proj["title"], "Fixture Board")
        self.assertEqual(items[0], {"number": 42,
                                    "title": "fixture story one",
                                    "stage": "CODING"})
        self.assertEqual(items[1]["stage"], None)
        self.assertEqual(items[2]["title"], "(draft)")


class MainCliTests(unittest.TestCase):
    """CLI rc contract: 0 ok · 1 refusal · 2 usage/configuration — driven
    through main() with a monkeypatched transport factory (no network)."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        (self.root / "workspace.toml").write_text(
            'board = { project_id = "fixture-org/projects/7" }\n'
            '[[repos]]\nid = "app"\nrole = "primary"\n'
            'url = "github.test/fixture-org/app"\n', encoding="utf-8")
        self.t = FakeTransport()
        self._orig_board = SB.Board
        SB.Board = lambda pid, gql=None: self._orig_board(pid, gql=self.t)

    def tearDown(self):
        SB.Board = self._orig_board
        self._td.cleanup()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = SB.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_status_ok(self):
        rc, out, _ = self.run_main(["--root", str(self.root), "status"])
        self.assertEqual(rc, 0)
        self.assertIn("Fixture Board", out)

    def test_set_dry_run_ok_no_write(self):
        rc, out, _ = self.run_main(
            ["--root", str(self.root), "set", "--issue", "42",
             "--stage", "merged", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIn('value: {singleSelectOptionId: "OPT_MERGED"}', out)
        self.assertEqual(self.t.mutations(), [])

    def test_set_writes(self):
        rc, out, _ = self.run_main(
            ["--root", str(self.root), "set", "--issue", "42",
             "--stage", "CODING"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.t.mutations()), 1)

    def test_invalid_stage_is_usage_rc2_before_transport(self):
        rc, out, _ = self.run_main(
            ["--root", str(self.root), "set", "--issue", "42",
             "--stage", "SHIPPED"])
        self.assertEqual(rc, 2)
        self.assertIn("REFUSED: invalid stage 'SHIPPED'", out)
        self.assertEqual(self.t.calls, [])

    def test_missing_option_is_refusal_rc1(self):
        rc, out, _ = self.run_main(
            ["--root", str(self.root), "set", "--issue", "42",
             "--stage", "CLOSED"])
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED: Stage option 'CLOSED' missing", out)

    def test_deferred_project_is_rc2(self):
        (self.root / "workspace.toml").write_text(
            'board = { project_id = "<org>/projects/<n>" }\n',
            encoding="utf-8")
        rc, out, _ = self.run_main(["--root", str(self.root), "status"])
        self.assertEqual(rc, 2)
        self.assertIn("REFUSED: board.project_id is deferred", out)
        self.assertEqual(self.t.calls, [])  # nothing configured → no I/O

    def test_cli_project_override_used(self):
        rc, _, _ = self.run_main(
            ["--root", str(self.root), "--project", "cli-org/projects/2",
             "status"])
        self.assertEqual(rc, 0)
        schema_call = self.t.calls[0]
        self.assertEqual(schema_call[1], {"id": "cli-org/projects/2"})


if __name__ == "__main__":
    unittest.main()
