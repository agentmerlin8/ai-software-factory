"""Deterministic review package builder and checker.

``tools/review/package.py`` (S3.5a T2) materialises a git commit range into a
self-describing Markdown review package.  Byte-identical rebuilds hold over
the same repository state and remote: the diff bytes come from the pinned
base/head SHAs, and the Identity ``repo:`` line embeds the configured origin
URL (or the local checkout path when no origin is set), so a different clone,
directory, or remote may change that line even at the same SHAs.  Nothing
derived from wall-clock time, terminal width, or working-tree file content
enters the package.

Package layout (fixed section order)::

    <!-- review-package v1 -->
    package_sha256: <64 hex>
    <blank>
    # Review package — <repo-name> <base7>..<head7>
    ## Identity
    ## Scope
    ## Contract inputs
    ## Gate evidence
    ## Diff stat
    ## Diff

``package_sha256`` is the SHA-256 of everything after the blank line that
follows the hash line (UTF-8), so the package is self-certifying.

CLI::

    package.py build --repo PATH --base SHA --head SHA --out FILE
        [--contract PATH[:LABEL]]... [--gate PATH[:LABEL]]...
        [--allowed FILE] [--withheld FILE]
        [--max-diff-bytes N] [--max-input-bytes N]

    package.py check --package FILE --repo ... --base ... --head ... [same]

Exit codes: 0 ok, 1 named refusal or difference, 2 usage.  Refusals carry
the fixed ``review package: `` prefix; unexpected failures are reported as a
one-line internal error, never a bare traceback.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import NoReturn

# Tools modules must also run when invoked directly as a file path, not only
# as a module (S3.5a T1 lesson): put the repository root on sys.path before
# any package imports.  This module itself only uses the standard library.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

VERSION_LINE = "<!-- review-package v1 -->"
HASH_LINE_PREFIX = "package_sha256: "
TITLE_PREFIX = "# Review package — "
DEFAULT_MAX_DIFF_BYTES = 1_500_000
DEFAULT_MAX_INPUT_BYTES = 200_000
TRUNCATED_FORMAT = "…[truncated: {n} bytes omitted]…"
FENCE_LANGUAGES = {".md": "md", ".py": "py", ".json": "json", ".txt": "txt"}
HASH_LINE_RE = re.compile(r"package_sha256: ([0-9a-f]{64})$")


def fail(message: str) -> NoReturn:
    """Print a named refusal and exit 1 (never a traceback)."""
    print(message, file=sys.stderr)
    raise SystemExit(1)


def git(repo, *args: str) -> subprocess.CompletedProcess:
    """Run git with a sanitised environment so output is config-immune.

    LC_ALL=C pins locale behaviour; nulled system/global config keeps the
    emitted bytes a pure function of the repository state and the flags.
    """
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def is_git_worktree(repo) -> bool:
    proc = git(repo, "rev-parse", "--is-inside-work-tree")
    return proc.returncode == 0 and proc.stdout.strip() == b"true"


def resolve_sha(repo, sha: str) -> str | None:
    """Full 40-hex commit sha, or None when the sha does not resolve."""
    proc = git(repo, "rev-parse", "--verify", f"{sha}^{{commit}}")
    if proc.returncode != 0:
        return None
    return proc.stdout.strip().decode("ascii", "replace")


def remote_origin_url(repo):
    proc = git(repo, "remote", "get-url", "origin")
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip().decode("utf-8", "replace")
    return None


def repo_name_from_url(url: str) -> str:
    name = url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[: -len(".git")]
    return name or "repo"


def strip_userinfo(url: str) -> str:
    """Rebuild ``url`` without any userinfo component.

    An https origin carrying credentials (``user:token@host``) would leak
    that token into the review package verbatim; the package instead
    embeds the origin with the userinfo removed.  Unparseable URLs pass
    through unchanged.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.username is None and parsed.password is None:
            return url
        hostname = parsed.hostname
    except ValueError:
        return url
    if not hostname:
        return url
    if ":" in hostname and not hostname.startswith("["):  # IPv6 literal
        hostname = f"[{hostname}]"
    netloc = f"{hostname}:{parsed.port}" if parsed.port else hostname
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path,
                                    parsed.query, parsed.fragment))


def fence_marker(text: str) -> str:
    """A fence marker longer than any backtick run in ``text`` (minimum 3).

    Fencing content with the longest-inside run plus one guarantees no
    content line can close the block early, no matter what the content
    contains.
    """
    longest = 0
    run = 0
    for char in text:
        if char == "`":
            run += 1
            if run > longest:
                longest = run
        else:
            run = 0
    return "`" * max(3, longest + 1)


def changed_statuses(repo, base: str, head: str):
    """[(status, path)] for every changed file, sorted by path.

    Renames are disabled, so a rename shows up as delete + add, matching the
    per-file diff command used under ## Diff.  ``-z`` makes the output
    NUL-delimited records (status NUL path NUL) with paths verbatim: no
    C-quoting, so quotes/backslashes/tabs/newlines in filenames parse back
    to the real path instead of feeding the per-file diff a quoted name.
    """
    proc = git(repo, "-c", "core.quotepath=false", "diff", "--no-renames",
               "--name-status", "-z", base, head)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip()
                           or "git diff --name-status failed")
    records = [record for record in proc.stdout.split(b"\0") if record]
    rows = []
    for index in range(0, len(records) - 1, 2):  # status NUL path NUL
        code, path = records[index], records[index + 1]
        rows.append((code.decode("ascii", "replace"),
                     path.decode("utf-8", "replace")))
    rows.sort(key=lambda row: row[1])
    return rows


def numstat_rows(repo, base: str, head: str):
    """[(added, deleted, path)] straight from git diff --numstat.

    Never ``git diff --stat``: that output depends on terminal width.
    Binary rows carry added=deleted="-".  ``-z`` terminates each record
    (added TAB deleted TAB path NUL) after the whole path, so paths with
    embedded tabs or newlines no longer break record boundaries either.
    """
    proc = git(repo, "-c", "core.quotepath=false", "diff", "--no-renames",
               "--numstat", "-z", base, head)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip()
                           or "git diff --numstat failed")
    rows = []
    for record in proc.stdout.split(b"\0"):
        if not record:
            continue
        parts = record.split(b"\t", 2)
        if len(parts) != 3:
            continue
        rows.append((parts[0].decode("ascii", "replace"),
                     parts[1].decode("ascii", "replace"),
                     parts[2].decode("utf-8", "replace")))
    return rows


def file_diff_text(repo, base: str, head: str, path: str) -> str:
    # --literal-pathspecs: a changed path passed after ``--`` is the literal
    # filename, never a glob — a file really named ``src/*.py`` must not
    # select (and embed) every other changed file under src/.
    proc = git(repo,
               "--literal-pathspecs",
               "-c", "diff.renames=false", "-c", "core.quotepath=false",
               "diff", "--no-renames", "--no-color", "--no-ext-diff",
               "--full-index", "-U3", base, head, "--", path)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip()
                           or f"git diff failed for {path}")
    return proc.stdout.decode("utf-8", "replace")


def split_spec(spec: str):
    """'PATH[:LABEL]' -> (path, label); default label is the path stem."""
    if ":" in spec:
        path, _, label = spec.partition(":")
        label = label or None
    else:
        path, label = spec, None
    return path, label or Path(path).stem


def read_input_bytes(path: str) -> bytes:
    """Read a contract/gate input, refusing by name on failure."""
    if not os.path.exists(path):
        fail(f"review package: input missing: {path}")
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except (OSError, ValueError):
        fail(f"review package: input unreadable: {path}")


def read_list_file(path: str, flag: str) -> list[str]:
    """--allowed/--withheld: one path per line; failures are usage errors."""
    if path is None:
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        print(f"usage: {flag}: file not found or unreadable: {path}",
              file=sys.stderr)
        raise SystemExit(2)
    return [line.strip() for line in text.splitlines() if line.strip()]


def render_input_block(path: str, label: str, data: bytes,
                       max_input_bytes: int) -> str:
    """One ``### label — path (sha256 …)`` fenced block.

    The hash covers the FULL file; the fenced content is deterministically
    capped at max_input_bytes with a literal truncation marker.
    """
    digest = hashlib.sha256(data).hexdigest()
    if len(data) > max_input_bytes:
        payload = (data[:max_input_bytes].decode("utf-8", "replace")
                   + TRUNCATED_FORMAT.format(n=len(data) - max_input_bytes))
    else:
        payload = data.decode("utf-8", "replace")
    if not payload.endswith("\n"):
        payload += "\n"
    lang = FENCE_LANGUAGES.get(Path(path).suffix.lower(), "")
    marker = fence_marker(payload)
    return (f"### {label} — {path} (sha256 {digest})\n\n"
            f"{marker}{lang}\n{payload}{marker}\n")


def render_inputs(specs: list[str], max_input_bytes: int) -> str:
    if not specs:
        return ""
    blocks = []
    for spec in specs:
        path, label = split_spec(spec)
        blocks.append(render_input_block(path, label, read_input_bytes(path),
                                         max_input_bytes))
    return "\n".join(blocks)


def diff_section_block(path: str, status: str, text: str, binary: bool) -> str:
    heading = f"### {path} ({status})\n\n"
    if binary:
        return heading + "[binary]\n"
    if not text.endswith("\n"):
        text += "\n"
    marker = fence_marker(text)
    return heading + f"{marker}diff\n" + text + marker + "\n"


def matches_entry(path: str, entries: list[str]) -> bool:
    """True when path equals one of ``entries`` or sits beneath it
    (component-wise, never a bare string prefix)."""
    for entry in entries:
        scope = entry.rstrip("/")
        if not scope:
            continue
        if path == scope or path.startswith(scope + "/"):
            return True
    return False


def matches_include(path: str, includes: list[str]) -> bool:
    """A path is inside an --include scope when it equals the scope path or
    sits beneath it (component-wise, never a bare string prefix)."""
    return matches_entry(path, includes)


def render_scope(allowed: list[str] | None, withheld: list[str] | None,
                 omitted: list[tuple[str, int]],
                 includes: list[str] | None = None,
                 binary: list[str] | None = None) -> str:
    lines = []
    if includes is None:
        lines.append("included_paths: all changed files\n")
    else:
        lines.append("included_paths:\n")
        lines.extend(f"- {p}\n" for p in includes)
    if allowed is None:
        lines.append("allowed_files: all changed files\n")
    else:
        lines.append("allowed_files:\n")
        lines.extend(f"- {p}\n" for p in allowed)
    if withheld is None:
        lines.append("withheld_files: none\n")
    else:
        lines.append("withheld_files:\n")
        lines.extend(f"- {p}\n" for p in withheld)
    if not omitted:
        lines.append("omitted_files: none\n")
    else:
        lines.append("omitted_files:\n")
        lines.extend(f"- {p} ({n} bytes)\n" for p, n in omitted)
    if not binary:
        lines.append("binary_files: none\n")
    else:
        lines.append("binary_files:\n")
        lines.extend(f"- {p}\n" for p in binary)
    return "".join(lines)


def render_section(heading: str, payload: str) -> str:
    if payload:
        return f"## {heading}\n\n{payload}"
    return f"## {heading}\n"


def compose_package(args) -> tuple[str, str]:
    """Build package text + its sha256. Raises SystemExit on refusals."""
    base = resolve_sha(args.repo, args.base)
    if base is None:
        fail(f"review package: unresolvable base sha: {args.base}")
    head = resolve_sha(args.repo, args.head)
    if head is None:
        fail(f"review package: unresolvable head sha: {args.head}")
    if base == head:
        fail("review package: base and head are the same commit")

    include_values = getattr(args, "include", None) or []
    for value in include_values:
        if not value.strip():
            # An empty include would otherwise build an empty list, skip the
            # scope filter entirely, and ship the whole diff.
            fail("review package: --include value must not be empty")
    includes = None
    if include_values:
        includes = [value.strip() for value in include_values]
    allowed = None
    if args.allowed is not None:
        allowed = read_list_file(args.allowed, "--allowed")
    withheld = None
    if args.withheld is not None:
        withheld = read_list_file(args.withheld, "--withheld")

    statuses = changed_statuses(args.repo, base, head)
    stats = numstat_rows(args.repo, base, head)
    if includes:
        statuses = [(status, path) for status, path in statuses
                    if matches_include(path, includes)]
        stats = [(a, d, path) for a, d, path in stats
                 if matches_include(path, includes)]
        if not statuses:
            fail("review package: --include matched no changed files: "
                 + ", ".join(includes))
    # --allowed/--withheld constrain what actually ships, not just the
    # Scope section: withheld entries (names or directory prefixes) must
    # not appear in the Diff stat or the Diff, and an allowed list keeps
    # only the listed paths.  Withheld wins over allowed when both match.
    if allowed is not None:
        statuses = [(status, path) for status, path in statuses
                    if matches_entry(path, allowed)]
        stats = [(a, d, path) for a, d, path in stats
                 if matches_entry(path, allowed)]
    if withheld is not None:
        statuses = [(status, path) for status, path in statuses
                    if not matches_entry(path, withheld)]
        stats = [(a, d, path) for a, d, path in stats
                 if not matches_entry(path, withheld)]
    if not statuses:
        fail("review package: no reviewable files after "
             "allowed/withheld filtering")
    num_by_path = {}
    for added, deleted, path in stats:
        num_by_path[path] = (added, deleted)
    # Binary files get a body marker but no readable content; the Scope
    # section lists them explicitly so a lens can see content is missing.
    binary_paths = sorted(path for a, d, path in stats
                          if a == "-" and d == "-")
    insertions = sum(int(a) for a, _ in num_by_path.values() if a.isdigit())
    deletions = sum(int(d) for _, d in num_by_path.values() if d.isdigit())

    blocks = []  # (path, size, text) in sorted path order
    for status, path in statuses:
        added, deleted = num_by_path.get(path, ("0", "0"))
        binary = added == "-" and deleted == "-"
        block = diff_section_block(path, status,
                                   file_diff_text(args.repo, base, head, path),
                                   binary)
        blocks.append((path, len(block.encode("utf-8")), block))

    # Whole-file inclusion until the next block would exceed the cap; that
    # block and every remaining one go to omitted_files (deterministic).
    total = 0
    cutoff = len(blocks)
    for index, (_, size, _) in enumerate(blocks):
        if total + size > args.max_diff_bytes:
            cutoff = index
            break
        total += size
    included = blocks[:cutoff]
    omitted = blocks[cutoff:]

    origin = remote_origin_url(args.repo)
    if origin is not None:
        origin = strip_userinfo(origin)
        identity_repo = origin
        repo_name = repo_name_from_url(origin)
    else:
        identity_repo = os.path.abspath(args.repo)
        repo_name = Path(identity_repo).name

    title = f"{TITLE_PREFIX}{repo_name} {base[:7]}..{head[:7]}\n"

    identity = "".join([
        f"repo: {identity_repo}\n",
        f"base_sha: {base}\n",
        f"head_sha: {head}\n",
        f"changed_files: {len(blocks)}\n",
        f"insertions: {insertions}\n",
        f"deletions: {deletions}\n",
    ])

    scope = render_scope(allowed, withheld,
                         [(p, s) for p, s, _ in omitted], includes,
                         binary_paths)

    stat_lines = [f"{a}\t{d}\t{p}\n" for a, d, p in stats]
    stat_lines.append(f"total: {len(blocks)} files, +{insertions} -{deletions}\n")

    body = (
        title + "\n"
        + render_section("Identity", identity) + "\n"
        + render_section("Scope", scope) + "\n"
        + render_section("Contract inputs",
                         render_inputs(args.contract or [],
                                       args.max_input_bytes)) + "\n"
        + render_section("Gate evidence",
                         render_inputs(args.gate or [],
                                       args.max_input_bytes)) + "\n"
        + render_section("Diff stat", "".join(stat_lines)) + "\n"
        + render_section("Diff", "\n".join(text for _, _, text in included))
    )

    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    text = f"{VERSION_LINE}\n{HASH_LINE_PREFIX}{digest}\n\n{body}"
    return text, digest


def parse_package_hash(data: bytes, path: str) -> str:
    """The hash recorded on line 2 of a package, refused by name if absent."""
    first_lines = data.split(b"\n", 2)
    if len(first_lines) < 2:
        fail(f"review package: package hash line missing: {path}")
    line = first_lines[1].decode("utf-8", "replace").strip()
    match = HASH_LINE_RE.match(line)
    if match is None:
        fail(f"review package: package hash line missing: {path}")
    return match.group(1)


def cmd_build(args) -> int:
    if not is_git_worktree(args.repo):
        fail(f"review package: not a git worktree: {args.repo}")
    text, digest = compose_package(args)
    try:
        with open(args.out, "wb") as fh:
            fh.write(text.encode("utf-8"))
    except OSError:
        fail(f"review package: out path not writable: {args.out}")
    print(f"package_path: {args.out}")
    print(f"package_sha256: {digest}")
    return 0


def cmd_check(args) -> int:
    if not is_git_worktree(args.repo):
        fail(f"review package: not a git worktree: {args.repo}")
    try:
        with open(args.package, "rb") as fh:
            recorded = fh.read()
    except FileNotFoundError:
        fail(f"review package: input missing: {args.package}")
    except OSError:
        fail(f"review package: input unreadable: {args.package}")
    recorded_hash = parse_package_hash(recorded, args.package)
    built_text, built_hash = compose_package(args)
    built = built_text.encode("utf-8")
    if built == recorded:
        print(f"review package: identical ({built_hash})")
        return 0
    offset = 0
    for pos, (lhs, rhs) in enumerate(zip(recorded, built)):
        if lhs != rhs:
            offset = pos
            break
    else:
        offset = min(len(recorded), len(built))
    print(f"review package: differs (built {built_hash}, "
          f"recorded {recorded_hash}) at byte {offset}")
    return 1


def nonnegative_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}")
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value


def add_common_arguments(parser: argparse.ArgumentParser, out_flag: bool) -> None:
    parser.add_argument("--repo", required=True, help="path to the git repository")
    parser.add_argument("--base", required=True, help="base commit sha")
    parser.add_argument("--head", required=True, help="head commit sha")
    parser.add_argument("--contract", action="append", default=[],
                        help="contract input PATH[:LABEL] (repeatable)")
    parser.add_argument("--gate", action="append", default=[],
                        help="gate evidence input PATH[:LABEL] (repeatable)")
    parser.add_argument("--allowed",
                        help="file listing allowed artifact paths, one per line")
    parser.add_argument("--include", action="append", default=[],
                        metavar="PATH",
                        help="review scope: only changed paths at or under "
                             "this path (repeatable)")
    parser.add_argument("--withheld",
                        help="file listing withheld artifact paths, one per line")
    parser.add_argument("--max-diff-bytes", type=nonnegative_int,
                        default=DEFAULT_MAX_DIFF_BYTES)
    parser.add_argument("--max-input-bytes", type=nonnegative_int,
                        default=DEFAULT_MAX_INPUT_BYTES)
    if out_flag:
        parser.add_argument("--out", required=True, help="package output path")
    else:
        parser.add_argument("--package", required=True,
                            help="package file to verify")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="package.py",
        description="Deterministic review package builder/checker (tools/review).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    sub = subparsers.add_parser("build", help="build a review package")
    add_common_arguments(sub, out_flag=True)
    sub = subparsers.add_parser("check",
                                help="verify a package rebuilds identically")
    add_common_arguments(sub, out_flag=False)
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        if args.command == "build":
            return cmd_build(args)
        return cmd_check(args)
    except Exception as exc:  # no bare tracebacks
        print(f"review package: internal error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())