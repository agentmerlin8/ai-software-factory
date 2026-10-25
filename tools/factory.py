#!/usr/bin/env python3
"""tools/factory.py — workspace CLI, read path (S2-T7).

The workspace-layer read surface from architecture.md §CLI surface:

    python3 tools/factory.py [--workspace <dir>] workspace show [--repo <path>] [--json]
    python3 tools/factory.py [--workspace <dir>] env show <name|path> [--json]
    python3 tools/factory.py [--workspace <dir>] env resolve <name|path> [--strict] [--json]

`--workspace` defaults to the cwd and points at the control-home root
(the directory holding `workspace.toml`, `environments/`,
`factory/ledger/events.jsonl`).

Laws:

  * READ-ONLY: these commands never write anything under the workspace
    (proven by a sha256-walk test over a fixture tree).
  * No validation is re-implemented here: tools/config_validator.py is
    the schema authority (its exact messages are surfaced verbatim) and
    tools/ledger_lib.py is the ledger authority (load_events +
    current_state). This CLI only derives presentation state: pin
    status, per-repo local-checkout status, ledger summary, credential
    reference PRESENCE (an `env:NAME` reference is checked with
    `NAME in os.environ` — the value is never read; a `file:path`
    reference is an existence check relative to the workspace root).
  * Exit codes: 0 ok · 1 refusal/validation findings · 2 usage.
  * Text and `--json` carry the SAME data; JSON is sorted-key, LF,
    with a trailing newline.

Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

try:
    from . import config_validator as CV
    from . import ledger_lib as L
except ImportError:  # direct-run layout: load the tools package itself
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools import config_validator as CV
    from tools import ledger_lib as L

# Pin-resolution messages are the validator's CLI-layer findings (its own
# docstring: validate_workspace EXCLUDES resolution; pin_errors appends
# them). `workspace show` renders resolution as a factory_pin STATUS
# (deferred | resolved | unresolvable | not-checked) per the frozen
# output contract, so these two prefixes are separated from the
# schema findings that abort with rc 1.
_PIN_PREFIXES = ("pin resolution required:", "unresolvable pin:")


# ----------------------------------------------------------------- helpers ---

def _emit_json(data: Any) -> None:
    """JSON contract: sorted keys, LF, trailing newline."""
    sys.stdout.write(json.dumps(data, sort_keys=True, indent=2,
                                ensure_ascii=False) + "\n")


def _read_doc(path: Path) -> dict:
    """Parse an already-validated TOML file (validation stays with
    config_validator; this only re-reads the document for presentation)."""
    with path.open("rb") as f:
        return tomllib.load(f)


def _findings_rc(findings: list[str]) -> int:
    for err in findings:
        print(err)
    return 1


def _git(args: list[str]) -> subprocess.CompletedProcess | None:
    """Run git; None when the git binary is unavailable."""
    try:
        return subprocess.run(["git"] + args, capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        return None


def _repo_local_status(ws: Path, rel: str) -> str:
    """present | missing | not-git for a repo's local checkout dir under
    the workspace (`git rev-parse --git-dir` probe; missing git binary
    ⇒ not-git)."""
    d = ws / rel
    if not d.exists():
        return "missing"
    result = _git(["-C", str(d), "rev-parse", "--git-dir"])
    if result is None or result.returncode != 0:
        return "not-git"
    return "present"


def _pin_status(pin: Any, repo: str | None) -> dict:
    """Derive the factory_pin presentation state via config_validator
    (pin_kind / check_pin — never re-implemented here)."""
    kind = CV.pin_kind(pin)
    status: dict[str, Any] = {"pin": pin, "status": kind, "resolved": None,
                              "detail": None}
    if kind in ("deferred", "malformed"):
        return status
    if not repo:
        status["status"] = "not-checked"
        return status
    err = CV.check_pin(str(pin), repo)
    if err is None:
        status["status"] = "resolved"
        result = _git(["-C", repo, "rev-parse", f"{pin}^{{commit}}"])
        status["resolved"] = (result.stdout.strip()
                              if result is not None
                              and result.returncode == 0 else pin)
    else:
        status["status"] = "unresolvable"
        status["detail"] = err
    return status


def _pin_text(status: dict) -> str:
    pin = status["pin"]
    state = status["status"]
    if state == "deferred":
        return f"factory_pin: {pin} deferred"
    if state == "not-checked":
        return f"factory_pin: {pin} not-checked (pass --repo)"
    if state == "resolved":
        return f"factory_pin: {pin} resolved {status['resolved']}"
    detail = str(status["detail"]).removeprefix(f"unresolvable pin: {pin} ")
    detail = detail.removeprefix("(").removesuffix(")")
    return f"factory_pin: {pin} unresolvable — {detail}"


def _ledger_summary(ws: Path) -> tuple[dict | None, list[str]]:
    """(summary|None, findings). None ⇒ no ledger file (`ledger: none`).
    A rejected ledger is a named finding (rc 1) — ledger_lib is the
    authority."""
    path = ws / "factory" / "ledger" / "events.jsonl"
    if not path.exists():
        return None, []
    try:
        events = L.load_events(path)
    except L.LedgerError as exc:
        return None, [str(exc)]
    story_ids = sorted({str(e.get("story_id")) for e in events
                        if isinstance(e, dict) and e.get("story_id")})
    stories = {sid: L.current_state(events, sid) for sid in story_ids}
    latest = events[-1].get("recorded_at") if events else None
    return {"events": len(events), "latest": latest, "stories": stories}, []


def _ledger_text(summary: dict | None) -> str:
    if summary is None:
        return "ledger: none"
    stories = ", ".join(f"{sid}:{state or 'none'}"
                        for sid, state in summary["stories"].items())
    return (f"ledger: events={summary['events']} "
            f"latest={summary['latest']} stories={stories}")


def _environments(ws: Path) -> list[str]:
    env_dir = ws / "environments"
    if not env_dir.is_dir():
        return []
    return sorted(p.stem for p in env_dir.glob("*.toml"))


def _env_path(ws: Path, arg: str) -> Path:
    """A value containing '/' or ending '.toml' is a path (relative to
    the workspace root unless absolute); else a name under
    <workspace>/environments/."""
    if "/" in arg or arg.endswith(".toml"):
        p = Path(arg)
        return p if p.is_absolute() else ws / p
    return ws / "environments" / f"{arg}.toml"


def _validate_env(path: Path, strict: bool) -> tuple[dict, list[str]]:
    """Validate one environment file via config_validator (exact
    messages); returns (doc or {} on findings, findings)."""
    errs, parsed = CV.validate_path(path, strict=strict)
    if not parsed or errs:
        return {}, errs
    return _read_doc(path), []


def _kv(table: dict | None, keys: tuple[str, ...]) -> str:
    parts = []
    for key in keys:
        if table and key in table:
            parts.append(f"{key}={table[key]}")
    return " ".join(parts)


def _table(doc: dict, key: str) -> dict:
    """A declared sub-table, or {} when absent/not a table (validation
    already reported the type finding upstream)."""
    value = doc.get(key)
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------- workspace show ---

def cmd_workspace_show(args: argparse.Namespace) -> int:
    ws = Path(args.workspace)
    ws_toml = ws / "workspace.toml"
    errs, parsed = CV.validate_path(ws_toml, strict=False, repo=args.repo)
    if not parsed:
        return _findings_rc(errs)
    schema_errs = [e for e in errs if not e.startswith(_PIN_PREFIXES)]
    if schema_errs:
        return _findings_rc(schema_errs)  # validation first: no partial output
    doc = _read_doc(ws_toml)

    pin = _table(doc, "factory").get("pin")
    pin_status = _pin_status(pin, args.repo)

    repos = []
    for item in doc.get("repos") or []:
        if not isinstance(item, dict):
            continue
        rel = item.get("path") or f"repos/{item.get('id')}"
        entry = {"id": item.get("id"), "role": item.get("role"),
                 "url": item.get("url"),
                 "local": _repo_local_status(ws, str(rel)),
                 "path": item.get("path")}
        repos.append(entry)

    summary, ledger_errs = _ledger_summary(ws)
    if ledger_errs:
        return _findings_rc(ledger_errs)
    envs = _environments(ws)

    if args.json:
        _emit_json({
            "workspace": doc.get("workspace"),
            "control_home": doc.get("control_home"),
            "factory_pin": pin_status,
            "repos": repos,
            "ledger": summary,
            "environments": envs,
        })
        return 0

    print(f"workspace: {doc.get('workspace')}")
    print(f"control_home: {doc.get('control_home')}")
    print(_pin_text(pin_status))
    print("repos:")
    for r in repos:
        line = (f"  - id={r['id']} role={r['role']} url={r['url']} "
                f"local={r['local']}")
        if r["path"]:
            line += f" path={r['path']}"
        print(line)
    print(_ledger_text(summary))
    print(f"environments: {', '.join(envs) if envs else '(none)'}")
    return 1 if pin_status["status"] == "unresolvable" else 0


# ---------------------------------------------------------------- env show ---

def cmd_env_show(args: argparse.Namespace) -> int:
    ws = Path(args.workspace)
    path = _env_path(ws, args.env)
    if not path.exists():
        return _findings_rc([f"environment not found: {args.env} ({path})"])
    doc, errs = _validate_env(path, strict=False)
    if errs:
        return _findings_rc(errs)

    dispatch = _table(doc, "dispatch")
    state = _table(doc, "state")
    models = _table(doc, "models")
    refs = _table(doc, "credentials").get("refs") or []
    limits = _table(doc, "limits")

    if args.json:
        _emit_json({
            "environment": args.env,
            "path": str(path),
            "runtime": doc.get("runtime"),
            "dispatch": dispatch,
            "state": state,
            "models": models,
            "credentials": {"refs": refs},
            "limits": limits,
        })
        return 0

    print(f"environment: {args.env}")
    print(f"runtime: {doc.get('runtime')}")
    print(f"dispatch: {_kv(dispatch, ('mechanism', 'isolation'))}")
    print(f"state: {_kv(state, ('agent_home', 'transcripts', 'usage'))}")
    tiered = [t for t in CV.MODEL_TIERS if t in models]
    print("models: " + " ".join(f"{t}={models[t]}" for t in tiered))
    print(f"credentials: {', '.join(refs) if refs else '(none)'}")
    print(f"limits: {_kv(limits, ('wall', 'runs', 'budget'))}")
    return 0


# -------------------------------------------------------------- env resolve ---

def _resolve_ref(ws: Path, ref: str) -> str:
    """Credential-reference status: PRESENCE ONLY for env:NAME (the value
    is never read); existence for file:path (relative to the workspace
    root unless absolute); deferred placeholders stay deferred."""
    if CV.is_deferred(ref):
        return "deferred"
    if ref.startswith("env:"):
        return "present" if ref[4:] in os.environ else "MISSING"
    if ref.startswith("file:"):
        p = Path(ref[5:])
        if not p.is_absolute():
            p = ws / p
        return "exists" if p.exists() else "MISSING"
    return "MISSING"  # unreachable after validation (REF_RE), fail closed


def cmd_env_resolve(args: argparse.Namespace) -> int:
    ws = Path(args.workspace)
    path = _env_path(ws, args.env)
    if not path.exists():
        return _findings_rc([f"environment not found: {args.env} ({path})"])
    doc, errs = _validate_env(path, strict=args.strict)
    if errs:
        return _findings_rc(errs)

    models = _table(doc, "models")
    limits = _table(doc, "limits")
    refs = _table(doc, "credentials").get("refs") or []

    resolved_models = {t: (f"deferred: {models[t]}"
                           if CV.is_deferred(models[t]) else models[t])
                       for t in models}
    resolved_limits = {k: (f"deferred: {v}" if CV.is_deferred(v) else v)
                       for k, v in limits.items()}
    cred_status = [{"ref": r, "status": _resolve_ref(ws, r)} for r in refs]
    missing = [c["ref"] for c in cred_status if c["status"] == "MISSING"]

    if args.json:
        _emit_json({
            "environment": args.env,
            "path": str(path),
            "models": resolved_models,
            "limits": resolved_limits,
            "credentials": cred_status,
            "missing": missing,
        })
    else:
        print(f"environment: {args.env}")
        tiered = [t for t in CV.MODEL_TIERS if t in resolved_models]
        print("models: " + " ".join(f"{t}={resolved_models[t]}"
                                    for t in tiered))
        print(f"limits: {_kv(resolved_limits, ('wall', 'runs', 'budget'))}")
        print("credentials:")
        for c in cred_status:
            print(f"  {c['ref']} {c['status']}")
    for ref in missing:
        print(f"missing credential reference: {ref}")
    return 1 if missing else 0


# --------------------------------------------------------------------- CLI ----

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factory",
        description="Workspace CLI (read path): workspace show · env "
                    "show|resolve. Read-only; validation is delegated to "
                    "config_validator / ledger_lib.")
    parser.add_argument("--workspace", default=".", metavar="dir",
                        help="control-home root (default: cwd)")
    sub = parser.add_subparsers(dest="group")

    wsp = sub.add_parser("workspace", help="workspace-level commands")
    wsub = wsp.add_subparsers(dest="action")
    show = wsub.add_parser("show", help="show workspace identity + derived "
                                        "state")
    show.add_argument("--repo", metavar="path", default=None,
                      help="factory repo for pin resolution (optional; "
                           "without it a concrete pin reports not-checked)")
    show.add_argument("--json", action="store_true")

    envp = sub.add_parser("env", help="environment commands")
    esub = envp.add_subparsers(dest="action")
    eshow = esub.add_parser("show", help="show an environment as declared")
    eshow.add_argument("env", metavar="name|path")
    eshow.add_argument("--json", action="store_true")
    eres = esub.add_parser("resolve", help="resolve an environment "
                                           "(models, limits, credential "
                                           "reference presence)")
    eres.add_argument("env", metavar="name|path")
    eres.add_argument("--strict", action="store_true",
                      help="refuse unresolved '<...>' placeholders")
    eres.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.group == "workspace" and args.action == "show":
        return cmd_workspace_show(args)
    if args.group == "env" and args.action == "show":
        return cmd_env_show(args)
    if args.group == "env" and args.action == "resolve":
        return cmd_env_resolve(args)
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
