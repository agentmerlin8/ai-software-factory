# Secrets posture

Where credentials live, what never gets committed, and how a fresh machine
gets them.

## Where credential references live

- `environments/*.toml` declare credentials as REFERENCES only
  (`credentials.refs = ["env:NAME"]` or `["file:path"]`) — the validator
  refuses an inline literal.
- A reference points at an environment variable or a git-ignored file
  (e.g. `secrets/<name>`); the value itself lives outside the repo.

## What is never committed

- Credential VALUES — never in TOML, never in code, never in the ledger.
- `.env` files and the `secrets/` directory — both must be git-ignored
  (verify check C-12 refuses a `.gitignore` without secret-ish patterns).

## How a fresh machine gets credentials

1. Forge auth: `gh auth login` (or export `GH_TOKEN`) — the scopes the
   workspace needs are named in the readiness registry.
2. Environment variables: set each `env:`-referenced name in the shell
   profile or the runner's secret store.
3. Secret files: recreate each `file:`-referenced path under the
   git-ignored location from the operator's password store.
