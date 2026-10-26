#!/usr/bin/env python3
"""tools/gates/pre_review_gate.py — deterministic pre-review gate
(gate catalog slugs: `plan-gate`, `build-gate` — two MODES of this one
script).

Runs BEFORE any LLM reviewer is spawned (mechanical-before-judgment law),
and again at BUILD_GREEN. Checks ACTUAL ARTIFACTS (contract block, real
diff, real test discovery), not string presence — prose is never scanned
for commands.

Usage:
    # plan stage (before plan review; PLANNED → GATE_PASSED):
    python3 tools/gates/pre_review_gate.py plan <story-dir> [more-dirs...]
        [--spec <path>] [--inflight auto|none]
        [--base-sha <sha>] [--base-ref main] [--runner <name>]...
        [--runner-opt <name>=<flag>]... [--root <repo>]
    # installed control-home form:
    python3 factory/gates/pre_review_gate.py plan <story-dir> ...

    # build stage (BUILD_GREEN — before code review):
    python3 tools/gates/pre_review_gate.py build <story-dir>
        [--base main] [--diff-file <name-status file>]
        [--discovery <project>=<list-tests file>]...
        [--discovery-command <project>=<cmd>]... [--run-discovery]
        [--run-verify] [--command-result <json>]... [--ui-results <json>]
        [--runner <name>]... [--runner-opt <name>=<flag>]... [--root <repo>]

    Bare story dirs (no mode word) = plan mode.

Check families (descriptive slugs — the kit carries no numeric gate ids):

PLAN MODE
  contract        contract.yaml block parses + validates (contract_lib)
  ac-coverage     every AC-N in the story spec has >=1 contract tests
                  entry with a NAMED test (repeated AC prose does not
                  satisfy this)
  collisions      batch CREATE manifests checked against each other AND
                  against in-flight story/* branch diffs (--inflight auto)
  anchors         code anchors in impl-plan.md resolve at the base sha
                  (ladder: --base-sha > context-digest.md BASE_SHA/§8 >
                  --base-ref, default main); repo-rooted path misses and
                  line-beyond-EOF = reject
  seams           contract.seams verified via dispatch_prep.seam_verify
                  at the base sha (declared-only; integration pending =
                  informational)
  coverage        coverage set-diffs: failure_model discriminating tests
                  verbatim in tests:; duplicate names reject; dotted
                  test-class prefixes must match a declared file stem;
                  tests[].ac must exist in the spec; per-AC Test-hooks
                  blocks with zero named tests reject (rename-latitude
                  gaps informational)
  commands        bare-runner-command ban (activation-gated on declared
                  --runner names; the reference's runner/flag grammar was
                  project vocabulary)
  snippets        any code-snippet fence in the plan carries
                  `compiled: true` + evidence path, or
                  `quoted_from: <file>@<sha>` (annotation comment
                  immediately above the fence)

BUILD MODE
  manifest-diff   `git diff --name-status base...HEAD` (or --diff-file)
                  reconciled against contract.files — undeclared
                  CREATE/EXTEND = reject; declared CREATE missing from
                  the diff = reject
  discovery       AC ↔ test discovery: every contract test name exists
                  in the discovery listing for its declared project
                  (--discovery <project>=<file>, or --run-discovery
                  executing the DECLARED --discovery-command; no command
                  declared = named usage refusal)
  pinned-commands exact verify-command execution (--run-verify) or
                  injected-result matching (--command-result); no
                  command rewriting
  api-shape       declared api_operations ↔ touched sources (generic
                  set-matching; activation-gated on the declaration)
  ui-assertions   declared ui_assertions ↔ rendered selector results
                  (--ui-results JSON; activation-gated on the
                  declaration)

Exit 0 = pass; exit 1 = findings printed; exit 2 = usage. Fixtures in
tools/gates/fixtures/ are mandatory acceptance criteria: the gate is not
deployed until it rejects ALL seeded-invalid fixtures and accepts all
valid-but-unusual ones (run: python3 tools/gates/test_gate.py).

Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent            # tools/gates (repo) | factory/gates (installed)
_TOOLS = _HERE.parent if (_HERE.parent / "contract_lib.py").exists() else _HERE.parent / "tools"
sys.path.insert(0, str(_TOOLS))
if str(_HERE) not in sys.path:                     # discovery-import safety
    sys.path.insert(0, str(_HERE))
import contract_lib as C  # noqa: E402
import mechanical_checks as M  # noqa: E402
import plan_checks as PC  # noqa: E402

# Fence languages treated as CODE SNIPPETS (must carry an annotation).
# Command/config/doc fences are exempt: shell commands are checked via
# the contract's verify_commands, not as snippets.
SNIPPET_LANGS = frozenset({
    "py", "python", "ts", "tsx", "js", "jsx", "sql", "json", "yaml",
    "yml", "toml", "xml", "html", "css", "rs", "go", "java", "rb",
    "c", "h", "cpp"})
SNIPPET_ANNOT_RE = re.compile(
    r"<!--\s*snippet\s+(compiled:\s*true\s+evidence:\s*\S+"
    r"|quoted_from:\s*\S+@\S+)\s*-->")
FENCE_RE = re.compile(r"^```(\w*)\s*$")
AC_RE = re.compile(r"\bAC-(\d+)\b")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True,
                          check=True).stdout


class Gate:
    """Findings collector + the two lane orchestrators. `root` is the
    project repo the gate resolves paths/anchors/branches against
    (--root, default cwd)."""

    def __init__(self, root: Path | None = None,
                 runners: list[str] | None = None,
                 base_ref: str = "main",
                 runner_value_flags: dict[str, set[str]] | None = None
                 ) -> None:
        self.root = Path(root) if root else Path.cwd()
        self.runners = list(runners or [])
        self.runner_value_flags = runner_value_flags or {}
        self.base_ref = base_ref
        self.findings: list[str] = []
        self.notes: list[str] = []

    def fail(self, check: str, sid: str, msg: str) -> None:
        self.findings.append(f"{sid}: {check} — {msg}")

    # ------------------------------------------------------------ plan ----

    def load_contract(self, story_dir: Path, sid: str) -> dict | None:
        plan = story_dir / "impl-plan.md"
        if not plan.exists():
            self.fail("contract", sid, "no impl-plan.md in story dir")
            return None
        try:
            plan_text = plan.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            # named refusal, never a raw traceback (S2-F2): a non-UTF-8
            # or unreadable impl-plan.md is a `contract` finding.
            self.fail("contract", sid, f"impl-plan.md unreadable: {e}")
            return None
        try:
            contract = C.extract_contract(plan_text)
        except C.ContractParseError as e:
            self.fail("contract", sid, f"contract block: {e}")
            return None
        errs = C.validate_contract(contract)
        for e in errs:
            self.fail("contract", sid, e)
        # Iteration safety (S2-F2): a list-typed field that failed the
        # type check above already carries a NAMED finding; normalize it
        # to [] so downstream checks (contract_paths, verify_commands,
        # tests coverage) report everything else at once instead of
        # raising TypeError on `for x in 42`.
        for key in ("files", "tests", "verify_commands", "invariants",
                    "decisions"):
            if key in contract and not isinstance(contract[key], list):
                contract[key] = []
        # return the contract even on schema errors so downstream checks
        # (commands, ac-coverage, snippets) still run and report
        # everything at once
        return contract

    def resolve_spec(self, story_dir: Path, sid: str,
                     spec_override: Path | None) -> Path | None:
        """Spec resolution order: --spec override (single-dir batches),
        then <story-dir>/spec.md, then stories/<sid>/spec.md and
        stories/<sid>.md under the gate root."""
        if spec_override is not None and spec_override.exists():
            return spec_override
        for cand in (story_dir / "spec.md",
                     self.root / "stories" / sid / "spec.md",
                     self.root / "stories" / f"{sid}.md"):
            if cand.exists():
                return cand
        return None

    def check_spec_ac_coverage(self, sid: str, contract: dict,
                               spec_path: Path | None) -> None:
        """ac-coverage (plan): every AC in the story spec has a NAMED
        contract test."""
        if spec_path is None:
            self.fail("ac-coverage", sid, "story spec not found (looked "
                      f"for {sid}/spec.md and stories/{sid}[/].md) — "
                      "cannot verify AC coverage")
            return
        spec_acs = set(AC_RE.findall(spec_path.read_text(encoding="utf-8")))
        covered: set[str] = set()
        for t in contract.get("tests") or []:
            if isinstance(t, dict) and t.get("name") and t.get("ac"):
                covered.add(str(t["ac"]).replace("AC-", ""))
        for ac in sorted(spec_acs - covered):
            self.fail("ac-coverage", sid, f"AC-{ac} in the spec has no "
                      "named contract test (prose mentions do not count)")

    def check_commands(self, sid: str, contract: dict) -> None:
        """commands (plan): bare-runner ban, activation-gated on the
        declared runner names (--runner). No runners declared → explicit
        informational skip."""
        M.check_bare_runners(
            contract, self.root, self.runners,
            lambda msg: self.fail("commands", sid, msg),
            lambda msg: self.notes.append(f"{sid}: {msg}"),
            value_flags=self.runner_value_flags)

    def check_snippets(self, story_dir: Path, sid: str) -> None:
        """snippets (plan): code fences need compiled/quoted_from
        annotations (comment on the line immediately above the fence)."""
        plan = story_dir / "impl-plan.md"
        if not plan.exists():
            return
        lines = plan.read_text(encoding="utf-8").splitlines()
        pending_annot = False
        in_fence = False
        fence_lang = ""
        fence_start = 0
        fence_annotated = False
        for i, line in enumerate(lines, 1):
            annot = bool(SNIPPET_ANNOT_RE.search(line))
            m = FENCE_RE.match(line.strip())
            if m and not in_fence:
                in_fence, fence_lang, fence_start = True, m.group(1), i
                fence_annotated = pending_annot
                pending_annot = False
                continue
            if in_fence and line.strip().startswith("```"):
                if fence_lang.lower() in SNIPPET_LANGS and \
                        not fence_annotated:
                    self.fail("snippets", sid,
                              f"impl-plan.md:{fence_start} `{fence_lang}` "
                              "snippet without annotation — add "
                              "`<!-- snippet compiled: true evidence: "
                              "<path> -->` or `<!-- snippet quoted_from: "
                              "<file>@<sha> -->` above the fence")
                in_fence = False
                continue
            if annot:
                pending_annot = True
            elif line.strip():
                pending_annot = False

    def check_collisions(self, batches: dict[str, set[str]], sid: str,
                         creates: set[str], inflight: str) -> None:
        """collisions: batch-internal CREATE collisions + in-flight
        story/* branches."""
        for other_sid, other_creates in batches.items():
            if other_sid == sid:
                continue
            overlap = creates & other_creates
            if overlap:
                self.fail("collisions", sid, f"CREATE collision with "
                          f"{other_sid} on {sorted(overlap)} — must run "
                          "SEQUENTIALLY")
        if inflight == "auto":
            try:
                refs = git(self.root, "for-each-ref",
                           "--format=%(refname:short)",
                           "refs/heads/story/").split()
            except subprocess.CalledProcessError:
                refs = []
            # The gated story's OWN branch is not a cross-story collision:
            # its bundle/digest/plan-prep/review artifacts ride the
            # branch by design and are declared CREATE in its own
            # manifest (the declare-at-planning rule). Only OTHER
            # stories' in-flight branches can genuinely collide.
            story_key = sid.split("-")[-1]  # STORY-934 -> 934
            for ref in refs:
                if ref in (f"story/{sid}", f"story/{story_key}") or \
                        ref.startswith(f"story/{story_key}-"):
                    continue
                try:
                    diff = git(self.root, "diff", "--name-status",
                               f"{self.base_ref}...{ref}")
                except subprocess.CalledProcessError:
                    continue
                inflight_added = {ln.split("\t", 1)[1]
                                  for ln in diff.splitlines()
                                  if ln.startswith("A\t")}
                overlap = creates & inflight_added
                if overlap:
                    self.fail("collisions", sid, f"CREATE collision with "
                              f"in-flight branch {ref} on {sorted(overlap)}")

    def plan_mode(self, story_dirs: list[Path], inflight: str,
                  spec_override: Path | None,
                  base_sha: str | None = None) -> None:
        batches: dict[str, set[str]] = {}
        for d in story_dirs:
            sid = d.name
            contract = self.load_contract(d, sid)
            if contract is None:
                continue
            creates, _ = C.contract_paths(contract)
            batches[sid] = creates
            # anchors — resolution at the base-sha ladder rung
            sha, sha_src = PC.resolve_base_sha(d, base_sha, self.base_ref)
            self.notes.append(f"{sid}: anchors/seams base sha = {sha} "
                              f"(source: {sha_src})")
            PC.check_plan_anchors(
                self.root, d, sid, sha, creates,
                lambda msg: self.fail("anchors", sid, msg),
                lambda msg: self.notes.append(f"{sid}: {msg}"))
            # seams — declared-only verification via dispatch_prep
            PC.check_seams(self.root, contract, sha,
                           lambda msg: self.fail("seams", sid, msg),
                           lambda msg: self.notes.append(f"{sid}: {msg}"))
            # coverage set-diffs + spec Test-hooks coverage
            ov = spec_override if len(story_dirs) == 1 else None
            spec_path = self.resolve_spec(d, sid, ov)
            plan_text = (d / "impl-plan.md").read_text(encoding="utf-8") \
                if (d / "impl-plan.md").exists() else ""
            # d3's plan-text latitude ("class declared as a new file")
            # scans the PROSE only — the contract fence is stripped so a
            # dotted tests[].name can never satisfy its own class check.
            plan_prose = C.CONTRACT_FENCE_RE.sub("", plan_text)
            self.check_spec_ac_coverage(sid, contract, spec_path)
            if spec_path is None:
                self.fail("coverage", sid, "story spec not found — cannot "
                          "run coverage set-diffs or Test-hooks coverage")
            else:
                spec_text = spec_path.read_text(encoding="utf-8")
                PC.check_coverage_sets(
                    contract, spec_text, plan_prose,
                    lambda msg: self.fail("coverage", sid, msg))
                PC.check_hook_coverage(
                    contract, spec_text, plan_prose,
                    lambda msg: self.fail("coverage", sid, msg),
                    lambda msg: self.notes.append(f"{sid}: {msg}"))
            self.check_commands(sid, contract)
            self.check_snippets(d, sid)
        for sid, creates in batches.items():
            self.check_collisions(batches, sid, creates, inflight)

    # ----------------------------------------------------------- build ----

    def build_mode(self, story_dir: Path, base: str,
                   diff_file: Path | None, discovery: dict[str, Path],
                   run_discovery: bool = False,
                   discovery_commands: dict[str, str] | None = None,
                   command_results: list[dict] | None = None,
                   run_verify: bool = False,
                   ui_results: dict | None = None) -> None:
        sid = story_dir.name
        contract = self.load_contract(story_dir, sid)
        if contract is None:
            return
        # manifest-diff — declared files ↔ the real diff
        if diff_file:
            try:
                diff = diff_file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as e:
                # named refusal, never a raw traceback (S2-F2)
                self.fail("manifest-diff", sid,
                          f"injected diff file {diff_file} unreadable: {e}")
                return
        else:
            try:
                diff = git(self.root, "diff", "--name-status",
                           f"{base}...HEAD")
            except subprocess.CalledProcessError as e:
                self.fail("manifest-diff", sid,
                          f"git diff {base}...HEAD failed: {e}")
                diff = ""
        creates, extends = C.contract_paths(contract)
        declared = creates | extends
        changed: dict[str, str] = {}
        for line in diff.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                status, path = parts[0], parts[-1]
                changed[path] = status
        for path, status in sorted(changed.items()):
            if path not in declared:
                kind = "CREATE" if status.startswith("A") else "EXTEND"
                self.fail("manifest-diff", sid, f"undeclared {kind}: "
                          f"`{path}` ({status}) is not in contract.files")
            elif status.startswith("A") and path in extends and \
                    path not in creates:
                self.fail("manifest-diff", sid, f"`{path}` declared EXTEND "
                          "but diff shows it ADDED (should be CREATE)")
            elif status.startswith("M") and path in creates and \
                    path not in extends:
                self.fail("manifest-diff", sid, f"`{path}` declared CREATE "
                          "but diff shows it MODIFIED (should be EXTEND)")
        for path in sorted(creates - set(changed)):
            self.fail("manifest-diff", sid, f"declared CREATE `{path}` "
                      "never appeared in the diff — record a deviation "
                      "event or fix the manifest")

        # api-shape — activation-gated on the api_operations declaration
        if M.check_api_operations(
                contract, changed,
                lambda msg: self.fail("api-shape", sid, msg)) == "inactive":
            self.notes.append(f"{sid}: api-shape inactive (no "
                              "api_operations declared)")

        # ui-assertions — activation-gated on the ui_assertions declaration
        if M.check_ui_results(
                contract,
                lambda msg: self.fail("ui-assertions", sid, msg),
                ui_results) == "inactive":
            self.notes.append(f"{sid}: ui-assertions inactive (no "
                              "ui_assertions declared)")

        # pinned-commands — exact declared commands, injected or live
        if run_verify or command_results is not None:
            M.check_verify_commands(
                contract, self.root,
                lambda msg: self.fail("pinned-commands", sid, msg),
                command_results, run_verify)

        # discovery — AC ↔ real test discovery
        tests: list[dict] = [t for t in contract.get("tests") or []
                             if isinstance(t, dict) and t.get("name")]
        need_projects = sorted({t.get("project", "") for t in tests})
        listing: dict[str, list[str]] = {}
        for proj in need_projects:
            if not proj:
                continue
            if proj in discovery:
                listing[proj] = discovery[proj].read_text(
                    encoding="utf-8").splitlines()
            elif run_discovery:
                listing[proj] = self._run_discovery_command(
                    sid, proj, (discovery_commands or {}).get(proj))
            else:
                self.fail("discovery", sid, f"no test discovery for "
                          f"project `{proj}` — pass --discovery "
                          f"{proj}=<file> or --run-discovery with a "
                          "declared --discovery-command (names must be "
                          "proven to exist, not just declared)")
        union = [ln for lns in listing.values() for ln in lns]
        for t in tests:
            proj, name = t.get("project"), str(t.get("name"))
            if proj:
                names = listing.get(proj)
                if names is None:
                    continue  # missing-listing finding already emitted
                if not any(name in ln for ln in names):
                    self.fail("discovery", sid, f"contract test `{name}` "
                              f"(AC {t.get('ac')}) NOT FOUND in discovery "
                              f"output for `{proj}` — the discovery "
                              "listing is the authority, not the plan's "
                              "prose")
            else:
                if not listing:
                    self.fail("discovery", sid, f"contract test `{name}` "
                              "declares no project and no discovery "
                              "listing was provided — pass --discovery "
                              "<project>=<file> covering it")
                elif not any(name in ln for ln in union):
                    self.fail("discovery", sid, f"contract test `{name}` "
                              "(no declared project) NOT FOUND in any "
                              "provided discovery listing")

    def _run_discovery_command(self, sid: str, proj: str,
                               command: str | None) -> list[str]:
        """Execute the DECLARED discovery command exactly (never
        synthesized). Callers refuse at usage level when none is
        declared; this guard is defense-in-depth."""
        if not command:
            self.fail("discovery", sid, f"--run-discovery for project "
                      f"`{proj}` has no declared command "
                      "(--discovery-command "
                      f"{proj}=<list-tests cmd>) — refusing to invent one")
            return []
        argv = shlex.split(command)
        try:
            result = subprocess.run(argv, cwd=self.root,
                                    capture_output=True, text=True,
                                    timeout=900)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            self.fail("discovery", sid, f"declared discovery command for "
                      f"`{proj}` could not run: {e}")
            return []
        if result.returncode != 0:
            self.fail("discovery", sid, f"test discovery failed for "
                      f"`{proj}` (exit {result.returncode}): "
                      f"{result.stderr.strip()[-1000:]}")
            return []
        return [ln.strip().lstrip("-").strip()
                for ln in result.stdout.splitlines() if ln.strip()]


def _read_injected_json(path: str):
    """Read + parse an injected evidence file (--command-result,
    --ui-results). Any read/decode/JSON failure is a NAMED rc-2 usage
    refusal (S2-F2), never a raw traceback. Returns the parsed value,
    or None after printing `usage: unreadable <path> — <detail>`."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as e:
        print(f"usage: unreadable {path} — {e}", file=sys.stderr)
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"usage: unreadable {path} — invalid JSON: {e}",
              file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="pre_review_gate",
        description="deterministic pre-review gate (plan/build modes)")
    ap.add_argument("mode_or_dirs", nargs="+",
                    help="'plan'|'build' followed by story dirs, or bare "
                         "story dirs (= plan mode)")
    ap.add_argument("--spec", default=None, help="story spec path (plan)")
    ap.add_argument("--inflight", default="auto", choices=("auto", "none"),
                    help="check in-flight story/* branches (collisions)")
    ap.add_argument("--base-sha", default=None,
                    help="base sha for anchor+seam resolution (plan); "
                         "ladder: --base-sha > context-digest.md > "
                         "--base-ref")
    ap.add_argument("--base-ref", default="main",
                    help="base ref, ladder default rung (plan)")
    ap.add_argument("--base", default="main", help="diff base (build)")
    ap.add_argument("--diff-file", default=None,
                    help="injected name-status diff (build, tests)")
    ap.add_argument("--discovery", action="append", default=[],
                    metavar="PROJECT=FILE",
                    help="test-discovery listing per project (build)")
    ap.add_argument("--discovery-command", action="append", default=[],
                    metavar="PROJECT=CMD",
                    help="declared list-tests command per project, run by "
                         "--run-discovery (build)")
    ap.add_argument("--run-discovery", action="store_true",
                    help="run the DECLARED discovery commands live "
                         "(build); no command declared = usage refusal")
    ap.add_argument("--run-verify", action="store_true",
                    help="execute contract verify commands exactly as "
                         "declared (build)")
    ap.add_argument("--command-result", action="append", default=[],
                    metavar="JSON",
                    help="injected pinned-command result JSON "
                         "(fixtures/tests only)")
    ap.add_argument("--ui-results", default=None, metavar="JSON",
                    help="injected ui-assertion results JSON "
                         "(fixtures/tests only)")
    ap.add_argument("--runner", action="append", default=[], metavar="NAME",
                    help="declare a test-runner name for the "
                         "bare-invocation ban (plan/build)")
    ap.add_argument("--runner-opt", action="append", default=[],
                    metavar="NAME=FLAG",
                    help="declare a VALUE-TAKING option flag for a runner "
                         "(repeatable, e.g. --runner-opt pytest=-k): the "
                         "flag's next token is its value, never a "
                         "positional target (plan/build commands check)")
    ap.add_argument("--root", default=".",
                    help="project repo root (default: cwd)")
    args = ap.parse_args(argv)

    first = args.mode_or_dirs[0]
    if first in ("plan", "build"):
        mode, dirs = first, [Path(p) for p in args.mode_or_dirs[1:]]
    else:
        mode, dirs = "plan", [Path(p) for p in args.mode_or_dirs]
    root = Path(args.root).resolve()

    runner_value_flags: dict[str, set[str]] = {}
    for item in args.runner_opt:
        name, sep, flag = item.partition("=")
        if not sep or not name or not flag:
            print(f"usage: --runner-opt takes NAME=FLAG (got {item!r})",
                  file=sys.stderr)
            return 2
        runner_value_flags.setdefault(name, set()).add(flag)

    gate = Gate(root=root, runners=args.runner, base_ref=args.base_ref,
                runner_value_flags=runner_value_flags)
    if mode == "plan":
        gate.plan_mode(dirs, args.inflight,
                       Path(args.spec) if args.spec else None,
                       args.base_sha)
    else:
        if len(dirs) != 1:
            print("build mode takes exactly one story dir",
                  file=sys.stderr)
            return 2
        disc: dict[str, Path] = {}
        for item in args.discovery:
            proj, _, f = item.partition("=")
            disc[proj] = Path(f)
        disc_cmds: dict[str, str] = {}
        for item in args.discovery_command:
            proj, _, cmd = item.partition("=")
            disc_cmds[proj] = cmd
        if args.run_discovery:
            # named usage refusal: --run-discovery executes DECLARED
            # commands only — never a synthesized one
            try:
                contract = C.extract_contract(
                    (dirs[0] / "impl-plan.md").read_text(encoding="utf-8"))
            except (OSError, C.ContractParseError):
                contract = {}
            projects = sorted({str(t.get("project"))
                               for t in contract.get("tests") or []
                               if isinstance(t, dict) and t.get("project")})
            missing = [p for p in projects if p not in disc_cmds]
            if missing:
                print("usage: --run-discovery requires a declared "
                      "discovery command for every test project — "
                      "missing --discovery-command for: "
                      + ", ".join(f"{p}=<cmd>" for p in missing),
                      file=sys.stderr)
                return 2
        command_results: list[dict] = []
        for p in args.command_result:
            parsed = _read_injected_json(p)
            if parsed is None:
                return 2
            # both shapes legal: a LIST of results (extend) or a single
            # result object (append) — never wrap a list as one element.
            if isinstance(parsed, list):
                command_results.extend(parsed)
            else:
                command_results.append(parsed)
        ui_results = None
        if args.ui_results:
            ui_results = _read_injected_json(args.ui_results)
            if ui_results is None:
                return 2
        if args.diff_file:
            try:
                Path(args.diff_file).read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as e:
                print(f"usage: unreadable {args.diff_file} — {e}",
                      file=sys.stderr)
                return 2
        gate.build_mode(dirs[0], args.base,
                        Path(args.diff_file) if args.diff_file else None,
                        disc, args.run_discovery, disc_cmds,
                        command_results if args.command_result else None,
                        args.run_verify, ui_results)

    if gate.findings:
        print(f"PRE-REVIEW GATE ({mode}): FAIL")
        for f in gate.findings:
            print(f"  - {f}")
        _print_notes(gate)
        return 1
    print(f"PRE-REVIEW GATE ({mode}): PASS ({len(dirs)} story dir(s))")
    _print_notes(gate)
    return 0


def _print_notes(gate: Gate) -> None:
    """Informational gate notes (anchors, seams, activation skips)."""
    if not gate.notes:
        return
    print("  informational:")
    for n in gate.notes:
        print(f"    - {n}")


if __name__ == "__main__":
    sys.exit(main())
