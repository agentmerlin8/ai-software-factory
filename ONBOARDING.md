# ONBOARDING.md — the guided onboarding session

This document is the brief for a **guided onboarding session**: one operator
(human, or any agent runtime) walking a repository onto the factory. Read it
start to finish, run the pipeline commands in order, and walk the operator
through each choice before it is made. The guide **never installs on its own
and never invents steps** — everything it does is a named command from
`onboarding/onboard.py`, and every command prints exactly one `NEXT:` line
naming the next action.

Exit codes for every pipeline command: `0` ok · `1` refusal or findings ·
`2` usage.

## The pipeline

All commands run from the kit checkout. `<target>` is the repository being
onboarded (the future control home).

### 1. preflight — named readiness checks (never writes)

```bash
python3 onboarding/onboard.py preflight <target> [--forge-fixture <file>]
```

Checks, each printed as `PASS|FAIL <name> — <detail>`: `python3` (>= 3.11),
`git`, `gh`, forge auth (via the forge probe seam), and `<target>` inside a
git work tree. rc 1 when any check FAILs. Fix the fact, re-run.

### 2. inspect — local facts only (no network, never writes)

```bash
python3 onboarding/onboard.py inspect <target> [--json]
```

Reports the target's **posture** (`greenfield` / `brownfield` / `bundled` —
see below), workspace-slug candidate, origin URL and branch, which config
surfaces already exist, source-file and bundle-doc counts, and the kit facts
(kit root, kit HEAD sha — the default pin). `--json` emits the same data with
sorted keys.

Posture rules (deterministic): `bundled` iff at least 3 of
`docs/{prd,architecture,ui-design,test-strategy}*.md` exist; else
`brownfield` iff any source file exists; else `greenfield`.

### 3. propose — draft the config surface (scratch area only)

```bash
python3 onboarding/onboard.py propose <target> \
    [--workspace-name N] [--control-home dedicated|in-repo] \
    [--repo-id ID] [--repo-url URL] [--pin PIN] \
    [--envs local,github] [--strict]
```

Drafts `workspace.toml` + `environments/*.toml` into
`<target>/onboarding-drafts/` from the kit's `templates/workspace/*` with
substitutions (defaults come from inspect + kit HEAD). Refuses when
`<target>/workspace.toml` already exists — an onboarded repo edits config
directly. Drafts are validated with the kit's config validator (messages
verbatim) and every deferred `<...>` placeholder key is listed; `--strict`
refuses when any remain. The drafts area is scratch: propose always
(re)writes it deterministically — the never-overwrite law binds `apply`,
not propose.

### 4. apply — install the control-home surface (idempotent, never overwrites)

```bash
python3 onboarding/onboard.py apply <target> [--dry-run] \
    [--forge-fixture <file>] [--ui | --no-ui]
```

Laws: **idempotent**; **never overwrites** — an existing file prints
`EXISTS, keeping:` and is left alone; `--dry-run` prints the exact planned
actions (including every label argv) and writes NOTHING. rc 1 on any FAIL.

What apply installs (from the validated drafts + kit assets):

- root `workspace.toml` and `environments/*.toml`
- `factory/tools/` and `factory/gates/` (the control plane; never
  `__pycache__`)
- `factory/ledger/events.jsonl` (empty seed) and
  `factory/locks/OVERRIDE.example`
- `factory/templates/` (story, story-state, story-handoff-issue)
- `factory/KICKOFF.md`, `factory/SECRETS.md`, `.gitignore`
- `.github/ISSUE_TEMPLATE/idea.yml` and `stories/README.md`
- `factory/onboarding-registry.json` — the onboarding record (registry
  version, kit version sha, applied-at timestamp, and the `declarations`
  incl. `ui`)
- posture extras: greenfield → `docs/` context-bundle stubs; brownfield →
  `factory/bootstrap-checklist.md`

Forge side: every base label in `onboarding/registry.json` is ensured on the
home repo (via the forge probe seam). Apply requires the drafts to exist and
validate — run propose first when it refuses.

### 5. verify — the readiness registry, fail-closed

```bash
python3 onboarding/onboard.py verify <target> [--forge-fixture <file>] [--json]
```

Thin delegation to `onboarding/readiness.py` (the argv tail passes through
verbatim). Runs every check row of the readiness registry
(`onboarding/registry.json`) against the control home: each check = id,
category (CORE/WORKSPACE/UI/ADAPTER/ADVISORY), verify method, remedy text,
`attest_allowed`. Output: one line per check
(`PASS|FAIL|N-A|ADVISORY <id> <title>[ — <detail>]`), `DEFERRED(S4)` lines
for S4-owned parts, and a summary footer. rc 0 = green (no required FAIL).
`factory/PREFLIGHT.md` is regenerated on every run and is the **report of
record**.

### 6. attest — the scripted manual gate

```bash
python3 onboarding/onboard.py attest <target> <check-id> --by <login> --note <text>
```

Checks that cannot be automated (`attest_allowed: true` in the registry) are
answered by the operator and recorded as an ANSWERED `human_gate` row in the
home ledger — full schema strictness intact, never a silent pass. The
attest-allowed set (exact list readable from `onboarding/registry.json`):

- **C-13** — test environment runnable (a dedicated control home ALSO
  needs one binding-scoped row per declared repo binding:
  `attest <target> C-13:<repo-id> --by <login> --note <text>`)
- **U-1** — Storybook builds locally (applies when UI declared)
- **U-2** — visual review path defined (applies when UI declared)
- **A-1..A-6** — adapter prerequisite checks (human; and one row per
  declared agent adapter: hermes, claude-code, codex, api-direct, copilot)

## The guided-session choices

Walk the operator through these before/while running propose and apply; each
one changes what lands in the config surface:

| Choice | Flag | What it affects |
|---|---|---|
| Control-home mode | `--control-home dedicated\|in-repo` | `control_home` in `workspace.toml`. Default: `dedicated` — a separate workspace repo holds the ledger/config and binds product repos. Use `in-repo` when the target repo should be its own control home (the one-repo case, where a binding to the target's own origin is allowed). Drives which WORKSPACE checks apply in verify. |
| Workspace name | `--workspace-name N` | The `workspace` slug (`^[a-z0-9][a-z0-9-]*$`, ≤64). Default: slugified target directory name. |
| Repo binding | `--repo-id ID` / `--repo-url URL` | The primary `repos[]` entry. Id grammar `^[a-z][a-z0-9-]*$`; url is the normalized `host/owner/name` form (default: from the target's origin remote). |
| Environments | `--envs local,github` | Which `environments/*.toml` are drafted (must exist as kit templates; default `local,github`). **Models are per-environment** — the tier→model table lives in each environment file, never in the workspace. |
| Factory pin | `--pin PIN` | The kit version the workspace pins (tag or 7–40 hex sha). Default: the kit's HEAD sha at propose time. Concrete pins must resolve against the kit repo. |
| UI declaration | `--ui` / `--no-ui` (apply) | The registry record's `declarations.ui`. Declared UI turns on the UI checks (U-1, U-2) in verify; default is `--no-ui`. |
| Deferred keys | `--strict` (propose) | Refuse drafts that still carry `<...>` placeholders. Without it they are listed for the operator to fill in before apply. |

## The verify loop

Verify is a loop, not a step:

1. Run `verify`. Every FAIL prints its remedy text.
2. Fix **the fact** the check names (create the label, set the credential
   ref, answer the attest row) — never the check.
3. Re-run `verify`. Repeat until rc 0.
4. `factory/PREFLIGHT.md` (regenerated every run) is the report of record —
   commit it.

## S4 deferral

Some checked components are owned by the next story (S4): the runbook,
process rules, agent role packages, and adapter packages (e.g. C-7's
`factory/runbook.md`, `factory/process/`, `factory/agents/`,
`factory/adapters/`; W-6's contract-block template; A-2's adapter
manifest). Verify renders these as `DEFERRED(S4)` — visible, not enforced.
They are enforced at the S4 alignment pass.

## Where to go next

On green verify, the control home is ready for factory runs. Cold start is
`factory/KICKOFF.md` in the target (installed by apply) — read it in a fresh
session and follow it exactly. The idea→PR runbook itself ships with S4.

## Test/smoke runs only: `--forge-fixture`

`preflight`, `apply`, and `verify` accept `--forge-fixture <file>`: the
sanctioned fixture-backed seam that swaps the forge (gh) transport for a
dataset-driven one. It exists for tests and smoke runs — a real onboarding
session never passes it.
