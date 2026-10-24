#!/usr/bin/env python3
"""tools/config_validator.py — workspace + environment declarative-layer schema.

The declarative layer is two TOML shapes, frozen in v0.1
(architecture.md §Configuration): one `workspace.toml` per control home
(identity, repo bindings, factory pin, policy, identity) and one
`environments/<name>.toml` per execution environment (runtime, dispatch,
state paths, tier→model table, credential references, limits). This module
is the machine-readable form of those tables; the doc + module are the same
contract — keep them consistent. `kind` is classified by file basename:
`workspace.toml` → workspace schema, anything else → environment schema.

Refusal classes (stable error prefixes — tests match them):

  unknown key:                  any key outside the frozen tables
  bad type: <path> expected …   wrong TOML type (bools are never ints)
  malformed binding:            repo id/role/url/path, duplicate ids, >1 primary
  malformed pin:                pin matches neither tag nor hex grammar
  unresolvable pin:             concrete pin not found in the factory repo
  pin resolution required:      concrete pin validated without --repo
  deferred placeholder not resolved:  an unresolved "<…>" under --strict
  unknown gate profile:         policy.gates outside the v0.1 set {default}
  inline secret:                credential value not an env:/file: reference
  malformed TOML:               tomllib-level syntax error

Rules enforced:

  * Deferred placeholders: a string value matching ^<.*>$ stands for a
    value the operator substitutes at onboarding (templates ship with
    them). Default mode accepts them; --strict refuses each with its
    dotted path. `--repo <path>` is REQUIRED for a concrete (non-placeholder)
    pin — hex pins resolve with `git cat-file -e <sha>^{commit}`, tags with
    `git rev-parse --verify refs/tags/<tag>` against the factory repo.
    Deferred pins never resolve.
  * Pin grammar: kit version tag `^v?[0-9]+(\\.[0-9]+)*([-+][A-Za-z0-9.-]+)?$`
    or a 7–40 lowercase hex commit. Tag grammar is checked BEFORE hex, so a
    digit-only string is treated as a tag.
  * Inline secrets are refused twice over: `credentials.refs` entries must
    be `env:NAME` / `file:path` references (or deferred); and any key whose
    FINAL segment is secret-shaped (`api_key`, `access_key`, `secret`,
    `token`, `password`, `passwd`, `private_key`, `client_secret`, or ending
    in `_token` / `_secret` — case-insensitive) must hold a reference or a
    deferred placeholder, never a literal. Such a key is usually ALSO an
    unknown key — both errors are reported.
  * Fail closed on approvals: `identity.approvers` EMPTY (or absent) means
    human-approved actions refuse — controller-side (S2); the validator
    only enforces that entries are non-empty GitHub logins.
  * Unknown keys are refused everywhere (nested paths dotted, array items
    `repos[<i>].<key>`); v0.1 gate profile set = {default}.

Stdlib only (`tomllib`, `subprocess` for git). Python >= 3.11.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- grammar ----

WS_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")          # workspace name, ≤64
ENV_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")        # runtime, mechanism
REPO_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
TAG_RE = re.compile(r"^v?[0-9]+(\.[0-9]+)*([-+][A-Za-z0-9.-]+)?$")
HEX_RE = re.compile(r"^[0-9a-f]{7,40}$")
URL_RE = re.compile(r"^[a-z0-9.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9-]+/projects/\d+$")
WALL_RE = re.compile(r"^\d+[smhd]$")
REF_RE = re.compile(r"^(env:[A-Za-z_][A-Za-z0-9_]*|file:[^\s]+)$")
DEFERRED_RE = re.compile(r"^<.*>$")
SECRET_KEY_RE = re.compile(
    r"(?i)^(api[_-]?key|access[_-]?key|secret|token|password|passwd|"
    r"private[_-]?key|client[_-]?secret)$"
)

CONTROL_HOMES = ("dedicated", "in-repo")
REPO_ROLES = ("primary", "member")
USAGE_MODES = ("harvest", "manual")
GATE_PROFILES = ("default",)          # frozen v0.1 set
MODEL_TIERS = ("T1", "T2", "T3")

WORKSPACE_KEYS = frozenset({
    "workspace", "control_home", "default_branch",
    "factory", "repos", "board", "policy", "identity",
})
ENVIRONMENT_KEYS = frozenset({
    "runtime", "dispatch", "state", "models", "credentials", "limits",
})


# ----------------------------------------------------------------- helpers ---

def is_deferred(value: Any) -> bool:
    """True when a string is an unresolved '<…>' template placeholder."""
    return isinstance(value, str) and DEFERRED_RE.fullmatch(value) is not None


def type_name(value: Any) -> str:
    """TOML type name for error messages (bool is never int)."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "table"
    return type(value).__name__


def bad_type(path: str, expected: str, value: Any) -> str:
    return f"bad type: {path} expected {expected}, got {type_name(value)}"


def _skip_placeholder(path: str, value: Any) -> bool:
    """True when a deferred placeholder stands in for the value: constraint
    checks are skipped (template mode); --strict reports it in a dedicated
    walk instead."""
    return is_deferred(value)


def pin_kind(value: Any) -> str:
    """Classify a factory pin: 'deferred' | 'tag' | 'hex' | 'malformed'."""
    if not isinstance(value, str):
        return "malformed"
    if is_deferred(value):
        return "deferred"
    if TAG_RE.fullmatch(value):
        return "tag"
    if HEX_RE.fullmatch(value):
        return "hex"
    return "malformed"


def is_secret_key(name: str) -> bool:
    """True when a key's FINAL segment is secret-shaped (case-insensitive)."""
    return (SECRET_KEY_RE.match(name) is not None
            or name.lower().endswith(("_token", "_secret")))


# -------------------------------------------------------------- workspace ----

def _check_workspace_sections(doc: dict, errs: list[str]) -> None:
    """Unknown-key checks for every nested table."""
    for key in doc:
        if key not in WORKSPACE_KEYS:
            errs.append(f"unknown key: {key}")
    for table, known in (("factory", ("pin",)), ("board", ("project_id",)),
                         ("policy", ("gates",)), ("identity", ("approvers",))):
        if isinstance(doc.get(table), dict):
            for key in doc[table]:
                if key not in known:
                    errs.append(f"unknown key: {table}.{key}")


def _check_workspace_repos(doc: dict, errs: list[str]) -> None:
    repos = doc.get("repos")
    if not isinstance(repos, list):
        return  # type error already reported
    ids: set[str] = set()
    primaries = 0
    for i, item in enumerate(repos):
        if not isinstance(item, dict):
            errs.append(bad_type(f"repos[{i}]", "table", item))
            continue
        for key in item:
            if key not in ("id", "role", "url", "path"):
                errs.append(f"unknown key: repos[{i}].{key}")
        # id
        rid = item.get("id")
        if "id" not in item:
            errs.append(f"missing required key: repos[{i}].id")
        elif not isinstance(rid, str):
            errs.append(bad_type(f"repos[{i}].id", "str", rid))
        elif not REPO_ID_RE.fullmatch(rid):
            errs.append(f"malformed binding: repos[{i}].id must be a "
                        "lowercase slug (^[a-z][a-z0-9-]*$)")
        elif rid in ids:
            errs.append(f"malformed binding: duplicate repo id {rid!r}")
        else:
            ids.add(rid)
        # role
        role = item.get("role")
        if "role" not in item:
            errs.append(f"missing required key: repos[{i}].role")
        elif not isinstance(role, str):
            errs.append(bad_type(f"repos[{i}].role", "str", role))
        elif role not in REPO_ROLES:
            errs.append(f"malformed binding: repos[{i}].role must be "
                        f"primary|member (got {role!r})")
        elif role == "primary":
            primaries += 1
        # url
        url = item.get("url")
        if "url" not in item:
            errs.append(f"missing required key: repos[{i}].url")
        elif not isinstance(url, str):
            errs.append(bad_type(f"repos[{i}].url", "str", url))
        elif not URL_RE.fullmatch(url):
            errs.append(f"malformed binding: repos[{i}].url must be "
                        "host/owner/name — never a URL scheme")
        # path
        if "path" in item:
            path = item["path"]
            if not isinstance(path, str):
                errs.append(bad_type(f"repos[{i}].path", "str", path))
            elif not _skip_placeholder(f"repos[{i}].path", path) and (
                    not path or path.startswith("/")
                    or ".." in path.split("/")):
                errs.append(f"malformed binding: repos[{i}].path must be a "
                            "relative path with no '..'")
    if primaries > 1:
        errs.append(f"malformed binding: at most one primary repo "
                    f"(found {primaries})")


def validate_workspace(doc: dict, *, strict: bool = False) -> list[str]:
    """Return schema errors for a workspace document ([] = valid).

    Excludes factory-pin RESOLUTION (needs the factory repo): the CLI
    appends `pin resolution required` / `unresolvable pin` itself.
    """
    errs: list[str] = []
    _check_workspace_sections(doc, errs)

    # workspace (slug, ≤64)
    if "workspace" not in doc:
        errs.append("missing required key: workspace")
    elif not isinstance(doc["workspace"], str):
        errs.append(bad_type("workspace", "str", doc["workspace"]))
    elif not _skip_placeholder("workspace", doc["workspace"]) and (
            not WS_SLUG_RE.fullmatch(doc["workspace"])
            or len(doc["workspace"]) > 64):
        errs.append("bad value: workspace must match ^[a-z0-9][a-z0-9-]*$ "
                    "(≤64 chars)")

    # control_home
    if "control_home" not in doc:
        errs.append("missing required key: control_home")
    elif not isinstance(doc["control_home"], str):
        errs.append(bad_type("control_home", "str", doc["control_home"]))
    elif not _skip_placeholder("control_home", doc["control_home"]) and \
            doc["control_home"] not in CONTROL_HOMES:
        errs.append("bad value: control_home must be dedicated|in-repo "
                    f"(got {doc['control_home']!r})")

    # default_branch (default "main" applies at consumption)
    if "default_branch" in doc:
        branch = doc["default_branch"]
        if not isinstance(branch, str):
            errs.append(bad_type("default_branch", "str", branch))
        elif not _skip_placeholder("default_branch", branch) and \
                not BRANCH_RE.fullmatch(branch):
            errs.append("bad value: default_branch must match "
                        "^[A-Za-z0-9][A-Za-z0-9._/-]*$")

    # factory + pin
    factory = doc.get("factory")
    if "factory" not in doc:
        errs.append("missing required table: factory")
    elif not isinstance(factory, dict):
        errs.append(bad_type("factory", "table", factory))
    else:
        pin = factory.get("pin")
        if "pin" not in factory:
            errs.append("missing required key: factory.pin")
        elif not isinstance(pin, str):
            errs.append(bad_type("factory.pin", "str", pin))
        elif not _skip_placeholder("factory.pin", pin) and \
                pin_kind(pin) == "malformed":
            errs.append(f"malformed pin: {pin}")

    # repos
    if "repos" not in doc:
        errs.append("missing required table: repos")
    elif not isinstance(doc["repos"], list):
        errs.append(bad_type("repos", "array of tables", doc["repos"]))
    elif not doc["repos"]:
        errs.append("bad value: repos must declare at least one repo")
    else:
        _check_workspace_repos(doc, errs)

    # board
    board = doc.get("board")
    if isinstance(board, dict) and "project_id" in board:
        pid = board["project_id"]
        if not isinstance(pid, str):
            errs.append(bad_type("board.project_id", "str", pid))
        elif not _skip_placeholder("board.project_id", pid) and \
                not PROJECT_ID_RE.fullmatch(pid):
            errs.append("bad value: board.project_id must be "
                        "<org>/projects/<number>")

    # policy
    policy = doc.get("policy")
    if isinstance(policy, dict) and "gates" in policy:
        gates = policy["gates"]
        if not isinstance(gates, str):
            errs.append(bad_type("policy.gates", "str", gates))
        elif not _skip_placeholder("policy.gates", gates) and \
                gates not in GATE_PROFILES:
            errs.append(f"unknown gate profile: {gates}")

    # identity (fail closed: an empty allowlist means human-approved
    # actions refuse — controller-side, S2)
    identity = doc.get("identity")
    if isinstance(identity, dict) and "approvers" in identity:
        approvers = identity["approvers"]
        if not isinstance(approvers, list):
            errs.append(bad_type("identity.approvers", "array of str",
                                 approvers))
        else:
            for i, entry in enumerate(approvers):
                if not isinstance(entry, str):
                    errs.append(bad_type(f"identity.approvers[{i}]", "str",
                                         entry))
                elif not _skip_placeholder(f"identity.approvers[{i}]",
                                           entry) and not entry.strip():
                    errs.append(f"bad value: identity.approvers[{i}] must be "
                                "a non-empty GitHub login")
    return errs


# -------------------------------------------------------------- environment --

def _check_environment_sections(doc: dict, errs: list[str]) -> None:
    for key in doc:
        if key not in ENVIRONMENT_KEYS:
            errs.append(f"unknown key: {key}")
    for table, known in (("dispatch", ("mechanism", "isolation")),
                         ("state", ("agent_home", "transcripts", "usage")),
                         ("models", MODEL_TIERS),
                         ("credentials", ("refs",)),
                         ("limits", ("wall", "runs", "budget"))):
        if isinstance(doc.get(table), dict):
            for key in doc[table]:
                if key not in known:
                    errs.append(f"unknown key: {table}.{key}")


def _slug_string(errs: list[str], doc: dict, key: str) -> None:
    """Required slug-string field (runtime, dispatch.mechanism, …)."""
    value = doc.get(key)
    if key not in doc:
        errs.append(f"missing required key: {key}")
    elif not isinstance(value, str):
        errs.append(bad_type(key, "str", value))
    elif not _skip_placeholder(key, value) and not ENV_SLUG_RE.fullmatch(value):
        errs.append(f"bad value: {key} must match ^[a-z0-9][a-z0-9_-]*$")


def validate_environment(doc: dict, *, strict: bool = False) -> list[str]:
    """Return schema errors for an environment document ([] = valid)."""
    errs: list[str] = []
    _check_environment_sections(doc, errs)

    _slug_string(errs, doc, "runtime")

    # dispatch
    dispatch = doc.get("dispatch")
    if "dispatch" not in doc:
        errs.append("missing required table: dispatch")
    elif not isinstance(dispatch, dict):
        errs.append(bad_type("dispatch", "table", dispatch))
    else:
        _slug_string(errs, dispatch, "mechanism")
        if "isolation" in dispatch:
            isolation = dispatch["isolation"]
            if not isinstance(isolation, str):
                errs.append(bad_type("dispatch.isolation", "str", isolation))
            elif not _skip_placeholder("dispatch.isolation", isolation) and \
                    not ENV_SLUG_RE.fullmatch(isolation):
                errs.append("bad value: dispatch.isolation must match "
                            "^[a-z0-9][a-z0-9_-]*$")

    # state
    state = doc.get("state")
    if isinstance(state, dict):
        for key in ("agent_home", "transcripts"):
            if key in state:
                value = state[key]
                if not isinstance(value, str):
                    errs.append(bad_type(f"state.{key}", "str", value))
                elif not _skip_placeholder(f"state.{key}", value) and \
                        not value.strip():
                    errs.append(f"bad value: state.{key} must be a "
                                "non-empty path")
        if "usage" in state:
            usage = state["usage"]
            if not isinstance(usage, str):
                errs.append(bad_type("state.usage", "str", usage))
            elif not _skip_placeholder("state.usage", usage) and \
                    usage not in USAGE_MODES:
                errs.append(f"bad value: state.usage must be harvest|manual "
                            f"(got {usage!r})")

    # models (keys ⊆ {T1,T2,T3}; unknown tiers are unknown keys)
    models = doc.get("models")
    if "models" not in doc:
        errs.append("missing required table: models")
    elif not isinstance(models, dict):
        errs.append(bad_type("models", "table", models))
    else:
        if not models:
            errs.append("bad value: models must declare at least one tier "
                        "(T1, T2, T3)")
        for tier, model in models.items():
            if not isinstance(model, str):
                errs.append(bad_type(f"models.{tier}", "str", model))
            elif not _skip_placeholder(f"models.{tier}", model) and \
                    not model.strip():
                errs.append(f"bad value: models.{tier} must be a non-empty "
                            "model id")

    # credentials (references only)
    creds = doc.get("credentials")
    if isinstance(creds, dict) and "refs" in creds:
        refs = creds["refs"]
        if not isinstance(refs, list):
            errs.append(bad_type("credentials.refs", "array of str", refs))
        else:
            for entry in refs:
                if not isinstance(entry, str):
                    errs.append(bad_type("credentials.refs", "array of str",
                                         refs))
                    break
                if not _skip_placeholder("credentials.refs", entry) and \
                        not REF_RE.fullmatch(entry):
                    errs.append("inline secret: credentials.refs must "
                                "reference env:NAME or file:path — never a "
                                "literal")

    # limits
    limits = doc.get("limits")
    if isinstance(limits, dict):
        if "wall" in limits:
            wall = limits["wall"]
            if not isinstance(wall, str):
                errs.append(bad_type("limits.wall", "str", wall))
            elif not _skip_placeholder("limits.wall", wall) and \
                    not WALL_RE.fullmatch(wall):
                errs.append('bad value: limits.wall must match ^\\d+[smhd]$ '
                            '(e.g. "2h")')
        if "runs" in limits:
            runs = limits["runs"]
            if not isinstance(runs, int) or isinstance(runs, bool):
                errs.append(bad_type("limits.runs", "int", runs))
            elif runs < 1:
                errs.append("bad value: limits.runs must be ≥ 1")
        if "budget" in limits:
            budget = limits["budget"]
            if not isinstance(budget, str):
                errs.append(bad_type("limits.budget", "str", budget))
            elif not _skip_placeholder("limits.budget", budget) and \
                    not budget.strip():
                errs.append("bad value: limits.budget must be a non-empty "
                            "string")
    return errs


# ----------------------------------------------------------------- secret ----

def _walk_secret_keys(value: Any, path: str, errs: list[str]) -> None:
    """Inline-secret rule 2: secret-shaped keys must hold a reference."""
    if isinstance(value, dict):
        for key, sub in value.items():
            sub_path = f"{path}.{key}" if path else str(key)
            if sub_path == "credentials.refs":
                continue  # entries checked by the refs rule
            if is_secret_key(key):
                if not (isinstance(sub, str)
                        and (REF_RE.fullmatch(sub) or is_deferred(sub))):
                    errs.append(f"inline secret: {sub_path} must be a "
                                "reference, never a literal")
            _walk_secret_keys(sub, sub_path, errs)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _walk_secret_keys(item, f"{path}[{i}]", errs)


def _walk_deferred(value: Any, path: str, errs: list[str]) -> None:
    """Strict mode: every unresolved '<…>' placeholder, anywhere."""
    if isinstance(value, dict):
        for key, sub in value.items():
            sub_path = f"{path}.{key}" if path else str(key)
            if is_deferred(sub):
                errs.append(f"deferred placeholder not resolved: {sub_path}")
            _walk_deferred(sub, sub_path, errs)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _walk_deferred(item, f"{path}[{i}]", errs)


# --------------------------------------------------------- pin resolution ---

def check_pin(pin: str, repo: str) -> str | None:
    """Verify a concrete pin against the factory repo (None = resolves)."""
    if pin_kind(pin) == "hex":
        cmd = ["git", "-C", repo, "cat-file", "-e", f"{pin}^{{commit}}"]
    else:
        cmd = ["git", "-C", repo, "rev-parse", "--verify", f"refs/tags/{pin}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return f"unresolvable pin: {pin} (not found in {repo})"
    if result.returncode != 0:
        return f"unresolvable pin: {pin} (not found in {repo})"
    return None


def pin_errors(doc: dict, repo: str | None) -> list[str]:
    """Resolution requirements for a workspace pin (CLI layer)."""
    factory = doc.get("factory")
    if not isinstance(factory, dict):
        return []
    pin = factory.get("pin")
    if not isinstance(pin, str) or pin_kind(pin) not in ("tag", "hex"):
        return []  # deferred (never resolves) or malformed (already reported)
    if not repo:
        return ["pin resolution required: no factory repo provided (--repo)"]
    err = check_pin(pin, repo)
    return [err] if err else []


# ------------------------------------------------------------- validation ---

def validate_document(doc: dict, kind: str, *,
                      strict: bool = False) -> list[str]:
    """Return schema + secret + deferred errors for a parsed document."""
    if kind == "workspace":
        errs = validate_workspace(doc, strict=strict)
    else:
        errs = validate_environment(doc, strict=strict)
    _walk_secret_keys(doc, "", errs)
    if strict:
        _walk_deferred(doc, "", errs)
    errs.sort()
    return errs


def validate_path(path: str | Path, *, strict: bool = False,
                  repo: str | None = None) -> tuple[list[str], bool]:
    """Validate one file: (errors, parsed_ok). parse error → single error."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ([f"unreadable file: {exc}"], False)
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return ([f"malformed TOML: {exc}"], False)
    kind = "workspace" if path.name == "workspace.toml" else "environment"
    errs = validate_document(doc, kind, strict=strict)
    if kind == "workspace":
        errs.extend(pin_errors(doc, repo))
        errs.sort()
    return (errs, True)


# --------------------------------------------------------------------- CLI ----

def main(argv: list[str] | None = None) -> int:
    """Validate files; exit 0 all OK, 1 any FAIL, 2 usage (no files)."""
    parser = argparse.ArgumentParser(
        prog="config_validator",
        description="Validate workspace + environment TOML against the "
                    "frozen v0.1 declarative-layer schema.")
    parser.add_argument("files", nargs="*", metavar="file")
    parser.add_argument("--strict", action="store_true",
                        help="refuse unresolved '<...>' placeholders "
                             "(onboarding mode)")
    parser.add_argument("--repo", metavar="path",
                        help="factory repo for pin resolution (required when "
                             "the workspace pin is concrete)")
    args = parser.parse_args(argv)
    if not args.files:
        parser.print_usage(sys.stderr)
        return 2
    all_ok = True
    for path in args.files:
        errs, parsed = validate_path(path, strict=args.strict, repo=args.repo)
        if not errs:
            print(f"OK {path}")
        else:
            all_ok = False
            print(f"FAIL {path}")
            for err in errs:
                print(f"  - {err}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
