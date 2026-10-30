#!/usr/bin/env python3
"""onboarding/test_out.py — the fresh-repo test-out driver.

Issue #22 part 4 (kit-v0.1 S3): the from-scratch test-out procedure,
encoded as a direct-run CLI with two modes:

  --smoke (default; the S3 acceptance mode)
      Deterministic and TEMP-ONLY: seeds a fresh repo from the kit's
      benchmark-app/ tree (or --seed-dir), git init -b main + an
      initial commit + the fake origin
      https://github.com/fixture-org/benchmark-app.git, then drives
      the REAL pipeline through the REAL CLIs (subprocess, argv
      lists): preflight --forge-fixture → inspect → propose
      --control-home in-repo → the operator-review edits (align the
      draft project_id; dummy LLM_API_KEY in the subprocess env —
      presence-only) → apply --forge-fixture --ui → a second apply
      (idempotency: all EXISTS, rc 0) → readiness.append_attest for
      C-13/U-1/U-2/A-2 → verify --forge-fixture → expect GREEN.
      The forge side is fixture-backed (never fetched) via
      onboarding/tests/fixtures/test_out/smoke_forge.json. Never
      touches the kit repo or the machine's repos.

  --full (the encoded v0.1.x procedure; NOT executed in S3)
      The real from-scratch run against a fresh GitHub repo (LIVE
      forge — no fixture): gh auth check → gh repo create → clone →
      the SAME pipeline MINUS synthetic attestations → the operator
      handoff. The live path NEVER appends attestations: it prints
      `ATTEST REQUIRED:` operator commands (C-13/U-1/U-2/A-2) and the
      operator runs the attests + `verify <repo>` themselves. On
      completion (success or failure) the temp checkout is RETAINED
      and its durable path printed (`kept: <path>`) — the checkout
      stays; remove it when done. Refuses without --confirm-full-run
      (named, rc 1, prints the plan instead); with it, every command
      is printed verbatim before execution. This live path is
      UNEXERCISED in v0.1-S3 (v0.1.x hardening item #1).

Every run prints `PASS:`-lines per step (or one `FAIL: <step> —
<rc + first error line>` and stops) and exactly ONE final `NEXT:`
line. Exit codes: 0 ok · 1 refusal/failure · 2 usage.

Laws: stdlib only; Python >= 3.11; subprocess = argv LISTS (never a
shell) with PYTHONDONTWRITEBYTECODE=1 (python children add -B); named
lowercase fail-closed messages, never a raw traceback on bad input;
LF line endings; temp-dir writes only (the kit repo is read-only).
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # read-only heritage (tools/factory.py law)

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

KIT_ROOT = Path(__file__).resolve().parents[1]
if str(KIT_ROOT) not in sys.path:
    sys.path.insert(0, str(KIT_ROOT))

try:
    from . import readiness as RD
except ImportError:  # direct-run layout
    from onboarding import readiness as RD

ONBOARD = KIT_ROOT / "onboarding" / "onboard.py"
DEFAULT_SEED = KIT_ROOT / "benchmark-app"
SMOKE_DATASET = (KIT_ROOT / "onboarding" / "tests" / "fixtures"
                 / "test_out" / "smoke_forge.json")
FAKE_ORIGIN = "https://github.com/fixture-org/benchmark-app.git"
SMOKE_PROJECT_ID = "fixture-org/projects/7"
SMOKE_NAME = "benchmark-smoke"
ATTEST_IDS = ("C-13", "U-1", "U-2", "A-2")
ATTEST_BY = "test-out-driver"
EMDASH = "\u2014"
GIT_IDENTITY = ("-c", "user.name=test-out-driver",
                "-c", "user.email=test-out-driver@fixture.invalid")

SMOKE_NEXT = ("NEXT: full from-scratch run (fresh GitHub repo) = the "
              "v0.1.x hardening item #1 — see --full.")
FAIL_NEXT = ("NEXT: fix the reported FAIL step, then re-run "
             "python3 onboarding/test_out.py --smoke.")
FULL_UNEXERCISED = "unexercised in v0.1-S3 (v0.1.x hardening item)"


class TestOutError(RuntimeError):
    """Named driver refusal (rc 1; never a raw traceback)."""


def validate_dir_name(name: str) -> None:
    """Confine a --name/--repo-name value to a single relative directory
    name: non-empty, no NUL, no '/' or '\\', not '.'/'..', and equal to
    its own Path(...).name. Anything else is a named refusal BEFORE any
    path join (an absolute or '..' name would escape the temp dir)."""
    bad = (not isinstance(name, str)
           or not name
           or "\x00" in name
           or "/" in name or "\\" in name
           or name in (".", "..")
           or Path(name).name != name)
    if bad:
        raise TestOutError(
            f"refused: bad --name '{name}' — must be a single relative "
            "directory name")


# ---------------------------------------------------------------- plumbing --

def child_env(live: bool = False) -> dict[str, str]:
    """The subprocess environment: bytecode off everywhere, and a dummy
    value for every `env:` credential ref the default drafts declare
    (W-3 is presence-only — values are never read).

    G8: git-config isolation (/dev/null for GIT_CONFIG_GLOBAL/SYSTEM)
    applies to the SMOKE path only. The LIVE path (--full) clones and
    pushes a REAL private GitHub repo, which needs the operator's git
    configuration (e.g. the gh credential helper) — nulling it there
    would break auth. `live=True` keeps os.environ's git config."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if not live:
        env["GIT_CONFIG_GLOBAL"] = "/dev/null"
        env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    env.setdefault("LLM_API_KEY", "dummy-presence-only-value")
    return env


def run_argv(argv: list[str]) -> subprocess.CompletedProcess:
    """The ONE subprocess seam: argv lists only (never a shell)."""
    return subprocess.run(argv, capture_output=True, text=True,
                          env=child_env())


def run_argv_live(argv: list[str]) -> subprocess.CompletedProcess:
    """The LIVE-path runner: same argv-list seam, but the child keeps
    the operator's git configuration (G8)."""
    return subprocess.run(argv, capture_output=True, text=True,
                          env=child_env(live=True))


def python_argv(script: Path, *args: str) -> list[str]:
    """Subprocess law: python invocations always carry -B."""
    return [sys.executable, "-B", str(script), *args]


def first_error_line(out: subprocess.CompletedProcess) -> str:
    """The child's first meaningful error line (named FAIL detail)."""
    text = f"{out.stdout}\n{out.stderr}".strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines:
        if ln.startswith(("FAIL", "refused:", "verify error:")):
            return ln
    return lines[-1] if lines else f"rc={out.returncode}"


# -------------------------------------------------------------------- seed --

def seed_repo(seed_dir: Path, dest: Path) -> None:
    """Copy the seed tree (files only) into dest and make it a fresh
    git repo: init -b main + an initial commit + the fake origin.
    Refuses (named) when the seed carries a nested .git OR any symlink
    — a preserved symlink could point OUTSIDE the temp dir and later
    writes (e.g. ensure_gitignore_secrets) would mutate the original
    target. The scan happens BEFORE any copy: a refusal leaves dest
    uncreated. The copy itself is symlink-free too (defense in depth)."""
    if not seed_dir.is_dir():
        raise TestOutError(f"seed directory not found: {seed_dir}")
    nested = sorted(seed_dir.rglob(".git"))
    if nested:
        raise TestOutError(f"refused: seed carries a nested .git: "
                           f"{nested[0]} (the seed must be files only)")
    for path in sorted(seed_dir.rglob("*")):
        if path.is_symlink():
            raise TestOutError(
                f"refused: seed carries a symlink: {path} "
                "(the seed must be symlink-free)")
    shutil.copytree(seed_dir, dest, symlinks=False)
    for argv in (
        ["git", "init", "-b", "main", str(dest)],
        ["git", "-C", str(dest), *GIT_IDENTITY, "add", "-A"],
        ["git", "-C", str(dest), *GIT_IDENTITY, "commit", "-m",
         "initial commit (test-out seed)"],
        ["git", "-C", str(dest), "remote", "add", "origin", FAKE_ORIGIN],
    ):
        out = run_argv(argv)
        if out.returncode != 0:
            raise TestOutError(f"seed git step failed: {' '.join(argv[2:5])}"
                               f"… — {first_error_line(out)}")


# ------------------------------------------------------------------ pipeline --

Runner = Callable[[list[str]], subprocess.CompletedProcess]


class StepFailure(RuntimeError):
    """One pipeline step did not meet its expectation."""

    def __init__(self, step: str, detail: str):
        super().__init__(detail)
        self.step = step
        self.detail = detail


def expect(runner: Runner, argv: list[str], step: str,
           want_rc: int = 0) -> subprocess.CompletedProcess:
    out = runner(argv)
    if out.returncode != want_rc:
        raise StepFailure(step, f"rc={out.returncode} "
                                f"(expected {want_rc}); "
                                f"{first_error_line(out)}")
    return out


def align_project_id(repo: Path, project_id: str) -> None:
    """Operator-review edit (a): align the draft's deferred
    project_id to the smoke dataset's project."""
    draft = repo / "onboarding-drafts" / "workspace.toml"
    if not draft.is_file():
        raise StepFailure("operator-review",
                          f"draft missing: {draft} (propose did not run?)")
    text = draft.read_text(encoding="utf-8")
    placeholder = 'project_id = "<org>/projects/<n>"'
    if placeholder not in text:
        raise StepFailure("operator-review",
                          "draft project_id placeholder not found — the "
                          "workspace template drifted")
    draft.write_text(text.replace(placeholder,
                                  f'project_id = "{project_id}"'),
                     encoding="utf-8", newline="\n")


def ensure_gitignore_secrets(repo: Path) -> None:
    """Operator-review edit (c): the seed may carry its own .gitignore
    (apply's never-overwrite law keeps it), so append the secret-ish
    ignore patterns C-12 requires when they are absent. G9: a seed
    .gitignore that is not valid UTF-8 is a NAMED operator-review
    StepFailure — never a raw UnicodeDecodeError traceback."""
    gitignore = repo / ".gitignore"
    text = ""
    if gitignore.is_file():
        try:
            text = gitignore.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise StepFailure(
                "operator-review",
                f"seed .gitignore unreadable: {gitignore} — {exc}")
        except OSError as exc:
            raise StepFailure(
                "operator-review",
                f"seed .gitignore unreadable: {gitignore} — {exc}")
    if any(pat in text.lower() for pat in RD.SECRETISH_PATTERNS):
        return
    block = ("\n# secrets posture (test-out operator review — C-12)\n"
             ".env\n.env.*\nsecrets/\n*.pem\n*secret*\n*token*\n")
    prefix = "" if (not text or text.endswith("\n")) else "\n"
    gitignore.write_text(text + prefix + block,
                         encoding="utf-8", newline="\n")


def run_pipeline(repo: Path, dataset: Optional[str], project_id: str,
                 runner: Runner, log: Callable[[str, str], None],
                 postures: tuple[str, ...] = ("brownfield",),
                 attest_mode: str = "synthetic") -> None:
    """The from-scratch pipeline through the REAL CLIs. `dataset`
    None = live forge (--full); otherwise --forge-fixture <dataset>.
    `postures` = acceptable `inspect` posture values: smoke seeds a
    source tree (brownfield only); --full clones a fresh GitHub repo
    (greenfield, or brownfield once seeded).
    `attest_mode`: "synthetic" (fixture-backed SMOKE only) appends
    approved human_gate rows via readiness.append_attest and drives
    `verify` to GREEN; "operator" (the LIVE path) NEVER appends
    attestations — it prints the exact `ATTEST REQUIRED:` operator
    commands for C-13/U-1/U-2/A-2 and hands off (the operator runs
    the attests + `verify <repo>` themselves).
    Raises StepFailure on the first deviation."""
    if attest_mode not in ("synthetic", "operator"):
        raise TestOutError(f"refused: unknown attest_mode {attest_mode!r}")
    fx = ["--forge-fixture", dataset] if dataset else []
    onboard = lambda *args: python_argv(ONBOARD, *args)  # noqa: E731

    expect(runner, onboard("preflight", str(repo), *fx), "preflight")
    log("preflight", f"rc 0 — named readiness checks green"
        + (f" (forge fixture: {Path(dataset).name})" if dataset
           else " (live forge)"))

    out = expect(runner, onboard("inspect", str(repo)), "inspect")
    posture = next((ln.split(": ", 1)[1].strip()
                    for ln in out.stdout.splitlines()
                    if ln.startswith("posture: ")), None)
    if posture not in postures:
        expected = " / ".join(repr(p) for p in postures)
        raise StepFailure("inspect", f"posture {posture!r} — expected "
                                     f"{expected}")
    log("inspect", f"rc 0 — posture {posture}")

    expect(runner, onboard("propose", str(repo), "--control-home", "in-repo"),
           "propose")
    log("propose", "rc 0 — drafts written + validated (control-home in-repo)")

    align_project_id(repo, project_id)
    ensure_gitignore_secrets(repo)
    log("operator-review",
        f"draft project_id aligned to {project_id}; .gitignore carries the "
        "secret-ish patterns (C-12); subprocess env carries a dummy "
        "LLM_API_KEY (presence-only — values never read)")

    out = expect(runner, onboard("apply", str(repo), *fx, "--ui"), "apply")
    installed = sum(1 for ln in out.stdout.splitlines()
                    if ln.startswith("INSTALLED:"))
    if installed == 0:
        raise StepFailure("apply", "no INSTALLED: lines — apply wrote nothing")
    log("apply", f"rc 0 — {installed} file(s) installed (--ui; labels ensured)")

    out = expect(runner, onboard("apply", str(repo), *fx, "--ui"),
                 "apply-idempotent")
    if any(ln.startswith("INSTALLED:") for ln in out.stdout.splitlines()):
        raise StepFailure("apply-idempotent",
                          "second apply still INSTALLs — not idempotent")
    exists = sum(1 for ln in out.stdout.splitlines()
                 if ln.startswith("EXISTS, keeping:"))
    if exists == 0:
        raise StepFailure("apply-idempotent",
                          "second apply printed no EXISTS skip lines")
    log("apply-idempotent",
        f"rc 0 — re-apply skipped everything ({exists} EXISTS lines)")

    if attest_mode == "operator":
        # LIVE path: synthetic attestations would be lies — print the
        # exact operator commands and hand off (no ledger writes here).
        kit = str(KIT_ROOT / "onboarding" / "onboard.py")
        for cid in ATTEST_IDS:
            print(f"ATTEST REQUIRED: python3 {kit} attest {repo} {cid} "
                  f"--by <you> --note \"<command|manual>\"")
        log("attests", "LIVE path: NO synthetic attestations appended — "
                       f"operator must run the {len(ATTEST_IDS)} "
                       "ATTEST REQUIRED commands above, then "
                       f"`python3 {kit} verify {repo}` (expect GREEN)")
        return

    for cid in ATTEST_IDS:
        try:
            RD.append_attest(repo, cid, ATTEST_BY, f"test-out attest {cid}")
        except (RD.AttestError, OSError) as exc:
            raise StepFailure("attests", f"append_attest {cid} refused: {exc}")
    log("attests", f"fixture-backed smoke: {', '.join(ATTEST_IDS)} appended "
                   "via readiness.append_attest (synthetic rows, "
                   f"attributed {ATTEST_BY})")

    out = expect(runner, onboard("verify", str(repo), *fx), "verify")
    fails = [ln for ln in out.stdout.splitlines() if ln.startswith("FAIL ")]
    if "verify status: GREEN" not in out.stdout or fails:
        detail = fails[0] if fails else "no GREEN status line"
        raise StepFailure("verify", f"not GREEN — {detail}")
    c13 = next((ln for ln in out.stdout.splitlines()
                if ln.startswith("PASS C-13")), None)
    if c13 is None or "attested by" not in c13:
        raise StepFailure("verify", "C-13 line does not show the attest")
    summary = next((ln for ln in out.stdout.splitlines()
                    if ln.startswith("required:")), "required: (unknown)")
    log("verify", f"rc 0 — verify status: GREEN ({summary}; "
                  f"{c13.split(' — ')[-1].strip()})")


# ------------------------------------------------------------------- smoke --

def run_smoke(args: argparse.Namespace) -> int:
    """The S3 acceptance mode: deterministic, temp-only, fixture-backed."""
    try:
        validate_dir_name(args.name)
    except TestOutError as exc:
        print(str(exc))
        print(FAIL_NEXT)
        return 1
    dataset = Path(args.dataset)
    seed_dir = Path(args.seed_dir) if args.seed_dir else DEFAULT_SEED
    tmp = Path(tempfile.mkdtemp(prefix="test-out-smoke."))
    repo = tmp / args.name
    passed: list[str] = []
    try:
        print(f"smoke: seed {seed_dir} -> {repo}")
        try:
            seed_repo(seed_dir, repo)
        except TestOutError as exc:
            print(f"FAIL: seed — {exc}")
            print(FAIL_NEXT)
            return 1
        passed.append("seed")
        print(f"PASS: seed — {args.name} seeded (git init -b main + initial "
              f"commit + fake origin fixture-org/benchmark-app)")
        try:
            run_pipeline(repo, str(dataset), SMOKE_PROJECT_ID, run_argv,
                         lambda step, detail: (passed.append(step),
                                               print(f"PASS: {step} — "
                                                     f"{detail}"))[0])
        except StepFailure as failure:
            print(f"FAIL: {failure.step} — {failure.detail}")
            print(FAIL_NEXT)
            return 1
        except (TestOutError, OSError) as exc:
            print(f"FAIL: pipeline — {exc}")
            print(FAIL_NEXT)
            return 1
    finally:
        if args.keep:
            print(f"temp dir kept: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"PASS: smoke — {len(passed) + 1} step(s) green end-to-end "
          f"(the real CLIs, fixture-backed forge)")
    print(SMOKE_NEXT)
    return 0


# -------------------------------------------------------------------- full --

def full_plan(name: str, owner: str, tmp: str, project_id: str,
              visibility: str = "--private") -> list[str]:
    """The encoded v0.1.x from-scratch procedure (one line per step;
    the commands print verbatim before execution)."""
    repo = f"{tmp}/{name}"
    kit = KIT_ROOT.as_posix()
    return [
        "gh auth status",
        f"gh repo create {name} {visibility}",
        f"git clone https://github.com/{owner}/{name}.git {repo}",
        f"python3 {kit}/onboarding/onboard.py preflight {repo}",
        f"python3 {kit}/onboarding/onboard.py inspect {repo}",
        f"python3 {kit}/onboarding/onboard.py propose {repo} "
        f"--control-home in-repo --repo-url github.com/{owner}/{name}",
        f"# operator review: set board.project_id = {project_id} in "
        f"{repo}/onboarding-drafts/workspace.toml + export every env: "
        f"credential ref (presence-only)",
        f"python3 {kit}/onboarding/onboard.py apply {repo} --ui",
        f"python3 {kit}/onboarding/onboard.py apply {repo} --ui   "
        f"# idempotency: all EXISTS, rc 0",
        f"python3 {kit}/onboarding/onboard.py attest {repo} C-13 "
        f"--by <login> --note 'test-out attest C-13'   # + U-1, U-2, A-2",
        f"python3 {kit}/onboarding/onboard.py verify {repo}   "
        f"# expect GREEN",
        f"# handoff: {repo}/factory/KICKOFF.md — next action",
    ]


def run_full(args: argparse.Namespace) -> int:
    """The encoded v0.1.x procedure. Refuses without
    --confirm-full-run (prints the plan; ZERO subprocess calls).
    The live path is labeled: never executed in v0.1-S3."""
    name = args.repo_name or "<repo-name>"
    owner = "<owner>"
    tmp = "<tmp-dir>"
    project_id = args.project_id or "<org>/projects/<n>"
    print(f"full from-scratch run — LIVE forge path, {FULL_UNEXERCISED}:")
    visibility = "--public" if args.public else "--private"
    for i, line in enumerate(full_plan(name, owner, tmp, project_id,
                                       visibility), 1):
        print(f"  {i:2d}. {line}")
    if not args.confirm_full_run:
        print("refused: --full requires --confirm-full-run (this mode "
              "creates a real GitHub repo and drives the LIVE forge)")
        print("NEXT: re-run with --confirm-full-run --repo-name <name> "
              "--project-id <org>/projects/<n> to execute the plan above.")
        return 1
    if args.repo_name is None or args.project_id is None:
        print("refused: --confirm-full-run needs --repo-name and "
              "--project-id (the live run cannot guess them)")
        print("NEXT: re-run with --repo-name <name> --project-id "
              "<org>/projects/<n>.")
        return 1
    try:
        validate_dir_name(args.repo_name)
    except TestOutError as exc:
        print(str(exc))
        print(FAIL_NEXT)
        return 1
    # --- live execution (unexercised in v0.1-S3) --------------------------
    # G8: EVERY live-path subprocess uses run_argv_live — the operator's
    # git configuration (credential helper etc.) must reach clone/push.
    tmp_dir = Path(tempfile.mkdtemp(prefix="test-out-full."))
    repo = tmp_dir / args.repo_name
    try:
        out = expect(run_argv_live, ["gh", "auth", "status"], "gh-auth")
        print(f"PASS: gh-auth — {first_error_line(out)}")
        login = None
        for ln in f"{out.stdout}\n{out.stderr}".splitlines():
            if "Logged in to" in ln:
                if "account" not in ln:
                    # the missing-token shape: split("account") would
                    # silently yield the line's FIRST whitespace token
                    # (e.g. a check-mark glyph) as the login that names
                    # `gh repo create` ownership — a named refusal
                    # instead (RA-003).
                    raise TestOutError(
                        "gh-auth — cannot parse the account login from "
                        f"the line (no 'account' token): {ln.strip()!r}")
                tail = ln.split("account")[-1].split()
                if not tail:
                    raise TestOutError(
                        "gh-auth — cannot parse the account login from "
                        "the line (nothing after 'account'): "
                        f"{ln.strip()!r}")
                login = tail[0]
        owner = login or "<owner>"
        argv = ["gh", "repo", "create", args.repo_name, visibility]
        print(f"RUN: {argv!r}")
        out = expect(run_argv_live, argv, "gh-repo-create")
        url = out.stdout.strip() or out.stderr.strip()
        clone_url = url if url.startswith("http") else \
            f"https://github.com/{owner}/{args.repo_name}.git"
        print(f"PASS: gh-repo-create — {clone_url}")
        argv = ["git", "clone", clone_url, str(repo)]
        print(f"RUN: {argv!r}")
        expect(run_argv_live, argv, "clone")
        print(f"PASS: clone — {repo}")
        for argv in (["git", "-C", str(repo), *GIT_IDENTITY, "commit",
                      "--allow-empty", "-m", "initial commit (test-out)"],
                     ["git", "-C", str(repo), "push", "origin", "HEAD"]):
            print(f"RUN: {argv!r}")
            expect(run_argv_live, argv, "initial-commit")
        run_pipeline(repo, None, args.project_id, run_argv_live,
                     lambda step, detail: print(f"PASS: {step} — {detail}"),
                     postures=("greenfield", "brownfield"),
                     attest_mode="operator")
        print(f"PASS: handoff — see {repo / 'factory' / 'KICKOFF.md'} "
              "(next action)")
    except StepFailure as failure:
        print(f"FAIL: {failure.step} — {failure.detail}")
        _print_full_retention(tmp_dir, repo)
        print(FAIL_NEXT)
        return 1
    _print_full_retention(tmp_dir, repo)
    print("NEXT: run the ATTEST REQUIRED commands above, then `python3 "
          f"{KIT_ROOT / 'onboarding' / 'onboard.py'} verify {repo}` "
          "(expect GREEN); open the repo's factory/KICKOFF.md and start "
          "the first factory run.")
    return 0


def _print_full_retention(tmp_dir: Path, repo: Path) -> None:
    """F8: the --full checkout is RETAINED (success or failure) and its
    durable path printed — the KICKOFF.md handoff must exist somewhere
    after the driver returns. The checkout stays; remove it when done.
    (--keep is a no-op for --full: retention is unconditional.)"""
    print(f"kept: {tmp_dir}")
    print(f"kept: checkout {repo} — factory/KICKOFF.md handoff inside "
          "(the checkout stays; remove it when done)")


# --------------------------------------------------------------------- CLI --

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="test_out.py",
        description="fresh-repo test-out driver: --smoke (deterministic, "
                    "fixture-backed, temp-only) | --full (the encoded "
                    "v0.1.x live procedure; requires --confirm-full-run)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke", action="store_true",
                      help="the S3 acceptance mode (default)")
    mode.add_argument("--full", action="store_true",
                      help=f"the encoded v0.1.x procedure "
                           f"({FULL_UNEXERCISED})")
    parser.add_argument("--seed-dir", default=None, metavar="DIR",
                        help="override the smoke seed source (default: "
                             "the kit's benchmark-app/)")
    parser.add_argument("--name", default=SMOKE_NAME, metavar="NAME",
                        help=f"smoke seed repo name (default: {SMOKE_NAME})")
    parser.add_argument("--dataset", default=str(SMOKE_DATASET),
                        metavar="FILE", help="the smoke forge dataset")
    parser.add_argument("--keep", action="store_true",
                        help="keep the temp dir and print its path")
    parser.add_argument("--confirm-full-run", action="store_true",
                        help="required to EXECUTE --full (live forge)")
    parser.add_argument("--repo-name", default=None, metavar="NAME",
                        help="--full: the fresh GitHub repo name")
    parser.add_argument("--project-id", default=None,
                        metavar="<org>/projects/<n>",
                        help="--full: the board project locator")
    parser.add_argument("--public", action="store_true",
                        help="--full: create the repo public (default: private)")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry: 0 ok · 1 refusal/failure · 2 usage."""
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse usage error
        return int(exc.code) if isinstance(exc.code, int) else 2
    try:
        if args.full:
            return run_full(args)
        return run_smoke(args)
    except TestOutError as exc:
        print(f"refused: {exc}")
        print(FAIL_NEXT)
        return 1
    except OSError as exc:
        print(f"refused: {exc}")
        print(FAIL_NEXT)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
