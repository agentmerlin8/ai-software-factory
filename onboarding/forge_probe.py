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
                       -> {target, found, enforcement, bypass, rules,
                           read[, error]}
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

  Per-repo override keys: an op entry may also appear as
  "<op>@<slug>" where <slug> is the last two path segments of the
  call's params["repo"], normalized from BOTH accepted shapes
  (`github.com/fixture-org/app` -> `fixture-org/app`;
  `fixture-org/workspace` -> `fixture-org/workspace`; an https URL's
  scheme/host and a trailing `.git` are stripped first). Lookup order
  at call time: "<op>@<slug>" -> "<op>" -> the existing missing-entry
  refusal. An override key whose base (the part before `@`) is not a
  known op is a FixtureError at load time (a malformed dataset is
  never silently ignored).

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

# Locator grammar — mirrors sync_board's PROJECT_ID_RE (not imported),
# with capture groups so ONE fullmatch parse yields (org, number).
_PROJECT_LOCATOR_RE = re.compile(r"([A-Za-z0-9-]+)/projects/(\d+)")

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

def parse_project_locator(locator: str) -> tuple[str, int]:
    """Parse `<org>/projects/<n>` -> (org, number). ONE parser used by
    both the transport's project branch and Forge.project_fields'
    grammar check; fullmatch strictness, named refusal on bad grammar
    (mirrors tools/sync_board.py's parse semantics)."""
    m = _PROJECT_LOCATOR_RE.fullmatch(locator)
    if not m:
        raise ProbeError(
            f"refused: bad project locator '{locator}' — must match "
            "<org>/projects/<n>")
    return m.group(1), int(m.group(2))


def build_label_ensure_argv(repo: str, name: str, color: str,
                            description: str) -> list[str]:
    """The EXACT argv for the single sanctioned write op (label ensure).
    Pure: no I/O, no shell — `gh label create ... --force` upserts."""
    return ["gh", "label", "create", name,
            "--color", color,
            "--description", description,
            "--repo", repo,
            "--force"]


def rest_repo(repo: str) -> str:
    """Normalize a repo value to the `owner/name` form the REST API path
    segment requires. `gh api repos/<repo>/...` breaks on the workspace
    canonical form `github.com/owner/name` (-> `repos/github.com/owner/
    name/...`), which is what readiness.target_ruleset_problem() passes
    for Mode-2 bindings. A leading host segment (and an https URL's
    scheme/host, a trailing `.git`) is stripped; the last two non-empty
    path segments are kept. `gh repo view` accepts the host-prefixed
    form and is left untouched. Falls back to the value verbatim when
    fewer than two segments are present (the REST call then fails on
    its own terms, named)."""
    if not isinstance(repo, str) or not repo:
        return repo
    slug = fixture_repo_slug(repo)
    return slug if slug else repo


_GLOB_CACHE: dict[str, re.Pattern] = {}


def _ref_pattern_re(pattern: str) -> re.Pattern:
    """Compile ONE GitHub ruleset ref pattern to a FULLMATCH regex
    (compiled patterns cached under the RAW selector — deterministic,
    no wall clock)."""
    rx = _GLOB_CACHE.get(pattern)
    if rx is not None:
        return rx
    raw = pattern
    if not pattern.startswith("refs/"):
        pattern = f"refs/heads/{pattern}"
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")      # any characters, INCLUDING '/'
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")   # any characters EXCEPT '/'
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    rx = re.compile("".join(out))
    _GLOB_CACHE[raw] = rx
    return rx


def ref_selector_matches(selector: str, ref: str) -> bool:
    """GitHub-compatible ref-pattern selector test — FULLMATCH semantics
    over the concrete ref string (the ruleset probe always evaluates
    `refs/heads/<branch>`, § Forge.ruleset).

    Selector semantics (frozen):
      * `~ALL` matches every ref.
      * `~DEFAULT_BRANCH` matches — the probe evaluates the default
        branch by contract, so the sentinel keeps that meaning.
      * Any other selector is a ref glob compiled to a regex and
        FULLMATCHED: `**` matches any characters including `/`, `*`
        matches any characters except `/`, everything else literal.
        A pattern without a `refs/` prefix is a BRANCH pattern —
        `refs/heads/<pattern>` is matched (GitHub's unqualified-name
        rule). fnmatch is never used (its `*` crosses `/`)."""
    if selector == "~ALL" or selector == "~DEFAULT_BRANCH":
        return True
    return _ref_pattern_re(selector).fullmatch(ref) is not None


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
        # The list endpoint returns summaries WITHOUT `conditions`/`rules`
        # and truncates at its default page size, so page
        # `?per_page=100&page=N` to exhaustion first (a short page ends
        # the loop — the same law as the labels branch, F2). Then fetch
        # each detail BY ID: the per-id detail carries them. Merge the
        # COMPLETE detail payload — detail wins for every key it
        # carries, so the branch matcher (Forge.ruleset) sees the real
        # conditions/rules on the live path (fixtures mask this by
        # pre-merging). The repo is normalized to owner/name for the
        # REST path segment (F5).
        repo = rest_repo(params["repo"])
        summaries: list[dict] = []
        page = 1
        while True:
            out = _run(["gh", "api",
                        f"repos/{repo}/rulesets?per_page=100&page={page}"],
                       "ruleset")
            batch = _json_or_refuse(out.stdout, "ruleset")
            if not isinstance(batch, list):
                raise ProbeError(
                    "probe failed: ruleset — cannot parse gh output: "
                    "expected an array of rulesets")
            summaries.extend(entry for entry in batch
                             if isinstance(entry, dict))
            if len(batch) < 100:
                break
            page += 1
        rulesets = []
        for entry in summaries:
            rid = entry.get("id")
            detail = _json_or_refuse(
                _run(["gh", "api", f"repos/{repo}/rulesets/{rid}"],
                     "ruleset").stdout,
                "ruleset")
            merged = dict(entry)
            if isinstance(detail, dict):
                merged.update(detail)  # complete detail payload wins
            rulesets.append(merged)
        return {"rulesets": rulesets}
    if op == "labels":
        # G1: `gh label list --limit 200` truncates on large repos —
        # page the REST labels endpoint to exhaustion instead (short
        # page ends the loop). The repo is normalized to owner/name
        # for the REST path segment (same law as the ruleset branch).
        repo = rest_repo(params["repo"])
        labels: list[dict] = []
        page = 1
        while True:
            out = _run(["gh", "api",
                        f"repos/{repo}/labels?per_page=100&page={page}"],
                       "labels")
            batch = _json_or_refuse(out.stdout, "labels")
            if not isinstance(batch, list):
                raise ProbeError(
                    "probe failed: labels — cannot parse gh output: "
                    "expected an array of labels")
            for entry in batch:
                if isinstance(entry, dict):
                    labels.append({"name": entry.get("name"),
                                   "color": entry.get("color"),
                                   "description": entry.get("description")})
            if len(batch) < 100:
                break
            page += 1
        return {"labels": labels}
    if op == "issue_form":
        repo, filename = params["repo"], params["filename"]
        path = f".github/ISSUE_TEMPLATE/{filename}"
        # contents path segment: owner/name form only (F5 normalization)
        argv = ["gh", "api", f"repos/{rest_repo(repo)}/contents/{path}"]
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
        org, number = parse_project_locator(params["locator"])
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


def fixture_repo_slug(repo: Any) -> Optional[str]:
    """Normalize a params['repo'] value to the override `<slug>`:
    the last two non-empty path segments (`github.com/fixture-org/app`
    -> `fixture-org/app`; `fixture-org/workspace` -> itself; a trailing
    `.git` suffix is stripped). No two segments -> None (no override)."""
    if not isinstance(repo, str) or not repo:
        return None
    parts = [p for p in repo.split("/") if p]
    if len(parts) < 2:
        return None
    slug = "/".join(parts[-2:])
    if slug.endswith(".git"):
        slug = slug[: -len(".git")]
    return slug or None


def build_fixture_transport(path: str | Path) -> Callable[[str, dict], dict]:
    """Load a fixture dataset (§ docstring schema) and return a transport
    backed by it. Malformed JSON / non-object dataset / non-object entry
    -> FixtureError; an override key ("<op>@<slug>") with an unknown
    base op -> FixtureError at load time; missing op entry -> ProbeError
    at call time; an {"error": ...} entry raises ProbeError with that
    named detail. Lookup order: "<op>@<slug>" (slug from params['repo'],
    § fixture_repo_slug) -> "<op>" -> the missing-entry refusal."""
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise FixtureError(f"fixture dataset unreadable: {p} — {exc}")
    except UnicodeDecodeError as exc:
        # non-UTF-8 dataset: named refusal, never a raw traceback (F6)
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
        if isinstance(key, str) and "@" in key:
            base_op = key.split("@", 1)[0]
            if base_op not in _FIXTURE_KEYS:
                raise FixtureError(
                    f"fixture dataset malformed: {p} — override key "
                    f"'{key}' has unknown base op '{base_op}'")

    def transport(op: str, params: dict) -> dict:
        if op not in _FIXTURE_KEYS:
            raise ProbeError(f"fixture has no entry for op: {op}")
        key = op
        slug = fixture_repo_slug(params.get("repo"))
        if slug and f"{op}@{slug}" in data:
            key = f"{op}@{slug}"
        elif op not in data:
            raise ProbeError(f"fixture has no entry for op: {op}")
        entry = data[key]
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
        """{target, found, enforcement, bypass, rules, read[, error]} for
        the branch ruleset. Refused reads (plan-gated 403 etc.) come back
        as read='refused' + error — never an exception; no rulesets at
        all -> found=False. A ruleset matches iff its
        conditions.ref_name.include matches the branch AND no
        conditions.ref_name.exclude matches it (exclude wins over
        include; F1/G2). Both lists carry GitHub ref-pattern selectors
        under FULLMATCH semantics over `refs/heads/<branch>`: `~ALL`
        matches every ref; `~DEFAULT_BRANCH` matches (the probe
        evaluates the default branch by contract); any other pattern is
        a glob — `**` crosses `/`, `*` does not, everything else
        literal — and a pattern without a `refs/` prefix is a branch
        pattern `refs/heads/<pattern>` (fnmatch never used). Only
        rulesets whose `target` is absent or exactly `branch`
        participate; a tag/push ruleset never protects a branch (F3).
        Among matching rulesets the first ACTIVE one is selected; only
        when none is active does the first matching non-active one
        surface (so the `staged, not enforcing` diagnosis is never
        hidden by order; G3). `rules` carries the protection-rule
        objects the check layer validates (F10)."""
        try:
            data = self._call("ruleset", "ruleset",
                              {"repo": repo, "branch": branch})
        except ProbeError as exc:
            return {"target": "branch", "found": False,
                    "enforcement": None, "bypass": [], "rules": [],
                    "read": "refused", "error": str(exc)}
        want = f"refs/heads/{branch}"

        def _selector_matches(selectors: list) -> bool:
            return any(ref_selector_matches(str(sel), want)
                       for sel in selectors)

        matches: list[dict] = []
        for rs in data.get("rulesets", []):
            # F3: only rulesets that target branches take part — an
            # active tag ruleset with ~ALL can never shadow the
            # default-branch ruleset.
            target = rs.get("target")
            if target is not None and target != "branch":
                continue
            ref_name = (rs.get("conditions") or {}).get("ref_name") or {}
            include = ref_name.get("include") or []
            exclude = ref_name.get("exclude") or []
            if _selector_matches(include) and not _selector_matches(exclude):
                matches.append(rs)
        # G3: prefer the first ACTIVE match; fall back to the first
        # matching non-active one (deterministic, order-preserving).
        chosen = next((rs for rs in matches
                       if rs.get("enforcement") == "active"),
                      matches[0] if matches else None)
        if chosen is None:
            return {"target": "branch", "found": False,
                    "enforcement": None, "bypass": [], "rules": [],
                    "read": "ok"}
        bypass = [{"actor_type": a.get("actor_type"),
                   "mode": a.get("mode") or a.get("bypass_mode"),
                   "actor_id": a.get("actor_id")}
                  for a in chosen.get("bypass_actors", [])]
        return {"target": chosen.get("target") or "branch",
                "found": True,
                "enforcement": chosen.get("enforcement"),
                "bypass": bypass,
                "rules": chosen.get("rules") or [],
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
        parse_project_locator(locator)  # grammar check — ONE parser
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
