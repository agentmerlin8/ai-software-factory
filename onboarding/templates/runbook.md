# Factory Runbook

Operating rules for driving the pipeline in THIS repo. The orchestrator follows this checklist; humans can audit against it.

## Stage flow (per story)

```
decompose → test-plan → impl-plan (DESIGN CONTRACT: file manifest, signatures,
                                       test names/assertions, conventions, decisions — NO full code)
  → script gate → PLAN REVIEW (fresh context, PASS/PASS-WITH-NITS/FAIL)
  ├─ PASS → coder (delegate_task subagent; sole code author;
  │           exit: build green + story's own tests green + committed)
  │           → build+test results feed the review package (gate evidence)
  │           → REVIEW LANE (deterministic package → three blind lenses × the
  │               declared panel → dispositions → record at the head)
  │               ├─ APPROVED → nits fixed in-session (script/diff-verified,
  │               │             no loop consumed) → full test execution → merge-ready
  │               └─ CHANGES REQUESTED → back to coder (max 2)
  └─ FAIL → back to planner (max 2)
```

Verdict rules: nits must be enumerated, mechanical, touch no logic, and carry
a `nit` defect-class tag; anything requiring judgment is FAIL. Every revision
loop is tagged at the moment it happens (mechanical/reasoning/contract/nit) —
never backfilled. Mechanical checks run as a pre-review script gate
(`factory/gates/pre_review_gate.py`, installed by onboarding) before any
LLM reviewer spawns; loop-2 re-reviews are scoped to the artifact diff + prior
findings only. The review lane's record at the pinned head IS the review
evidence: any blocker/major → CHANGES REQUESTED, and every finding carries
exactly one disposition (`fixed@sha`, `rejected`, `deferred`) — a record with
an undispositioned finding will not render, and the `review-record` required
CI check refuses a PR whose head has no valid record.

## Review lane

Commands (installed form — `factory/tools/review/...`; in the kit repo itself
the same scripts live at `tools/review/...`). Packages are never committed:
the record carries `package.manifest` and `check` rebuilds the package from
the pinned base/head.

```bash
# 1. build the deterministic review package (base/head pinned; manifest for the record)
python3 factory/tools/review/package.py build \
  --repo . --base <BASE_SHA> --head <HEAD_SHA> \
  [--contract <path>[:label]]... [--gate <path>[:label]]... \
  [--allowed <list-file>] [--withheld <list-file>] \
  --out <record-dir>/package.md --manifest-out <record-dir>/package-manifest.json

# 2. render the record from the panel's findings docs + dispositions
python3 factory/tools/review/record.py render \
  --repo . --story <STORY-ID> --pr <N> --base <BASE_SHA> --head <HEAD_SHA> \
  --package <record-dir>/package.md \
  --package-manifest <record-dir>/package-manifest.json \
  --findings <ROLE@MODEL>=<file>... --dispositions <ledger-file> \
  --out-dir <record-dir>

# 3. re-verify a rendered record (rebuilds the package from the manifest)
python3 factory/tools/review/record.py check --record <record-dir> --repo .

# 4. the review-record required check (CI runs this on every PR)
python3 factory/tools/review/ci_gate.py --repo . --pr <N> --head <HEAD_SHA>
```

All findings docs, gate evidence and the record live under ONE record
directory (`stories/<id>/review/` in a control home) so the post-review tail
commit is record-only.

## Model allocation policy

Use the cheapest model sufficient for each role. Scarce frontier-model budget
is reserved for a **meta-advisor**: bounded process-improvement consults at
phase boundaries, logged with dispositions in `factory/FABLE-CONSULTS.md`,
with a kill rule (two consecutive consults with nothing adopted → stop).

## Batching

- **Planning stages** (decompose, test-plan, impl-plan, reviews): max 2 stories per planning subagent when impl plans carry full code; 3–5 for lightweight artifacts.
- **Coder batches:** 3–5 stories max per subagent (2–3 for test-heavy).
- **Never** one subagent per role per story — that is 6×N calls and burns hours.
- Keep a build/test verification between every batch. If the build is red, stop and fix before the next batch.

## Coder handoff contract

1. A story enters the coder ONLY with a written handoff at `stories/STORY-{ID}/handoff.md`: story summary, approved impl-plan path, acceptance criteria, exact files in scope (CREATE vs EXTEND), verification commands with expected output, exact commit message.
2. The coder subagent prompt must be **mechanical**: read ONLY listed files, write the first edit within 10 tool calls, STOP and report if anything else is needed. Never "study the repo then fix."
3. Coder works on a `story/STORY-{ID}-<slug>` branch, commits as it goes.
4. The review lane runs on the branch diff by **fresh-context blind reviewers** that never saw the planning conversation (the deterministic package and pinned SHAs only); if a reviewer must build, it uses a worktree under /tmp — never the shared tree.
5. Review feedback goes back as copy-paste-ready diffs, or the orchestrator applies small fixes directly.
6. Stories that CREATE the same file run **sequentially**; EXTEND-only stories may parallelize.
7. Merge only after local test execution is green (full suite + the story's named test project).

## Checkpoints between batches

- [ ] `git log` shows the expected commits
- [ ] Build green (0 errors, 0 warnings where applicable)
- [ ] Tests green
- [ ] `stories/STORY-{ID}/state.md` updated
- [ ] `factory/DASHBOARD.md` regenerated

## Timeout / failure recovery

1. On any agent timeout: check `git status` and `git log` FIRST — work is often done but uncommitted.
2. Verify partial work compiles; commit it under the story's commit message.
3. Resume the remaining scope with a fresh agent run, not a full redo.

## Escalation (non-negotiable)

- After **2 failed revision loops** at any stage → STOP, mark the story `factory:blocked`, add `needs-human` label, report to the human with: which stage failed, what the failure was, what feedback was given.
- **Never** let an agent silently self-patch a plan or skip a gate.
- **Never** merge a PR without green local tests.

## Storybook-first gate (if the project has a web UI)

1. Dashboard/UI components are drafted as Storybook stories with mock data.
2. Human reviews and approves the stories (checkbox in DASHBOARD.md).
3. Only then may feature code wire the components.

## Visibility contract

- Every stage transition updates `stories/STORY-{ID}/state.md` AND regenerates `factory/DASHBOARD.md` in the same commit.
- Commit messages always carry the story ID: `STORY-003: ...`
