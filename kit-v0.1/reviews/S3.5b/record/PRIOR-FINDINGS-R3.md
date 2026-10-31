# Prior findings — S3.5b round 3 (fix-only review)

Round 2 ran at head `a02c2e5` and returned **APPROVED** with three minors, all
deferred to issue #33. Round 1 (21 findings, 16 fixed / 5 rejected) is recorded
at `record/round-1/review.json` and summarized in the round-2 record's prior
rounds.

## Why round 3 exists

The required `review-record` CI check failed on the round-2 record at head
`a02c2e5`: the package rebuilt to a different sha256 in the GitHub Actions
checkout. Cause (reproduced by cloning with and without the `.git` suffix): the
package Identity's `repo:` line embedded the origin verbatim, and Actions
checks out `https://github.com/<owner>/<repo>` while a local clone carries
`…/<repo>.git`. Fix in `9d1ba06`: `canonical_repo_url` always renders the
`.git` form, so both clones rebuild byte-identically. The existing package
`353be80b…` is unchanged and now rebuilds green under either origin form
(verified by re-running `record.py check` with the origin set to the no-suffix
form).

## Round-2 dispositions (for reference)

| id | severity | disposition |
|---|---|---|
| `RA-001@qwen3.8-max` | minor | deferred → #33 item 1 (fail-closed branch needs a controller test) |
| `RA-001@deepseek-v4-pro-0813` | minor | deferred → #33 item 2 (manifest label round-trip shape) |
| `RA-002@deepseek-v4-pro-0813` | minor | deferred → #33 item 3 (historical DRAFT_OPENED pr detail) |

## Round-3 scope

This review covers ONLY the fix diff `a02c2e5..9d1ba06`:

- `tools/review/package.py` — `canonical_repo_url` applied to the Identity
  `repo:` line.
- `tools/review/tests/test_package.py` — a test that builds with the origin in
  both forms and asserts byte-identical packages.

Confirm: the fix actually makes the rebuild environment-independent; it does not
weaken the package identity or hide a real difference between clones; and no
new defect is introduced. Do not re-raise settled round-1/2 items without new
evidence in this package.