# T6 — ruleset staging and live drill (evidence)

Recorded 2026-10-31, before the record commit, on PR #32 (`kit/s35b-review-gate-cutover`).

## Ruleset

- id `26277566`, name `main-review-gate`, target `branch`, condition `~DEFAULT_BRANCH`.
- Required status checks: `kit-ci`, `review-record`.
- Staged `enforcement: disabled` at creation; the contexts were read back from the
  API (`required_status_checks` = the two names above), then the ruleset was
  flipped to `enforcement: active` (owner decision 2a). Active state re-read from
  `GET /repos/agentmerlin8/ai-software-factory/rulesets/26277566` after the flip.
- No bypass actors: the owner merge gate is unchanged in who may merge; the
  ruleset requires the two checks. Branch protection additionally requires
  code-owner review (owner decision 2026-11-06, see
  `OWNER-DECISION-codeowner-review.md` in this directory).

## Red half (no record)

- Head `3aa3b59`, workflow run
  `https://github.com/agentmerlin8/ai-software-factory/actions/runs/41218861160`
  (job `124589027523`), conclusion **FAILURE**, 5s.
- Job log: `review record gate: no review record for PR 32 under kit-v0.1/reviews`.
- `kit-ci` on the same run: **SUCCESS** (56s). The push-event run skipped
  `review-record` by design (`if: github.event_name == 'pull_request'`).
- PR #32 merge state at that head: `BLOCKED` (draft + required check red).

## Code-owner review (round-4 fix, 2026-11-06)

The workflow BODY ships with the PR (GitHub evaluates `pull_request` workflows
from the PR ref), so the base-revision checkout protects the verifier, its
authority and the panel — not the job definition. The compensation, read back
from the API at the time of the round-4 fix:

- `GET /repos/agentmerlin8/ai-software-factory/branches/main/protection/required_pull_request_reviews`
  → `require_code_owner_reviews: true`, `required_approving_review_count: 1`.
  The setting was PATCHed on 2026-11-06 and read back after the change.
- `.github/CODEOWNERS` names `@randy-jerome` and `@agentmerlin8-ops` for
  `.github/workflows/`, `tools/review/` and `kit-v0.1/review-panel.toml`.
- Residual, stated rather than hidden: an admin (the owner) can still merge
  without that approval. That is the owner's own trust boundary, documented
  in `architecture.md`; no agent-pushed PR can self-clear the check.

## Green half

The valid record is this directory's `review.json`; the `review-record` run on
the record commit is the green half and is cited by the PR's check rollup. It
cannot be a pre-record gate input (the run only exists after the record is
pushed); the PR check is the evidence.