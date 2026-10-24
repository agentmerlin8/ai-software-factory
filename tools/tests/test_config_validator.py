#!/usr/bin/env python3
"""Tests for tools/config_validator.py — every refusal class is NAMED.

Single fixture root: `tools/tests/fixtures/config/`. Each fixture is a
directory named after its behavior holding one parseable TOML file — kind
classification is by file basename (`workspace.toml` → workspace schema,
anything else → environment schema), so workspace fixtures ship
`workspace.toml` and environment fixtures ship `env.toml`.

Every test drives `CV.main([...])` (exit code: 0 OK · 1 FAIL · 2 usage) with
stdout captured, asserting the exact named error lines — not exit codes
alone. Pin-resolution fixtures (real HEAD sha, real tag, absent sha) are
built in a temp git repo per test class and are never committed.
"""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools import config_validator as CV

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "config"
KIT_ROOT = Path(__file__).resolve().parents[2]
WS_TEMPLATE = KIT_ROOT / "templates" / "workspace" / "workspace.toml"
ENV_TEMPLATES = [
    KIT_ROOT / "templates" / "workspace" / "environments" / "local.toml",
    KIT_ROOT / "templates" / "workspace" / "environments" / "github.toml",
]
SHIPPED_TEMPLATES = [WS_TEMPLATE, *ENV_TEMPLATES]

WS_BASE = """\
workspace = "demo"
control_home = "dedicated"
[factory]
pin = "<kit>"
[[repos]]
id = "app"
role = "primary"
url = "github.com/acme/app"
"""

ENV_BASE = """\
runtime = "runner"
[dispatch]
mechanism = "invoke"
[models]
T1 = "model-x"
"""


def run_main(args) -> tuple[int, list[str]]:
    """Drive CV.main, returning (exit_code, stdout lines)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = CV.main([str(a) for a in args])
    return code, buf.getvalue().splitlines()


def error_lines(output: list[str]) -> list[str]:
    return [line for line in output if line.startswith("  - ")]


def fixture(name: str) -> Path:
    """The single TOML file inside fixtures/config/<name>/."""
    path = FIXTURES / name
    for candidate in ("workspace.toml", "env.toml"):
        if (path / candidate).exists():
            return path / candidate
    raise FileNotFoundError(path)


def write_tmp(doc: str, name: str = "env.toml") -> Path:
    path = Path(tempfile.mkdtemp(prefix="cv-")) / name
    path.write_text(doc)
    return path


class TestShippedTemplates(unittest.TestCase):
    def test_templates_pass_default_mode(self):
        code, output = run_main(SHIPPED_TEMPLATES)
        self.assertEqual(code, 0, output)
        self.assertEqual(len(output), len(SHIPPED_TEMPLATES), output)
        for line in output:
            self.assertTrue(line.startswith("OK "), line)

    def test_templates_refused_in_strict_mode(self):
        code, output = run_main(["--strict", *SHIPPED_TEMPLATES])
        self.assertEqual(code, 1, output)
        self.assertIn("  - deferred placeholder not resolved: factory.pin",
                      output)
        self.assertIn("  - deferred placeholder not resolved: "
                      "board.project_id", output)
        self.assertIn("  - deferred placeholder not resolved: models.T1",
                      output)

    def test_subprocess_smoke(self):
        script = KIT_ROOT / "tools" / "config_validator.py"
        result = subprocess.run(
            [sys.executable, str(script), *map(str, SHIPPED_TEMPLATES)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for line in result.stdout.splitlines():
            self.assertTrue(line.startswith("OK "), line)


class TestWorkspaceFailures(unittest.TestCase):
    def _refuse(self, name: str, messages: list[str],
                absent: tuple[str, ...] = ()) -> None:
        code, output = run_main([fixture(name)])
        self.assertEqual(code, 1, output)
        errors = error_lines(output)
        for message in messages:
            self.assertIn(message, errors)
        for message in absent:
            self.assertNotIn(message, errors)

    def test_unknown_key(self):
        self._refuse("bad_unknown_key", ["  - unknown key: bogus"])

    def test_binding_role(self):
        self._refuse("bad_binding_role",
                     ["  - malformed binding: repos[0].role must be "
                      "primary|member (got 'secondary')"])

    def test_binding_dup_ids(self):
        self._refuse("bad_binding_dup_ids",
                     ["  - malformed binding: duplicate repo id 'app'"])

    def test_binding_url_scheme(self):
        self._refuse("bad_binding_url_scheme",
                     ["  - malformed binding: repos[0].url must be "
                      "host/owner/name — never a URL scheme"])

    def test_pin_malformed(self):
        self._refuse("bad_pin_malformed", ["  - malformed pin: not-a-pin"],
                     absent=("pin resolution required",))

    def test_gate_profile(self):
        self._refuse("bad_gate_profile",
                     ["  - unknown gate profile: strict"])

    def test_inline_secret_reports_unknown_key_and_secret(self):
        # A secret-shaped key is usually ALSO an unknown key — both errors.
        self._refuse("bad_inline_secret_refs",
                     ["  - unknown key: api_key",
                      "  - inline secret: api_key must be a reference, "
                      "never a literal"])

    def test_optional_section_wrong_type_refused(self):
        for section, value in (("policy", '"strict"'), ("board", "[]"),
                               ("identity", "false")):
            doc = f"{section} = {value}\n" + WS_BASE
            code, output = run_main([write_tmp(doc, "workspace.toml")])
            self.assertEqual(code, 1, (section, output))
            self.assertIn(f"  - bad type: {section} expected table, got "
                          + {"policy": "str", "board": "array",
                             "identity": "bool"}[section],
                          error_lines(output))


class TestDeferredRepoBindings(unittest.TestCase):
    """F2 regression: deferred `<…>` placeholders are allowed for EVERY
    string-typed repos[] key (id/role/url/path) in default mode — the
    frozen rule admits placeholders wherever a string is expected.
    --strict reports each with its indexed dotted path."""

    DEFERRED_WS = """\
workspace = "demo"
control_home = "dedicated"
[factory]
pin = "<kit>"
[[repos]]
id = "<repo-id>"
role = "<repo-role>"
url = "<repo-url>"
path = "<checkout-path>"
"""

    def test_deferred_bindings_pass_default_mode(self):
        path = write_tmp(self.DEFERRED_WS, "workspace.toml")
        code, output = run_main([path])
        self.assertEqual(code, 0, output)
        self.assertEqual(output, [f"OK {path}"])

    def test_deferred_bindings_strict_mode_indexed_errors(self):
        path = write_tmp(self.DEFERRED_WS, "workspace.toml")
        code, output = run_main(["--strict", path])
        self.assertEqual(code, 1, output)
        errors = error_lines(output)
        for expected in ("deferred placeholder not resolved: repos[0].id",
                         "deferred placeholder not resolved: repos[0].role",
                         "deferred placeholder not resolved: repos[0].url",
                         "deferred placeholder not resolved: repos[0].path",
                         "deferred placeholder not resolved: factory.pin"):
            self.assertIn(f"  - {expected}", errors)
        # default mode still passes
        code, output = run_main([path])
        self.assertEqual(code, 0, output)

    def test_deferred_ids_never_count_as_duplicates(self):
        doc = self.DEFERRED_WS + ('[[repos]]\nid = "<repo-id>"\n'
                                  'role = "<repo-role>"\n'
                                  'url = "<repo-url>"\n')
        code, output = run_main([write_tmp(doc, "workspace.toml")])
        self.assertEqual(code, 0, output)

    def test_non_deferred_invalid_values_still_refused(self):
        head = ('workspace = "demo"\ncontrol_home = "dedicated"\n'
                '[factory]\npin = "<kit>"\n[[repos]]\n')
        for label, doc, expected in (
                ("id", 'id = "App"\nrole = "primary"\n'
                 'url = "github.com/acme/app"\n',
                 "malformed binding: repos[0].id must be a "
                 "lowercase slug (^[a-z][a-z0-9-]*$)"),
                ("role", 'id = "app"\nrole = "secondary"\n'
                 'url = "github.com/acme/app"\n',
                 "malformed binding: repos[0].role must be "
                 "primary|member (got 'secondary')"),
                ("url", 'id = "app"\nrole = "primary"\n'
                 'url = "https://github.com/acme/app"\n',
                 "malformed binding: repos[0].url must be host/owner/name "
                 "— never a URL scheme"),
                ("path", 'id = "app"\nrole = "primary"\n'
                 'url = "github.com/acme/app"\npath = "../escape"\n',
                 "malformed binding: repos[0].path must be a "
                 "relative path with no '..'")):
            with self.subTest(field=label):
                code, output = run_main([write_tmp(head + doc,
                                                   "workspace.toml")])
                self.assertEqual(code, 1, output)
                self.assertIn(f"  - {expected}", error_lines(output))

    def test_shipped_templates_still_pass_default_mode(self):
        code, output = run_main(SHIPPED_TEMPLATES)
        self.assertEqual(code, 0, output)
        for line in output:
            self.assertTrue(line.startswith("OK "), line)


class TestEnvironmentFailures(unittest.TestCase):
    def _refuse(self, name: str, messages: list[str]) -> None:
        code, output = run_main([fixture(name)])
        self.assertEqual(code, 1, output)
        errors = error_lines(output)
        for message in messages:
            self.assertIn(message, errors)

    def test_bad_type(self):
        self._refuse("bad_type",
                     ["  - bad type: limits.runs expected int, got str"])

    def test_unknown_tier_key(self):
        self._refuse("bad_env_unknown_tier", ["  - unknown key: models.X"])

    def test_ref_grammar(self):
        self._refuse("bad_env_ref_grammar",
                     ["  - inline secret: credentials.refs must reference "
                      "env:NAME or file:path — never a literal"])

    def test_good_minimal_passes(self):
        path = fixture("good_env_minimal")
        code, output = run_main([path])
        self.assertEqual(code, 0, output)
        self.assertEqual(output, [f"OK {path}"])

    def test_unknown_key_nested(self):
        code, output = run_main([write_tmp(ENV_BASE + "[state]\nfoo = 1\n")])
        self.assertEqual(code, 1)
        self.assertIn("  - unknown key: state.foo", error_lines(output))

    def test_wall_bad_grammar(self):
        code, output = run_main(
            [write_tmp(ENV_BASE + '[limits]\nwall = "2hours"\n')])
        self.assertEqual(code, 1)
        self.assertIn('  - bad value: limits.wall must match ^\\d+[smhd]$ '
                      '(e.g. "2h")', error_lines(output))

    def test_runs_bool_refused(self):
        code, output = run_main(
            [write_tmp(ENV_BASE + "[limits]\nruns = true\n")])
        self.assertEqual(code, 1)
        self.assertIn("  - bad type: limits.runs expected int, got bool",
                      error_lines(output))

    def test_env_optional_section_wrong_type_refused(self):
        for section, value, got in (("credentials", '"plain-secret"', "str"),
                                    ("state", "false", "bool"),
                                    ("limits", "[]", "array")):
            doc = f"{section} = {value}\n" + ENV_BASE
            code, output = run_main([write_tmp(doc)])
            self.assertEqual(code, 1, (section, output))
            errors = error_lines(output)
            self.assertIn(f"  - bad type: {section} expected table, "
                          f"got {got}", errors)
            # children never examined: no phantom child errors
            self.assertFalse(any(f"{section}." in e for e in errors), errors)

    def test_env_credentials_wrong_type_bypasses_nothing(self):
        # a str credentials must NOT silently skip the refs+secret walk
        code, output = run_main(
            [write_tmp('credentials = "plain-secret"\n' + ENV_BASE)])
        self.assertEqual(code, 1)
        self.assertIn("  - bad type: credentials expected table, got str",
                      error_lines(output))

    def test_strict_deferred_array_elements(self):
        doc = ENV_BASE + '[credentials]\nrefs = ["<credential-ref>"]\n'
        path = write_tmp(doc)
        code, output = run_main(["--strict", path])
        self.assertEqual(code, 1)
        self.assertIn("  - deferred placeholder not resolved: "
                      "credentials.refs[0]", error_lines(output))
        # default (non-strict) mode still accepts the template placeholder
        code, output = run_main([path])
        self.assertEqual(code, 0, output)

    def test_strict_deferred_workspace_approvers(self):
        doc = WS_BASE + '[identity]\napprovers = ["<github-login>"]\n'
        path = write_tmp(doc, "workspace.toml")
        code, output = run_main(["--strict", path])
        self.assertEqual(code, 1)
        self.assertIn("  - deferred placeholder not resolved: "
                      "identity.approvers[0]", error_lines(output))
        code, output = run_main([path])
        self.assertEqual(code, 0, output)

    def test_strict_deferred_nested_in_arrays(self):
        doc = 'extra = [{ pin = "<kit>" }, ["<x>"]]\n' + ENV_BASE
        code, output = run_main(["--strict", write_tmp(doc)])
        self.assertEqual(code, 1)
        errors = error_lines(output)
        self.assertIn("  - deferred placeholder not resolved: "
                      "extra[0].pin", errors)
        self.assertIn("  - deferred placeholder not resolved: "
                      "extra[1][0]", errors)

    def test_two_primaries(self):
        doc = WS_BASE + ('[[repos]]\nid = "web"\nrole = "primary"\n'
                         'url = "github.com/acme/web"\n')
        code, output = run_main([write_tmp(doc, "workspace.toml")])
        self.assertEqual(code, 1)
        self.assertIn("  - malformed binding: at most one primary repo "
                      "(found 2)", error_lines(output))

    def test_empty_file(self):
        code, output = run_main([write_tmp("")])
        self.assertEqual(code, 1)
        errors = error_lines(output)
        for message in ("missing required key: runtime",
                        "missing required table: dispatch",
                        "missing required table: models"):
            self.assertIn(f"  - {message}", errors)

    def test_malformed_toml(self):
        code, output = run_main([write_tmp("not = [broken")])
        self.assertEqual(code, 1)
        self.assertTrue(any(e.startswith("  - malformed TOML: ")
                            for e in error_lines(output)), output)

    def test_unreadable_file(self):
        code, output = run_main(
            [Path(tempfile.mkdtemp(prefix="cv-")) / "nope.toml"])
        self.assertEqual(code, 1)
        self.assertTrue(any(e.startswith("  - unreadable file: ")
                            for e in error_lines(output)), output)


class TestPinResolution(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo = Path(tempfile.mkdtemp(prefix="cv-git-"))
        env = {**os.environ,
               "GIT_AUTHOR_NAME": "Kit Test",
               "GIT_AUTHOR_EMAIL": "kit@example.test",
               "GIT_COMMITTER_NAME": "Kit Test",
               "GIT_COMMITTER_EMAIL": "kit@example.test"}
        subprocess.run(["git", "init", "-q", str(cls.repo)], check=True)
        (cls.repo / "README.md").write_text("# probe\n")
        subprocess.run(["git", "-C", str(cls.repo), "add", "."],
                       check=True, env=env)
        subprocess.run(["git", "-C", str(cls.repo), "commit", "-qm", "init"],
                       check=True, env=env)
        cls.head_sha = subprocess.check_output(
            ["git", "-C", str(cls.repo), "rev-parse", "HEAD"],
            text=True).strip()
        subprocess.run(
            ["git", "-C", str(cls.repo), "tag", "v1.0.0"], check=True)

    def ws_with_pin(self, pin: str) -> Path:
        doc = WS_BASE.replace('pin = "<kit>"', f'pin = "{pin}"')
        return write_tmp(doc, "workspace.toml")

    def test_real_head_sha_resolves(self):
        code, output = run_main(
            [self.ws_with_pin(self.head_sha), "--repo", self.repo])
        self.assertEqual(code, 0, output)

    def test_tag_resolves(self):
        code, output = run_main(
            [self.ws_with_pin("v1.0.0"), "--repo", self.repo])
        self.assertEqual(code, 0, output)

    def test_absent_sha_unresolvable(self):
        code, output = run_main(
            [self.ws_with_pin("f" * 40), "--repo", self.repo])
        self.assertEqual(code, 1)
        self.assertTrue(any(e.startswith(f"  - unresolvable pin: {'f' * 40}")
                            for e in error_lines(output)), output)

    def test_concrete_pin_requires_repo(self):
        code, output = run_main([self.ws_with_pin("v1.0.0")])
        self.assertEqual(code, 1)
        self.assertIn("  - pin resolution required: no factory repo "
                      "provided (--repo)", error_lines(output))

    def test_deferred_pin_never_resolves(self):
        # --repo present but the pin is deferred: resolution is skipped,
        # and no error is produced (deferred never resolves).
        code, output = run_main(
            [self.ws_with_pin("<kit>"), "--repo", self.repo])
        self.assertEqual(code, 0, output)

    def test_committed_fixture_bad_pin_unresolvable(self):
        # The committed fixture (40 zeros) must be refused with the named
        # unresolvable line when a real --repo checkout is provided.
        path = fixture("bad_pin_unresolvable")
        code, output = run_main([path, "--repo", self.repo])
        self.assertEqual(code, 1, output)
        self.assertTrue(any(e.startswith("  - unresolvable pin: "
                                         + "0" * 40)
                            for e in error_lines(output)), output)


class TestCLI(unittest.TestCase):
    def test_no_files_is_usage_error(self):
        self.assertEqual(CV.main([]), 2)

    def test_mixed_ok_and_fail_exit_one(self):
        code, output = run_main([fixture("good_env_minimal"),
                                 fixture("bad_unknown_key")])
        self.assertEqual(code, 1)
        self.assertTrue(any(line.startswith("OK ") for line in output))
        self.assertTrue(any(line.startswith("FAIL ") for line in output))


def write_tmp_bytes(data: bytes, name: str = "env.toml") -> Path:
    path = Path(tempfile.mkdtemp(prefix="cv-")) / name
    path.write_bytes(data)
    return path


class TestInvalidUtf8(unittest.TestCase):
    """An invalid-UTF-8 config file is a NAMED malformed-TOML refusal —
    never a UnicodeDecodeError traceback out of the CLI."""

    def test_invalid_utf8_named_refusal(self):
        bad = write_tmp_bytes(b'x = "\xff"')
        code, output = run_main([bad])  # must not raise
        self.assertEqual(code, 1)
        self.assertIn("  - malformed TOML: file is not valid UTF-8",
                      error_lines(output))
        self.assertNotIn("Traceback", "\n".join(output))

    def test_invalid_utf8_does_not_block_other_files(self):
        bad = write_tmp_bytes(b'x = "\xff\xfe\x00"')
        code, output = run_main([bad, fixture("good_env_minimal")])
        self.assertEqual(code, 1)
        self.assertTrue(any(line.startswith("OK ") for line in output), output)
        self.assertTrue(any(line.startswith("FAIL ") for line in output),
                        output)
        self.assertIn("  - malformed TOML: file is not valid UTF-8",
                      error_lines(output))

    def test_valid_utf8_file_unaffected(self):
        code, output = run_main([fixture("good_env_minimal")])
        self.assertEqual(code, 0)
        self.assertTrue(any(line.startswith("OK ") for line in output), output)


class TestStrictEnforcedInDirectCallers(unittest.TestCase):
    """N5: validate_workspace / validate_environment enforce the deferred-
    placeholder walk themselves when strict=True — direct callers (not just
    validate_document / the CLI) must refuse templates; and validate_document
    must NOT double-report (the walk runs exactly once per path)."""

    @staticmethod
    def parse(path: Path) -> dict:
        import tomllib
        with open(path, "rb") as fh:
            return tomllib.load(fh)

    def test_validate_workspace_strict_refuses_template(self):
        doc = self.parse(WS_TEMPLATE)
        errs = CV.validate_workspace(doc, strict=True)
        self.assertIn("deferred placeholder not resolved: factory.pin", errs)
        self.assertIn("deferred placeholder not resolved: board.project_id",
                      errs)

    def test_validate_environment_strict_refuses_template(self):
        for template in ENV_TEMPLATES:
            errs = CV.validate_environment(self.parse(template), strict=True)
            self.assertIn("deferred placeholder not resolved: models.T1",
                          errs, template)

    def test_non_strict_still_clean_for_templates(self):
        self.assertEqual(CV.validate_workspace(self.parse(WS_TEMPLATE)), [])
        for template in ENV_TEMPLATES:
            self.assertEqual(
                CV.validate_environment(self.parse(template)), [], template)

    def test_validate_document_no_duplicate_deferred_lines(self):
        # The walk now lives in the per-kind validators; validate_document
        # must report each deferred path EXACTLY once.
        ws = self.parse(WS_TEMPLATE)
        errs = CV.validate_document(ws, "workspace", strict=True)
        deferred = [e for e in errs
                    if e.startswith("deferred placeholder not resolved:")]
        self.assertTrue(deferred)
        self.assertEqual(len(deferred), len(set(deferred)))
        self.assertIn("deferred placeholder not resolved: factory.pin", errs)
        for template in ENV_TEMPLATES:
            errs = CV.validate_document(self.parse(template), "environment",
                                        strict=True)
            deferred = [e for e in errs
                        if e.startswith("deferred placeholder not resolved:")]
            self.assertEqual(len(deferred), len(set(deferred)), template)

    def test_document_strict_matches_direct_validator_plus_secrets(self):
        # validate_document(strict=True) == direct validator(strict=True)
        # + secret walk, sorted — proving the deferred walk runs once.
        doc = self.parse(WS_TEMPLATE)
        expected = CV.validate_workspace(doc, strict=True)
        secret_errs: list[str] = []
        CV._walk_secret_keys(doc, "", secret_errs)
        expected = sorted(expected + secret_errs)
        self.assertEqual(
            CV.validate_document(doc, "workspace", strict=True), expected)


if __name__ == "__main__":
    unittest.main()
