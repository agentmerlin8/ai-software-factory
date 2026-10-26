#!/usr/bin/env python3
"""onboarding/forge_probe.py — read-only GitHub forge probing for onboarding.

The ONE injectable forge seam: every read probe (auth scopes, repo view,
branch ruleset, labels, issue form, project fields) and the single
sanctioned write op (label ensure) go through ONE callable transport —
`transport(op: str, params: dict) -> dict`. The default transport maps
each op to `gh` argv invocations (argv LISTS, never a shell) and parses
the result into the op's return dict; tests substitute an in-memory
recorder — no network, no gh. Conventions mirror tools/sync_board.py
(ONE transport seam, pure argv builders, named fail-closed refusals);
this module deliberately does NOT import it.

Probes (op name == probe name; all read-only unless noted):
  * auth_scopes()      -> {login, scopes, missing}
  * repo_view(repo)    -> {name_with_owner, default_branch, visibility}
  * ruleset(repo, branch)
                       -> {target, found, enforcement, bypass, read[, error]}
  * labels(repo, required)
                       -> {present, missing}
  * issue_form(repo, filename)
                       -> {present, path}
  * project_fields(locator)
                       -> {node_id, fields, found[, error]}
  * label_ensure(repo, name, color, description)   [THE single write op]
                       -> {argv, ok}

Required classic token scopes (frozen): REQUIRED_SCOPES = ("repo",
"project", "read:org"). Fine-grained PAT mapping (C-2): Contents:read +
Issues:read + Pull requests:read + Metadata:read <- `repo`; Projects:read
(Projects v2) <- `project`; Administration:read <- `repo` (ruleset reads
live under the administration surface). A fine-grained token must grant
at least these; the probe reports classic scopes and the check layer
decides severity.

Ruleset reads: plan-gated/unreadable (HTTP 403 etc.) is NOT a probe
exception — the probe returns read="refused" with the detail in an
`error` key; the CHECK (not the probe) decides severity ("staged, not
enforcing" / "unverified" wording lives downstream). No rulesets at all
-> found=False.

Project locators: `<org>/projects/<n>` only (grammar mirrors
sync_board's PROJECT_ID_RE); the transport resolves the locator to the
OPAQUE ProjectV2 node id via organization(login:).projectV2(number:),
then reads fields(first: 100). An unresolvable locator -> found=False
with a named `error` key, never an exception; bad grammar -> named
ProbeError refusal.

Errors: ProbeError(ValueError, rc=1 semantics) with named lowercase
messages (`probe failed: repo view — <detail>`); FixtureError(ProbeError)
for fixture dataset problems. Malformed input never produces a raw
traceback — every failure path is named.

Fixture dataset schema (frozen; used by Forge.from_fixture):
  A JSON object whose top-level keys map to ops: "auth_scopes",
  "repo_view", "ruleset", "labels", "issue_form", "project" (and
  optionally "label_ensure" — a recorder-store; reads never need it).
  Each value is either the op's transport-level return-shape dict, or
  {"error": "<named detail>"} which makes the fixture transport raise
  ProbeError with that detail. A call for an op with no entry raises
  ProbeError("fixture has no entry for op: <op>"). Malformed JSON or a
  non-object dataset raises FixtureError at load time.

Stdlib only. Python >= 3.11. Deterministic: no wall-clock, no randomness.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

# Frozen required classic-scope set (see docstring for the C-2 mapping).
REQUIRED_SCOPES = ("repo", "project", "read:org")

# Locator grammar — mirrors sync_board's PROJECT_ID_RE (not imported).
PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9-]+/projects/\d+$")

_TOKEN_SCOPES_RE = re.compile(r"Token scopes:\s*(.*)$")
_LOGIN_RE = re.compile(r"Logged in to github\.com account\s+([^\s(]+)")


class ProbeError(ValueError):
    """Named runtime refusal (fail-closed; nothing written). rc 1."""
    rc = 1


class FixtureError(ProbeError):
    """Named fixture-dataset refusal (missing/malformed entries)."""
    rc = 1


# ---------------------------------------------------------------------------
# pure builders (deterministic argv lists — tests assert them verbatim)
# ---------------------------------------------------------------------------

def build_label_ensure_argv(repo: str, name: str, color: str,
                            description: str) -> list[str]:
    """The EXACT argv for the single sanctioned write op (label ensure).
    Pure: no I/O, no shell — `gh label create ... --force` upserts."""
    return ["gh", "label", "create", name,
            "--color", color,
            "--description", description,
            "--repo", repo,
            "--force"]


# ---------------------------------------------------------------------------
# default transport — maps ops to gh argv invocations (no shell anywhere)
# ---------------------------------------------------------------------------

def _run(argv: list[str], label: str) -> subprocess.CompletedProcess:
    """Run one gh argv list; non-zero rc is a named ProbeError."""
    try:
        out = subprocess.run(argv, capture_output=True, text=True)
    except OSError as exc:
        raise ProbeError(f"probe failed: {label} — cannot run gh: {exc}")
    if out.returncode != 0:
        detail = (out.stderr or out.stdout or "").strip()
        raise ProbeError(f"probe failed: {label} — {detail}")
    return out


def _json_or_refuse(text: str, label: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProbeError(
            f"probe failed: {label} — cannot parse gh output: {exc}")


def gh_probe_transport(op: str, params: dict) -> dict:
    """The default gh-backed transport. Every op is an argv LIST handed
    to subprocess.run — never a shell string."""
    if op == "auth_scopes":
        argv = ["gh", "auth", "status"]
        try:
            out = subprocess.run(argv, capture_output=True, text=True)
        except OSError as exc:
            raise ProbeError(
                f"probe failed: auth scopes — cannot run gh: {exc}")
        if out.returncode != 0:
            detail = (out.stderr or out.stdout or "").strip()
            raise ProbeError(f"probe failed: auth scopes — {detail}")
        return {"stdout": out.stdout, "stderr": out.stderr}
    if op == "repo_view":
        repo = params["repo"]
        out = _run(["gh", "repo", "view", repo, "--json",
                    "nameWithOwner,defaultBranchRef,visibility"],
                   "repo view")
        return _json_or_refuse(out.stdout, "repo view")
    if op == "ruleset":
        repo = params["repo"]
        listing = _json_or_refuse(
            _run(["gh", "api", f"repos/{repo}/rulesets"],
                 "ruleset").stdout,
            "ruleset")
        rulesets = []
        for entry in listing if isinstance(listing, list) else []:
            rid = entry.get("id")
            detail = _json_or_refuse(
                _run(["gh", "api", f"repos/{repo}/rulesets/{rid}"],
                     "ruleset").stdout,
                "ruleset")
            merged = dict(entry)
            merged["bypass_actors"] = detail.get("bypass_actors", [])
            merged["enforcement"] = detail.get(
                "enforcement", entry.get("enforcement"))
            rulesets.append(merged)
        return {"rulesets": rulesets}
    if op == "labels":
        repo = params["repo"]
        out = _run(["gh", "label", "list", "-R", repo, "--limit", "200",
                    "--json", "name,color,description"], "labels")
        return {"labels": _json_or_refuse(out.stdout, "labels")}
    if op == "issue_form":
        repo, filename = params["repo"], params["filename"]
        path = f".github/ISSUE_TEMPLATE/{filename}"
        argv = ["gh", "api", f"repos/{repo}/contents/{path}"]
        try:
            out = subprocess.run(argv, capture_output=True, text=True)
        except OSError as exc:
            raise ProbeError(
                f"probe failed: issue form — cannot run gh: {exc}")
        if out.returncode == 0:
            data = _json_or_refuse(out.stdout, "issue form")
            return {"present": True,
                    "path": data.get("path") if isinstance(data, dict)
                    else path}
        detail = (out.stderr or "").strip()
        if "404" in detail:  # absent template is a finding, not a failure
            return {"present": False, "path": None}
        raise ProbeError(f"probe failed: issue form — {detail}")
    if op == "project":
        locator = params["locator"]
        m = PROJECT_ID_RE.fullmatch(locator)
        if not m:
            raise ProbeError(
                f"refused: bad project locator '{locator}' — must match "
                "<org>/projects/<n>")
        org, number = m.group(1), int(m.group(2))
        resolve_q = (
            "query {\n"
            f'  organization(login: "{org}") {{\n'
            f"    projectV2(number: {number}) {{ id }}\n"
            "  }\n"
            "}"
        )
        out = _run(["gh", "api", "graphql", "-f", f"query={resolve_q}"],
                   "project fields")
        data = _json_or_refuse(out.stdout, "project fields")
        node = ((data.get("data") or {}).get("organization")
                or {}).get("projectV2") or {}
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            return {"node_id": None, "fields": {"nodes": []}}
        fields_q = (
            "query {\n"
            f'  node(id: "{node_id}") {{\n'
            "    ... on ProjectV2 {\n"
            "      fields(first: 100) { nodes {\n"
            "        __typename\n"
            "        ... on ProjectV2Field { id name }\n"
            "        ... on ProjectV2SingleSelectField {\n"
            "          id name options { id name } }\n"
            "      } }\n"
            "    }\n"
            "  }\n"
            "}"
        )
        out = _run(["gh", "api", "graphql", "-f", f"query={fields_q}"],
                   "project fields")
        data = _json_or_refuse(out.stdout, "project fields")
        fields = (((data.get("data") or {}).get("node") or {})
                  .get("fields") or {"nodes": []})
        return {"node_id": node_id, "fields": fields}
    if op == "label_ensure":
        argv = params["argv"]
        try:
            out = subprocess.run(argv, capture_output=True, text=True)
        except OSError as exc:
            raise ProbeError(f"probe failed: label ensure — {exc}")
        return {"ok": out.returncode == 0,
                "stderr": out.stderr.strip() or None}
    raise ProbeError(f"probe failed: unknown transport op: {op}")


# ---------------------------------------------------------------------------
# fixture transport — dataset-backed, for T2/T4 fixture runs
# ---------------------------------------------------------------------------

_FIXTURE_KEYS = ("auth_scopes", "repo_view", "ruleset", "labels",
                 "issue_form", "project", "label_ensure")


def build_fixture_transport(path: str | Path) -> Callable[[str, dict], dict]:
    """Load a fixture dataset (§ docstring schema) and return a transport
    backed by it. Malformed JSON / non-object dataset / non-object entry
    -> FixtureError; missing op entry -> ProbeError at call time; an
    {"error": ...} entry raises ProbeError with that named detail."""
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise FixtureError(f"fixture dataset unreadable: {p} — {exc}")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise FixtureError(f"fixture dataset malformed: {p} — {exc}")
    if not isinstance(data, dict):
        raise FixtureError(
            f"fixture dataset malformed: {p} — top level must be an object")
    for key, value in data.items():
        if not isinstance(value, dict):
            raise FixtureError(
                f"fixture dataset malformed: {p} — entry for '{key}' "
                "must be an object")

    def transport(op: str, params: dict) -> dict:
        if op not in _FIXTURE_KEYS:
            raise ProbeError(f"fixture has no entry for op: {op}")
        if op not in data:
            raise ProbeError(f"fixture has no entry for op: {op}")
        entry = data[op]
        if "error" in entry:
            raise ProbeError(str(entry["error"]))
        return entry

    return transport


# ---------------------------------------------------------------------------
# Forge — probes over the ONE transport seam
# ---------------------------------------------------------------------------

class Forge:
    """Read-only forge probes + the single sanctioned write op.

    Constructed as Forge(transport=None) — the default is the real
    gh-backed transport; tests pass a recorder. Forge.from_fixture(path)
    builds a dataset-backed transport (§ fixture schema)."""

    def __init__(self, transport: Optional[Callable[[str, dict], dict]]
                 = None):
        self._t = transport or gh_probe_transport  # the ONE seam

    @classmethod
    def from_fixture(cls, path: str | Path) -> "Forge":
        return cls(build_fixture_transport(path))

    def _call(self, op: str, label: str, params: dict) -> dict:
        """Invoke the transport; never leak a raw traceback — unexpected
        exceptions become named ProbeErrors."""
        try:
            result = self._t(op, params)
        except ProbeError:
            raise
        except Exception as exc:  # fail closed, named
            raise ProbeError(f"probe failed: {label} — {exc}") from exc
        if not isinstance(result, dict):
            raise ProbeError(
                f"probe failed: {label} — transport returned a "
                f"non-object for op '{op}'")
        return result

    # -- read probes --------------------------------------------------------

    def auth_scopes(self) -> dict:
        """{login, scopes, missing} from `gh auth status`. Unparseable
        output (no Token scopes line) is a named ProbeError."""
        data = self._call("auth_scopes", "auth scopes", {})
        text = f"{data.get('stdout', '')}\n{data.get('stderr', '')}"
        scopes: Optional[list[str]] = None
        for line in text.splitlines():
            m = _TOKEN_SCOPES_RE.search(line)
            if m:
                scopes = re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))
                break
        if scopes is None:
            raise ProbeError(
                "probe failed: auth scopes — cannot determine token scopes")
        login_m = _LOGIN_RE.search(text)
        missing = sorted(set(REQUIRED_SCOPES) - set(scopes))
        return {"login": login_m.group(1) if login_m else None,
                "scopes": scopes,
                "missing": missing}

    def repo_view(self, repo: str) -> dict:
        """{name_with_owner, default_branch, visibility}. Not-found or
        forbidden is a named ProbeError (the transport names it)."""
        data = self._call("repo_view", "repo view", {"repo": repo})
        if "error" in data:
            raise ProbeError(f"probe failed: repo view — {data['error']}")
        name = data.get("nameWithOwner")
        if not isinstance(name, str) or not name:
            raise ProbeError(
                "probe failed: repo view — cannot parse repository payload")
        branch_ref = data.get("defaultBranchRef") or {}
        branch = branch_ref.get("name") if isinstance(branch_ref, dict) \
            else None
        return {"name_with_owner": name,
                "default_branch": branch,
                "visibility": data.get("visibility")}

    def ruleset(self, repo: str, branch: str) -> dict:
        """{target, found, enforcement, bypass, read[, error]} for the
        branch ruleset. Refused reads (plan-gated 403 etc.) come back as
        read='refused' + error — never an exception; no rulesets at all
        -> found=False."""
        try:
            data = self._call("ruleset", "ruleset",
                              {"repo": repo, "branch": branch})
        except ProbeError as exc:
            return {"target": "branch", "found": False,
                    "enforcement": None, "bypass": [],
                    "read": "refused", "error": str(exc)}
        want = f"refs/heads/{branch}"
        chosen = None
        for rs in data.get("rulesets", []):
            include = (((rs.get("conditions") or {}).get("ref_name")
                        or {}).get("include") or [])
            if want in include or "~DEFAULT_BRANCH" in include:
                chosen = rs
                break
        if chosen is None:
            return {"target": "branch", "found": False,
                    "enforcement": None, "bypass": [], "read": "ok"}
        bypass = [{"actor_type": a.get("actor_type"),
                   "mode": a.get("mode") or a.get("bypass_mode"),
                   "actor_id": a.get("actor_id")}
                  for a in chosen.get("bypass_actors", [])]
        return {"target": chosen.get("target") or "branch",
                "found": True,
                "enforcement": chosen.get("enforcement"),
                "bypass": bypass,
                "read": "ok"}

    def labels(self, repo: str, required: list[str]) -> dict:
        """{present, missing} — present maps name -> {color, description};
        missing = required names not present, sorted."""
        data = self._call("labels", "labels", {"repo": repo})
        present: dict[str, dict] = {}
        for label in data.get("labels", []):
            name = label.get("name")
            if not isinstance(name, str) or not name:
                continue
            present[name] = {"color": label.get("color"),
                             "description": label.get("description")}
        missing = sorted(n for n in required if n not in present)
        return {"present": present, "missing": missing}

    def issue_form(self, repo: str, filename: str) -> dict:
        """{present, path} for .github/ISSUE_TEMPLATE/<filename>. A 404
        is present=False (not an error); other failures are named."""
        data = self._call("issue_form", "issue form",
                          {"repo": repo, "filename": filename})
        expected = f".github/ISSUE_TEMPLATE/{filename}"
        return {"present": bool(data.get("present")),
                "path": data.get("path") or expected}

    def project_fields(self, locator: str) -> dict:
        """{node_id, fields, found[, error]} for `<org>/projects/<n>`.
        Bad grammar -> named refusal; unresolvable locator -> found=False
        + error key (the CHECK decides, not the probe)."""
        if not PROJECT_ID_RE.fullmatch(locator):
            raise ProbeError(
                f"refused: bad project locator '{locator}' — must match "
                "<org>/projects/<n>")
        try:
            data = self._call("project", "project fields",
                              {"locator": locator})
        except ProbeError as exc:
            return {"node_id": None, "fields": {}, "found": False,
                    "error": str(exc)}
        node_id = data.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            return {"node_id": None, "fields": {}, "found": False,
                    "error": f"project not found: {locator}"}
        fields: dict[str, dict] = {}
        for node in (data.get("fields") or {}).get("nodes", []):
            name = node.get("name")
            if not name:
                continue
            options = None
            if node.get("__typename") == "ProjectV2SingleSelectField":
                options = [o.get("name") for o in node.get("options", [])
                           if o.get("name")]
            fields[name] = {"type": node.get("__typename"),
                            "options": options}
        return {"node_id": node_id, "fields": fields, "found": True}

    # -- the single sanctioned write op -------------------------------------

    def label_ensure(self, repo: str, name: str, color: str,
                     description: str) -> dict:
        """Upsert one label via `gh label create ... --force` (the ONLY
        mutation in this module). argv built by the pure builder; the
        transport receives it verbatim in params."""
        argv = build_label_ensure_argv(repo, name, color, description)
        data = self._call("label_ensure", "label ensure",
                          {"repo": repo, "name": name, "color": color,
                           "description": description, "argv": argv})
        return {"argv": argv, "ok": bool(data.get("ok"))}
