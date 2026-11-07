# Owner decision — code-owner review on main (S3.5b round-5 scope exception)

Date: 2026-11-06
Owner: Randy (`@randy-jerome`)
Recorded by: Merlin (ops identity `@agentmerlin8-ops`)

## Decision

The S3.5b contract excludes "any change to the owner merge gate" from this
slice. The owner explicitly grants a scope exception for exactly this change:

- `main` branch protection requires **code-owner review**
  (`require_code_owner_reviews: true`) in addition to the existing one
  approving review and the `kit-ci` / `review-record` required checks.
- `.github/CODEOWNERS` names `@randy-jerome` and `@agentmerlin8-ops` for
  `.github/workflows/`, `tools/review/` and `kit-v0.1/review-panel.toml`.

Owner reply in the operator session: "Go with 1" (option 1 = approve and
record the exception).

## Why

GitHub evaluates `pull_request` workflows from the PR ref, so the
base-revision verifier checkout protects the verifier, its authority and the
declared panel, but not the job definition itself. Without an owner approval
on workflow and gate changes, the required check is advisory for any
push-capable actor.

## Effect on the owner merge gate

Who may merge is unchanged — the owner. What changes is that a PR touching
the workflow, the review tools or the declared panel needs a code-owner
approval before merge. Admin merges remain the owner's own trust boundary.

## Scope

This exception covers only the code-owner-review requirement above, for the
lifetime of the review gate. Any wider change to merge protection remains
out of scope for S3.5b.