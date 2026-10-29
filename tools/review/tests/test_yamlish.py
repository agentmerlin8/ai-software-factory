"""Unit tests for tools.review.yamlish (round-trips + named refusals + CLI)."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tools.review import yamlish
from tools.review.yamlish import YamlishError


def error_of(text: str) -> YamlishError:
    try:
        yamlish.loads(text)
    except YamlishError as exc:
        return exc
    raise AssertionError(f"expected a YamlishError for {text!r}")


class RoundTripTests(unittest.TestCase):
    """loads(dumps(x)) == x for every supported shape."""

    def rt(self, obj: object) -> None:
        emitted = yamlish.dumps(obj)
        self.assertEqual(yamlish.loads(emitted), obj, f"round trip failed for {obj!r}: {emitted!r}")

    def test_nested_mapping_and_lists(self) -> None:
        obj = {
            "a": {"b": [1, 2, 3], "c": "x"},
            "d": [{"k": "v"}, {"k2": None}],
            "empty": {},
            "empty_list": [],
        }
        self.rt(obj)

    def test_sequence_of_mappings_canonical_form(self) -> None:
        doc = """findings:
  - id: RC-001
    severity: major
    rubric_dimension: contract_fidelity
    actionable: true
  - id: RC-002
    severity: minor
"""
        self.assertEqual(
            yamlish.loads(doc),
            {
                "findings": [
                    {
                        "id": "RC-001",
                        "severity": "major",
                        "rubric_dimension": "contract_fidelity",
                        "actionable": True,
                    },
                    {"id": "RC-002", "severity": "minor"},
                ]
            },
        )
        self.rt(yamlish.loads(doc))

    def test_nested_sequences(self) -> None:
        self.assertEqual(yamlish.loads("- - 1\n  - 2\n- 3\n"), [[1, 2], 3])
        self.rt([[1, 2], 3, [{"x": ["y"]}]])

    def test_scalar_types(self) -> None:
        for obj in (True, False, None, 0, 1, -42, 10**18, "text", "", "with space"):
            self.rt(obj)
        doc = "a: true\nb: false\nc: null\nd: ~\ne: 0\nf: -17\n"
        self.assertEqual(
            yamlish.loads(doc),
            {"a": True, "b": False, "c": None, "d": None, "e": 0, "f": -17},
        )

    def test_single_quoted_escape(self) -> None:
        self.assertEqual(yamlish.loads("a: 'it''s fine'"), {"a": "it's fine"})
        self.rt({"a": "it's fine"})

    def test_double_quoted_escapes(self) -> None:
        doc = r'a: "newline: \n tab: \t quote: \" backslash: \\"'
        self.assertEqual(
            yamlish.loads(doc),
            {"a": 'newline: \n tab: \t quote: " backslash: \\'},
        )
        self.rt({"a": 'newline: \n tab: \t quote: " backslash: \\'})

    def test_quoted_words_stay_strings(self) -> None:
        self.assertEqual(
            yamlish.loads("a: 'true'\nb: '1.5'\nc: 'null'"),
            {"a": "true", "b": "1.5", "c": "null"},
        )
        self.rt({"a": "true", "b": "1.5", "c": "null", "d": "yes", "e": "True"})

    def test_literal_block_scalar(self) -> None:
        doc = "key: |\n  line one\n  line two\n"
        self.assertEqual(yamlish.loads(doc), {"key": "line one\nline two"})
        doc = "key: |\n  keep\n\n  blank\n"
        self.assertEqual(yamlish.loads(doc), {"key": "keep\n\nblank"})

    def test_literal_dash_chomp_identical(self) -> None:
        self.assertEqual(
            yamlish.loads("key: |-\n  a\n  b\n"),
            yamlish.loads("key: |\n  a\n  b\n"),
        )

    def test_literal_blank_line_before_end_preserved(self) -> None:
        # one explicit blank line is content; only the final terminator is dropped
        self.assertEqual(yamlish.loads("key: |\n  a\n\n"), {"key": "a\n"})
        self.assertEqual(yamlish.loads("key: |\n  a\n"), {"key": "a"})

    def test_folded_block_scalar(self) -> None:
        self.assertEqual(
            yamlish.loads("key: >\n  a\n  b\n\n  c\n"),
            {"key": "a b\nc"},
        )

    def test_block_scalar_in_sequence(self) -> None:
        doc = "items:\n  - |\n    first\n    second\n  - x\n"
        self.assertEqual(yamlish.loads(doc), {"items": ["first\nsecond", "x"]})
        self.rt({"items": ["first\nsecond", "x"]})

    def test_multiline_with_trailing_newline_round_trips(self) -> None:
        self.rt({"key": "a\nb\n"})
        self.rt(["tail\n"])

    def test_multiline_block_round_trip(self) -> None:
        for value in ("a\nb", "a\n\nb", " a\nb", "a\n b", "a\n\tb", "line\n  indented\n"):
            self.rt({"key": value})

    def test_empty_flow_collections(self) -> None:
        self.assertEqual(yamlish.loads("a: []\nb: {}\n"), {"a": [], "b": {}})
        self.rt({"a": [], "b": {}})

    def test_comments_and_blank_lines(self) -> None:
        doc = "# full-line comment\n\na: 1  # trailing comment\n'b key': two\n"
        self.assertEqual(yamlish.loads(doc), {"a": 1, "b key": "two"})
        doc_b = "# full-line comment\n\na: 1\n'b key': two\n"
        self.assertEqual(yamlish.loads(doc_b), yamlish.loads(doc))

    def test_root_scalar(self) -> None:
        self.assertEqual(yamlish.loads("hello\n"), "hello")
        self.rt("hello world")

    def test_root_scalar_with_quotes(self) -> None:
        self.assertEqual(yamlish.loads('"quoted root"\n'), "quoted root")
        self.rt("it's a root")

    def test_empty_document_is_none(self) -> None:
        self.assertIsNone(yamlish.loads(""))
        self.assertIsNone(yamlish.loads("# nothing but comments\n\n"))
        self.rt(None)

    def test_quoted_keys(self) -> None:
        self.assertEqual(yamlish.loads("'weird key': 1\n"), {"weird key": 1})
        self.assertEqual(yamlish.loads('"k\\"": 2\n'), {'k"': 2})
        self.rt({"weird key": 1, 'k"': 2, "": 3})

    def test_dumps_indent_param(self) -> None:
        obj = {"a": {"b": [{"c": 1}]}, "d": "x\ny"}
        emitted = yamlish.dumps(obj, indent=4)
        self.assertEqual(yamlish.loads(emitted), obj)
        self.assertIn("\n    b:", emitted)

    def test_dumps_preserves_insertion_order(self) -> None:
        emitted = yamlish.dumps({"zebra": 1, "alpha": 2, "mid": 3})
        self.assertLess(emitted.index("zebra"), emitted.index("alpha"))
        self.assertLess(emitted.index("alpha"), emitted.index("mid"))
        self.assertEqual(yamlish.loads(emitted), {"zebra": 1, "alpha": 2, "mid": 3})

    def test_dumps_ends_with_newline(self) -> None:
        self.assertTrue(yamlish.dumps({"a": 1}).endswith("\n"))
        self.assertEqual(yamlish.dumps({}), "{}\n")
        self.assertEqual(yamlish.dumps([]), "[]\n")

    def test_dumps_multi_line_as_literal_block(self) -> None:
        emitted = yamlish.dumps({"evidence": "first\nsecond"})
        self.assertIn("evidence: |\n", emitted)
        self.assertEqual(yamlish.loads(emitted), {"evidence": "first\nsecond"})

    def test_roundtrip_strings_that_look_structural(self) -> None:
        # Fuzz-found: all of these must round-trip (quoted or escaped as needed).
        for value in (
            "a: b", "a:", "trailing:", "back\\slash", "...", "---", "a : b",
            "trailing space !", "a: b!", "x:y", "http://example.com",
            "colon: end", ".config", "1e3", "a #b",
        ):
            for wrapped in (value, {"k": value}, [value]):
                self.rt(wrapped)

    def test_roundtrip_hash_inside_block_is_content(self) -> None:
        self.assertEqual(yamlish.loads("k: |\n  a # not a comment\n"), {"k": "a # not a comment"})
        self.rt({"k": "new\nline #x"})

    def test_roundtrip_escaped_blocks_when_dedent_would_eat_spaces(self) -> None:
        # every non-blank line has leading whitespace -> block would lose a
        # space; the emitter must use the escaped double-quoted form instead
        for value in ("\n #x", " x\n  y", "\r\n lines", "x\r\ny"):
            self.rt({"k": value})

    def test_crlf_input_normalized(self) -> None:
        self.assertEqual(yamlish.loads("a: 1\r\nb: 2\r\n"), {"a": 1, "b": 2})

    def test_dumps_refuses_floats_and_non_str_keys(self) -> None:
        with self.assertRaises(YamlishError) as ctx:
            yamlish.dumps({"a": 1.5})
        self.assertIn("floats", str(ctx.exception))
        with self.assertRaises(YamlishError):
            yamlish.dumps({1: "a"})
        with self.assertRaises(YamlishError):
            yamlish.dumps(object())

    def test_yamlish_error_format(self) -> None:
        exc = YamlishError("boom", 7)
        self.assertEqual(str(exc), "yamlish: boom (line 7)")


class RefusalTests(unittest.TestCase):
    """One test per refusal class: prefix 'yamlish:' and a line number."""

    def assert_refusal(self, text, message_part, line):
        exc = error_of(text)
        self.assertTrue(str(exc).startswith("yamlish:"), str(exc))
        self.assertIn(message_part, str(exc))
        self.assertEqual(exc.line, line)

    def test_tabs_refused(self) -> None:
        self.assert_refusal("a:\n\tb: 1\n", "tabs are not allowed", 2)

    def test_anchors_refused(self) -> None:
        self.assert_refusal("a: &name 1\n", "anchors and aliases", 1)
        self.assert_refusal("a: b *ref\n", "anchors and aliases", 1)

    def test_aliases_refused(self) -> None:
        self.assert_refusal("a: *name\n", "anchors and aliases", 1)

    def test_tags_refused(self) -> None:
        self.assert_refusal("a: !!str 1\n", "tags", 1)
        self.assert_refusal("a: !custom thing\n", "tags", 1)

    def test_document_start_marker_refused(self) -> None:
        self.assert_refusal("---\na: 1\n", "document markers", 1)

    def test_document_end_marker_refused(self) -> None:
        self.assert_refusal("a: 1\n...\n", "document markers", 2)

    def test_multiple_documents_refused(self) -> None:
        self.assert_refusal("a: 1\n---\nb: 2\n", "multiple documents", 2)

    def test_nonempty_flow_sequence_refused(self) -> None:
        self.assert_refusal("a: [1, 2]\n", "flow collections", 1)

    def test_nonempty_flow_mapping_refused(self) -> None:
        self.assert_refusal("a: {x: 1}\n", "flow collections", 1)

    def test_duplicate_keys_refused_with_line(self) -> None:
        exc = error_of("a: 1\nb: 2\na: 3\n")
        self.assertIn("duplicate key 'a'", str(exc))
        self.assertEqual(exc.line, 3)

    def test_inconsistent_indentation_refused(self) -> None:
        self.assert_refusal("a:\n    b: 1\n  c: 2\n", "inconsistent indentation", 3)

    def test_sequence_item_mapping_key_wrong_indent_refused(self) -> None:
        exc = error_of("items:\n  - id: X\n    severity: y\n  id: Z\n")
        self.assertIn("sequence item with a mapping key", str(exc))
        self.assertEqual(exc.line, 4)

    def test_sequence_item_key_at_dash_indent_refused(self) -> None:
        exc = error_of("- id: X\nseverity: y\n")
        self.assertIn("sequence item with a mapping key", str(exc))
        self.assertEqual(exc.line, 2)
        self.assertIsInstance(exc, YamlishError)

    def test_unterminated_single_quote_refused(self) -> None:
        self.assert_refusal("a: 'unterminated\n", "unterminated quoted scalar", 1)

    def test_unterminated_double_quote_refused(self) -> None:
        self.assert_refusal('a: "unterminated\n', "unterminated quoted scalar", 1)

    def test_invalid_double_escape_refused(self) -> None:
        self.assert_refusal('a: "bad \\q here"\n', "invalid escape", 1)

    def test_float_forms_refused(self) -> None:
        self.assert_refusal("a: 1.5\n", "floats are not supported", 1)
        self.assert_refusal("a: .5\n", "floats are not supported", 1)
        self.assert_refusal("a: 1e3\n", "floats are not supported", 1)
        self.assert_refusal("a: 1.\n", "floats are not supported", 1)

    def test_content_after_quoted_scalar_refused(self) -> None:
        self.assert_refusal("a: 'x' stray\n", "content after a quoted scalar", 1)

    def test_multiline_plain_scalar_refused(self) -> None:
        self.assert_refusal("a: hello\n  world\n", "plain scalars may not span", 2)
        self.assert_refusal("- hello\n  world\n", "plain scalars may not span", 2)

    def test_mixed_mapping_and_sequence_refused(self) -> None:
        self.assert_refusal("a: 1\n- x\n", "cannot mix mapping keys", 2)

    def test_invalid_mapping_key_refused(self) -> None:
        self.assert_refusal("a b: 1\n", "invalid mapping key", 1)

    def test_complex_key_refused(self) -> None:
        self.assert_refusal("a: 1\n? complex\n: 2\n", "complex keys", 2)

    def test_misplaced_block_header_refused(self) -> None:
        self.assert_refusal("a: |x\n", "block scalar headers", 1)
        self.assert_refusal("| x\n", "only allowed directly after 'key:' or '- '", 1)

    def test_backslash_outside_quotes_refused(self) -> None:
        self.assert_refusal("a: bad\\scape\n", "backslashes", 1)

    def test_stray_line_after_quoted_scalar(self) -> None:
        exc = error_of("'done'\nextra\n")
        self.assertIn("plain scalars may not span", str(exc))
        self.assertEqual(exc.line, 2)


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = yamlish.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_loads_ok_exit_0(self) -> None:
        path = self.dir / "doc.yaml"
        path.write_text("a: 1\nb: two\n", encoding="utf-8")
        code, out, err = self.run_cli(["yamlish", "loads", str(path)])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(json.loads(out), {"a": 1, "b": "two"})

    def test_loads_nested_round_trip_via_cli(self) -> None:
        path = self.dir / "doc.yaml"
        obj = {"a": [{"b": "x\ny"}, True, None], "c": -3}
        path.write_text(yamlish.dumps(obj), encoding="utf-8")
        code, out, _ = self.run_cli(["yamlish", "loads", str(path)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), obj)

    def test_loads_parse_error_exit_1_stderr(self) -> None:
        path = self.dir / "bad.yaml"
        path.write_text("a: 1.5\n", encoding="utf-8")
        code, _, err = self.run_cli(["yamlish", "loads", str(path)])
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("yamlish:"), err)
        self.assertIn("line 1", err)

    def test_usage_exit_2(self) -> None:
        for argv in (["yamlish"], ["yamlish", "loads"], ["yamlish", "nope", "f"], ["yamlish", "loads", "a", "b"]):
            code, _, err = self.run_cli(argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("usage", err)

    def test_missing_file_exit_1(self) -> None:
        code, _, err = self.run_cli(["yamlish", "loads", str(self.dir / "nope.yaml")])
        self.assertEqual(code, 1)
        self.assertIn("cannot read", err)


if __name__ == "__main__":
    unittest.main()