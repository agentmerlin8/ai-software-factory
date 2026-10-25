#!/usr/bin/env python3
"""Tests for tools/contract_lib.py — extraction from plan markdown, the
YAML-subset parser, schema/semantic validation with verbatim refusal
messages, contract_paths, and the CLI exit codes (0/1/2)."""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from tools import contract_lib as C

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "contract")

VALID_BLOCK = """```contract.yaml
contract_version: 1
files:
  - path: src/app/parser.py
    op: CREATE
tests:
  - ac: AC-1
    name: rejects_malformed_input
invariants:
  - id: INV-1
    text: "parse errors never write state"
verify_commands:
  - "python3 -m unittest"
decisions:
  - decision: "hand-rolled YAML subset"
    rejected: ["full YAML parser"]
```"""


def extract(block_md: str = VALID_BLOCK) -> dict:
    return C.extract_contract(block_md)


class TestExtract(unittest.TestCase):
    def test_from_fixture_plan(self):
        text = Path(os.path.join(FIXTURES, "good_plan.md")).read_text(
            encoding="utf-8")
        c = C.extract_contract(text)
        self.assertEqual(c["contract_version"], 1)
        self.assertEqual(len(c["files"]), 2)
        self.assertEqual(c["files"][0]["path"], "src/app/parser.py")
        self.assertEqual(c["files"][1]["op"], "EXTEND")
        self.assertEqual(c["tests"][1]["project"], "tests/unit")
        self.assertEqual(C.validate_contract(c), [])

    def test_fence_aliases(self):
        for fence in ("contract.yaml", "yaml contract", "contract"):
            text = VALID_BLOCK.replace("contract.yaml", fence, 1)
            self.assertEqual(extract(text)["contract_version"], 1)

    def test_missing_block_refusal_verbatim(self):
        text = Path(os.path.join(FIXTURES, "no_block_plan.md")).read_text(
            encoding="utf-8")
        with self.assertRaises(C.ContractParseError) as cm:
            C.extract_contract(text)
        self.assertEqual(str(cm.exception),
                         "malformed contract block: no ```contract.yaml "
                         "fenced block found in plan")

    def test_empty_block_refusal(self):
        with self.assertRaises(C.ContractParseError) as cm:
            C.extract_contract("```contract.yaml\n```")
        self.assertEqual(str(cm.exception),
                         "malformed contract block: empty contract block")

    def test_non_mapping_block_refusal(self):
        with self.assertRaises(C.ContractParseError) as cm:
            C.extract_contract("```contract.yaml\n- a\n- b\n```")
        self.assertEqual(str(cm.exception),
                         "malformed contract block: contract block must be "
                         "a mapping")

    def test_bad_indent_refusal(self):
        with self.assertRaises(C.ContractParseError) as cm:
            C.extract_contract("```contract.yaml\nkey: 1\n  bad: 2\n```")
        self.assertTrue(str(cm.exception).startswith(
            "malformed contract block: "), str(cm.exception))


class TestYamlSubset(unittest.TestCase):
    def test_scalars_and_flow(self):
        val = C.parse_yaml_subset(
            's: "quoted # not comment"\nn: 3\nf: 1.5\nb: true\nz: null\n'
            "flow: [a, 'b c', 2]\nempty: []\n")
        self.assertEqual(val["s"], "quoted # not comment")
        self.assertEqual(val["n"], 3)
        self.assertEqual(val["f"], 1.5)
        self.assertIs(val["b"], True)
        self.assertIsNone(val["z"])
        self.assertEqual(val["flow"], ["a", "b c", 2])
        self.assertEqual(val["empty"], [])

    def test_nested_map_in_seq(self):
        val = C.parse_yaml_subset(
            "items:\n  - id: A\n    text: x\n  - id: B\n")
        self.assertEqual(val["items"],
                         [{"id": "A", "text": "x"}, {"id": "B"}])


class TestValidate(unittest.TestCase):
    def test_valid_minimal(self):
        self.assertEqual(C.validate_contract(extract()), [])

    def test_valid_but_unusual_fixture(self):
        text = Path(os.path.join(FIXTURES, "unusual_plan.md")).read_text(
            encoding="utf-8")
        self.assertEqual(C.validate_contract(C.extract_contract(text)), [])

    def test_bad_version_verbatim(self):
        c = extract()
        c["contract_version"] = 2
        self.assertIn("malformed contract block: contract_version must be 1",
                      C.validate_contract(c))

    def test_missing_key_verbatim(self):
        c = extract()
        del c["files"]
        self.assertIn("malformed contract block: contract missing `files`",
                      C.validate_contract(c))

    def test_wrong_type_verbatim(self):
        c = extract()
        c["tests"] = "not-a-list"
        self.assertIn("malformed contract block: `tests` must be a list, "
                      "got str", C.validate_contract(c))

    def test_unknown_op_refused(self):
        c = extract()
        c["files"] = [{"path": "src/x.py", "op": "MODIFY"}]
        errs = C.validate_contract(c)
        self.assertIn("malformed contract block: files entry needs path + "
                      "op CREATE|EXTEND, got {'path': 'src/x.py', 'op': "
                      "'MODIFY'}", errs)

    def test_absolute_and_dotdot_paths_refused(self):
        c = extract()
        c["files"] = [{"path": "/etc/passwd", "op": "CREATE"},
                      {"path": "../escape.py", "op": "CREATE"}]
        errs = C.validate_contract(c)
        self.assertEqual(2, sum(e.startswith("contract block: file path —")
                                for e in errs), errs)

    def test_tests_without_names_refused(self):
        c = extract()
        c["tests"] = [{"ac": "AC-1"}]
        errs = C.validate_contract(c)
        self.assertIn("malformed contract block: tests entry missing `name`: "
                      "{'ac': 'AC-1'}", errs)

    def test_empty_lists_are_semantic_findings(self):
        c = extract()
        c["files"] = []
        c["tests"] = []
        c["invariants"] = []
        c["verify_commands"] = []
        c["decisions"] = []
        errs = C.validate_contract(c)
        self.assertIn("contract block: files — manifest must be non-empty",
                      errs)
        self.assertIn("contract block: tests — must be non-empty (the design "
                      "contract binds the test list)", errs)
        self.assertIn("contract block: invariants — must be non-empty "
                      "(binding on reviewers independently of the test list)",
                      errs)
        self.assertIn("contract block: verify_commands — must be non-empty",
                      errs)
        self.assertIn("contract block: decisions — must be non-empty "
                      "(frozen rationale)", errs)

    def test_failure_model_shape(self):
        c = extract()
        c["failure_model"] = [{"risk": "stale input"}]
        self.assertIn("malformed contract block: failure_model entry needs "
                      "risk + discriminating_test, got {'risk': 'stale "
                      "input'}", C.validate_contract(c))

    def test_api_operations(self):
        c = extract()
        c["api_operations"] = [
            {"method": "FETCH", "route": "/x", "source": "src/a.py",
             "action": "f", "responses": [{"status": 200, "type": "T"}]},
        ]
        errs = C.validate_contract(c)
        self.assertTrue(any("api_operations method —" in e for e in errs),
                        errs)
        c["api_operations"] = [
            {"method": "GET", "route": "x", "source": "/abs.py",
             "action": "", "responses": []},
            {"method": "GET", "route": "x", "source": "src/a.py",
             "action": "f", "removed": True},
        ]
        errs = C.validate_contract(c)
        self.assertTrue(any("api_operations route —" in e for e in errs), errs)
        self.assertTrue(any("api_operations source —" in e for e in errs),
                        errs)
        self.assertTrue(any("duplicate" in e for e in errs), errs)

    def test_api_duplicate_status(self):
        c = extract()
        c["api_operations"] = [
            {"method": "POST", "route": "/items", "source": "src/a.py",
             "action": "create",
             "responses": [{"status": 201, "type": "A"},
                           {"status": 201, "type": "B"}]},
        ]
        self.assertIn("contract block: api_operations duplicate status — "
                      "201 for ('POST', '/items')", C.validate_contract(c))


class TestContractPaths(unittest.TestCase):
    def test_split(self):
        creates, extends = C.contract_paths(extract(
            VALID_BLOCK.replace("op: CREATE", "op: CREATE")))
        self.assertEqual(creates, {"src/app/parser.py"})
        self.assertEqual(extends, set())

    def test_ignores_junk(self):
        creates, extends = C.contract_paths(
            {"files": [{"op": "EXTEND"}, "junk",
                       {"path": "a.py", "op": "EXTEND"}]})
        self.assertEqual((creates, extends), (set(), {"a.py"}))


class TestCli(unittest.TestCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = C.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_ok_exit_0(self):
        rc, out, _ = self.run_main(
            [os.path.join(FIXTURES, "good_plan.md")])
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("OK "))

    def test_findings_exit_1(self):
        rc, out, _ = self.run_main(
            [os.path.join(FIXTURES, "no_block_plan.md")])
        self.assertEqual(rc, 1)
        self.assertIn("malformed contract block:", out)

    def test_no_args_exit_2(self):
        rc, _, err = self.run_main([])
        self.assertEqual(rc, 2)
        self.assertIn("usage:", err)

    def test_unreadable_exit_2(self):
        with tempfile.TemporaryDirectory() as td:
            rc, _, err = self.run_main([os.path.join(td, "missing.md")])
        self.assertEqual(rc, 2)
        self.assertIn("cannot read plan:", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
