#!/usr/bin/env python3
"""onboarding/readiness.py — the `onboard verify` engine + attest CLI.

Machine-readable form of kit-v0.1/target-repo-readiness.md: every check
row in `onboarding/registry.json` maps to exactly one implementation
here (a registry row without an implementation, or an implementation
without a registry row, is a hard named error — never a silent skip).

CLI (direct-run usable: `python3 onboarding/readiness.py verify <home>`):

  verify <target> [--forge-fixture <file>] [--json]
      Run every registry check against the control home at <target>.
      --forge-fixture swaps the gh transport for a dataset-backed one
      (tests/fixture runs). Text output: a header, one line per check
      (`PASS|FAIL|N-A|ADVISORY <id> <title>[ — <detail>]`), DEFERRED(S4)
      lines for deferred_to_s4 parts, and a footer summary. --json emits
      the same data (sorted keys, LF, trailing newline).

  attest <target> <check-id> --by <login> --note <text>
      Record an operator attest as an ANSWERED human_gate row in the
      home ledger (gate=meta, question `onboard attest: <check-id>
      <note>`), appended via the kit's ledger_lib.append_event — full
      schema strictness intact.

Exit codes: 0 = GREEN (no required FAIL) · 1 = any required FAIL,
engine/registry errors, C-REG failures · 2 = usage.

Laws:
  * Sanctioned writes ONLY: <home>/factory/PREFLIGHT.md (regenerated
    every verify) and the C-8/W-5 render regeneration (the kit
    renderers run with --root <home>, then --check). Everything else
    is strict read-only.
  * Subprocess law: every subprocess call passes argv LISTS (never a
    shell) and PYTHONDONTWRITEBYTECODE=1; python subprocesses add -B.
  * Fail closed: forge probe refusals render `cannot probe: ...` FAIL
    details; malformed input never produces a raw traceback.
  * Determinism: no wall-clock in text/JSON output (PREFLIGHT.md may
    carry a `generated:` UTC line — nothing else is time-dependent).
  * Applicability: non-applicable checks render N-A with a named
    reason — never silent, never FAIL-by-default, never PASS.

Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # read-only heritage (tools/factory.py law)

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

KIT_ROOT = Path(__file__).resolve().parents[1]
if str(KIT_ROOT) not in sys.path:
    sys.path.insert(0, str(KIT_ROOT))

try:
    from . import forge_probe as FP
except ImportError:  # direct-run layout
    from onboarding import forge_probe as FP

from tools import config_validator as CV
from tools import ledger_lib as L
from tools import state_machine as SM

REGISTRY_PATH = Path(__file__).resolve().parent / "registry.json"
LEDGER_REL = Path("factory") / "ledger" / "events.jsonl"
PREFLIGHT_REL = Path("factory") / "PREFLIGHT.md"
ATTEST_PREFIX = "onboard attest: "
SECRETISH_PATTERNS = (".env", "secret", ".pem", "token")
EMDASH = "\u2014"

PASS, FAIL, NA, ADVISORY = "PASS", "FAIL", "N-A", "ADVISORY"


class EngineError(RuntimeError):
    """Named engine/registry refusal (rc 1; never a raw traceback)."""


class AttestError(ValueError):
    """Named attest refusal (nothing appended)."""


# ---------------------------------------------------------------- plumbing --

def run_argv(argv: list[str]) -> subprocess.CompletedProcess:
    """The ONE subprocess seam: argv lists only (never a shell), with
    PYTHONDONTWRITEBYTECODE=1 so no run mutates a tree with bytecode."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(argv, capture_output=True, text=True, env=env)


def python_argv(script: Path, *args: str) -> list[str]:
    """Subprocess law: python invocations always carry -B."""
    return [sys.executable, "-B", str(script), *args]


def cannot_probe(exc: Exception) -> str:
    """Fail-closed forge detail: `cannot probe: <named detail>`."""
    msg = str(exc)
    if msg.startswith("probe failed: "):
        msg = msg[len("probe failed: "):]
    return f"cannot probe: {msg}"


def parse_toml(path: Path) -> tuple[Optional[dict], Optional[str]]:
    """Tolerant TOML read: (doc, None) or (None, named detail)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"unreadable file: {exc}"
    except UnicodeDecodeError as exc:
        return None, f"malformed TOML: file is not valid UTF-8 ({exc})"
    try:
        return json.loads(json.dumps(tomllib.loads(text))), None
    except tomllib.TOMLDecodeError as exc:
        return None, f"malformed TOML: {exc}"
    except (TypeError, ValueError) as exc:
        return None, f"malformed TOML: {exc}"


_REMOTE_HTTPS_RE = re.compile(r"^https?://[^/]+/(.+?)(?:\.git)?$")
_REMOTE_SSH_RE = re.compile(r"^[^@\s]+@[^:]+:(.+?)(?:\.git)?$")


def repo_slug_from_url(url: str) -> Optional[str]:
    """`host/owner/name` or an https/ssh git remote -> `owner/name`."""
    if not url:
        return None
    m = _REMOTE_HTTPS_RE.fullmatch(url) or _REMOTE_SSH_RE.fullmatch(url)
    if m:
        url = m.group(1)
    parts = url.split("/")
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return None


# ---------------------------------------------------------------- registry --

def load_registry(path: Path | str = REGISTRY_PATH) -> dict:
    """Load + structurally validate registry.json (named refusals)."""
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise EngineError(f"registry unreadable: {p} — {exc}")
    except UnicodeDecodeError as exc:
        raise EngineError(f"registry malformed: {p} — not valid UTF-8 ({exc})")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise EngineError(f"registry malformed: {p} — {exc}")
    if not isinstance(data, dict):
        raise EngineError(f"registry malformed: {p} — top level must be an object")
    if not isinstance(data.get("registry_version"), int):
        raise EngineError(f"registry malformed: {p} — registry_version must be an int")
    checks = data.get("checks")
    if not isinstance(checks, list) or not checks:
        raise EngineError(f"registry malformed: {p} — checks must be a non-empty array")
    seen: set[str] = set()
    for row in checks:
        if not isinstance(row, dict):
            raise EngineError(f"registry malformed: {p} — check row must be an object")
        cid = row.get("id")
        if not isinstance(cid, str) or not cid:
            raise EngineError(f"registry malformed: {p} — check row without a string id")
        if cid in seen:
            raise EngineError(f"registry malformed: {p} — duplicate check id {cid}")
        seen.add(cid)
        for field in ("category", "title", "applies", "remedy"):
            if not isinstance(row.get(field), str):
                raise EngineError(
                    f"registry malformed: {p} — check {cid} missing string `{field}`")
        if not isinstance(row.get("required"), bool):
            raise EngineError(f"registry malformed: {p} — check {cid} missing bool `required`")
        if not isinstance(row.get("verify"), dict):
            raise EngineError(f"registry malformed: {p} — check {cid} missing object `verify`")
    for field in ("labels", "labels_conditional"):
        if not isinstance(data.get(field), list):
            raise EngineError(f"registry malformed: {p} — {field} must be an array")
    if not isinstance(data.get("issue_form"), str):
        raise EngineError(f"registry malformed: {p} — issue_form must be a string")
    return data


def base_label_names(registry: dict) -> list[str]:
    """The base label set (names, registry order) — C-4's required list."""
    return [lbl["name"] for lbl in registry["labels"]
            if isinstance(lbl, dict) and isinstance(lbl.get("name"), str)]


# ----------------------------------------------------------------- context --

class Context:
    """Everything the checks share about one home + forge seam. All
    resolution errors are STORED (named) and surfaced by the checks —
    context building never raises for a merely non-compliant home."""

    def __init__(self, home: Path, forge: FP.Forge, registry: dict,
                 kit_sha: str):
        self.home = home
        self.forge = forge
        self.registry = registry
        self.kit_sha = kit_sha
        # workspace.toml
        ws_path = home / "workspace.toml"
        self.ws_doc, self.ws_error = (None, f"workspace.toml missing: {ws_path}") \
            if not ws_path.is_file() else parse_toml(ws_path)
        doc = self.ws_doc or {}
        ch = doc.get("control_home")
        self.control_home = ch if isinstance(ch, str) else None
        self.mode = self.control_home if self.control_home in ("dedicated", "in-repo") \
            else "unknown"
        branch = doc.get("default_branch")
        self.default_branch = branch if isinstance(branch, str) and branch else "main"
        # primary binding
        self.primary_url: Optional[str] = None
        repos = doc.get("repos")
        if isinstance(repos, list):
            for item in repos:
                if isinstance(item, dict) and item.get("role") == "primary" \
                        and isinstance(item.get("url"), str):
                    self.primary_url = item["url"]
                    break
            if self.primary_url is None:
                for item in repos:
                    if isinstance(item, dict) and isinstance(item.get("url"), str):
                        self.primary_url = item["url"]
                        break
        self.repo_bindings: list[tuple[str, str]] = []
        if isinstance(repos, list):
            for item in repos:
                if isinstance(item, dict) and isinstance(item.get("url"), str):
                    rid = item.get("id")
                    self.repo_bindings.append(
                        (rid if isinstance(rid, str) else item["url"], item["url"]))
        board = doc.get("board")
        self.project_id = board.get("project_id") if isinstance(board, dict) else None
        # origin remote (home repo)
        self.origin_url: Optional[str] = None
        self.origin_slug: Optional[str] = None
        if home.is_dir():
            out = run_argv(["git", "-C", str(home), "remote", "get-url", "origin"])
            if out.returncode == 0 and out.stdout.strip():
                self.origin_url = out.stdout.strip()
                if self.origin_url is not None:
                    self.origin_slug = repo_slug_from_url(self.origin_url)
        # environments
        self.env_docs: list[tuple[str, Optional[dict], Optional[str]]] = []
        env_dir = home / "environments"
        if env_dir.is_dir():
            for path in sorted(env_dir.glob("*.toml")):
                edoc, eerr = parse_toml(path)
                self.env_docs.append((path.name, edoc, eerr))
        runtimes: set[str] = set()
        for _name, edoc, _eerr in self.env_docs:
            if isinstance(edoc, dict) and isinstance(edoc.get("runtime"), str):
                runtimes.add(edoc["runtime"])
        self.runtimes = sorted(runtimes)
        # home registry record
        rec_path = home / "factory" / "onboarding-registry.json"
        self.record: Optional[dict] = None
        self.record_error: Optional[str] = None
        if not rec_path.is_file():
            self.record_error = f"record missing: factory/onboarding-registry.json"
        else:
            try:
                data = json.loads(rec_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError,
                    ValueError) as exc:
                data = None
                self.record_error = \
                    f"record unparseable: factory/onboarding-registry.json — {exc}"
            if data is not None:
                if isinstance(data, dict):
                    self.record = data
                else:
                    self.record_error = \
                        "record unparseable: factory/onboarding-registry.json — " \
                        "top level must be an object"
        decl = (self.record or {}).get("declarations")
        self.ui_declared = bool(isinstance(decl, dict) and decl.get("ui") is True)
        # ledger (raw load; validation is C-8/W-5's job)
        self.ledger_events: Optional[list[dict]] = None
        self.ledger_error: Optional[str] = None
        try:
            self.ledger_events = L.load_ledger(home / LEDGER_REL)
        except L.LedgerError as exc:
            self.ledger_error = str(exc)
        except OSError as exc:
            self.ledger_error = f"ledger unreadable: {exc}"
        # cached render result (C-8 and W-5 share ONE regeneration)
        self._renders: Optional[tuple[bool, str]] = None

    # -- shared mechanics ----------------------------------------------------

    def renders(self) -> tuple[bool, str]:
        """C-8/W-5 mechanics: kit renderers regenerate (sanctioned write)
        then --check; (ok, detail). Runs ONCE per verify (cached)."""
        if self._renders is not None:
            return self._renders
        findings = L.validate_ledger(self.home / LEDGER_REL)
        if findings:
            self._renders = (False, "ledger rejected: " + "; ".join(findings[:3]))
            return self._renders
        runs = [("render_dashboard.py", []), ("render_state.py", ["--all"]),
                ("render_provenance.py", ["--all"])]
        for script, extra in runs:
            for pass_args in ([], ["--check"]):
                argv = python_argv(KIT_ROOT / "tools" / script,
                                   "--root", str(self.home), *extra, *pass_args)
                out = run_argv(argv)
                if out.returncode != 0:
                    detail = (out.stderr or out.stdout or "").strip().splitlines()
                    tail = detail[-1] if detail else f"rc={out.returncode}"
                    suffix = " --check" if pass_args else ""
                    self._renders = (False, f"renderer {script}{suffix} — {tail}")
                    return self._renders
        self._renders = (True, "ledger valid; renders regenerated + --check clean")
        return self._renders

    def home_states(self) -> list[str]:
        """W-8 authority: the home's INSTALLED state_machine when
        importable, else the kit's — imported, never re-typed."""
        installed = self.home / "factory" / "tools" / "state_machine.py"
        if installed.is_file():
            try:
                spec = importlib.util.spec_from_file_location(
                    "_home_state_machine", installed)
                if spec is not None and spec.loader is not None:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    states = getattr(mod, "STATES", None)
                    if isinstance(states, list) and states:
                        return list(states)
            except Exception:
                pass  # fall through to the kit authority
        return list(SM.STATES)


# ------------------------------------------------------------ attest rows --

def find_attest(events: list[Any], check_id: str) -> Optional[dict]:
    """The latest ANSWERED attest row for a check (latest wins)."""
    prefix = f"{ATTEST_PREFIX}{check_id} "
    latest: Optional[dict] = None
    for evt in events or []:
        if not isinstance(evt, dict) or evt.get("event") != "human_gate":
            continue
        if evt.get("gate") != "meta":
            continue
        question = evt.get("question")
        if not (isinstance(question, str) and question.startswith(prefix)):
            continue
        if evt.get("answered_at") is None:
            continue
        by = evt.get("answered_by")
        if not (isinstance(by, str) and by.strip()):
            continue
        latest = evt
    return latest


def append_attest(home: Path | str, check_id: str, by: str, note: str) -> dict:
    """Append an answered human_gate attest row via the KIT ledger lib
    (schema strictness intact — a refused row is never written)."""
    if not isinstance(by, str) or not by.strip():
        raise AttestError("attest refused: --by must be a non-empty login")
    if not isinstance(note, str) or not note.strip():
        raise AttestError("attest refused: --note must be non-empty text")
    now = L.utcnow()
    event_id = f"onboard-attest-{check_id}-{int(time.time())}"
    # A second attest within the same unix second keeps ids unique
    # (the ledger's uniqueness law) with a deterministic -<n> suffix.
    try:
        existing = {e.get("event_id") for e in
                    L.load_ledger(Path(home) / LEDGER_REL)}
    except L.LedgerError:
        existing = set()
    n = 2
    while event_id in existing:
        event_id = f"onboard-attest-{check_id}-{int(time.time())}-{n}"
        n += 1
    evt = {
        "event": "human_gate",
        "event_id": event_id,
        "recorded_at": now,
        "provenance": "live",
        "story_id": None,
        "story_id_reason": "workspace-level attest (no story)",
        "gate": "meta",
        "question": f"{ATTEST_PREFIX}{check_id} {note.strip()}",
        "asked_at": now,
        "answered_at": now,
        "decision": "approved",
        "answered_by": by.strip(),
    }
    L.append_event(Path(home) / LEDGER_REL, evt)
    return evt


# --------------------------------------------------------------- mechanics --

def bypass_error(bypass: list[dict]) -> Optional[str]:
    """The frozen bypass rule (C-3 + W-7): all entries Integration, at
    most ONE; anything else is a named FAIL."""
    if any(b.get("actor_type") != "Integration" for b in bypass):
        return "human bypass present — the merge gate is not mechanically real"
    if len(bypass) > 1:
        return "multiple integration bypasses — expected the factory bot only"
    return None


# C-3 requires the FULL protection set (kit-v0.1/target-repo-readiness.md):
# PR review with >= 1 approval, required status checks, no force-push, no
# deletion. `active` alone is not protection — an empty rules list is not.
REQUIRED_PROTECTIONS = ("deletion", "non_fast_forward", "pull_request",
                        "required_status_checks")


def missing_protections(rules: list) -> list[str]:
    """The protection rule types missing from a ruleset's `rules` array
    (sorted, deterministic). `pull_request` counts only when its
    parameters require >= 1 approving review. Nothing beyond these four
    types is required."""
    by_type = {}
    for rule in rules if isinstance(rules, list) else []:
        if isinstance(rule, dict) and isinstance(rule.get("type"), str):
            by_type.setdefault(rule["type"], rule)
    missing = []
    for name in REQUIRED_PROTECTIONS:
        rule = by_type.get(name)
        if rule is None:
            missing.append(name)
            continue
        if name == "pull_request":
            params = rule.get("parameters") or {}
            count = params.get("required_approving_review_count")
            if not isinstance(count, int) or isinstance(count, bool) \
                    or count < 1:
                missing.append(name)
    return missing


def protections_error(rules: list) -> Optional[str]:
    """Named FAIL when the protection set is incomplete (F10); None when
    all four required rule types are present."""
    missing = missing_protections(rules)
    if not missing:
        return None
    return f"ruleset missing protections: {', '.join(missing)}"


def evaluate_ruleset(ctx: Context, repo: Optional[str],
                     label: str) -> tuple[str, str]:
    """Shared C-3/W-7 ruleset evaluation (frozen wording)."""
    if not repo:
        return FAIL, f"no repo to probe for {label}"
    try:
        rs = ctx.forge.ruleset(repo, ctx.default_branch)
    except FP.ProbeError as exc:
        return FAIL, cannot_probe(exc)
    if rs.get("read") == "refused":
        return FAIL, ("enforcement unverified — ruleset read refused "
                      f"({rs.get('error')}) — never claimed as protection")
    if not rs.get("found"):
        return FAIL, f"no ruleset targeting {ctx.default_branch}"
    if rs.get("enforcement") != "active":
        return FAIL, (f"ruleset staged, not enforcing "
                      f"(enforcement={rs.get('enforcement')!r})")
    err = bypass_error(rs.get("bypass") or [])
    if err:
        return FAIL, err
    err = protections_error(rs.get("rules") or [])
    if err:
        return FAIL, err
    return PASS, (f"active ruleset on {ctx.default_branch}; bypass = the "
                  f"factory bot only ({len(rs.get('bypass') or [])} Integration)")


def validator_findings(ctx: Context, *, workspace: bool,
                       environments: bool) -> tuple[list[str], str]:
    """C-10/W-2/W-3 mechanics: the kit config_validator is the authority
    (its messages surface verbatim); pins resolve against the KIT ROOT."""
    findings: list[str] = []
    detail_bits: list[str] = []
    if workspace:
        ws_path = ctx.home / "workspace.toml"
        if not ws_path.is_file():
            findings.append("workspace.toml missing")
        else:
            errs, _parsed = CV.validate_path(ws_path, repo=str(KIT_ROOT))
            findings.extend(errs)
            if not errs:
                pin = ((ctx.ws_doc or {}).get("factory") or {}).get("pin")
                if CV.is_deferred(pin):
                    detail_bits.append("pin deferred (template mode)")
                else:
                    detail_bits.append(f"pin {str(pin)[:7]} resolves in the kit repo")
    if environments:
        if not ctx.env_docs:
            findings.append("no environments/*.toml declared")
        else:
            for name, _doc, err in ctx.env_docs:
                if err:
                    findings.append(f"environments/{name}: {err}")
                else:
                    errs, _parsed = CV.validate_path(
                        ctx.home / "environments" / name)
                    findings.extend(f"environments/{name}: {e}" for e in errs)
            detail_bits.append(f"{len(ctx.env_docs)} environment file(s) validate")
    return findings, "; ".join(detail_bits)


def env_ref_problems(ctx: Context) -> list[str]:
    """W-3: credential refs are checked for PRESENCE only — values are
    never read. Deferred `<...>` refs are fine."""
    problems: list[str] = []
    for name, doc, err in ctx.env_docs:
        if err or not isinstance(doc, dict):
            continue
        creds = doc.get("credentials")
        refs = creds.get("refs") if isinstance(creds, dict) else None
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if not isinstance(ref, str) or CV.is_deferred(ref):
                continue
            if ref.startswith("env:"):
                if ref[4:] not in os.environ:
                    problems.append(
                        f"environments/{name}: missing credential ref: {ref}")
            elif ref.startswith("file:"):
                if not (ctx.home / ref[5:]).exists():
                    problems.append(
                        f"environments/{name}: missing credential ref: {ref}")
    return problems


def attest_status(ctx: Context, check_id: str) -> tuple[str, str]:
    """Shared attest-check mechanics (C-13, U-*, A-*)."""
    if ctx.ledger_error:
        return FAIL, f"ledger unreadable — {ctx.ledger_error.splitlines()[0]}"
    row = find_attest(ctx.ledger_events or [], check_id)
    if row is None:
        return FAIL, "no answered attest recorded"
    if row.get("decision") != "approved":
        return FAIL, (f"latest attest not approved (decision="
                      f"{row.get('decision')!r} by {row.get('answered_by')})")
    return PASS, f"attested by {row.get('answered_by')}"


# ------------------------------------------------------------------- checks --
# Each implementation: (ctx, row) -> (status, detail). Every registry id
# MUST appear here — validate_implementations() enforces the bijection.

def check_c1(ctx: Context, row: dict) -> tuple[str, str]:
    # C-1 is a CONTROL-HOME check: the home repo (origin remote), not
    # the primary binding (binding reachability is W-4's job).
    if not ctx.origin_slug:
        return FAIL, "no origin remote on the control home"
    try:
        ctx.forge.repo_view(ctx.origin_slug)
    except FP.ProbeError as exc:
        return FAIL, cannot_probe(exc)
    return PASS, f"origin {ctx.origin_slug}; repo_view({ctx.origin_slug}) reachable"


def check_c2(ctx: Context, row: dict) -> tuple[str, str]:
    try:
        auth = ctx.forge.auth_scopes()
    except FP.ProbeError as exc:
        return FAIL, cannot_probe(exc)
    missing = auth.get("missing") or []
    if missing:
        return FAIL, f"missing: {', '.join(missing)}"
    login = auth.get("login") or "unknown"
    return PASS, f"authenticated as {login} with scopes {', '.join(auth.get('scopes') or [])}"


def target_ruleset_problem(ctx: Context, rid: str,
                           url: str) -> Optional[str]:
    """The C-3 Mode-2 target variant for ONE binding: require found +
    enforcement == active + NO Integration bypass actor (the factory
    bypass lives on the workspace repo only). Returns the named
    violation, or None when the target is clean."""
    try:
        rs = ctx.forge.ruleset(url, ctx.default_branch)
    except FP.ProbeError as exc:
        return f"target {rid}: {cannot_probe(exc)}"
    if rs.get("read") == "refused":
        return (f"target {rid}: cannot probe: ruleset read refused "
                f"({rs.get('error')})")
    if not rs.get("found"):
        return f"target {rid}: no ruleset targeting {ctx.default_branch}"
    if rs.get("enforcement") != "active":
        return (f"target {rid}: ruleset not enforcing "
                f"(enforcement={rs.get('enforcement')!r})")
    if any(b.get("actor_type") == "Integration"
           for b in rs.get("bypass") or []):
        return (f"target {rid}: carries a factory bypass — the bypass "
                "lives on the workspace repo only")
    err = protections_error(rs.get("rules") or [])
    if err:
        return f"target {rid}: {err}"
    return None


def check_c3(ctx: Context, row: dict) -> tuple[str, str]:
    """C-3 is a CONTROL-HOME check: the home repo's ruleset (frozen
    wording kept) PLUS the Mode-2 target variant — when control_home is
    dedicated, every bound target repo keeps standard protection with
    NO factory bypass. ONE line; ALL problems joined `; ` (home first)."""
    problems: list[str] = []
    home_detail = ""
    if not ctx.origin_slug:
        problems.append("no origin remote on the control home")
    else:
        home_status, home_detail = evaluate_ruleset(
            ctx, ctx.origin_slug, "the control home")
        if home_status != PASS:
            problems.append(home_detail)
    evaluated: list[str] = []
    if ctx.control_home == "dedicated":
        for rid, url in ctx.repo_bindings:
            problem = target_ruleset_problem(ctx, rid, url)
            if problem:
                problems.append(problem)
            else:
                evaluated.append(rid)
    if problems:
        return FAIL, "; ".join(problems)
    if ctx.control_home == "dedicated" and evaluated:
        home_detail += (f"; targets: {', '.join(evaluated)} "
                        "standard protection ok")
    return PASS, home_detail


def check_c4(ctx: Context, row: dict) -> tuple[str, str]:
    # C-4 is a CONTROL-HOME check: labels on the home repo.
    if not ctx.origin_slug:
        return FAIL, "no origin remote on the control home"
    required = base_label_names(ctx.registry)
    try:
        probe = ctx.forge.labels(ctx.origin_slug, required)
    except FP.ProbeError as exc:
        return FAIL, cannot_probe(exc)
    missing = probe.get("missing") or []
    if missing:
        return FAIL, f"missing: {', '.join(missing)}"
    return PASS, f"all {len(required)} base labels present on {ctx.origin_slug}"


def check_c5(ctx: Context, row: dict) -> tuple[str, str]:
    # C-5 is a CONTROL-HOME check: form file + the idea label on the
    # home repo.
    form = ctx.home / ".github" / "ISSUE_TEMPLATE" / ctx.registry["issue_form"]
    if not form.is_file():
        return FAIL, f"missing .github/ISSUE_TEMPLATE/{ctx.registry['issue_form']}"
    if not ctx.origin_slug:
        return FAIL, "no origin remote on the control home"
    try:
        probe = ctx.forge.labels(ctx.origin_slug, ["idea"])
    except FP.ProbeError as exc:
        return FAIL, cannot_probe(exc)
    if probe.get("missing"):
        return FAIL, "missing: idea label on the forge"
    return PASS, (f"{ctx.registry['issue_form']} present; idea label on "
                  f"{ctx.origin_slug}")


def check_c6(ctx: Context, row: dict) -> tuple[str, str]:
    locator = ctx.project_id
    if not isinstance(locator, str) or not locator or CV.is_deferred(locator):
        return FAIL, "board.project_id not configured"
    try:
        proj = ctx.forge.project_fields(locator)
    except FP.ProbeError as exc:
        return FAIL, cannot_probe(exc)
    if not proj.get("found"):
        return FAIL, proj.get("error") or f"project not found: {locator}"
    stage = (proj.get("fields") or {}).get("Stage")
    if not stage or not stage.get("options"):
        return FAIL, "no Stage field with options on the board"
    return PASS, f"board {locator} linked; Stage field present ({len(stage['options'])} options)"


_C7_PRESENCE = ("factory/tools", "factory/gates", "factory/templates",
                "factory/locks/OVERRIDE.example", "factory/ledger/events.jsonl")


def check_c7(ctx: Context, row: dict) -> tuple[str, str]:
    missing = []
    for rel in _C7_PRESENCE:
        p = ctx.home / rel
        want_dir = not rel.endswith((".example", ".jsonl"))
        if want_dir:
            if not p.is_dir():
                missing.append(rel)
            elif rel == "factory/templates" and not any(p.iterdir()):
                missing.append(f"{rel} (empty)")
        elif not p.is_file():
            missing.append(rel)
    if missing:
        return FAIL, f"missing: {', '.join(missing)}"
    validator = ctx.home / "factory" / "tools" / "config_validator.py"
    if not validator.is_file():
        return FAIL, "missing: factory/tools/config_validator.py (installed copy)"
    argv = python_argv(validator, str(ctx.home / "workspace.toml"),
                       "--repo", str(KIT_ROOT))
    out = run_argv(argv)
    if out.returncode != 0:
        lines = (out.stdout or "").strip().splitlines()
        detail = next((ln.strip() for ln in lines if ln.strip().startswith("- ")),
                      f"installed config_validator rc={out.returncode}")
        return FAIL, f"installed config_validator — {detail.lstrip('- ').strip()}"
    return PASS, "v0.1-S3 surface present; installed config_validator rc=0"


def check_c8(ctx: Context, row: dict) -> tuple[str, str]:
    ok, detail = ctx.renders()
    return (PASS if ok else FAIL), detail


def check_c9(ctx: Context, row: dict) -> tuple[str, str]:
    if sys.version_info < (3, 11):
        return FAIL, f"python3 too old: {sys.version.split()[0]} (< 3.11)"
    for tool in ("git", "gh"):
        try:
            out = run_argv([tool, "--version"])
        except OSError as exc:
            return FAIL, f"{tool} not runnable: {exc}"
        if out.returncode != 0:
            return FAIL, f"{tool} --version rc={out.returncode}"
    return PASS, (f"python3 {sys.version_info.major}.{sys.version_info.minor}, "
                  "git, gh present")


def check_c10(ctx: Context, row: dict) -> tuple[str, str]:
    findings, detail = validator_findings(ctx, workspace=True, environments=True)
    if findings:
        return FAIL, "; ".join(findings[:4])
    return PASS, f"workspace.toml + environments validate; {detail}"


def check_c11(ctx: Context, row: dict) -> tuple[str, str]:
    if not (ctx.home / "factory" / "KICKOFF.md").is_file():
        return FAIL, "missing factory/KICKOFF.md"
    return PASS, "factory/KICKOFF.md present; factory/PREFLIGHT.md regenerated this run"


def check_c12(ctx: Context, row: dict) -> tuple[str, str]:
    secrets = ctx.home / "factory" / "SECRETS.md"
    if not secrets.is_file() or not secrets.read_text(encoding="utf-8").strip():
        return FAIL, "missing or empty factory/SECRETS.md"
    gitignore = ctx.home / ".gitignore"
    if not gitignore.is_file():
        return FAIL, "missing .gitignore"
    text = gitignore.read_text(encoding="utf-8").lower()
    if not any(pat in text for pat in SECRETISH_PATTERNS):
        return FAIL, (".gitignore carries no secret-ish ignore pattern "
                      "(.env / *secret* / *.pem / *token*)")
    return PASS, "factory/SECRETS.md present; .gitignore ignores secret-ish paths"


def check_c13(ctx: Context, row: dict) -> tuple[str, str]:
    return attest_status(ctx, "C-13")


def check_c14(ctx: Context, row: dict) -> tuple[str, str]:
    missing = [rel for rel in ("factory/KICKOFF.md",
                               "factory/locks/OVERRIDE.example")
               if not (ctx.home / rel).is_file()]
    if missing:
        return FAIL, f"missing: {', '.join(missing)}"
    return PASS, "KICKOFF.md + locks/OVERRIDE.example present (agreement discoverable)"


def check_registry_record(ctx: Context, row: dict) -> tuple[str, str]:
    if ctx.record_error:
        return FAIL, ctx.record_error
    recorded = (ctx.record or {}).get("registry_version")
    kit_version = ctx.registry["registry_version"]
    if recorded != kit_version:
        return FAIL, f"registry drift: recorded {recorded}, kit ships {kit_version}"
    return PASS, f"registry_version {recorded} recorded; kit_version checked"


def registry_advisory_detail(ctx: Context) -> Optional[str]:
    """The kit-sha drift ADVISORY (does NOT gate): recorded kit_version
    vs the kit repo's current HEAD."""
    if ctx.record_error or not ctx.record:
        return None
    recorded = ctx.record.get("kit_version")
    if recorded == ctx.kit_sha:
        return None
    return (f"kit version drift: recorded {recorded}, "
            f"kit ships {ctx.kit_sha} (advisory — does not gate)")


def check_w1(ctx: Context, row: dict) -> tuple[str, str]:
    out = run_argv(["git", "-C", str(ctx.home), "rev-parse", "--show-toplevel"])
    if out.returncode != 0:
        return FAIL, f"home is not inside a git repo ({out.stderr.strip().splitlines()[-1] if out.stderr.strip() else 'rev-parse failed'})"
    toplevel = out.stdout.strip()
    if Path(toplevel) != ctx.home.resolve():
        return FAIL, f"git toplevel {toplevel} is not the control home"
    return PASS, "control_home=dedicated; home root is the git repo root"


def check_w2(ctx: Context, row: dict) -> tuple[str, str]:
    findings, detail = validator_findings(ctx, workspace=True, environments=False)
    if findings:
        return FAIL, "; ".join(findings[:4])
    return PASS, f"workspace.toml validates; {detail}"


def check_w3(ctx: Context, row: dict) -> tuple[str, str]:
    findings, _detail = validator_findings(ctx, workspace=False, environments=True)
    problems = findings + env_ref_problems(ctx)
    if problems:
        return FAIL, "; ".join(problems[:4])
    return PASS, (f"{len(ctx.env_docs)} environment file(s) validate; "
                  "credential refs present (values never read)")


def check_w4(ctx: Context, row: dict) -> tuple[str, str]:
    if not ctx.repo_bindings:
        return FAIL, "no repo bindings declared in workspace.toml"
    for rid, url in ctx.repo_bindings:
        try:
            ctx.forge.repo_view(url)
        except FP.ProbeError as exc:
            return FAIL, f"binding {rid} unreachable — {cannot_probe(exc)}"
    ids = ", ".join(rid for rid, _ in ctx.repo_bindings)
    return PASS, f"bindings reachable: {ids}"


def check_w5(ctx: Context, row: dict) -> tuple[str, str]:
    ok, detail = ctx.renders()
    return (PASS if ok else FAIL), detail


def check_w6(ctx: Context, row: dict) -> tuple[str, str]:
    templates = ctx.home / "factory" / "templates"
    if not templates.is_dir() or not any(templates.iterdir()):
        return FAIL, "missing factory/templates/ (no template files)"
    return PASS, "factory/templates/ present (contract-block file lands with S4)"


def check_w7(ctx: Context, row: dict) -> tuple[str, str]:
    # W-7 probes the control-home repo ONLY (origin remote; no primary
    # fallback — the home repo is the surface).
    if not ctx.origin_slug:
        return FAIL, "no origin remote on the control home"
    return evaluate_ruleset(ctx, ctx.origin_slug, "the control-home repo")


def check_w8(ctx: Context, row: dict) -> tuple[str, str]:
    locator = ctx.project_id
    if not isinstance(locator, str) or not locator or CV.is_deferred(locator):
        return FAIL, "board.project_id not configured"
    try:
        proj = ctx.forge.project_fields(locator)
    except FP.ProbeError as exc:
        return FAIL, cannot_probe(exc)
    if not proj.get("found"):
        return FAIL, proj.get("error") or f"project not found: {locator}"
    stage = (proj.get("fields") or {}).get("Stage")
    if not stage or not stage.get("options"):
        return FAIL, "no Stage field with options on the board"
    states = ctx.home_states()
    missing = [s for s in states if s not in set(stage["options"])]
    if missing:
        return FAIL, f"Stage options missing states: {', '.join(missing)}"
    return PASS, f"Stage options cover all {len(states)} pipeline states"


def check_u1(ctx: Context, row: dict) -> tuple[str, str]:
    return attest_status(ctx, "U-1")


def check_u2(ctx: Context, row: dict) -> tuple[str, str]:
    return attest_status(ctx, "U-2")


def check_a1(ctx: Context, row: dict) -> tuple[str, str]:
    return attest_status(ctx, "A-1")


def check_a2(ctx: Context, row: dict) -> tuple[str, str]:
    return attest_status(ctx, "A-2")


def check_a3(ctx: Context, row: dict) -> tuple[str, str]:
    return attest_status(ctx, "A-3")


def check_a4(ctx: Context, row: dict) -> tuple[str, str]:
    return attest_status(ctx, "A-4")


def check_a5(ctx: Context, row: dict) -> tuple[str, str]:
    return attest_status(ctx, "A-5")


def check_a6(ctx: Context, row: dict) -> tuple[str, str]:
    return attest_status(ctx, "A-6")


def check_a7(ctx: Context, row: dict) -> tuple[str, str]:
    wf = ctx.home / ".github" / "workflows"
    hit = None
    if wf.is_dir():
        for path in sorted(wf.iterdir()):
            if path.is_file():
                try:
                    if "unittest" in path.read_text(encoding="utf-8"):
                        hit = path.name
                        break
                except (OSError, UnicodeDecodeError):
                    continue
    return ADVISORY, (f"ci workflow runs unittest: {hit}" if hit
                      else "ci workflow runs unittest: none found")


def check_a8(ctx: Context, row: dict) -> tuple[str, str]:
    kickoff = ctx.home / "factory" / "KICKOFF.md"
    if kickoff.is_file():
        try:
            if "story/<id>-<slug>" in kickoff.read_text(encoding="utf-8"):
                return ADVISORY, "branch convention story/<id>-<slug> documented in KICKOFF.md"
        except (OSError, UnicodeDecodeError):
            pass
    return ADVISORY, "branch convention story/<id>-<slug> not documented"


def check_a9(ctx: Context, row: dict) -> tuple[str, str]:
    return ADVISORY, "manual (no automated probe)"


IMPLEMENTATIONS = {
    "C-1": check_c1, "C-2": check_c2, "C-3": check_c3, "C-4": check_c4,
    "C-5": check_c5, "C-6": check_c6, "C-7": check_c7, "C-8": check_c8,
    "C-9": check_c9, "C-10": check_c10, "C-11": check_c11, "C-12": check_c12,
    "C-13": check_c13, "C-14": check_c14, "REGISTRY": check_registry_record,
    "W-1": check_w1, "W-2": check_w2, "W-3": check_w3, "W-4": check_w4,
    "W-5": check_w5, "W-6": check_w6, "W-7": check_w7, "W-8": check_w8,
    "U-1": check_u1, "U-2": check_u2,
    "A-1": check_a1, "A-2": check_a2, "A-3": check_a3, "A-4": check_a4,
    "A-5": check_a5, "A-6": check_a6,
    "A-7": check_a7, "A-8": check_a8, "A-9": check_a9,
}

_ADAPTER_IDS = {"human": "A-1", "hermes": "A-2", "claude-code": "A-3",
                "codex": "A-4", "api-direct": "A-5", "copilot": "A-6"}


def validate_implementations(registry: dict) -> None:
    """The registry <-> implementation bijection (hard named errors)."""
    ids = {row["id"] for row in registry["checks"]}
    for row in registry["checks"]:
        if row["id"] not in IMPLEMENTATIONS:
            raise EngineError(f"registry check not implemented: {row['id']}")
    for impl_id in sorted(IMPLEMENTATIONS):
        if impl_id not in ids:
            raise EngineError(f"implementation without registry row: {impl_id}")


# ------------------------------------------------------------- applicability --

def applicability(ctx: Context, row: dict) -> Optional[str]:
    """None = applicable; else the NAMED N-A reason (frozen wording)."""
    applies = row["applies"]
    if applies == "always":
        return None
    if applies == "mode:dedicated":
        if ctx.control_home == "dedicated":
            return None
        if ctx.control_home == "in-repo":
            return "control_home=in-repo (Mode 1)"
        return "control_home not declared (workspace.toml unreadable or invalid)"
    if applies == "ui:declared":
        if ctx.ui_declared:
            return None
        return "no UI surface declared (declarations.ui=false)"
    if applies.startswith("adapter:"):
        name = applies.split(":", 1)[1]
        if name in ctx.runtimes:
            return None
        declared = ", ".join(ctx.runtimes) if ctx.runtimes else "no"
        return f"adapter '{name}' not in play ({declared} runtimes declared)" \
            if ctx.runtimes else f"adapter '{name}' not in play (no runtimes declared)"
    raise EngineError(f"unknown applicability {applies!r} for check {row['id']}")


# ------------------------------------------------------------------ engine --

def evaluate(ctx: Context) -> list[dict]:
    """Run every applicable registry check; return result rows."""
    results: list[dict] = []
    for row in ctx.registry["checks"]:
        cid = row["id"]
        entry: dict[str, Any] = {"id": cid, "category": row["category"],
                                 "title": row["title"]}
        na_reason = applicability(ctx, row)
        if na_reason is not None:
            entry.update(status=NA, detail=na_reason)
            results.append(entry)
            continue
        impl = IMPLEMENTATIONS[cid]
        status, detail = impl(ctx, row)
        if row["category"] == "ADVISORY":
            status = ADVISORY  # advisories never gate
        entry.update(status=status, detail=detail)
        if status == FAIL:
            entry["remedy"] = row["remedy"]
        deferred = row.get("deferred_to_s4")
        if status in (PASS, FAIL) and isinstance(deferred, list) and deferred:
            entry["deferred"] = list(deferred)
        results.append(entry)
        if cid == "REGISTRY":
            drift = registry_advisory_detail(ctx)
            if drift:
                results.append({"id": "REGISTRY", "category": "ADVISORY",
                                "status": ADVISORY,
                                "title": "kit version drift (advisory)",
                                "detail": drift})
    return results


def summarize(results: list[dict]) -> dict:
    passed = sum(1 for r in results if r["status"] == PASS)
    failed = sum(1 for r in results if r["status"] == FAIL)
    na = sum(1 for r in results if r["status"] == NA)
    advisory = sum(1 for r in results if r["status"] == ADVISORY)
    deferred = sum(len(r.get("deferred") or []) for r in results)
    return {"passed": passed, "failed": failed, "na": na,
            "advisory": advisory, "deferred": deferred}


def summary_lines(summary: dict, green: bool) -> list[str]:
    return [
        f"required: {summary['passed']} pass / {summary['failed']} fail / "
        f"{summary['na']} n-a \u00b7 advisory: {summary['advisory']}",
        f"verify status: {'GREEN' if green else 'RED'}",
        "report: factory/PREFLIGHT.md",
    ]


def write_preflight(ctx: Context, results: list[dict], summary: dict,
                    green: bool) -> Path:
    """Regenerate <home>/factory/PREFLIGHT.md (a sanctioned write; the
    ONLY time-dependent artifact — the `generated:` UTC line)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "# PREFLIGHT — factory onboarding verify",
        "",
        f"- target: {ctx.home}",
        f"- mode: {ctx.mode}",
        f"- registry_version: {ctx.registry['registry_version']}",
        f"- kit_version: {ctx.kit_sha}",
        f"- generated: {now}",
        "",
        "| id | category | status | detail |",
        "|----|----------|--------|--------|",
    ]
    for r in results:
        detail = (r.get("detail") or "").replace("|", "\\|")
        lines.append(f"| {r['id']} | {r['category']} | {r['status']} | {detail} |")
    lines += [""]
    lines += summary_lines(summary, green)[:2]
    if green:
        lines.append("NEXT: proceed — the control home is ready for factory runs.")
    else:
        lines.append("NEXT: work the FAIL rows above (verify prints a remedy "
                     "per failure), then re-run verify.")
    path = ctx.home / PREFLIGHT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def run_verify(target: str, forge_fixture: Optional[str] = None,
               as_json: bool = False,
               registry_path: Path | str = REGISTRY_PATH) -> int:
    """The verify engine; returns the rc (0 GREEN / 1 RED-or-error)."""
    try:
        registry = load_registry(registry_path)
        validate_implementations(registry)
        home = Path(target).resolve()
        if not home.is_dir():
            raise EngineError(f"target not found: {target}")
        out = run_argv(["git", "-C", str(KIT_ROOT), "rev-parse", "HEAD"])
        if out.returncode != 0:
            raise EngineError(
                f"cannot resolve kit version: git rev-parse HEAD — "
                f"{out.stderr.strip()}")
        kit_sha = out.stdout.strip()
        if forge_fixture:
            forge = FP.Forge.from_fixture(forge_fixture)  # FixtureError propagates
        else:
            forge = FP.Forge()
        ctx = Context(home, forge, registry, kit_sha)
        results = evaluate(ctx)
        summary = summarize(results)
        green = summary["failed"] == 0
        report = write_preflight(ctx, results, summary, green)
    except (EngineError, FP.FixtureError) as exc:
        print(f"verify error: {exc}")
        return 1
    except OSError as exc:
        print(f"verify error: {exc}")
        return 1
    if as_json:
        payload = {
            "target": str(home),
            "mode": ctx.mode,
            "registry_version": registry["registry_version"],
            "kit_version": kit_sha,
            "checks": results,
            "summary": summary,
            "status": "GREEN" if green else "RED",
            "report_path": str(report),
        }
        print(json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False))
    else:
        print(f"verify: {home} (mode={ctx.mode}, "
              f"registry={registry['registry_version']}, kit={kit_sha[:7]})")
        for r in results:
            line = f"{r['status']} {r['id']} {r['title']}"
            if r.get("detail"):
                line += f" {EMDASH} {r['detail']}"
            print(line)
            if r["status"] == FAIL and r.get("remedy"):
                print(f"  remedy: {r['remedy']}")
            for what in r.get("deferred") or []:
                print(f"DEFERRED(S4) {r['id']} {EMDASH} {what}")
        for line in summary_lines(summary, green):
            print(line)
    return 0 if green else 1


def run_attest(target: str, check_id: str, by: str, note: str,
               registry_path: Path | str = REGISTRY_PATH) -> int:
    """The attest command; returns the rc (0 recorded / 1 refused)."""
    try:
        registry = load_registry(registry_path)
        home = Path(target).resolve()
        if not home.is_dir():
            raise EngineError(f"target not found: {target}")
        row = next((r for r in registry["checks"] if r["id"] == check_id), None)
        if row is None:
            raise EngineError(f"unknown check id: {check_id}")
        if not row.get("attest_allowed"):
            raise EngineError(
                f"check {check_id} is not attest-allowed (machine-checked)")
        append_attest(home, check_id, by, note)
    except (EngineError, AttestError) as exc:
        print(f"attest refused: {exc}", file=sys.stderr)
        return 1
    except L.LedgerError as exc:
        print(f"attest refused: {exc}", file=sys.stderr)
        return 1
    print(f"attest recorded: {check_id} by {by.strip()}")
    return 0


def hash_walk(root: Path) -> dict[str, str]:
    """Read-only-law helper (used by the hash-walk test): rel path ->
    sha256 of every regular file under root."""
    digests: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root))
            digests[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


# --------------------------------------------------------------------- CLI --

def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry: verify|attest; 0 GREEN / 1 RED-or-refused / 2 usage."""
    parser = argparse.ArgumentParser(
        prog="readiness",
        description="factory onboarding readiness: verify + attest")
    sub = parser.add_subparsers(dest="command")
    p_verify = sub.add_parser("verify", help="run every registry check")
    p_verify.add_argument("target", help="control home to verify")
    p_verify.add_argument("--forge-fixture", default=None, metavar="file",
                          help="dataset-backed forge transport (tests)")
    p_verify.add_argument("--json", action="store_true", dest="as_json",
                          help="machine-readable output")
    p_attest = sub.add_parser("attest", help="record an operator attest")
    p_attest.add_argument("target", help="control home")
    p_attest.add_argument("check_id", help="registry check id (attest-allowed)")
    p_attest.add_argument("--by", required=True, metavar="login")
    p_attest.add_argument("--note", required=True, metavar="text")
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage(sys.stderr)
        return 2
    if args.command == "verify":
        return run_verify(args.target, forge_fixture=args.forge_fixture,
                          as_json=args.as_json)
    return run_attest(args.target, args.check_id, args.by, args.note)


if __name__ == "__main__":
    raise SystemExit(main())
