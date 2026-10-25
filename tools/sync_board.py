#!/usr/bin/env python3
"""tools/sync_board.py — factory <-> GitHub Projects v2 board bridge.

The board is the VISIBLE mirror of pipeline state; the ledger
(factory/ledger/events.jsonl) stays the append-only audit truth. Stage
writes are EXPLICIT, never inferred (architecture.md §Integrations): this
tool never derives a Stage from labels, titles, or other fields, and it
never creates or modifies board schema — fields and options are READ only.
A state with no matching Stage option is a fail-closed refusal naming the
missing option.

Commands (frozen):
    python3 tools/sync_board.py [--root R] [--project P] status
    python3 tools/sync_board.py [--root R] [--project P] [--repo O/N]
            add --issue <n> [--dry-run]
    python3 tools/sync_board.py [--root R] [--project P]
            set --issue <n> --stage <STATE> [--dry-run]

Project id resolution (no hardcoded project/org ids anywhere):
  * `--project <org>/projects/<n>` on the command line, else
  * `board.project_id` in the control home's `workspace.toml`
    (architecture.md §Configuration). Absent, deferred (`<...>`), or
    malformed is a named fail-closed refusal (rc 2).

Stage vocabulary: the state names from tools/state_machine.py (imported —
never re-typed here). `--stage` accepts them case-insensitively.

`--dry-run` on add/set prints the EXACT GraphQL payload and performs no
write (schema/item reads still happen). All GraphQL goes through ONE
injectable transport (Board's `_gql`): the default shells
`gh api graphql -f query=...` (gh CLI with `project` scope assumed);
tests substitute an in-memory recorder — no network, no gh calls.

Dropped from the reference implementation (genericization freeze): the
hardcoded project registry, the derived-cascade fields (Stage group /
Status / agent-step mirrors), field inference from labels/titles
(auto-add), lane README generation, and iteration/number/text field
writes — the kit board contract is the single explicit Stage write.

Exit codes (config_validator convention): 0 success · 1 refusal ·
2 usage / configuration. Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from . import state_machine as SM
except ImportError:  # direct-run layout: load the tools package itself
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools import state_machine as SM

# Stage vocabulary authority — imported, never re-typed.
STAGES = SM.STATES

PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9-]+/projects/\d+$")
DEFERRED_RE = re.compile(r"^<.*>$")
STAGE_FIELD = "Stage"


class BoardError(ValueError):
    """Named runtime refusal (fail-closed; nothing written). rc 1."""
    rc = 1


class ConfigError(BoardError):
    """Named configuration/usage refusal (fail-closed). rc 2."""
    rc = 2


# ---------------------------------------------------------------------------
# transport — ONE injectable seam. The real transport shells the gh CLI
# exactly like the reference; tests pass an in-memory recorder instead.
# ---------------------------------------------------------------------------

def gh_graphql(query: str, **variables: Any) -> dict:
    """Run a GraphQL query/mutation via gh; return .data (or raise).

    Only scalar variables (-f/-F) are supported. Object values are inlined
    into the query by the payload builders (gh cannot pass raw objects as
    variables)."""
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        if isinstance(v, bool):
            args += ["-F", f"{k}={str(v).lower()}"]
        elif isinstance(v, (int, float)):
            args += ["-F", f"{k}={v}"]
        else:
            args += ["-f", f"{k}={v}"]
    out = subprocess.run(args, capture_output=True, text=True)
    if out.returncode != 0:
        raise BoardError(f"gh graphql failed: {out.stderr.strip()}")
    data = json.loads(out.stdout)
    if "errors" in data:
        raise BoardError(f"GraphQL errors: {data['errors']}")
    return data.get("data", {})


# ---------------------------------------------------------------------------
# pure payload builders (deterministic strings — tests assert them verbatim)
# ---------------------------------------------------------------------------

def build_set_stage_payload(project_id: str, item_id: str,
                            field_id: str, option_id: str) -> str:
    """The exact Stage single-select write mutation (fully inlined)."""
    return (
        "mutation {\n"
        "  updateProjectV2ItemFieldValue(input: {\n"
        f'    projectId: "{project_id}", itemId: "{item_id}",\n'
        f'    fieldId: "{field_id}",\n'
        f'    value: {{singleSelectOptionId: "{option_id}"}}\n'
        "  }) { projectV2Item { id } }\n"
        "}"
    )


def build_add_item_payload(project_id: str, issue_id: str) -> str:
    """The exact add-item-to-project mutation (fully inlined)."""
    return (
        "mutation {\n"
        "  addProjectV2ItemById(input: {\n"
        f'    projectId: "{project_id}", contentId: "{issue_id}"\n'
        "  }) { item { id } }\n"
        "}"
    )


def build_issue_id_query(owner: str, name: str, number: int) -> str:
    """The exact issue-node-id lookup query (fully inlined)."""
    return (
        "query {\n"
        f'  repository(owner: "{owner}", name: "{name}") {{\n'
        f"    issue(number: {number}) {{ id }}\n"
        "  }\n"
        "}"
    )


def normalize_stage(value: str) -> str:
    """Normalize a stage input case-insensitively to the canonical state
    name. Raises ValueError listing every valid state on bad input."""
    if not isinstance(value, str):
        raise ValueError(f"stage must be a string, got {value!r}")
    canon = value.strip().upper()
    if canon not in STAGES:
        raise ValueError(
            f"invalid stage '{value}'. Valid stages ({len(STAGES)}): "
            + ", ".join(STAGES)
        )
    return canon


# ---------------------------------------------------------------------------
# configuration — project id and repo come from flags or workspace.toml,
# never from constants.
# ---------------------------------------------------------------------------

def load_project_id(root: Path, cli_value: Optional[str] = None) -> str:
    """Resolve `<org>/projects/<n>`: `--project` wins; else the control
    home's workspace.toml `board.project_id`. Absent, deferred (`<...>`),
    or malformed → named ConfigError (fail closed)."""
    if cli_value is not None:
        value = cli_value.strip()
        if DEFERRED_RE.fullmatch(value):
            raise ConfigError(
                f"REFUSED: --project is deferred ({value!r}) — pass a "
                "concrete <org>/projects/<n>")
        if not PROJECT_ID_RE.fullmatch(value):
            raise ConfigError(
                f"REFUSED: bad --project {value!r} — must match "
                "<org>/projects/<n>")
        return value
    p = root / "workspace.toml"
    unset = ConfigError(
        "REFUSED: board project id not configured — pass --project "
        f"<org>/projects/<n> or set board.project_id in {p}")
    try:
        doc = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raise unset from None
    board = doc.get("board")
    value = board.get("project_id") if isinstance(board, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise unset
    value = value.strip()
    if DEFERRED_RE.fullmatch(value):
        raise ConfigError(
            f"REFUSED: board.project_id is deferred ({value!r}) — set a "
            f"concrete <org>/projects/<n> in {p} or pass --project")
    if not PROJECT_ID_RE.fullmatch(value):
        raise ConfigError(
            f"REFUSED: bad board.project_id {value!r} — must match "
            "<org>/projects/<n>")
    return value


def load_repo(root: Path, cli_value: Optional[str] = None) -> tuple[str, str]:
    """Resolve (owner, name) for issue lookups: `--repo <owner>/<name>`
    wins; else the first `repos[].url` binding (`host/owner/name`) in the
    control home's workspace.toml. Absent/malformed → named ConfigError."""
    if cli_value is not None:
        parts = cli_value.strip().split("/")
        if len(parts) == 2 and all(parts):
            return parts[0], parts[1]
        raise ConfigError(
            f"REFUSED: bad --repo {cli_value!r} — must be <owner>/<name>")
    p = root / "workspace.toml"
    unset = ConfigError(
        "REFUSED: repo not configured — pass --repo <owner>/<name> or bind "
        f"repos in {p}")
    try:
        doc = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raise unset from None
    repos = doc.get("repos")
    if not isinstance(repos, list) or not repos:
        raise unset
    url = repos[0].get("url") if isinstance(repos[0], dict) else None
    parts = url.split("/") if isinstance(url, str) else []
    if len(parts) != 3 or not all(parts):
        raise unset
    return parts[1], parts[2]


# ---------------------------------------------------------------------------
# board — schema is READ only (board-schema changes are excluded); every
# GraphQL call goes through the single injectable transport `self._gql`.
# ---------------------------------------------------------------------------

class Board:
    def __init__(self, project_id: str,
                 gql: Optional[Callable[..., dict]] = None):
        self.project_id = project_id
        self._gql = gql or gh_graphql  # the ONE injectable transport
        self._fields: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        q = """
        query($id: ID!) {
          node(id: $id) {
            ... on ProjectV2 {
              fields(first: 100) {
                nodes {
                  __typename
                  ... on ProjectV2Field { id name }
                  ... on ProjectV2SingleSelectField {
                    id name options { id name }
                  }
                }
              }
            }
          }
        }
        """
        data = self._gql(q, id=self.project_id)
        for f in data["node"]["fields"]["nodes"]:
            name = f.get("name")
            if not name:
                continue
            entry = {"id": f["id"], "typename": f["__typename"],
                     "options": {}}
            if f["__typename"] == "ProjectV2SingleSelectField":
                entry["options"] = {o["name"]: o["id"]
                                    for o in f.get("options", [])}
            self._fields[name] = entry

    def stage_field(self) -> dict:
        """The Stage single-select field entry, or a named refusal (the
        board schema is read-only here — a missing field is never created
        and never inferred around)."""
        f = self._fields.get(STAGE_FIELD)
        if not f or f["typename"] != "ProjectV2SingleSelectField":
            raise BoardError(
                f"REFUSED: project {self.project_id} has no "
                f"'{STAGE_FIELD}' single-select field — board-schema "
                "changes are excluded; fields present: "
                f"{sorted(self._fields)}")
        return f

    def stage_option_id(self, state: str) -> str:
        """The option id for a canonical state, or a fail-closed refusal
        NAMING the missing option (architecture.md §Integrations)."""
        f = self.stage_field()
        oid = f["options"].get(state)
        if not oid:
            raise BoardError(
                f"REFUSED: Stage option '{state}' missing on project "
                f"{self.project_id} — board-schema changes are excluded; "
                f"options present: {sorted(f['options'])}")
        return oid

    def item_for_issue(self, number: int) -> Optional[str]:
        """The project item id for a repo issue number (None if absent)."""
        q = """
        query($p: ID!) {
          node(id: $p) {
            ... on ProjectV2 {
              items(first: 100) {
                nodes { id content { ... on Issue { number } } }
              }
            }
          }
        }
        """
        data = self._gql(q, p=self.project_id)
        for node in data["node"]["items"]["nodes"]:
            if node.get("content") and \
                    node["content"].get("number") == number:
                return node["id"]
        return None

    def issue_node_id(self, owner: str, name: str, number: int) -> str:
        q = build_issue_id_query(owner, name, number)
        data = self._gql(q)
        issue = (data.get("repository") or {}).get("issue")
        if not issue:
            raise BoardError(
                f"REFUSED: issue #{number} not found in {owner}/{name}")
        return issue["id"]

    def status_items(self) -> tuple[dict, list[dict]]:
        """(project {title,url}, items) — each item parsed to
        {number, title, stage} (stage None when unset)."""
        q = """
        query($p: ID!) {
          node(id: $p) {
            ... on ProjectV2 {
              title url
              items(first: 100) {
                nodes {
                  id
                  content { ... on Issue { number title }
                            ... on DraftIssue { title } }
                  fieldValues(first: 30) {
                    nodes {
                      ... on ProjectV2ItemFieldValueCommon {
                        field { ... on ProjectV2FieldCommon { name } } }
                      ... on ProjectV2ItemFieldSingleSelectValue { name }
                    }
                  }
                }
              }
            }
          }
        }
        """
        proj = self._gql(q, p=self.project_id)["node"]
        items = []
        for node in proj["items"]["nodes"]:
            c = node.get("content") or {}
            stage = None
            for fv in node.get("fieldValues", {}).get("nodes", []):
                fname = (fv.get("field") or {}).get("name")
                if fname == STAGE_FIELD and fv.get("name"):
                    stage = fv["name"]
            items.append({"number": c.get("number"),
                          "title": c.get("title") or "(draft)",
                          "stage": stage})
        return {"title": proj.get("title", ""),
                "url": proj.get("url", "")}, items


# ---------------------------------------------------------------------------
# commands (pure orchestration over Board — tests drive these with a fake
# transport; main() only resolves configuration and maps refusals to rcs)
# ---------------------------------------------------------------------------

def cmd_status(board: Board) -> int:
    proj, items = board.status_items()
    print(f"Board: {proj['title']} ({proj['url']})")
    for it in items:
        num = it["number"] if it["number"] is not None else "-"
        print(f"  {num:>4}  {it['title'][:50]:<52} "
              f"Stage={it['stage'] or '(unset)'}")
    return 0


def cmd_add(board: Board, number: int, repo: tuple[str, str],
            dry_run: bool = False) -> int:
    owner, name = repo
    issue_id = board.issue_node_id(owner, name, number)
    payload = build_add_item_payload(board.project_id, issue_id)
    if dry_run:
        print(payload)
        print(f"dry-run: would add issue #{number} to project "
              f"{board.project_id} (no write performed)")
        return 0
    data = board._gql(payload)
    item = (data.get("addProjectV2ItemById") or {}).get("item") or {}
    print(f"added #{number} to board (item {item.get('id', '?')})")
    return 0


def cmd_set(board: Board, number: int, stage: str,
            dry_run: bool = False) -> int:
    """The explicit Stage write. The option must ALREADY exist on the
    board — a missing option is a named fail-closed refusal; nothing is
    created, nothing is inferred."""
    state = normalize_stage(stage)
    field = board.stage_field()
    option_id = board.stage_option_id(state)
    item_id = board.item_for_issue(number)
    if not item_id:
        raise BoardError(
            f"REFUSED: issue #{number} is not on the board — run "
            f"`add --issue {number}` first")
    payload = build_set_stage_payload(board.project_id, item_id,
                                      field["id"], option_id)
    if dry_run:
        print(payload)
        print(f"dry-run: would set issue #{number} Stage={state} on "
              f"project {board.project_id} (no write performed)")
        return 0
    board._gql(payload)
    print(f"updated #{number}: Stage={state}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Exit 0 success · 1 refusal · 2 usage/configuration."""
    ap = argparse.ArgumentParser(
        prog="sync_board",
        description="GitHub Projects v2 board bridge — explicit Stage "
                    "writes only, never inferred")
    ap.add_argument("--root", default=".", metavar="control-home",
                    help="control home root (default: cwd); workspace.toml "
                         "supplies board.project_id / repo bindings")
    ap.add_argument("--project", default=None, metavar="<org>/projects/<n>",
                    help="board project id (overrides workspace.toml)")
    ap.add_argument("--repo", default=None, metavar="<owner>/<name>",
                    help="repo for issue lookups (default: first "
                         "repos[].url binding in workspace.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="list project items + their Stage")

    p = sub.add_parser("add", help="add an issue to the board")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--dry-run", action="store_true",
                   help="print the exact GraphQL payload; no write")

    p = sub.add_parser("set", help="explicit Stage single-select write")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--stage", required=True,
                   help="a state name from tools/state_machine.py")
    p.add_argument("--dry-run", action="store_true",
                   help="print the exact GraphQL payload; no write")

    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        if args.cmd == "set":
            # validate the stage vocabulary BEFORE any transport work
            try:
                normalize_stage(args.stage)
            except ValueError as exc:
                print(f"REFUSED: {exc}")
                return 2
        project_id = load_project_id(root, args.project)
        board = Board(project_id)
        if args.cmd == "status":
            return cmd_status(board)
        if args.cmd == "add":
            repo = load_repo(root, args.repo)
            return cmd_add(board, args.issue, repo, args.dry_run)
        return cmd_set(board, args.issue, args.stage, args.dry_run)
    except BoardError as exc:
        print(str(exc))
        return exc.rc
    except (subprocess.SubprocessError, json.JSONDecodeError,
            KeyError, OSError) as exc:
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
