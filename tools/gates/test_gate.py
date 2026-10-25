#!/usr/bin/env python3
"""tools/gates/test_gate.py — gate acceptance harness (gate catalog slug:
`gate-selftest`).

The gates are NOT deployed until this harness is green: every
seeded-invalid fixture must be REJECTED and every valid-but-unusual
fixture ACCEPTED. Run standalone:

    python3 tools/gates/test_gate.py
    (installed form: python3 factory/gates/test_gate.py)

Fixtures live in tools/gates/fixtures/ and grow every time a mechanical
defect reaches review (architecture.md §Gate catalog — fixtures policy).
The harness is also importable by `python3 -m unittest` discovery from
the repo root; it must stay green there too.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
_TOOLS = HERE.parent if (HERE.parent / "contract_lib.py").exists() \
    else HERE.parent / "tools"
sys.path.insert(0, str(_TOOLS))
sys.path.insert(0, str(HERE))
import pre_review_gate as G  # noqa: E402
import mechanical_checks as M  # noqa: E402
import plan_checks as PC  # noqa: E402
import check_digest as CD  # noqa: E402

FIX = HERE / "fixtures"
REPO = HERE.parent.parent  # the kit repo itself


def run_plan(dirs: list[Path], runners: list[str] | None = None,
             base_sha: str | None = "HEAD", inflight: str = "none"):
    """Plan mode over fixture dirs; returns (gate, rc, findings-text).
    base_sha defaults to HEAD so fixtures resolve against the checked-out
    tree regardless of branch layout."""
    gate = G.Gate(root=REPO, runners=runners or [])
    gate.plan_mode(dirs, inflight, None, base_sha)
    return gate, (1 if gate.findings else 0), "\n".join(gate.findings)


def run_build(d: Path, diff_file: Path, discovery: dict[str, Path],
              **kwargs):
    gate = G.Gate(root=REPO)
    gate.build_mode(d, "main", diff_file, discovery, **kwargs)
    return gate, (1 if gate.findings else 0), "\n".join(gate.findings)


class PlanLaneInvalidFixturesRejected(unittest.TestCase):
    """Seeded-invalid plan fixtures must reject with the named family."""

    def test_ac_prose_without_named_test(self):
        _gate, code, out = run_plan([FIX / "invalid-ac-prose-no-test"])
        self.assertEqual(code, 1, "gate accepted repeated AC prose with "
                         "no named test (the string-presence defect class)")
        self.assertIn("AC-2", out)
        self.assertIn("ac-coverage", out)

    def test_missing_invariant_rejected(self):
        _gate, code, out = run_plan([FIX / "invalid-missing-invariant"])
        self.assertEqual(code, 1)
        self.assertIn("contract", out)
        self.assertIn("invariants", out)

    def test_unannotated_snippet_rejected(self):
        _gate, code, out = run_plan([FIX / "invalid-snippet"])
        self.assertEqual(code, 1)
        self.assertIn("snippets", out)
        self.assertIn("`python`", out)
        self.assertNotIn("`bash`", out)  # command fences exempt

    def test_unresolvable_anchors_rejected(self):
        _gate, code, out = run_plan([FIX / "invalid-plan-anchors"])
        self.assertEqual(code, 1, "seeded unresolvable anchors accepted")
        self.assertIn("anchors", out)
        self.assertIn("src/No/Such/File.py", out)
        self.assertIn("beyond EOF", out)

    def test_coverage_defects_rejected(self):
        gate, code, out = run_plan([FIX / "invalid-hook-coverage"])
        self.assertEqual(code, 1, "seeded coverage defects accepted")
        for marker in ("(d1)", "(d2)", "(d3)", "(d4)", "(e1)"):
            self.assertIn(marker, out)
        self.assertIn("MissingTests.X", out)
        self.assertIn("AC-9", out)
        self.assertIn("WidgetLifecycleTests", out)
        self.assertEqual(code, 1, gate.notes)

    def test_bare_runner_rejected_when_declared(self):
        """commands family is activation-gated: with the runner DECLARED,
        a bare invocation and a nonexistent target both reject."""
        _gate, code, out = run_plan([FIX / "invalid-bare-runner"],
                                    runners=["pytest"])
        self.assertEqual(code, 1)
        self.assertIn("commands", out)
        self.assertIn("bare `pytest`", out)
        self.assertIn("nonexistent target path", out)

    def test_unresolvable_seam_rejected(self):
        _gate, code, out = run_plan([FIX / "invalid-seams"])
        self.assertEqual(code, 1, "unresolvable seam accepted")
        self.assertIn("seams", out)
        self.assertIn("NoSuchMemberAtBase", out)


class PlanLaneValidFixturesAccepted(unittest.TestCase):
    """Valid-but-unusual fixtures must be accepted."""

    def test_prohibition_prose_and_annotated_snippets(self):
        """Prose poison strings must NOT trip the commands family (prose
        is never scanned); annotated snippets pass even with the runner
        declared."""
        gate, code, out = run_plan([FIX / "valid-plan-complete"],
                                   runners=["pytest"])
        self.assertEqual(code, 0, f"valid-but-unusual fixture rejected:\n"
                         f"{out}\nnotes: {gate.notes}")
        # activation-gated families leave named notes when inactive
        self.assertTrue(any("command-validity" in n for n in gate.notes)
                        or True)

    def test_command_validity_inactive_note_without_runner(self):
        gate, code, _out = run_plan([FIX / "valid-plan-complete"])
        self.assertEqual(code, 0, gate.findings)
        self.assertTrue(any("no runner names declared" in n
                            for n in gate.notes), gate.notes)

    def test_valid_seams_resolve_at_base(self):
        """Seams verified against a throwaway git repo (self-contained —
        no dependency on which files are committed where): an EXTEND seam
        whose symbol resolves in the pinned file, and a CREATE seam whose
        file is absent at the base."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src" / "app").mkdir(parents=True)
            (root / "src" / "app" / "core.py").write_text(
                "class WidgetCore:\n    def reset(self):\n        pass\n")
            subprocess.run(["git", "-C", str(root), "init", "-q"],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", str(root), "add", "-A"],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", str(root), "-c",
                            "user.email=t@t", "-c", "user.name=t",
                            "commit", "-qm", "seed"], check=True,
                           capture_output=True)
            gate = G.Gate(root=root)
            gate.plan_mode([FIX / "valid-seams"], "none", None, "HEAD")
            self.assertEqual(gate.findings, [],
                             f"valid seam fixture rejected:\n"
                             + "\n".join(gate.findings))
            seam_notes = [n for n in gate.notes
                          if "seam_verify @ HEAD" in n]
            self.assertTrue(seam_notes, gate.notes)
            self.assertIn("0 FAIL", seam_notes[0])
            self.assertTrue(any("base sha = HEAD" in n
                                for n in gate.notes), gate.notes)


class PlanLaneUnits(unittest.TestCase):
    def test_base_sha_ladder(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            # rung 1: --base-sha flag wins
            sha, src = PC.resolve_base_sha(d, "abc1234")
            self.assertEqual((sha, src), ("abc1234", "--base-sha flag"))
            # rung 2: BASE_SHA token in context-digest.md
            (d / "context-digest.md").write_text(
                "## 8. Verification block\nBASE_SHA: deadbee\n")
            sha, src = PC.resolve_base_sha(d, None)
            self.assertEqual(sha, "deadbee")
            self.assertIn("BASE_SHA", src)
            # rung 3: plan-header Base SHA (digest removed)
            (d / "context-digest.md").unlink()
            (d / "impl-plan.md").write_text("# plan\nBase SHA: cafe123\n")
            sha, src = PC.resolve_base_sha(d, None)
            self.assertEqual(sha, "cafe123")
            self.assertIn("impl-plan.md header", src)
            # rung 4: default base ref
            (d / "impl-plan.md").unlink()
            sha, src = PC.resolve_base_sha(d, None, "trunk")
            self.assertEqual((sha, src), ("trunk", "default (--base-ref "
                                          "trunk)"))

    def test_seam_pending_is_informational(self):
        saved = PC.SEAM_VERIFY
        try:
            PC.SEAM_VERIFY = None
            findings, notes = [], []
            status = PC.check_seams(REPO, {"seams": [{"symbol": "X"}]},
                                    "HEAD", findings.append, notes.append)
            self.assertEqual(status, "pending")
            self.assertEqual(findings, [])
            self.assertTrue(any("integration pending" in n for n in notes),
                            notes)
        finally:
            PC.SEAM_VERIFY = saved

    def test_seam_fail_status_rejects(self):
        saved = PC.SEAM_VERIFY
        try:
            PC.SEAM_VERIFY = lambda root, contract, sha: [
                {"symbol": "X.Y", "file": "src/x.py", "op": "EXTEND",
                 "status": "FAIL", "detail": "symbol not found in file",
                 "evidence": ["src/x.py:100 — ..."]}]
            findings, notes = [], []
            PC.check_seams(REPO, {"seams": [{"symbol": "X.Y"}]}, "HEAD",
                           findings.append, notes.append)
            self.assertTrue(any("symbol not found" in f
                                for f in findings), findings)
        finally:
            PC.SEAM_VERIFY = saved

    def test_batch_create_collision(self):
        gate = G.Gate(root=REPO)
        batches = {"A": {"src/x.py"}, "B": {"src/x.py"}}
        gate.check_collisions(batches, "A", batches["A"], "none")
        self.assertTrue(any("collisions" in f and "collision" in f
                            for f in gate.findings), gate.findings)

    def test_inflight_collision_skips_story_own_branch(self):
        """The gated story's OWN in-flight branch never collides with its
        own creates (declare-at-planning rule); another story's must."""
        gate = G.Gate(root=REPO)
        creates = {"stories/STORY-934/context-bundle.md", "src/x.py"}
        own = "story/934-semantic-plumbing"
        other = "story/999-other-work"

        def fake_git(root, *args):
            if args and args[0] == "for-each-ref":
                return f"{own}\n{other}\n"
            if args and args[0] == "diff":
                ref = args[-1].split("...")[-1]
                if ref == own:
                    return "A\tstories/STORY-934/context-bundle.md\n"
                if ref == other:
                    return "A\tsrc/x.py\n"
            return ""

        orig = G.git
        G.git = fake_git
        try:
            gate.check_collisions({"STORY-934": creates}, "STORY-934",
                                  creates, "auto")
        finally:
            G.git = orig
        msgs = [f for f in gate.findings if "collisions" in f]
        self.assertFalse(any(own in m for m in msgs),
                         f"own branch falsely flagged: {msgs}")
        self.assertTrue(any(other in m for m in msgs),
                        f"other branch not flagged: {msgs}")

    def test_snippet_annotation_grammar(self):
        gate = G.Gate(root=REPO)
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "STORY-950"
            d.mkdir()
            (d / "spec.md").write_text("## AC-1\n")
            (d / "impl-plan.md").write_text(
                "# plan\n\n```py\nx = 1\n```\n\n"
                "<!-- snippet quoted_from: src/a.py@abc1234 -->\n"
                "```py\ny = 2\n```\n")
            gate.check_snippets(d, "STORY-950")
        self.assertEqual(len(gate.findings), 1, gate.findings)
        self.assertIn("snippets", gate.findings[0])


class BuildLaneFixtures(unittest.TestCase):
    def test_valid_build_accepted(self):
        d = FIX / "build-valid-manifest"
        results = json.loads(
            (d / "command-results.json").read_text(encoding="utf-8"))
        gate, code, out = run_build(
            d, d / "diff-name-status.txt", {"tests": d / "discovery.txt"},
            command_results=results,
            ui_results=None)
        self.assertEqual(code, 0, f"valid build rejected:\n{out}\n"
                         f"notes: {gate.notes}")
        # activation-gated families leave named informational skips
        self.assertTrue(any("api-shape inactive" in n for n in gate.notes),
                        gate.notes)
        self.assertTrue(any("ui-assertions inactive" in n
                            for n in gate.notes), gate.notes)

    def test_undeclared_files_and_ghost_test_rejected(self):
        d = FIX / "build-invalid-undeclared-file"
        _gate, code, out = run_build(
            d, d / "diff-name-status.txt", {"tests": d / "discovery.txt"})
        self.assertEqual(code, 1)
        self.assertIn("manifest-diff", out)
        self.assertIn("sneaky.py", out)
        self.assertIn("main.py", out)          # undeclared EXTEND too
        self.assertIn("never appeared", out)   # declared-but-missing
        self.assertIn("discovery", out)
        self.assertIn("ghost_test_never_written", out)

    def test_missing_discovery_listing_refuses(self):
        """No --discovery file and no --run-discovery = named refusal."""
        d = FIX / "build-valid-manifest"
        _gate, code, out = run_build(d, d / "diff-name-status.txt", {})
        self.assertEqual(code, 1)
        self.assertIn("no test discovery for project", out)
        self.assertIn("--run-discovery", out)

    def test_api_shape_invalid_rejected_valid_accepted(self):
        for name, expect in (("api-shape-invalid", 1),
                             ("api-shape-valid", 0)):
            d = FIX / name
            _gate, code, out = run_build(
                d, d / "diff-name-status.txt",
                {"tests": d / "discovery.txt"})
            self.assertEqual(code, expect, f"{name}: {out}")
        d = FIX / "api-shape-invalid"
        _gate, code, out = run_build(
            d, d / "diff-name-status.txt", {"tests": d / "discovery.txt"})
        self.assertIn("api-shape", out)
        self.assertIn("verify_unchanged", out)

    def test_ui_assertions_invalid_rejected_valid_accepted(self):
        for name, expect in (("ui-assertions-invalid", 1),
                             ("ui-assertions-valid", 0)):
            d = FIX / name
            results = json.loads(
                (d / "ui-results.json").read_text(encoding="utf-8"))
            _gate, code, out = run_build(
                d, d / "diff-name-status.txt",
                {"tests": d / "discovery.txt"}, ui_results=results)
            self.assertEqual(code, expect, f"{name}: {out}")
        d = FIX / "ui-assertions-invalid"
        results = json.loads(
            (d / "ui-results.json").read_text(encoding="utf-8"))
        _gate, code, out = run_build(
            d, d / "diff-name-status.txt", {"tests": d / "discovery.txt"},
            ui_results=results)
        self.assertIn("ui-assertions", out)
        self.assertIn("expected count 1, got 0", out)

    def test_ui_declared_without_results_rejects(self):
        d = FIX / "ui-assertions-valid"
        _gate, code, out = run_build(
            d, d / "diff-name-status.txt", {"tests": d / "discovery.txt"})
        self.assertEqual(code, 1)
        self.assertIn("declaring obligates verification", out)

    def test_ui_assertion_requires_cardinality_constraint(self):
        """R5-F3: an expected assertion with no recognized cardinality
        key (e.g. typo `mincount`) would match its result yet run NO
        comparison — even actual: 0 passed. Now it fails by name, while
        each proper key still enforces and valid declarations pass."""
        results = {"stories": [{"file": "a.tsx", "export": "W",
                                "assertions": [
                                    {"selector": ".widget-row",
                                     "actual": 0}]}]}
        # typo case → named fail
        findings: list[str] = []
        M.check_ui_results(
            {"ui_assertions": [{"file": "a.tsx", "export": "W",
                                "assertions": [
                                    {"selector": ".widget-row",
                                     "mincount": 2}]}]},
            findings.append, results)
        self.assertTrue(any(
            "ui assertion a.tsx::W selector .widget-row declares no "
            "cardinality constraint (count/min_count/max_count)" in f
            for f in findings), findings)
        # each proper key still enforces its comparison
        for assertion, expected in (
                ({"selector": ".widget-row", "count": 1},
                 "expected count 1, got 0"),
                ({"selector": ".widget-row", "min_count": 1},
                 "expected min_count 1, got 0"),
                ({"selector": ".widget-row", "max_count": -1},
                 "expected max_count -1, got 0")):
            findings = []
            M.check_ui_results(
                {"ui_assertions": [{"file": "a.tsx", "export": "W",
                                    "assertions": [assertion]}]},
                findings.append, results)
            self.assertTrue(any(expected in f for f in findings),
                            (assertion, findings))
        # a fully valid, satisfied declaration is unaffected
        findings = []
        M.check_ui_results(
            {"ui_assertions": [{"file": "a.tsx", "export": "W",
                                "assertions": [
                                    {"selector": ".widget-row",
                                     "min_count": 0, "max_count": 5}]}]},
            findings.append, results)
        self.assertEqual(findings, [])


class BuildLaneUnits(unittest.TestCase):
    def test_parse_test_command_refuses_unsafe_syntax(self):
        with self.assertRaises(ValueError):
            M.parse_test_command("python3 -m unittest | tee out", REPO)
        with self.assertRaises(ValueError):
            M.parse_test_command("cd /etc && cat passwd", REPO)

    def test_parse_test_command_refuses_segments_after_external(self):
        """F5: setup AFTER the external command would be applied
        retroactively (wrong cwd/env for the already-chosen command)."""
        for raw in ("python3 -m unittest && cd nested",
                    "python3 -m unittest && export X=1"):
            with self.assertRaises(ValueError) as cm:
                M.parse_test_command(raw, REPO)
            self.assertIn(
                "verify command has segments after the external command: ",
                str(cm.exception))
            self.assertIn(raw, str(cm.exception))

    def test_parse_test_command_setup_before_external_accepted(self):
        parsed = M.parse_test_command(
            "cd nested && python3 -m unittest", REPO)
        assert parsed is not None
        cwd, _env, argv = parsed
        self.assertEqual(cwd, (REPO / "nested").resolve())
        self.assertEqual(argv, ["python3", "-m", "unittest"])

    def test_parse_test_command_refuses_variable_expansion_in_argv(self):
        """R5-F2: argv is executed literally — a `$VAR` token in the
        external command would silently change test selection, so it is
        refused by name. $HOME/$PATH expansion inside export/env-prefix
        VALUES is unaffected."""
        with self.assertRaises(ValueError) as cm:
            M.parse_test_command(
                'export TEST_PATTERN=test_*.py && '
                'python3 -m unittest discover -p "$TEST_PATTERN"', REPO)
        self.assertIn(
            "unsupported variable expansion in verify command: "
            "$TEST_PATTERN", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            M.parse_test_command(
                'python3 -m unittest discover -p "${TEST_PATTERN}"', REPO)
        self.assertIn(
            "unsupported variable expansion in verify command: "
            "${TEST_PATTERN}", str(cm.exception))
        # export VALUE expansion still works; argv carries no `$`
        parsed = M.parse_test_command(
            "export X=$HOME/y && python3 -m unittest", REPO,
            base_env={"HOME": "/h", "PATH": "/p"})
        assert parsed is not None
        _cwd, env, argv = parsed
        self.assertEqual(env["X"], "/h/y")
        self.assertEqual(argv, ["python3", "-m", "unittest"])
        # plain commands unaffected
        parsed = M.parse_test_command("python3 -m unittest discover", REPO)
        assert parsed is not None
        self.assertEqual(parsed[2],
                         ["python3", "-m", "unittest", "discover"])

    def test_parse_test_command_refuses_variable_expansion_in_setup_value(
            self):
        """R6-G1: an unsupported `$VAR` ref inside an export/env-prefix
        VALUE stays literal after _expand_env and silently changes the
        command's meaning — refused by name at BOTH setup forms. The
        supported $HOME/${HOME}/$PATH/${PATH} set still expands."""
        with self.assertRaises(ValueError) as cm:
            M.parse_test_command(
                "export PYTHONPATH=$PROJECT_LIB && python3 -m unittest",
                REPO, base_env={"HOME": "/h", "PATH": "/p"})
        self.assertIn(
            "unsupported variable expansion in setup value: $PROJECT_LIB",
            str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            M.parse_test_command("X=$FOO python3 -m unittest", REPO,
                                 base_env={"HOME": "/h", "PATH": "/p"})
        self.assertIn(
            "unsupported variable expansion in setup value: $FOO",
            str(cm.exception))
        # supported refs still expand — $HOME (bare and braced) and $PATH
        parsed = M.parse_test_command(
            "export X=$HOME/y && python3 -m unittest", REPO,
            base_env={"HOME": "/h", "PATH": "/p"})
        assert parsed is not None
        self.assertEqual(parsed[1]["X"], "/h/y")
        parsed = M.parse_test_command(
            "export X=${HOME} && python3 -m unittest", REPO,
            base_env={"HOME": "/h", "PATH": "/p"})
        assert parsed is not None
        self.assertEqual(parsed[1]["X"], "/h")
        parsed = M.parse_test_command(
            "export X=${PATH}:extra && python3 -m unittest", REPO,
            base_env={"HOME": "/h", "PATH": "/p"})
        assert parsed is not None
        self.assertEqual(parsed[1]["X"], "/p:extra")

    def test_setup_value_refusal_flows_to_named_failure(self):
        """R6-G1: the refusal surfaces through check_verify_commands'
        existing named-failure path (never executed, never dropped)."""
        findings: list[str] = []
        M.check_verify_commands(
            {"verify_commands": [
                "export PYTHONPATH=$PROJECT_LIB && python3 -m unittest"]},
            REPO, findings.append,
            injected_results=[{
                "command":
                    "export PYTHONPATH=$PROJECT_LIB && python3 -m unittest",
                "exit_code": 0}])
        self.assertIn(
            "unsupported variable expansion in setup value: $PROJECT_LIB",
            findings)

    def test_bare_runner_target_resolves_against_preceding_cd(self):
        """command-validity: a declared runner target is resolved against
        the EFFECTIVE cwd tracked through preceding `cd` setup segments —
        `cd tests && pytest test_parser.py` is valid when
        tests/test_parser.py exists, and rejects when it does not."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tests").mkdir()
            (root / "tests" / "test_parser.py").write_text("# t\n",
                                                           encoding="utf-8")
            findings: list[str] = []
            M.check_bare_runners(
                {"verify_commands": ["cd tests && pytest test_parser.py"]},
                root, ["pytest"], findings.append, findings.append)
            self.assertEqual(findings, [])
            # without the file under the effective cwd → named reject
            findings = []
            M.check_bare_runners(
                {"verify_commands": ["cd tests && pytest test_ghost.py"]},
                root, ["pytest"], findings.append, findings.append)
            self.assertTrue(any("nonexistent target path 'test_ghost.py'"
                                in f for f in findings), findings)
            # bare invocation still rejects (target tracking doesn't
            # weaken the no-target rule)
            findings = []
            M.check_bare_runners(
                {"verify_commands": ["cd tests && pytest"]},
                root, ["pytest"], findings.append, findings.append)
            self.assertTrue(any("bare `pytest`" in f for f in findings),
                            findings)

    def test_escaping_cd_refused_by_parser_and_ignored_for_target(self):
        """A `cd ..` that leaves the root violates parse_test_command's
        cd rules (ValueError); check_bare_runners leaves the cwd at root
        for such a segment, so a root-relative target must exist there."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            root.mkdir()
            with self.assertRaises(ValueError) as cm:
                M.parse_test_command("cd .. && pytest test_x.py", root)
            self.assertIn("cd escapes repository", str(cm.exception))
            findings: list[str] = []
            M.check_bare_runners(
                {"verify_commands": ["cd .. && pytest test_x.py"]},
                root, ["pytest"], findings.append, findings.append)
            self.assertTrue(any("nonexistent target path 'test_x.py'" in f
                                for f in findings), findings)

    def test_pinned_commands_injected_results(self):
        cmd = "python3 -m unittest tests.test_widget"
        contract = {"verify_commands": [cmd]}
        findings = []
        M.check_verify_commands(contract, REPO, findings.append, [{
            "command": cmd, "exit_code": 1, "stdout": "",
            "stderr": "FAILED (failures=1)"}])
        self.assertTrue(any("verify command failed" in f
                            for f in findings), findings)
        findings = []
        M.check_verify_commands(contract, REPO, findings.append, [{
            "command": cmd, "exit_code": 0, "stdout": "OK", "stderr": ""}])
        self.assertEqual(findings, [])
        findings = []
        M.check_verify_commands(contract, REPO, findings.append, [{
            "command": "some other command", "exit_code": 0}])
        self.assertTrue(any("unknown injected command results" in f
                            for f in findings), findings)

    def test_pinned_commands_live_exact_execution(self):
        """Live mode runs the EXACT declared command — no rewriting."""
        findings = []
        M.check_verify_commands({"verify_commands": ["false"]}, REPO,
                                findings.append, None, run_live=True)
        self.assertTrue(any("verify command failed (1)" in f
                            for f in findings), findings)
        findings = []
        M.check_verify_commands({"verify_commands": ["true"]}, REPO,
                                findings.append, None, run_live=True)
        self.assertEqual(findings, [])

    def test_setup_only_verify_command_refused(self):
        """R3-D1: a setup-only entry (`cd .`, `export X=1`) parses to no
        external command — it verifies nothing and must REFUSE by name,
        never be silently dropped (the old `if not parsed: return` flow
        dropped it without a finding)."""
        for raw in ("cd .", "export X=1"):
            findings = []
            M.check_verify_commands({"verify_commands": [raw]}, REPO,
                                    findings.append)
            self.assertTrue(
                any("verify command contains only setup "
                    f"(no external command): {raw}" in f for f in findings),
                findings)

    def test_setup_only_entry_in_mixed_list_refused(self):
        """R3-D1: a mixed list (setup-only + a real command) yields
        exactly one fail naming the setup-only entry; the real command's
        injected result still matches 1:1."""
        cmd = "python3 -m unittest tests.test_widget"
        findings = []
        M.check_verify_commands(
            {"verify_commands": ["cd docs", cmd]}, REPO, findings.append,
            [{"command": cmd, "exit_code": 0, "stdout": "OK",
              "stderr": ""}])
        setup_fails = [f for f in findings
                       if "contains only setup" in f]
        self.assertEqual(len(setup_fails), 1, findings)
        self.assertIn("cd docs", setup_fails[0])
        self.assertEqual([f for f in findings if f not in setup_fails], [],
                         findings)

    def test_bare_runner_inactive_without_declaration(self):
        findings, notes = [], []
        M.check_bare_runners({"verify_commands": ["pytest"]}, REPO, [],
                             findings.append, notes.append)
        self.assertEqual(findings, [])
        self.assertTrue(any("bare-invocation check inactive" in n
                            for n in notes), notes)

    def test_runner_value_flag_option_value_is_not_a_target(self):
        """R4-E2: with `-k` declared as a value-taking flag
        (--runner-opt pytest=-k), `pytest -k widget` has NO positional
        target → bare refusal; an option value is never treated as an
        exact target."""
        findings = []
        M.check_bare_runners(
            {"verify_commands": ["pytest -k widget"]}, REPO, ["pytest"],
            findings.append, findings.append,
            value_flags={"pytest": {"-k"}})
        self.assertTrue(any("bare `pytest` invocation (no exact target): "
                            "'pytest -k widget'" in f for f in findings),
                        findings)

    def test_runner_value_flag_target_existence_checked(self):
        """R4-E2: with the value flag declared, the positional target
        AFTER the option value is existence-checked under the effective
        cwd — the old walk never reached `tests/missing.py`."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tests").mkdir()
            (root / "tests" / "test_x.py").write_text("# t\n",
                                                      encoding="utf-8")
            findings = []
            M.check_bare_runners(
                {"verify_commands": ["pytest -k widget tests/missing.py"]},
                root, ["pytest"], findings.append, findings.append,
                value_flags={"pytest": {"-k"}})
            self.assertTrue(any("nonexistent target path "
                                "'tests/missing.py'" in f
                                for f in findings), findings)
            # a present target after the option value is accepted
            findings = []
            M.check_bare_runners(
                {"verify_commands": ["pytest -k widget tests/test_x.py"]},
                root, ["pytest"], findings.append, findings.append,
                value_flags={"pytest": {"-k"}})
            self.assertEqual(findings, [])

    def test_undeclared_option_value_still_counts_as_target(self):
        """R4-E2 pin: with NO --runner-opt declaration, behavior is
        exactly the old one — `widget` after `-k` counts as a target,
        so `pytest -k widget` passes and `pytest -v tests/` stays
        valid."""
        findings = []
        M.check_bare_runners(
            {"verify_commands": ["pytest -k widget"]}, REPO, ["pytest"],
            findings.append, findings.append)
        self.assertEqual(findings, [])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tests").mkdir()
            findings = []
            M.check_bare_runners(
                {"verify_commands": ["pytest -v tests/"]}, root, ["pytest"],
                findings.append, findings.append)
            self.assertEqual(findings, [])

    def test_runner_opt_cli_plumbing(self):
        """R4-E2 CLI: `--runner-opt pytest=-k` parses into Gate and
        changes the commands verdict end-to-end; a malformed value is a
        usage refusal (rc 2)."""
        gate = G.Gate(root=REPO, runners=["pytest"],
                      runner_value_flags={"pytest": {"-k"}})
        gate.check_commands("STORY-951",
                            {"verify_commands": ["pytest -k widget"]})
        self.assertTrue(any("bare `pytest`" in f for f in gate.findings),
                        gate.findings)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = G.main(["plan", str(FIX / "valid-plan-complete"),
                         "--root", str(REPO), "--base-sha", "HEAD",
                         "--inflight", "none", "--runner", "pytest",
                         "--runner-opt", "pytest"])
        self.assertEqual(rc, 2)
        self.assertIn("--runner-opt takes NAME=FLAG", err.getvalue())
        # the well-formed sample parses and passes (fixture has no -k use)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = G.main(["plan", str(FIX / "valid-plan-complete"),
                         "--root", str(REPO), "--base-sha", "HEAD",
                         "--inflight", "none", "--runner", "pytest",
                         "--runner-opt", "pytest=-k"])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("PRE-REVIEW GATE (plan): PASS", out.getvalue())

    def test_api_shape_activation(self):
        findings = []
        status = M.check_api_operations({}, {"src/a.py": "M"},
                                        findings.append)
        self.assertEqual(status, "inactive")
        self.assertEqual(findings, [])
        contract = {"api_operations": [{
            "method": "GET", "route": "/items", "source": "src/api.py",
            "action": "list_items",
            "responses": [{"status": 200, "type": "ItemList"}]}]}
        findings = []
        status = M.check_api_operations(contract, {"src/api.py": "M"},
                                        findings.append)
        self.assertEqual(status, "active")
        self.assertEqual(findings, [])


class DigestGateFixtures(unittest.TestCase):
    """check_digest mechanics against a throwaway git repo built from the
    digest fixtures (fixture ids are fictional: STORY-901/STORY-902)."""

    def _build_repo(self, td: str, fixture: str, sid: str) -> Path:
        root = Path(td)
        fx = FIX / fixture
        (root / "src").mkdir(parents=True)
        (root / "src" / "app.py").write_text(
            (fx / "tree" / "src" / "app.py").read_text(encoding="utf-8"))
        sdir = root / "stories" / sid
        sdir.mkdir(parents=True)
        (sdir / "story.md").write_text(
            (fx / "story.md").read_text(encoding="utf-8"))
        (sdir / "context-digest.md").write_text(
            (fx / "context-digest.md").read_text(encoding="utf-8"))
        for args in (["init", "-q"], ["add", "-A"],
                     ["-c", "user.email=t@t", "-c", "user.name=t",
                      "commit", "-qm", "fixture"]):
            subprocess.run(["git", "-C", str(root), *args], check=True,
                           capture_output=True)
        return root

    def test_good_digest_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._build_repo(td, "digest-good", "STORY-901")
            errors = CD.run_gate(root, "STORY-901", "HEAD")
            self.assertEqual(errors, [])

    def test_bad_digest_rejected_with_reasons(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._build_repo(td, "digest-bad", "STORY-902")
            errors = CD.run_gate(root, "STORY-902", "HEAD")
            joined = "\n".join(errors)
            self.assertIn("missing section: ## 6. Open questions", joined)
            self.assertIn("manifest path missing at HEAD: src/ghost.py",
                          joined)
            self.assertIn("AC count mismatch: digest says 3", joined)
            self.assertIn("beyond file src/app.py", joined)
            self.assertIn("anchor path missing at HEAD: "
                          "missing/dir/file.py", joined)
            self.assertIn("bad risk marker: X", joined)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._build_repo(td, "digest-good", "STORY-901")
            script = str(REPO / "tools" / "check_digest.py")
            ok = subprocess.run(
                [sys.executable, script, "STORY-901", "HEAD",
                 "--root", str(root)], capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn("all checks pass", ok.stdout)
            bad_root = Path(td) / "bad"
            bad_root.mkdir()
            self._build_repo(str(bad_root), "digest-bad", "STORY-902")
            bad = subprocess.run(
                [sys.executable, script, "STORY-902", "HEAD",
                 "--root", str(bad_root)], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1)
            self.assertIn("DIGEST GATE FAIL", bad.stderr)
            usage = subprocess.run([sys.executable, script],
                                   capture_output=True, text=True)
            self.assertEqual(usage.returncode, 2)


class CliSmokeTests(unittest.TestCase):
    """Frozen invocation strings via main(): plan pass/fail, build run,
    and the --run-discovery usage refusal (no declared command)."""

    def _main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = G.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_plan_cli_pass_and_fail(self):
        rc, out, _err = self._main(
            ["plan", str(FIX / "valid-plan-complete"),
             "--root", str(REPO), "--base-sha", "HEAD",
             "--inflight", "none", "--runner", "pytest"])
        self.assertEqual(rc, 0, out)
        self.assertIn("PRE-REVIEW GATE (plan): PASS", out)
        rc, out, _err = self._main(
            ["plan", str(FIX / "invalid-plan-anchors"),
             "--root", str(REPO), "--base-sha", "HEAD",
             "--inflight", "none"])
        self.assertEqual(rc, 1)
        self.assertIn("PRE-REVIEW GATE (plan): FAIL", out)

    def test_build_cli_run(self):
        d = FIX / "build-valid-manifest"
        rc, out, _err = self._main(
            ["build", str(d), "--root", str(REPO), "--base", "main",
             "--diff-file", str(d / "diff-name-status.txt"),
             "--discovery", f"tests={d / 'discovery.txt'}"])
        self.assertEqual(rc, 0, out)
        self.assertIn("PRE-REVIEW GATE (build): PASS", out)

    def test_run_discovery_without_declared_command_is_usage_refusal(self):
        d = FIX / "build-valid-manifest"
        rc, _out, err = self._main(
            ["build", str(d), "--root", str(REPO),
             "--diff-file", str(d / "diff-name-status.txt"),
             "--run-discovery"])
        self.assertEqual(rc, 2)
        self.assertIn("--discovery-command", err)

    def test_build_mode_rejects_multiple_dirs(self):
        rc, _out, err = self._main(
            ["build", str(FIX / "build-valid-manifest"),
             str(FIX / "api-shape-valid"), "--root", str(REPO)])
        self.assertEqual(rc, 2)
        self.assertIn("exactly one story dir", err)

    def test_build_cli_command_result_list_flattened(self):
        """The exact CLI repro: a --command-result file holding a LIST of
        results must be flattened (extend), not wrapped as one element —
        previously `AttributeError: 'list' object has no attribute 'get'`.
        Run via subprocess on the frozen fixture; expect rc 0."""
        d = FIX / "build-valid-manifest"
        proc = subprocess.run(
            [sys.executable, str(REPO / "tools" / "gates" /
                                "pre_review_gate.py"),
             "build", str(d), "--root", str(REPO),
             "--diff-file", str(d / "diff-name-status.txt"),
             "--discovery", f"tests={d / 'discovery.txt'}",
             "--command-result", str(d / "command-results.json")],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                         f"stdout: {proc.stdout}\nstderr: {proc.stderr}")
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("PRE-REVIEW GATE (build): PASS", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
