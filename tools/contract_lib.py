#!/usr/bin/env python3
"""tools/contract_lib.py — impl-plan contract block: extraction + validation.

Plans carry a machine-readable fenced block (architecture.md §Dispatch
package — "contract shape"; frozen schema: §Plan contract block):

    ```contract.yaml
    contract_version: 1
    files:
      - path: src/app/endpoint.py
        op: EXTEND               # CREATE | EXTEND
    tests:
      - ac: AC-1
        name: rejects_duplicate_input
        asserts: "refusal + no state written"
    invariants:
      - id: INV-1
        text: "..."
    verify_commands:
      - "python3 -m unittest"
    decisions:
      - decision: "..."
        rejected: ["..."]
    failure_model:               # optional; see hook note below
      - risk: "..."
        discriminating_test: test_name
    api_operations:              # optional; HTTP surface declarations
      - method: POST
        route: /items
        source: src/app/api.py
        action: create_item
        responses:
          - status: 201
            type: ItemCreated

The parser is a deliberately small YAML SUBSET (block mappings, block
sequences, inline flow sequences, quoted/plain scalars, comments). It is
not general YAML and does not pretend to be — anything outside the subset
is a parse error, which the gate treats as a reject.

Genericization notes (hook points for project declarations):

  * File/test-project/api-source paths are checked STRUCTURALLY only:
    non-empty, repo-relative, no `..`, not absolute. Layout-specific rules
    (source-tree prefixes, file extensions, namespaces) are declared by
    the project's gate configuration, not hardcoded here.
  * `verify_commands` entries must be non-empty strings; the command
    grammar (test runner, flags) is project-declared — this module never
    interprets a command.
  * `failure_model` is OPTIONAL here. The reference required it for
    elevated-risk strata; strata are trial telemetry and were dropped.
    Hook point: a project gate profile may declare conditions that make
    failure_model required (S2 gate catalog).

Refusal message style (stable — tests assert verbatim):
  parse/schema refusals   → `malformed contract block: <detail>`
  semantic refusals       → `contract block: <check> — <detail>`

CLI: `python3 tools/contract_lib.py <plan.md>` — exit 0 OK, 1 findings,
2 usage/unreadable file. Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

CONTRACT_FENCE_RE = re.compile(
    r"^```(?:contract\.yaml|yaml contract|contract)\s*\n(.*?)^```\s*$",
    re.M | re.S)

CONTRACT_VERSION = 1
FILE_OPS = ("CREATE", "EXTEND")
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
EXPORT_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")


class ContractParseError(ValueError):
    """Parse-level refusal; message is prefixed
    `malformed contract block: `."""


# ------------------------------------------------------------ yaml subset ---

def _strip_comment(line: str) -> str:
    """Strip a trailing `#` comment, YAML quote rules applied:

    * `#` starts a comment only OUTSIDE a quoted scalar AND only at
      start-of-token (beginning of line or preceded by whitespace), so
      embedded hashes in plain scalars survive (`test_x[case#1]`, `x#y`).
    * Inside a DOUBLE-quoted scalar a backslash escapes the next
      character (the R17 decoder set), so `\"` does not close the
      scalar and a `#` after it is still quoted text. Escape
      VALIDATION stays in _decode_double_quoted — the scanner only
      pairs characters, so an unsupported escape is still refused by
      name at decode time, never silently mis-scanned here.
    * Inside a SINGLE-quoted scalar only `''` doubling applies; the
      open/close toggle handles it symmetrically (backslashes are
      literal there, per YAML).
    """
    out: list[str] = []
    in_s, quote, prev_ws = False, "", True
    i = 0
    while i < len(line):
        ch = line[i]
        if in_s:
            out.append(ch)
            if quote == '"' and ch == "\\" and i + 1 < len(line):
                out.append(line[i + 1])
                i += 2
                prev_ws = False
                continue
            if ch == quote:
                in_s = False
            i += 1
            prev_ws = False
            continue
        if ch == "#" and prev_ws:
            break
        out.append(ch)
        if ch in "\"'":
            in_s, quote = True, ch
        prev_ws = ch.isspace()
        i += 1
    return "".join(out).rstrip()


_DOUBLE_ESCAPES = {"\\": "\\", '"': '"', "n": "\n", "t": "\t",
                   "r": "\r", "b": "\b", "f": "\f"}


def _decode_double_quoted(body: str) -> str:
    """Decode escapes inside a double-quoted YAML scalar. The standard set
    (\\\\, \\", \\n, \\t, \\r, \\b, \\f) decodes; any other backslash
    sequence is a named parse refusal — never silently kept, so the runner
    cannot execute a different program than the author intended."""
    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(body):
            raise ContractParseError(
                f"unsupported escape sequence: {ch!r} "
                "(trailing backslash in double-quoted scalar)")
        nxt = body[i + 1]
        if nxt not in _DOUBLE_ESCAPES:
            raise ContractParseError(
                f"unsupported escape sequence: {(ch + nxt)!r}")
        out.append(_DOUBLE_ESCAPES[nxt])
        i += 2
    return "".join(out)


def _scalar(tok: str) -> Any:
    tok = tok.strip()
    if tok == "" or tok in ("null", "~"):
        return None
    if tok in ("true", "True"):
        return True
    if tok in ("false", "False"):
        return False
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        body = tok[1:-1]
        if tok[0] == "'":
            # YAML single-quote rule: only '' escapes (to '); backslashes
            # stay literal.
            return body.replace("''", "'")
        return _decode_double_quoted(body)
    if re.fullmatch(r"-?\d+", tok):
        return int(tok)
    if re.fullmatch(r"-?\d+\.\d+", tok):
        return float(tok)
    if tok.startswith("[") and tok.endswith("]"):
        inner = tok[1:-1].strip()
        if not inner:
            return []
        return [_scalar(p) for p in _split_flow(inner)]
    return tok


def _split_flow(s: str) -> list[str]:
    """Split a flow-sequence body on top-level commas. Quote rules match
    _strip_comment: inside a DOUBLE-quoted scalar a backslash escapes the
    next character (`\"` does not close the scalar, so commas and
    brackets inside it never split); SINGLE-quoted scalars close on the
    quote (`''` doubling toggles symmetrically); nested `[ ]`/`{ }`
    raise the depth so their commas do not split. Each element survives
    verbatim to _scalar for decoding."""
    parts, depth, in_s, quote, cur = [], 0, False, "", []
    i = 0
    while i < len(s):
        ch = s[i]
        if in_s:
            cur.append(ch)
            if quote == '"' and ch == "\\" and i + 1 < len(s):
                cur.append(s[i + 1])
                i += 2
                continue
            if ch == quote:
                in_s = False
            i += 1
            continue
        if ch in "\"'":
            in_s, quote = True, ch
            cur.append(ch)
        elif ch in "[{":
            depth += 1
            cur.append(ch)
        elif ch in "]}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    if cur:
        parts.append("".join(cur))
    return [p.strip() for p in parts]


def _mapping_split(content: str) -> tuple[str, str] | None:
    """(key, rest) when `content` is a YAML key/value line — a colon
    counts as a mapping separator only when it sits OUTSIDE a quoted
    scalar (quote/escape rules match _strip_comment: `\"` does not close
    a double-quoted scalar; `''` doubling toggles a single-quoted one)
    AND is followed by whitespace or end-of-line, so colons INSIDE
    scalars (e.g. `pytest a.py::test_x`, `text: "a: b"`, or a quoted
    key containing `": "`) never split at the wrong point. None when
    it is not a mapping line. Keys in this subset are plain scalars;
    a quoted key is returned verbatim (quotes kept) — the scanner only
    guarantees the split point is outside quotes."""
    in_s, quote = False, ""
    i = 0
    while i < len(content):
        ch = content[i]
        if in_s:
            if quote == '"' and ch == "\\" and i + 1 < len(content):
                i += 2
                continue
            if ch == quote:
                in_s = False
        elif ch in "\"'":
            in_s, quote = True, ch
        elif ch == ":" and (i + 1 >= len(content)
                            or content[i + 1].isspace()):
            key = content[:i].strip()
            if not key:
                return None
            return key, content[i + 1:].strip()
        i += 1
    return None


def parse_yaml_subset(text: str) -> Any:
    """Parse the small YAML subset; raises ContractParseError (bare detail,
    no prefix — extract_contract adds it)."""
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        s = _strip_comment(raw)
        if not s.strip():
            continue
        indent = len(s) - len(s.lstrip(" "))
        lines.append((indent, s.strip()))
    if not lines:
        raise ContractParseError("empty contract block")
    val, end = _parse_block(lines, 0, lines[0][0])
    if end != len(lines):
        raise ContractParseError(
            f"unexpected indentation at line {end + 1}: {lines[end][1]!r}")
    return val


def _parse_block(lines: list[tuple[int, str]], i: int,
                 indent: int) -> tuple[Any, int]:
    if i < len(lines) and lines[i][1].startswith("- "):
        return _parse_seq(lines, i, indent)
    return _parse_map(lines, i, indent)


def _parse_seq(lines: list[tuple[int, str]], i: int,
               indent: int) -> tuple[list, int]:
    out: list[Any] = []
    while i < len(lines):
        ind, content = lines[i]
        if ind < indent or not content.startswith("- "):
            break
        if ind > indent:
            raise ContractParseError(f"bad indent in sequence near {content!r}")
        item = content[2:].strip()
        i += 1
        if item and not item.startswith(("[", '"', "'")) and \
                _mapping_split(item) is not None:
            # mapping item starting on the dash line: "- key: value"
            sub: list[tuple[int, str]] = [(indent + 2, item)]
            while i < len(lines) and lines[i][0] > indent:
                sub.append(lines[i])
                i += 1
            val, _ = _parse_map(sub, 0, indent + 2)
            out.append(val)
        elif item == "":
            val, i = _parse_block(lines, i, indent + 2)
            out.append(val)
        else:
            out.append(_scalar(item))
    return out, i


def _parse_map(lines: list[tuple[int, str]], i: int,
               indent: int) -> tuple[dict, int]:
    out: dict[str, Any] = {}
    while i < len(lines):
        ind, content = lines[i]
        if ind < indent:
            break
        if ind > indent:
            raise ContractParseError(
                f"unexpected indent at {content!r} (context {out!r:.80})")
        kv = _mapping_split(content)
        if kv is None:
            raise ContractParseError(f"expected `key: value`, got {content!r}")
        key, rest = kv
        if key in out:
            raise ContractParseError(f"duplicate mapping key: {key!r}")
        i += 1
        if rest:
            out[key] = _scalar(rest)
        elif i < len(lines) and lines[i][0] > indent:
            out[key], i = _parse_block(lines, i, lines[i][0])
        elif i < len(lines) and lines[i][0] == indent and \
                lines[i][1].startswith("- "):
            out[key], i = _parse_seq(lines, i, indent)
        else:
            out[key] = None
    return out, i


# ------------------------------------------------------- contract handling ---

def extract_contract(plan_text: str) -> dict[str, Any]:
    """Pull the contract block out of impl-plan markdown text.

    Raises ContractParseError prefixed `malformed contract block: ` on a
    missing block, a YAML-subset parse error, or a non-mapping block.
    """
    m = CONTRACT_FENCE_RE.search(plan_text)
    if not m:
        raise ContractParseError(
            "malformed contract block: no ```contract.yaml fenced block "
            "found in plan")
    try:
        val = parse_yaml_subset(m.group(1))
    except ContractParseError as e:
        raise ContractParseError(f"malformed contract block: {e}") from e
    if not isinstance(val, dict):
        raise ContractParseError(
            "malformed contract block: contract block must be a mapping")
    return val


def _repo_relative_errors(value: Any) -> bool:
    """True when `value` is NOT a usable repo-relative path (generic
    structural check: non-empty string, not absolute, no `..`)."""
    return not (isinstance(value, str) and value.strip()
                and not value.startswith(("/", "\\"))
                and ".." not in value.split("/")
                and ".." not in value.split("\\"))


def validate_contract(c: dict[str, Any]) -> list[str]:
    """Plan-gate schema check. Returns findings ([] = valid).

    Message classes: `malformed contract block: <detail>` for presence /
    type / entry-shape refusals; `contract block: <check> — <detail>` for
    semantic ones (non-emptiness laws, path safety, enums, duplicates).
    """
    errs: list[str] = []

    def req(key: str, typ: type | tuple) -> Any:
        v = c.get(key)
        if v is None:
            errs.append(f"malformed contract block: contract missing "
                        f"`{key}`")
        elif not isinstance(v, typ):
            errs.append(f"malformed contract block: `{key}` must be a "
                        f"{getattr(typ, '__name__', typ)}, "
                        f"got {type(v).__name__}")
        return v

    if c.get("contract_version") != CONTRACT_VERSION:
        errs.append("malformed contract block: contract_version must be "
                    f"{CONTRACT_VERSION}")

    files = req("files", list)
    if isinstance(files, list):
        if not files:
            errs.append("contract block: files — manifest must be non-empty")
        for f in files:
            if not isinstance(f, dict) or not f.get("path") or \
                    f.get("op") not in FILE_OPS:
                errs.append("malformed contract block: files entry needs "
                            f"path + op {'|'.join(FILE_OPS)}, got {f!r}")
            elif _repo_relative_errors(f["path"]):
                errs.append("contract block: file path — must be "
                            f"repo-relative (not absolute, no '..'): "
                            f"{f['path']!r}")

    tests = req("tests", list)
    if isinstance(tests, list):
        if not tests:
            errs.append("contract block: tests — must be non-empty (the "
                        "design contract binds the test list)")
        for t in tests:
            if not isinstance(t, dict):
                errs.append("malformed contract block: tests entry must be "
                            f"a mapping, got {t!r}")
                continue
            for k in ("ac", "name"):
                if not t.get(k):
                    errs.append("malformed contract block: tests entry "
                                f"missing `{k}`: {t!r}")
            if "project" in t and t["project"] is not None and \
                    _repo_relative_errors(t["project"]):
                errs.append("contract block: tests project — must be "
                            f"repo-relative (not absolute, no '..'): "
                            f"{t['project']!r}")

    inv = req("invariants", list)
    if isinstance(inv, list):
        if not inv:
            errs.append("contract block: invariants — must be non-empty "
                        "(binding on reviewers independently of the test "
                        "list)")
        for v in inv:
            if not isinstance(v, dict) or not v.get("id") or not v.get("text"):
                errs.append("malformed contract block: invariant needs "
                            f"id + text, got {v!r}")

    cmds = req("verify_commands", list)
    if isinstance(cmds, list):
        if not cmds:
            errs.append("contract block: verify_commands — must be "
                        "non-empty")
        for cm in cmds:
            if not isinstance(cm, str) or not cm.strip():
                errs.append("malformed contract block: verify_commands "
                            f"entry must be a non-empty string, got {cm!r}")

    dec = req("decisions", list)
    if isinstance(dec, list):
        if not dec:
            errs.append("contract block: decisions — must be non-empty "
                        "(frozen rationale)")
        for d in dec:
            if not isinstance(d, dict) or not d.get("decision"):
                errs.append("malformed contract block: decision entry "
                            f"needs `decision`, got {d!r}")

    fm = c.get("failure_model")
    if fm is not None:
        if not isinstance(fm, list):
            errs.append("malformed contract block: failure_model must be "
                        "a list")
        else:
            for e in fm:
                if not isinstance(e, dict) or not e.get("risk") or \
                        not e.get("discriminating_test"):
                    errs.append("malformed contract block: failure_model "
                                f"entry needs risk + discriminating_test, "
                                f"got {e!r}")

    api_ops = c.get("api_operations")
    if api_ops is not None:
        if not isinstance(api_ops, list):
            errs.append("malformed contract block: api_operations must be "
                        "a list")
        else:
            seen_ops: set[tuple[str, str]] = set()
            for op in api_ops:
                if not isinstance(op, dict):
                    errs.append("malformed contract block: api_operations "
                                f"entry must be a mapping: {op!r}")
                    continue
                method, route = op.get("method"), op.get("route")
                source, action = op.get("source"), op.get("action")
                if not isinstance(method, str) or method not in HTTP_METHODS:
                    errs.append("contract block: api_operations method — "
                                f"{method!r} not in {sorted(HTTP_METHODS)}")
                if not isinstance(route, str) or not route.startswith("/") \
                        or "?" in route or ".." in route:
                    errs.append("contract block: api_operations route — "
                                "must start with '/' without '?' or '..': "
                                f"{route!r}")
                if _repo_relative_errors(source):
                    errs.append("contract block: api_operations source — "
                                "must be a repo-relative source path (not "
                                f"absolute, no '..'): {source!r}")
                if not isinstance(action, str) or not action:
                    errs.append("malformed contract block: API operation "
                                f"needs action in {op!r}")
                key = (str(method), str(route))
                if key in seen_ops:
                    errs.append("contract block: api_operations duplicate "
                                f"— {key!r} appears twice")
                seen_ops.add(key)
                responses = op.get("responses")
                if op.get("removed") is True:
                    continue
                if not isinstance(responses, list) or not responses:
                    errs.append("contract block: api_operations responses "
                                f"— operation needs a non-empty responses "
                                f"list: {op!r}")
                    continue
                seen_status: set[int] = set()
                for response in responses:
                    if not isinstance(response, dict) or \
                            not isinstance(response.get("status"), int) or \
                            isinstance(response.get("status"), bool) or \
                            not 100 <= response["status"] <= 599 or \
                            not isinstance(response.get("type"), str) or \
                            not response["type"]:
                        errs.append("contract block: api_operations "
                                    "response — invalid entry (status int "
                                    f"100-599 + non-empty type): "
                                    f"{response!r}")
                        continue
                    if response["status"] in seen_status:
                        errs.append("contract block: api_operations "
                                    f"duplicate status — "
                                    f"{response['status']} for {key!r}")
                    seen_status.add(response["status"])
    return errs


def contract_paths(c: dict[str, Any]) -> tuple[set[str], set[str]]:
    """(create_paths, extend_paths) from a validated contract."""
    creates, extends = set(), set()
    files = c.get("files")
    if files is not None and not isinstance(files, list):
        # A non-list `files` is already a NAMED validate_contract
        # finding; never raise TypeError here (S2-F2) — iterate nothing.
        return creates, extends
    for f in files or []:
        if isinstance(f, dict) and f.get("path"):
            (creates if f.get("op") == "CREATE" else extends).add(f["path"])
    return creates, extends


# --------------------------------------------------------------------- CLI ----

def main(argv: list[str] | None = None) -> int:
    """Validate a plan's contract block; exit 0 OK, 1 findings, 2 usage."""
    parser = argparse.ArgumentParser(
        prog="contract_lib",
        description="Extract + validate the ```contract.yaml block of an "
                    "impl plan (frozen schema — architecture.md §Plan "
                    "contract block).")
    parser.add_argument("plan", nargs="?", metavar="plan.md")
    args = parser.parse_args(argv)
    if not args.plan:
        parser.print_usage(sys.stderr)
        return 2
    try:
        text = Path(args.plan).read_text(encoding="utf-8")
    except OSError as e:
        print(f"FAIL {args.plan}", file=sys.stderr)
        print(f"  - cannot read plan: {e}", file=sys.stderr)
        return 2
    try:
        contract = extract_contract(text)
    except ContractParseError as e:
        print(f"FAIL {args.plan}")
        print(f"  - {e}")
        return 1
    errs = validate_contract(contract)
    if not errs:
        print(f"OK {args.plan}")
        return 0
    print(f"FAIL {args.plan}")
    for err in errs:
        print(f"  - {err}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
