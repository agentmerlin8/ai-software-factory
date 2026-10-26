#!/usr/bin/env python3
"""onboarding/onboard.py — the onboarding pipeline CLI.

Commands (frozen contract, kit-v0.1 S3):

  preflight <target> [--forge-fixture F]
      Named PASS/FAIL readiness checks: python3 >= 3.11, git, gh, forge
      auth (via the forge_probe seam; dataset-driven under
      --forge-fixture), and <target> inside a git work tree. NEVER
      writes. rc 1 when any check FAILs.

  inspect <target> [--json]
      LOCAL facts only (no network): posture (bundled iff >=3 of
      docs/{prd,architecture,ui-design,test-strategy}*.md; else
      brownfield iff any source file exists; else greenfield), slug
      candidate, origin/branch, existing-config presence, counts, kit
      facts. --json emits the SAME data (sorted keys, LF). rc 0.

  propose <target> [--workspace-name N] [--control-home dedicated|in-repo]
                   [--repo-id ID] [--repo-url URL] [--pin PIN]
                   [--envs local,github] [--strict]
      Drafts workspace.toml + environments/*.toml into
      <target>/onboarding-drafts/ from the kit's templates/workspace/*
      with substitutions (defaults from inspect + kit HEAD). Refuses
      when <target>/workspace.toml already exists. Drafts are validated
      with tools/config_validator (messages verbatim) and deferred
      '<...>' keys are listed; --strict refuses deferred keys. Drafts
      ALWAYS (re)write deterministically — the never-overwrite law
      binds apply, not this scratch area.

  apply <target> [--dry-run] [--forge-fixture F] [--ui|--no-ui]
      Installs the control-home surface from the drafts + kit assets.
      Laws: idempotent; NEVER overwrites (an existing file prints
      `EXISTS, keeping:`); --dry-run prints the exact planned actions
      (including every label argv) and writes NOTHING. Install set:
      root config, factory/{tools,gates} (no __pycache__), ledger seed,
      locks/OVERRIDE.example, factory/templates/, KICKOFF.md,
      SECRETS.md, .gitignore, .github/ISSUE_TEMPLATE/idea.yml,
      stories/README.md, factory/onboarding-registry.json record,
      posture extras (greenfield docs stubs / brownfield bootstrap
      checklist). Forge side: every base label in onboarding/
      registry.json is ensured via forge_probe.label_ensure (fixture
      transport under --forge-fixture). rc 1 on any FAIL.

  verify / attest <argv tail>
      Thin delegation to onboarding/readiness.py:main — the argv tail
      passes through verbatim and so does the rc (output unchanged;
      readiness owns its own NEXT/report lines).

EVERY pipeline command ends with exactly ONE `NEXT:` line naming the
next action. Exit codes: 0 ok · 1 refusal/findings · 2 usage.

Laws: stdlib only; Python >= 3.11; subprocess = argv LISTS (never a
shell) with PYTHONDONTWRITEBYTECODE=1 (python adds -B); no wall-clock
outside recorded date fields (the apply record's `applied_at`); named
lowercase fail-closed messages, never a raw traceback on bad input; LF
line endings + trailing newline on every written file.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # read-only heritage (tools/factory.py law)

import argparse
import json
import os
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

KIT_ROOT = Path(__file__).resolve().parents[1]
if str(KIT_ROOT) not in sys.path:
    sys.path.insert(0, str(KIT_ROOT))

try:
    from . import forge_probe as FP
    from . import readiness as RD
except ImportError:  # direct-run layout
    from onboarding import forge_probe as FP
    from onboarding import readiness as RD

from tools import config_validator as CV

TEMPLATES = Path(__file__).resolve().parent / "templates"
WS_TEMPLATES = KIT_ROOT / "templates" / "workspace"

run_argv = RD.run_argv          # the ONE subprocess seam (argv lists only)
EMDASH = RD.EMDASH

SOURCE_EXTS = frozenset({".cs", ".py", ".ts", ".tsx", ".js", ".go",
                         ".java", ".rs"})
EXCLUDED_DIRS = frozenset({".git", "node_modules", "factory", "docs"})
BUNDLE_PREFIXES = ("prd", "architecture", "ui-design", "test-strategy")

# templates kept for factory/templates/ (see the S3-T3 disposition table)
FACTORY_TEMPLATE_FILES = ("story.md", "story-state.md", "story-handoff-issue.md")
GREENFIELD_DOCS = {
    "docs/prd.md": "context-bundle-prd.md",
    "docs/architecture.md": "context-bundle-architecture.md",
    "docs/ui-design.md": "context-bundle-ui-design.md",
    "docs/test-strategy.md": "context-bundle-test-strategy.md",
    "docs/context-bundle-guide.md": "context-bundle-guide.md",
}


class OnboardError(RuntimeError):
    """Named pipeline refusal (rc 1; never a raw traceback)."""


# ---------------------------------------------------------------- helpers --

def kit_head_sha() -> str:
    """The kit repo's HEAD full sha (the default factory pin)."""
    out = run_argv(["git", "-C", str(KIT_ROOT), "rev-parse", "HEAD"])
    if out.returncode != 0:
        raise OnboardError("cannot resolve kit version: git rev-parse HEAD — "
                           f"{out.stderr.strip()}")
    return out.stdout.strip()


def make_forge(fixture: Optional[str]) -> FP.Forge:
    """The forge seam factory (ONE place; tests may substitute)."""
    if fixture:
        return FP.Forge.from_fixture(fixture)
    return FP.Forge()


def git_fact(target: Path, *args: str) -> Optional[str]:
    """Tolerated git one-liner: stripped stdout, or None on any failure."""
    out = run_argv(["git", "-C", str(target), *args])
    if out.returncode == 0 and out.stdout.strip():
        return out.stdout.strip()
    return None


def slugify(name: str) -> str:
    """Sanitize to the workspace-slug grammar ^[a-z0-9][a-z0-9-]*$ (≤64)."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    s = re.sub(r"^[^a-z0-9]+", "", s)[:64].rstrip("-")
    return s or "workspace"


def normalize_repo_url(url: Optional[str]) -> Optional[str]:
    """A git remote URL -> `host/owner/name` (validator URL grammar),
    or None when it cannot be normalized (template placeholder stays)."""
    if not url:
        return None
    u = url.strip()
    m = re.fullmatch(r"[^@/\s]+@([^:/]+):(.+)", u)  # scp-like ssh remote
    if m:
        u = f"{m.group(1)}/{m.group(2)}"
    else:
        u = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", u)
        u = u.split("@")[-1]  # drop any userinfo
    u = re.sub(r"\.git$", "", u).strip("/")
    return u if CV.URL_RE.fullmatch(u) else None


def deferred_keys(doc: Any, path: str = "") -> list[str]:
    """Dotted paths of every deferred '<...>' string in a parsed doc."""
    keys: list[str] = []
    if isinstance(doc, dict):
        for key, sub in doc.items():
            sub_path = f"{path}.{key}" if path else str(key)
            if CV.is_deferred(sub):
                keys.append(sub_path)
            keys.extend(deferred_keys(sub, sub_path))
    elif isinstance(doc, list):
        for i, item in enumerate(doc):
            item_path = f"{path}[{i}]"
            if isinstance(item, str):
                if CV.is_deferred(item):
                    keys.append(item_path)
            else:
                keys.extend(deferred_keys(item, item_path))
    return keys


# ----------------------------------------------------------------- inspect --

def collect_facts(target: Path) -> dict:
    """Every LOCAL fact inspect reports (apply reuses posture + origin)."""
    source_files = 0
    for root, dirs, files in os.walk(target):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        source_files += sum(1 for f in files if Path(f).suffix in SOURCE_EXTS)
    docs_dir = target / "docs"
    bundle = [pre for pre in BUNDLE_PREFIXES
              if docs_dir.is_dir() and any(docs_dir.glob(pre + "*.md"))]
    if len(bundle) >= 3:
        posture = "bundled"
    elif source_files:
        posture = "brownfield"
    else:
        posture = "greenfield"
    return {
        "target": str(target),
        "target_name": target.name,
        "workspace_slug": slugify(target.name),
        "posture": posture,
        "origin_url": git_fact(target, "remote", "get-url", "origin"),
        "current_branch": git_fact(target, "symbolic-ref", "--short", "HEAD"),
        "default_branch_guess": "main",
        "existing": {
            "workspace_toml": (target / "workspace.toml").is_file(),
            "environments_dir": (target / "environments").is_dir(),
            "factory_install": (target / "factory").is_dir(),
            "onboarding_drafts": (target / "onboarding-drafts").is_dir(),
        },
        "counts": {"source_files": source_files,
                   "bundle_docs": len(bundle)},
        "bundle_docs": bundle,
        "kit": {"root": str(KIT_ROOT), "head_sha": kit_head_sha()},
    }


def cmd_inspect(args: argparse.Namespace) -> int:
    target = Path(args.target)
    if not target.is_dir():
        print(f"refused: target not found: {args.target}")
        print(f"NEXT: point inspect at an existing directory.")
        return 1
    facts = collect_facts(target)
    if args.as_json:
        print(json.dumps(facts, sort_keys=True, indent=2, ensure_ascii=False))
    else:
        print(f"inspect: {facts['target']}")
        print(f"posture: {facts['posture']}")
        print(f"target_name: {facts['target_name']}")
        print(f"workspace_slug: {facts['workspace_slug']}")
        print(f"origin_url: {facts['origin_url'] or '(none)'}")
        print(f"current_branch: {facts['current_branch'] or '(none)'}")
        print(f"default_branch_guess: {facts['default_branch_guess']}")
        for key in sorted(facts["existing"]):
            print(f"existing.{key}: {facts['existing'][key]}")
        print(f"source_files: {facts['counts']['source_files']}")
        print(f"bundle_docs: {facts['counts']['bundle_docs']} "
              f"({', '.join(facts['bundle_docs']) or 'none'})")
        print(f"kit_root: {facts['kit']['root']}")
        print(f"kit_head_sha: {facts['kit']['head_sha']}")
    print(f"NEXT: python3 onboarding/onboard.py propose {args.target}")
    return 0


# --------------------------------------------------------------- preflight --

def cmd_preflight(args: argparse.Namespace) -> int:
    target = Path(args.target)
    checks: list[tuple[str, bool, str]] = []

    py_ok = sys.version_info >= (3, 11)
    checks.append(("python3", py_ok,
                   f"{sys.version_info.major}.{sys.version_info.minor}."
                   f"{sys.version_info.micro} "
                   + (">= 3.11" if py_ok else "is older than 3.11")))

    for tool in ("git", "gh"):
        try:
            out = run_argv([tool, "--version"])
        except OSError as exc:
            checks.append((tool, False, f"not runnable: {exc}"))
            continue
        first = (out.stdout or out.stderr or "").strip().splitlines()
        detail = first[0] if first else f"rc={out.returncode}"
        checks.append((tool, out.returncode == 0,
                       detail if out.returncode == 0
                       else f"--version rc={out.returncode} ({detail})"))

    try:
        forge = make_forge(args.forge_fixture)
        auth = forge.auth_scopes()
        missing = auth.get("missing") or []
        if missing:
            checks.append(("forge-auth", False,
                           f"missing scopes: {', '.join(missing)}"))
        else:
            login = auth.get("login") or "unknown"
            checks.append(("forge-auth", True,
                           f"authenticated as {login} with scopes "
                           f"{', '.join(auth.get('scopes') or [])}"))
    except FP.ProbeError as exc:
        checks.append(("forge-auth", False, RD.cannot_probe(exc)))

    if not target.is_dir():
        checks.append(("target-git-worktree", False,
                       f"target not found: {args.target}"))
    else:
        out = run_argv(["git", "-C", str(target), "rev-parse", "--git-dir"])
        if out.returncode == 0:
            checks.append(("target-git-worktree", True,
                           f"git work tree ({out.stdout.strip()})"))
        else:
            detail = out.stderr.strip().splitlines()
            checks.append(("target-git-worktree", False,
                           "not inside a git work tree — "
                           + (detail[-1] if detail else "rev-parse failed")))

    failed = False
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'} {name} {EMDASH} {detail}")
        failed = failed or not ok
    print(f"NEXT: python3 onboarding/onboard.py inspect {args.target}")
    return 1 if failed else 0


# ----------------------------------------------------------------- propose --

_MEMBER_BLOCK_RE = re.compile(
    r"\n\[\[repos\]\]\nid = \"web\"\nrole = \"member\"\n"
    r"url = \"github\.com/ORG/web\"\n# path = [^\n]*\n")


def render_workspace_draft(facts: dict, args: argparse.Namespace) -> str:
    """The substituted workspace.toml draft text (deterministic)."""
    text = (WS_TEMPLATES / "workspace.toml").read_text(encoding="utf-8")
    text = text.replace('workspace = "example-workspace"',
                        f'workspace = "{args.workspace_name}"')
    text = text.replace('control_home = "dedicated"',
                        f'control_home = "{args.control_home}"')
    text = text.replace('pin = "<kit tag/sha>"', f'pin = "{args.pin}"')
    text = text.replace('id = "app"', f'id = "{args.repo_id}"', 1)
    if args.repo_url:
        text = text.replace('url = "github.com/ORG/app"',
                            f'url = "{args.repo_url}"', 1)
    # the template's example member binding is illustrative only — a
    # drafted workspace declares the ONE inspected primary binding.
    text = _MEMBER_BLOCK_RE.sub("\n", text)
    return text


def validate_draft(path: Path) -> list[str]:
    """config_validator over one draft (pin resolves against the kit)."""
    repo = str(KIT_ROOT) if path.name == "workspace.toml" else None
    errs, _parsed = CV.validate_path(path, repo=repo)
    return errs


def print_validation(path: Path, errs: list[str]) -> None:
    """The config_validator result, its messages verbatim."""
    if not errs:
        print(f"OK {path}")
    else:
        print(f"FAIL {path}")
        for err in errs:
            print(f"  - {err}")


def draft_deferred(drafts: list[Path]) -> list[str]:
    """`<file>:<dotted.key>` for every deferred placeholder, sorted."""
    keys: list[str] = []
    for path in drafts:
        try:
            doc = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            continue  # the validator already refused it, verbatim above
        for key in deferred_keys(doc):
            keys.append(f"{path.name}:{key}")
    return sorted(keys)


def cmd_propose(args: argparse.Namespace) -> int:
    target = Path(args.target)
    if not target.is_dir():
        print(f"refused: target not found: {args.target}")
        print("NEXT: point propose at an existing directory.")
        return 1
    if (target / "workspace.toml").is_file():
        print("refused: already onboarded — propose not applicable; "
              "edit config directly")
        print(f"NEXT: edit {target / 'workspace.toml'} directly.")
        return 1
    if args.control_home not in CV.CONTROL_HOMES:
        print("refused: control_home must be dedicated|in-repo "
              f"(got {args.control_home!r})")
        print("NEXT: re-run propose with --control-home dedicated|in-repo.")
        return 1

    facts = collect_facts(target)
    if args.workspace_name is None:
        args.workspace_name = facts["workspace_slug"]
    if args.pin is None:
        args.pin = facts["kit"]["head_sha"]
    if args.repo_url is None:
        args.repo_url = normalize_repo_url(facts["origin_url"])

    if not CV.WS_SLUG_RE.fullmatch(args.workspace_name or ""):
        print("refused: bad workspace name — must match ^[a-z0-9][a-z0-9-]*$ "
              f"(got {args.workspace_name!r})")
        print("NEXT: re-run propose with --workspace-name <slug>.")
        return 1
    if not CV.REPO_ID_RE.fullmatch(args.repo_id or ""):
        print("refused: bad repo id — must match ^[a-z][a-z0-9-]*$ "
              f"(got {args.repo_id!r})")
        print("NEXT: re-run propose with --repo-id <id>.")
        return 1
    if CV.is_deferred(args.pin) or CV.pin_kind(args.pin) == "malformed":
        print("refused: bad pin — neither tag nor hex grammar "
              f"(got {args.pin!r})")
        print("NEXT: re-run propose with --pin <kit tag/sha>.")
        return 1
    env_names = [n.strip() for n in args.envs.split(",") if n.strip()]
    for name in env_names:
        if not (WS_TEMPLATES / "environments" / f"{name}.toml").is_file():
            print(f"refused: unknown environment template: {name} "
                  f"(no templates/workspace/environments/{name}.toml)")
            print("NEXT: re-run propose with --envs <name,...> from the "
                  "kit templates.")
            return 1

    drafts_dir = target / "onboarding-drafts"
    ws_draft = drafts_dir / "workspace.toml"
    text = render_workspace_draft(facts, args)
    ws_draft.parent.mkdir(parents=True, exist_ok=True)
    ws_draft.write_text(text, encoding="utf-8", newline="\n")
    print(f"WROTE: {ws_draft}")
    drafts = [ws_draft]
    for name in env_names:
        src = WS_TEMPLATES / "environments" / f"{name}.toml"
        dst = drafts_dir / "environments" / f"{name}.toml"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"),
                       encoding="utf-8", newline="\n")
        print(f"WROTE: {dst}")
        drafts.append(dst)

    failed = False
    for path in drafts:
        errs = validate_draft(path)
        print_validation(path, errs)
        failed = failed or bool(errs)
    deferred = draft_deferred(drafts)
    for key in deferred:
        print(f"DEFERRED: {key}")
    if args.strict and deferred:
        print(f"refused: --strict with {len(deferred)} deferred "
              "placeholder key(s)")
        failed = True
    print(f"NEXT: review the drafts under {drafts_dir}, then "
          f"python3 onboarding/onboard.py apply {args.target}")
    return 1 if failed else 0


# ------------------------------------------------------------------- apply --

class Installer:
    """Skip-if-exists file installer (the never-overwrite law)."""

    def __init__(self, target: Path, dry_run: bool):
        self.target = target
        self.dry_run = dry_run

    def install_bytes(self, rel: str, data: bytes) -> None:
        dest = self.target / rel
        if dest.exists():
            print(f"EXISTS, keeping: {rel}")
        elif self.dry_run:
            print(f"WOULD INSTALL: {rel}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            print(f"INSTALLED: {rel}")

    def install_file(self, rel: str, src: Path) -> None:
        try:
            data = src.read_bytes()
        except OSError as exc:
            raise OnboardError(f"template unreadable: {src} — {exc}")
        self.install_bytes(rel, data)

    def install_tree(self, rel_dst: str, src_dir: Path) -> None:
        """Copy a kit tree file-by-file (never __pycache__)."""
        for path in sorted(src_dir.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.relative_to(src_dir).parts:
                continue
            rel = path.relative_to(src_dir).as_posix()
            self.install_bytes(f"{rel_dst}/{rel}", path.read_bytes())


def build_record(registry: dict, kit_sha: str, ui: bool) -> bytes:
    record = {
        "registry_version": registry["registry_version"],
        "kit_version": kit_sha,
        "applied_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "declarations": {"ui": ui},
    }
    return (json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode()


def cmd_apply(args: argparse.Namespace) -> int:
    target = Path(args.target)
    if not target.is_dir():
        print(f"refused: target not found: {args.target}")
        print("NEXT: point apply at an existing directory.")
        return 1
    drafts_dir = target / "onboarding-drafts"
    ws_draft = drafts_dir / "workspace.toml"
    if not ws_draft.is_file():
        print(f"refused: drafts missing — run propose first "
              f"(no {ws_draft})")
        print(f"NEXT: python3 onboarding/onboard.py propose {args.target}")
        return 1
    env_drafts = sorted((drafts_dir / "environments").glob("*.toml")) \
        if (drafts_dir / "environments").is_dir() else []
    failed = False
    for path in [ws_draft, *env_drafts]:
        errs = validate_draft(path)
        if errs:
            print_validation(path, errs)
            failed = True
    if failed:
        print("refused: drafts do not validate — fix them (or re-run "
              "propose) before apply")
        print(f"NEXT: python3 onboarding/onboard.py propose {args.target}")
        return 1

    try:
        facts = collect_facts(target)
        registry = RD.load_registry()
        kit_sha = facts["kit"]["head_sha"]
    except (OnboardError, RD.EngineError) as exc:
        print(f"refused: {exc}")
        print(f"NEXT: fix the reported problem, then re-run apply.")
        return 1

    inst = Installer(target, args.dry_run)
    # 1. root config from the drafts
    inst.install_file("workspace.toml", ws_draft)
    for path in env_drafts:
        inst.install_file(f"environments/{path.name}", path)
    # 2. kit tools + gates (never __pycache__)
    inst.install_tree("factory/tools", KIT_ROOT / "tools")
    inst.install_tree("factory/gates", KIT_ROOT / "tools" / "gates")
    # 3. ledger seed
    inst.install_bytes("factory/ledger/events.jsonl", b"")
    # 4. locks example
    inst.install_file("factory/locks/OVERRIDE.example",
                      TEMPLATES / "OVERRIDE.example")
    # 5. factory templates
    for name in FACTORY_TEMPLATE_FILES:
        inst.install_file(f"factory/templates/{name}", TEMPLATES / name)
    # 6. cold-start + secrets docs (and the C-12 .gitignore posture)
    inst.install_file("factory/KICKOFF.md", TEMPLATES / "KICKOFF.md")
    inst.install_file("factory/SECRETS.md", TEMPLATES / "SECRETS.md")
    inst.install_file(".gitignore", TEMPLATES / "gitignore")
    # 7. idea front door
    inst.install_file(".github/ISSUE_TEMPLATE/idea.yml",
                      TEMPLATES / "idea.yml")
    # 8. stories surface
    inst.install_file("stories/README.md", TEMPLATES / "stories-readme.md")
    # 9. the onboarding record
    inst.install_bytes("factory/onboarding-registry.json",
                       build_record(registry, kit_sha, args.ui))
    # 10. posture extras
    posture = facts["posture"]
    if posture == "greenfield":
        for rel, tpl in sorted(GREENFIELD_DOCS.items()):
            inst.install_file(rel, TEMPLATES / tpl)
    elif posture == "brownfield":
        inst.install_file("factory/bootstrap-checklist.md",
                          TEMPLATES / "bootstrap-checklist.md")
    print(f"posture: {posture}")

    # 11. forge side: ensure every base label on the home repo
    repo = RD.repo_slug_from_url(facts["origin_url"] or "")
    labels = [lbl for lbl in registry["labels"]
              if isinstance(lbl, dict) and isinstance(lbl.get("name"), str)]
    if args.dry_run:
        for lbl in labels:
            argv = FP.build_label_ensure_argv(
                repo or "<origin-slug>", lbl["name"],
                lbl.get("color") or "", lbl.get("description") or "")
            print(f"WOULD: {argv!r}")
    elif repo is None:
        print("FAIL: label ensure — no origin remote on the target "
              "(cannot resolve the home repo)")
        failed = True
    else:
        try:
            forge = make_forge(args.forge_fixture)
        except FP.ProbeError as exc:
            print(f"FAIL: label ensure — {RD.cannot_probe(exc)}")
            failed = True
            forge = None
        if forge is not None:
            for lbl in labels:
                name = lbl["name"]
                try:
                    result = forge.label_ensure(
                        repo, name, lbl.get("color") or "",
                        lbl.get("description") or "")
                except FP.ProbeError as exc:
                    print(f"FAIL: label ensure {name} — "
                          f"{RD.cannot_probe(exc)}")
                    failed = True
                    continue
                if result.get("ok"):
                    print(f"ENSURED: label {name}")
                else:
                    print(f"FAIL: label ensure {name} — the forge refused "
                          "the write")
                    failed = True

    suffix = f" --forge-fixture {args.forge_fixture}" \
        if args.forge_fixture else ""
    print(f"NEXT: python3 onboarding/onboard.py verify {args.target}"
          f"{suffix}")
    return 1 if failed else 0


# --------------------------------------------------------------------- CLI --

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="onboard.py",
        description="factory onboarding pipeline: preflight → inspect → "
                    "propose → apply (verify/attest delegate to "
                    "readiness.py)")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("preflight", help="named readiness checks")
    p.add_argument("target")
    p.add_argument("--forge-fixture", default=None, metavar="file")

    p = sub.add_parser("inspect", help="local target facts")
    p.add_argument("target")
    p.add_argument("--json", action="store_true", dest="as_json")

    p = sub.add_parser("propose", help="draft the config surface")
    p.add_argument("target")
    p.add_argument("--workspace-name", default=None, metavar="N")
    p.add_argument("--control-home", default="dedicated",
                   choices=sorted(CV.CONTROL_HOMES), metavar="dedicated|in-repo")
    p.add_argument("--repo-id", default="app", metavar="ID")
    p.add_argument("--repo-url", default=None, metavar="URL")
    p.add_argument("--pin", default=None, metavar="PIN")
    p.add_argument("--envs", default="local,github", metavar="name,...")
    p.add_argument("--strict", action="store_true")

    p = sub.add_parser("apply", help="install the control-home surface")
    p.add_argument("target")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--forge-fixture", default=None, metavar="file")
    ui = p.add_mutually_exclusive_group()
    ui.add_argument("--ui", action="store_true", dest="ui")
    ui.add_argument("--no-ui", action="store_false", dest="ui")
    p.set_defaults(ui=False)

    sub.add_parser("verify", help="delegate: readiness verify")
    sub.add_parser("attest", help="delegate: readiness attest")
    return parser


DISPATCH = {
    "preflight": cmd_preflight,
    "inspect": cmd_inspect,
    "propose": cmd_propose,
    "apply": cmd_apply,
    "verify": None,   # delegation — handled before argparse
    "attest": None,
}
DELEGATED = ("verify", "attest")


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry: 0 ok · 1 refusal/findings · 2 usage."""
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv:
        parser.print_usage(sys.stderr)
        return 2
    command = argv[0]
    if command not in DISPATCH:
        parser.print_usage(sys.stderr)
        print(f"unknown command: {command}", file=sys.stderr)
        return 2
    if command in DELEGATED:
        # thin delegation: the argv tail passes through verbatim and so
        # does the rc; the wrapper adds ONLY the pipeline's single NEXT
        # line (readiness's own output is unchanged).
        rc = RD.main([command, *argv[1:]])
        if command == "verify":
            print("NEXT: proceed — the control home is ready for factory "
                  "runs (see factory/KICKOFF.md)." if rc == 0 else
                  "NEXT: work the FAIL rows above (verify prints a remedy "
                  "per failure), then re-run verify.")
        else:
            print("NEXT: re-run "
                  f"python3 onboarding/onboard.py verify {argv[1]}"
                  if rc == 0 and len(argv) > 1 else
                  "NEXT: fix the reported refusal, then re-run attest.")
        return rc
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse usage error
        return int(exc.code) if isinstance(exc.code, int) else 2
    try:
        return DISPATCH[command](args)
    except OnboardError as exc:
        print(f"refused: {exc}")
        return 1
    except OSError as exc:
        print(f"refused: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
