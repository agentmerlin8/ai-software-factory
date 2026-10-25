#!/usr/bin/env python3
"""mechanical_checks.py — build-lane mechanical checks (genericized port).

Check families (gate catalog: `build-gate`):

  command-validity     — bare-runner-command ban, PARAMETERIZED: the
                         project's runner names are declared (--runner);
                         a declared runner invoked with no exact target
                         rejects, and a declared target path that does
                         not exist rejects. No runners declared →
                         activation-gated OFF with a named note. PROSE IS
                         NEVER SCANNED for commands — only the contract's
                         verify_commands (reference T-B false-positive
                         class cannot recur).
  pinned-commands      — exact verify-command execution: parsed with a
                         safe-syntax refusal (pipes/redirects/command
                         substitution reject), `export`/`cd`/env-prefix
                         segments honored, ONE external command executed
                         EXACTLY as declared — never rewritten. Injected
                         results (fixtures/tests) must match the declared
                         commands 1:1 (unknown/duplicate/missing reject;
                         nonzero exit rejects).
  api-shape            — declared `api_operations` ↔ touched sources,
                         generic set-matching form: every declared
                         operation's `source` must appear in the diff, or
                         carry `verify_unchanged: true`. Activation-gated
                         on the contract declaring api_operations.
  ui-assertions        — declared `ui_assertions` (contract extension:
                         [{file, export, assertions:[{selector, count|
                         min_count|max_count}]}]) matched against an
                         injected/rendered results JSON (stories →
                         assertions → actual selector cardinalities).
                         Activation-gated on the declaration; a
                         declaration without results rejects (declaring
                         obligates verification).

Genericization dispositions (vs the reference):
  * DROPPED — pinned toolchain-version enforcement (the reference read a
    project-specific SDK manifest and refused version drift; the kit has
    no declared toolchain-pin surface — would require inventing a new
    contract field). Exact-command execution (the mechanism's core) is
    KEPT.
  * DROPPED — source-grammar API annotation parsing (the reference
    extracted route/response attributes from controller source files to
    compare against declarations; language-specific grammar). The
    declared↔touched set-matching is KEPT-GENERIC.
  * DROPPED — the live UI render runner (the reference built + served a
    project-specific story bundle and drove a project helper script).
    The results-matching half is KEPT (activation-gated, results
    injected via --ui-results).

Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Callable

# ------------------------------------------------------ command validity ----


def _segments(tokens: list[str]) -> list[list[str]]:
    """Split shlex tokens on '&&' into command segments."""
    segs: list[list[str]] = [[]]
    for tok in tokens:
        if tok == "&&":
            segs.append([])
        else:
            segs[-1].append(tok)
    return segs


def _operator_spans(raw: str) -> list[tuple[int, int, str]]:
    """Quote-aware scan of the RAW command for UNQUOTED shell operators
    (`&&`, `||`, `;`, `|`, lone `&`) REGARDLESS of surrounding whitespace
    — shlex keeps a glued `ok&&false` as ONE token, so the chain scan
    must run on the raw text. Mirrors shlex quoting: single quotes
    preserve everything, a backslash outside single quotes escapes the
    next character (also inside double quotes, so `\\"` never closes
    early). Operators inside quotes produce no span and stay legal.
    Returns (start, end, operator) spans in text order."""
    spans: list[tuple[int, int, str]] = []
    i, n, quote = 0, len(raw), None
    while i < n:
        c = raw[i]
        if quote == "'":
            if c == "'":
                quote = None
            i += 1
            continue
        if c == "\\":
            i += 2  # escaped next character (outside single quotes)
            continue
        if quote == '"':
            if c == '"':
                quote = None
            i += 1
            continue
        if c in "'\"":
            quote = c
            i += 1
            continue
        if raw.startswith("&&", i):
            spans.append((i, i + 2, "&&"))
            i += 2
            continue
        if raw.startswith("||", i):
            spans.append((i, i + 2, "||"))
            i += 2
            continue
        if raw.startswith("$(", i):
            spans.append((i, i + 2, "$("))
            i += 2
            continue
        if c in ";|&><`":
            spans.append((i, i + 1, c))
            i += 1
            continue
        i += 1
    return spans


def _separate_operators(raw: str,
                        spans: list[tuple[int, int, str]]) -> str:
    """Pad every UNQUOTED `&&` span with whitespace so a glued chain
    (`ok&&false`) tokenizes EXACTLY like its spaced form — the shlex
    token stream then carries `&&` as its own segment separator and the
    glued form receives the same chain treatment. Quoted content is
    byte-identical afterwards."""
    parts: list[str] = []
    prev = 0
    for start, end, op in spans:
        if op != "&&":
            continue
        parts.append(raw[prev:start])
        parts.append(f" {op} ")
        prev = end
    parts.append(raw[prev:])
    return "".join(parts)


def _split_tokens(raw: str) -> list[str]:
    """shlex tokens with quote-aware glued-operator separation."""
    return shlex.split(_separate_operators(raw, _operator_spans(raw)))


def _runner_target_paths(runner: str, target: str) -> list[str]:
    """Declared runner-specific target parsing: a `pytest` target may be
    a NODE ID (`file.py::class::test`) — existence-check the FILE
    portion only (the node suffix is not a filesystem path). The
    ORIGINAL target is preserved for execution; this only selects what
    gets existence-checked. Other runners check the target verbatim."""
    if runner == "pytest" and "::" in target:
        return [target.split("::", 1)[0]]
    return [target]


def _cd_under_root(cwd: Path, arg: str, root: Path) -> Path | None:
    """Resolve one `cd` argument against cwd, mirroring
    parse_test_command's cd rules EXACTLY (relative-only, must stay under
    root). Returns None when the argument violates those rules — the
    pinned-commands parser owns that refusal; callers here just leave the
    cwd unchanged."""
    if Path(arg).is_absolute():
        return None
    candidate = (cwd / arg).resolve()
    base = root.resolve()
    if base not in (candidate, *candidate.parents):
        return None
    if not candidate.is_dir():
        # R13-N2: the effective dir advances ONLY on a validated cd —
        # a target that does not exist cannot succeed at run time.
        return None
    return candidate


def check_bare_runners(contract: dict, root: Path, runners: list[str],
                       fail: Callable[[str], None],
                       info: Callable[[str], None],
                       value_flags: dict[str, set[str]] | None = None
                       ) -> None:
    """command-validity: a DECLARED runner must never appear as a bare
    invocation (no exact target), and a declared target path must exist
    under the EFFECTIVE cwd (repo root adjusted by preceding `cd` setup
    segments, same rules as parse_test_command). Activation-gated on
    `runners` being declared — the reference's runner/flag grammar was
    project vocabulary. `value_flags` optionally declares, per runner
    name, the option flags that TAKE A VALUE (e.g. {"pytest": {"-k"}}):
    a declared value flag consumes the next token as its value so an
    option value is never mistaken for a positional target. No
    declaration → every non-dash token counts as a target (the original
    behavior, unchanged)."""
    if not runners:
        info("command-validity: no runner names declared (--runner) — "
             "bare-invocation check inactive")
        return
    for raw in contract.get("verify_commands") or []:
        if not isinstance(raw, str) or not raw.strip():
            continue
        cmd = raw.strip()
        try:
            tokens = _split_tokens(cmd)
        except ValueError as e:
            fail(f"unparsable verify command {cmd!r}: {e}")
            continue
        cwd = root.resolve()
        for seg in _segments(tokens):
            while seg and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", seg[0]):
                seg = seg[1:]
            if seg and seg[0] == "cd":
                # track the effective working directory through preceding
                # setup segments; an unsafe/escaping cd leaves cwd as-is
                # (parse_test_command refuses it under pinned-commands)
                if len(seg) == 2:
                    moved = _cd_under_root(cwd, seg[1], root)
                    if moved is not None:
                        cwd = moved
                continue
            if not seg or seg[0] not in runners:
                continue
            declared_values = (value_flags or {}).get(seg[0]) or set()
            positional: list[str] = []
            it = iter(seg[1:])
            for tok in it:
                if tok in declared_values:
                    next(it, None)  # the flag's value is not a target
                elif not tok.startswith("-"):
                    positional.append(tok)
            if not positional:
                fail(f"bare `{seg[0]}` invocation (no exact target): "
                     f"{cmd!r}")
                continue
            for target in positional:
                # R13-N4: runner-specific target parsing — a pytest node
                # id (`file.py::class::test`) existence-checks its FILE
                # portion only; the ORIGINAL target is preserved for
                # execution (this selects what gets checked, never what
                # gets run). Under-root rules unchanged.
                for check_path in _runner_target_paths(seg[0], target):
                    if ("/" in check_path or "." in check_path) and \
                            not (cwd / check_path).exists():
                        fail(f"verify command names nonexistent target path "
                             f"{target!r}: {cmd!r}")


# ----------------------------------------------------- pinned commands ----

def _expand_env(value: str, env: dict[str, str]) -> str:
    value = value.replace("${HOME}", env.get("HOME", ""))
    value = value.replace("$HOME", env.get("HOME", ""))
    value = value.replace("${PATH}", env.get("PATH", ""))
    return value.replace("$PATH", env.get("PATH", ""))


def _expand_setup_value(value: str, env: dict[str, str]) -> str:
    """R6-G1: expand a setup (export/env-prefix) assignment value with the
    supported set ($HOME/$PATH only) and FAIL CLOSED on anything else —
    an unexpanded `$VAR` would stay LITERAL in the env value and silently
    change the command's meaning (same class as the argv refusal)."""
    expanded = _expand_env(value, env)
    if "$" in expanded:
        raise ValueError(
            f"unsupported variable expansion in setup value: {value}")
    return expanded


def parse_test_command(raw: str, root: Path,
                       base_env: dict[str, str] | None = None
                       ) -> tuple[Path, dict[str, str], list[str]] | None:
    """Parse one declared verify command into (cwd, env, argv) WITHOUT
    rewriting it. Safe-syntax refusal: pipes, redirects, command
    substitution and backgrounding reject (a single `&&` chain of
    export/cd/env-prefix + ONE external command is the accepted shape).
    R13-N1: the operator scan runs over the RAW text, quote-aware and
    WHITESPACE-INDEPENDENT — a glued `ok&&false` is a chain (rejected by
    the segment rules below), while operators inside quotes stay legal.
    Returns None when the command holds no external segment."""
    spans = _operator_spans(raw)
    if any(op != "&&" for _s, _e, op in spans):
        raise ValueError(f"unsafe verify command syntax: {raw!r}")
    tokens = _split_tokens(raw)
    segments = _segments(tokens)
    env = dict(base_env or os.environ)
    cwd = root
    external: list[str] | None = None
    for segment in segments:
        if not segment:
            raise ValueError(f"empty verify command segment: {raw!r}")
        if external is not None:
            # Setup (cd/export/env-prefix) is only legal BEFORE the external
            # command — applying it afterwards would retroactively change
            # the cwd/env of an already-chosen command.
            raise ValueError(
                f"verify command has segments after the external command: "
                f"{raw!r}")
        if segment[0] == "export":
            assignments = segment[1:]
            if not assignments:
                raise ValueError(f"empty export: {raw!r}")
            for assignment in assignments:
                if "=" not in assignment:
                    raise ValueError(
                        f"invalid export assignment: {assignment!r}")
                key, value = assignment.split("=", 1)
                env[key] = _expand_setup_value(value, env)
            continue
        if segment[0] == "cd":
            if len(segment) != 2 or Path(segment[1]).is_absolute():
                raise ValueError(f"unsafe cd in verify command: {raw!r}")
            candidate = (cwd / segment[1]).resolve()
            if root.resolve() not in (candidate, *candidate.parents):
                raise ValueError(
                    f"verify command cd escapes repository: {raw!r}")
            # R13-N2: EVERY cd target is validated before the effective
            # dir advances — a target that does not exist as a directory
            # cannot succeed, so the chain is rejected as a whole (a
            # non-advancing `cd missing` must not silently pass).
            if not candidate.is_dir():
                raise ValueError(
                    f"verify command cd names nonexistent directory: "
                    f"{raw!r}")
            cwd = candidate
            continue
        while segment and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*",
                                       segment[0]):
            key, value = segment.pop(0).split("=", 1)
            env[key] = _expand_setup_value(value, env)
        if not segment or external is not None:
            raise ValueError(
                f"verify command must contain one external command: "
                f"{raw!r}")
        external = segment
        # R5-F2: fail closed on unsupported variable expansion — argv is
        # executed literally, so a `$VAR` token would silently change
        # test selection (only $HOME/$PATH are expanded, and ONLY inside
        # export/env-prefix VALUES, never in the external command).
        for token in external:
            if "$" in token:
                raise ValueError(
                    "unsupported variable expansion in verify command: "
                    f"{token}")
    if not external:
        return None
    return cwd, env, external


def check_verify_commands(contract: dict, root: Path,
                          fail: Callable[[str], None],
                          injected_results: list[dict] | None = None,
                          run_live: bool = False, timeout: int = 900
                          ) -> None:
    """pinned-commands: execute/match the EXACT declared verify commands.
    Injected results (fixtures/tests) must cover every parsed command
    exactly once; unknown/duplicate/missing reject; nonzero exit rejects.
    Live mode runs argv unmodified (no rewriting, no runner
    substitution). The reference's pinned-SDK version enforcement was
    project-specific and is not ported (see module docstring)."""
    parsed: list[tuple[str, Path, dict[str, str], list[str]]] = []
    seen: set[str] = set()
    for raw in contract.get("verify_commands") or []:
        if not isinstance(raw, str) or raw in seen:
            continue
        seen.add(raw)
        try:
            value = parse_test_command(raw, root)
        except ValueError as exc:
            fail(str(exc))
            continue
        if value is None:
            # Setup-only entry (cd/export/env-prefix with no external
            # command): declaring it as a verify command is a contract
            # defect — it verifies nothing. Refuse by name; never drop.
            fail("verify command contains only setup (no external "
                 f"command): {raw}")
            continue
        parsed.append((raw, *value))
    if not parsed:
        return
    # R13-N5: validate EACH injected result row BEFORE building the
    # lookup — `command` must be present AND a string, or the row is a
    # named rejection (the one-to-one evidence match must never silently
    # skip a malformed row).
    for index, row in enumerate(injected_results or []):
        if not isinstance(row, dict):
            fail(f"invalid injected command result at index {index}: "
                 "row must be a mapping")
            continue
        if "command" not in row:
            fail(f"invalid injected command result at index {index}: "
                 "missing string `command` key")
            continue
        if not isinstance(row["command"], str):
            fail(f"invalid injected command result at index {index}: "
                 f"command must be a string (got "
                 f"{type(row['command']).__name__})")
    injected = {r["command"]: r for r in (injected_results or [])
                if isinstance(r, dict) and isinstance(r.get("command"), str)}
    if injected_results is not None:
        if len(injected) != len(injected_results):
            fail("duplicate injected command result")
        unknown = {str(k) for k in injected if isinstance(k, str)} - \
            {raw for raw, *_ in parsed}
        if unknown:
            fail(f"unknown injected command results: {sorted(unknown)}")
        for raw, _cwd, _env, _argv in parsed:
            result = injected.get(raw)
            if result is None:
                fail(f"missing command result for {raw!r}")
                continue
            if result.get("exit_code") != 0:
                fail(f"verify command failed ({result.get('exit_code')}): "
                     f"{raw!r}; stderr="
                     f"{str(result.get('stderr', ''))[-1000:]}")
        return
    if not run_live:
        return
    for raw, cwd, env, argv in parsed:
        result = subprocess.run(argv, cwd=cwd, env=env,
                                capture_output=True, text=True,
                                timeout=timeout)
        if result.returncode != 0:
            tail = "\n".join(
                (result.stdout + "\n" + result.stderr).splitlines()[-40:])
            fail(f"verify command failed ({result.returncode}): {raw!r}\n"
                 f"{tail}")


# ------------------------------------------------------------ api shape ----

def check_api_operations(contract: dict, changed: dict[str, str],
                         fail: Callable[[str], None]) -> str:
    """api-shape (generic set-matching): every declared api_operations
    entry's `source` must be touched in the diff, or the entry must carry
    `verify_unchanged: true`. Returns 'active'|'inactive' (activation is
    the api_operations declaration itself). The reference's source-grammar
    annotation comparison (routes/responses parsed out of controller
    files) is not ported — no generic grammar exists for it."""
    declared: dict[tuple[str, str], dict] = {}
    for o in contract.get("api_operations") or []:
        if isinstance(o, dict):
            declared[(str(o.get("method")), str(o.get("route")))] = o
    if not declared:
        return "inactive"
    for key, op in sorted(declared.items()):
        src = str(op.get("source") or "")
        if src in changed:
            continue  # touched AND declared
        if op.get("verify_unchanged") is not True:
            fail(f"api operation {key[0]} {key[1]} source `{src}` was not "
                 "touched in the diff — remove the declaration or set "
                 "verify_unchanged: true")
    return "active"


# -------------------------------------------------------- ui assertions ----

def check_ui_results(contract: dict, fail: Callable[[str], None],
                     results: dict | None) -> str:
    """ui-assertions: match declared selector cardinality/relationship
    assertions against rendered results. Contract extension shape:
      ui_assertions: [{file, export, assertions: [{selector, count|
                       min_count|max_count}]}]
    Results shape (injected via --ui-results or produced by the project's
    declared render harness):
      {"stories": [{file, export, error?, errors?,
                    assertions: [{selector, actual, error?}]}]}
    Activation-gated on the declaration; declared-without-results rejects
    (declaring obligates verification). Returns 'active'|'inactive'."""
    expected_items = contract.get("ui_assertions") or []
    if not expected_items:
        return "inactive"
    if results is None:
        fail("ui_assertions declared but no assertion results provided "
             "(--ui-results <json>) — declaring obligates verification")
        return "active"
    actual: dict[tuple[str, str, str], int] = {}
    for story in results.get("stories") or []:
        if story.get("error"):
            fail(f"ui render {story.get('file')}::{story.get('export')} — "
                 f"{story['error']}")
        if story.get("errors"):
            fail(f"ui render {story.get('file')}::{story.get('export')} "
                 f"console/page errors: {story['errors']}")
        for assertion in story.get("assertions") or []:
            if assertion.get("error"):
                fail(f"ui selector {assertion.get('selector')!r} failed: "
                     f"{assertion['error']}")
                continue
            key = (story.get("file"), story.get("export"),
                   assertion.get("selector"))
            if key in actual:
                fail(f"duplicate ui assertion result: {key!r}")
            actual[key] = assertion.get("actual")
    expected_keys: set[tuple[str, str, str]] = set()
    for item in expected_items:
        if not isinstance(item, dict):
            fail(f"ui_assertions entry must be a mapping: {item!r}")
            continue
        for assertion in item.get("assertions") or []:
            key = (item.get("file"), item.get("export"),
                   assertion.get("selector"))
            expected_keys.add(key)
            # R5-F3: an assertion with no recognized cardinality key
            # would match its result yet run no comparison — require one.
            if not any(k in assertion
                       for k in ("count", "min_count", "max_count")):
                fail(f"ui assertion {item.get('file')}::{item.get('export')} "
                     f"selector {assertion.get('selector')} declares no "
                     "cardinality constraint (count/min_count/max_count)")
            value = actual.get(key)
            if value is None:
                fail(f"missing ui assertion result for {key!r}")
                continue
            if "count" in assertion and value != assertion["count"]:
                fail(f"ui {key!r} expected count {assertion['count']}, "
                     f"got {value}")
            if "min_count" in assertion and value < assertion["min_count"]:
                fail(f"ui {key!r} expected min_count "
                     f"{assertion['min_count']}, got {value}")
            if "max_count" in assertion and value > assertion["max_count"]:
                fail(f"ui {key!r} expected max_count "
                     f"{assertion['max_count']}, got {value}")
    unknown = set(actual) - expected_keys
    if unknown:
        fail(f"unknown ui assertion results: {sorted(unknown)}")
    return "active"
