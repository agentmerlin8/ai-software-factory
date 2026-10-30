# Onboarding — the deterministic pipeline

Repeatable, fail-closed onboarding for **greenfield**, **brownfield**, and
**bundled** repositories: same commands, same result, every time. Everything
here is driven by `onboarding/onboard.py` (python3 ≥ 3.11, stdlib only) plus
the readiness engine in `onboarding/readiness.py`.

> **Start with the guided-session brief: [`ONBOARDING.md`](../ONBOARDING.md)**
> at the repo root. It walks an operator (human or any agent runtime) through
> every choice and every command. This README is the quick reference.

## Quickstart

```bash
git clone https://github.com/agentmerlin8-ops/ai-software-factory
cd ai-software-factory

python3 onboarding/onboard.py preflight /path/to/your/repo
python3 onboarding/onboard.py inspect   /path/to/your/repo
# --control-home in-repo: dedicated mode rejects a binding to the target's own origin
python3 onboarding/onboard.py propose   /path/to/your/repo --control-home in-repo
python3 onboarding/onboard.py apply     /path/to/your/repo --dry-run
python3 onboarding/onboard.py apply     /path/to/your/repo
python3 onboarding/onboard.py verify    /path/to/your/repo
```

Every command prints exactly one `NEXT:` line naming the next action. Exit
codes: `0` ok · `1` refusal/findings · `2` usage.

| Command | Does |
|---|---|
| `preflight` | Named PASS/FAIL readiness checks (python3, git, gh, forge auth, target is a git work tree). Never writes. |
| `inspect` | Local facts only: posture (`greenfield`/`brownfield`/`bundled`), slug candidate, origin/branch, existing config, kit HEAD. `--json` for the same data. |
| `propose` | Drafts `workspace.toml` + `environments/*.toml` into `<target>/onboarding-drafts/` from the kit templates, validated with messages verbatim. Choices: `--workspace-name`, `--control-home dedicated\|in-repo`, `--repo-id`, `--repo-url`, `--pin`, `--envs`, `--strict`. Refuses when the target already has `workspace.toml`. |
| `apply` | Installs the control-home surface from the drafts. **Idempotent, never overwrites** (`EXISTS, keeping:`); `--dry-run` prints every planned action and writes nothing. `--ui`/`--no-ui` sets the UI declaration. |
| `verify` | Runs the readiness registry fail-closed; regenerates `factory/PREFLIGHT.md` (the report of record). Delegates to `readiness.py`. |
| `attest` | Records an operator answer for an `attest_allowed` check as a `human_gate` ledger row. Delegates to `readiness.py`. |

`preflight`, `apply`, and `verify` accept `--forge-fixture <file>` — the
sanctioned fixture-backed forge seam for tests/smoke runs only.

## What apply installs

Root config (`workspace.toml`, `environments/*.toml`) · `factory/tools/` +
`factory/gates/` · the ledger seed (`factory/ledger/events.jsonl`) ·
`factory/locks/OVERRIDE.example` · `factory/templates/` ·
`factory/KICKOFF.md` · `factory/SECRETS.md` · `.gitignore` ·
`.github/ISSUE_TEMPLATE/idea.yml` · `stories/README.md` · the onboarding
record (`factory/onboarding-registry.json`) · posture extras (greenfield
`docs/` stubs, or the brownfield `factory/bootstrap-checklist.md`). Forge
side: every base label in `onboarding/registry.json` is ensured on the home
repo.

## Verify loop + report

Run `verify`; each FAIL prints its remedy; fix the fact; re-run. rc 0 =
green. `factory/PREFLIGHT.md` is regenerated on every verify and is the
report of record — commit it. S4-owned components (runbook, process, agents,
adapters) render as `DEFERRED(S4)`, not enforced.

## Guides, readiness, and the registry

- `onboarding/readiness.py` — the verify engine + attest CLI (direct-run
  usable: `python3 onboarding/readiness.py verify <home>`).
- `onboarding/registry.json` — the readiness check registry (ids,
  categories, verify methods, remedies, attest-allowed set) and the label
  set.
- `onboarding/forge_probe.py` — the forge (gh) probe seam, fixture-backed
  under `--forge-fixture`.
- `onboarding/templates/` — the install-set templates (KICKOFF, SECRETS,
  story templates, posture extras, issue form).
- `kit-v0.1/target-repo-readiness.md` — the human-readable readiness
  checklist the registry mechanizes.
- `docs/github-native-runtime.md` — forge permission/scopes background.

## Consistency guarantee

The pipeline is deterministic: same inputs → same drafts, same installs,
same report. Named lowercase refusals, never raw tracebacks; LF + trailing
newline on every written file; no wall-clock outside the apply record's
`applied_at`. Verify is CI-gateable (rc 1 on any required FAIL).
