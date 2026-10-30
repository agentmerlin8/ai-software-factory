# GitHub-Native Runtime — Proven Field Notes

> **STATUS (2026-09-06): HISTORICAL for the coder stage.** GitHub coding
> agents (`copilot-swe-agent[bot]`) are retired as the Coder runtime — not
> cost-efficient after credits ran out. The Coder now runs as Hermes
> `delegate_task` subagents; see the **Hermes-coder runtime** section below
> and `factory/runbook.md` (installed by onboarding) for the live contract. The field
> notes in this document remain valid evidence for everything else (gh CLI
> mechanics, PAT limits, pitfalls 1–17), and the issue→agent handoff
> mechanics below stay documented for any environment that still has
> agent credits.
>
> **Model allocation policy:** cheapest sufficient model per role. Strong
> frontier models with scarce budget (e.g. Fable 5.1 in Copilot Chat) are
> reserved as **meta-advisors**: bounded consults at phase boundaries on
> process improvement only, logged with dispositions in
> `factory/FABLE-CONSULTS.md`, with a kill rule (two consecutive consults
> with nothing adopted → stop consulting).

## Hermes-coder runtime (current, 2026-09-06)

```
Hermes/orchestrator writes approved plan
    ↓
stories/STORY-{ID}/handoff.md  (story + impl-plan path + acceptance criteria +
                          exact files: CREATE vs EXTEND + verify commands)
    ↓
delegate_task coder subagent — mechanical prompt: read ONLY listed files,
first edit within 10 tool calls, STOP-and-report otherwise
    ↓
story branch in canonical tree, commits as it goes (600s timeout →
check git status/log first; work is often done but uncommitted)
    ↓
fresh-context code review on `git diff main...story/...`
(worktree under /tmp if it must build — never the shared tree)
    ↓
full local test suite green → PR for human approval (main protected)
```

Batching: planning ≤2 stories/subagent when plans carry full code; coder
batches 3–5 (2–3 test-heavy). Stories that CREATE the same file run
sequentially; EXTEND-only stories may parallelize (field note 17).

## The handoff: issue → Copilot coding agent → draft PR (retired)

**Everything in this section was verified live on 2026-08-29 against the first production factory run (a private .NET repo) — not inferred from docs.**

```
Hermes/orchestrator writes approved plan
    ↓
gh issue create  (body = story + impl-plan reference + acceptance criteria)
    ↓
gh issue edit <N> --add-assignee copilot-swe-agent[bot]
    ↓
Copilot works in GitHub's cloud — no local process, no 600s timeout
    ↓
draft PR opened, references "Fixes #N", commits include "Initial plan"
    ↓
fresh-context code review on the PR  →  local test execution  →  gh pr merge
```

### Verified mechanics

| Fact | Evidence |
|---|---|
| Assignment works via REST assignees with exact string `copilot-swe-agent[bot]` | POST `/repos/{o}/{r}/issues/{n}/assignees` returned 200; issue showed assignee "Copilot" |
| The bot picks the issue up within ~minutes | PR #2 opened by `app/copilot-swe-agent`: draft, correct file, correct content, "Fixes #1" trailer |
| Assignable agents are discoverable | GraphQL `suggestedActors(capabilities:[CAN_BE_ASSIGNED])` — returned `copilot-swe-agent`, `anthropic-code-agent`, `openai-code-agent` on one repo |
| `/repos/{o}/{r}/agents/copilot/sessions` returns 404 with fine-grained PATs | Not required — assignment is the handoff; sessions are visible in the GitHub UI |
| Copilot output is reviewable like any PR | `gh pr view/diff` work; commits visible; draft state by default |

### Token permissions needed (fine-grained PAT)

| Permission | Access | Why |
|---|---|---|
| Contents | read/write | branches, code |
| Issues | read/write | handoff issues, labels |
| Pull requests | read/write | review, merge |
| Metadata | read | always required |

A fine-grained PAT **cannot list private repos** via `/users/{u}/repos` — direct access by repo name works fine. Design automation around named repos, not enumeration.

### Why Copilot for the Coder stage

- **No subagent timeout** — sessions run as long as they need in GitHub's cloud
- **Full repo context** — it sees the real code, not an injected excerpt
- **Paid in Copilot credits**, not orchestrator tokens
- **Auditable** — every agent session is linked to the issue in the GitHub UI
- **Benchmarkable** — the same issue can be assigned to different coding agents (`anthropic-code-agent`, `openai-code-agent`) for model comparison with everything else pinned

## Division of labor (hybrid runtime)

| Role | Runs on | Rationale |
|------|---------|-----------|
| Orchestrator, Decomposer, Planners | orchestrator host (Hermes / Copilot Chat) | needs conversation + local context |
| Plan Reviewer, Code Reviewer | fresh-context runs, artifacts only | context isolation is the point |
| **Coder** | **Copilot coding agent via `gh`** | cloud execution, credits, auditability |
| Test Executor | orchestrator host | hardware, docker, compose live there |
| Dashboard | script | regenerated after every stage transition |

## Pitfalls carved from live runs

1. **Reviewers must write reports incrementally to files** (section by section), ending with a one-line verdict. A reviewer composing one giant final message can lose the whole review to a provider timeout after minutes of successful reading.
2. **Check `git status`/`git log` after any timeout** before assuming work was lost.
3. **Batch planning stages 3–5 stories per run**; never one agent per role per story.
4. **Build must be green between batches.**
5. **Storybook-first gate for UI**: stories approved by a human before feature code wires components.
6. Max 2 revision loops per stage, then escalate — never silent failure.
7. **De-risk infrastructure before the first coding batch.** Boot every external service the bundle depends on (emulators, sidecars, databases) with a throwaway compose file and verify the real protocol port answers. Live result (2026-08-30): three config constraints in the Service Bus emulator config were caught pre-build (max TTL 1h, max duplicate-detection window 5m, mandatory Logging section) — crashes that would otherwise have hit a coding agent mid-story, consuming its session and a revision loop each. Crash messages can mislead: the emulator printed "Out of memory" on a config-validation failure.
8. **Planners exhaust iteration budgets on large batches.** A combined Test-Plan + Impl-Plan run for 3 foundation stories died at the iteration cap (~50 min, ~40 calls) after completing 2.5 of 6 artifacts. Mitigations that worked: (a) partial artifacts were already on disk because of write-as-you-go, so a continuation run could resume without redoing work; (b) the continuation prompt declares the prior run's completed files as READ-ONLY context, forbids re-verifying decisions (package versions come from the first plan), sets an explicit tool-call budget, and names the critical-path artifact to finish first. Rule of thumb: **max 2 stories per planning subagent** when impl plans carry full code.
9. **First-planner-in-a-batch sets the convention anchor.** STORY-001's plan carries the frozen package-version table and shared conventions; later stories must reference it verbatim, never re-derive it. Order planning so the foundation story is always planned first.
10. **Provider non-streaming timeouts kill large single generations.** A planner composing a 12KB+ code-heavy plan in one message died with "Non-streaming API call timed out after 180s" (3 retries) — same failure class as reviewers composing one giant report. The fix is the **skeleton-then-patch technique**: (a) one small write creates the full document skeleton (header + complete task list with empty sections), (b) each section is filled by a small patch replacing its title line, one patch per section, each generation kept under ~150 lines. Works because every individual generation stays under the provider's timeout. Mandate it in planner prompts for any code-heavy artifact; cap generation size explicitly ("never more than ~150 lines per write/patch").
11. **Skeleton-first is not always enough — split review scope instead.** A full-bundle fresh-context plan review (3 stories × 6 artifacts) died twice to the same 180s timeout even with skeleton-first discipline: the *reading + reasoning* over a large bundle is what overflows, not just the writing. Mitigations that work: (a) review per story, not per batch — one fresh reviewer per story keeps the context bounded; (b) for mechanical dimensions (TDD mapping, coverage counts, package/version coherence), fall back to a deterministic orchestrator script — regex/count checks are timeout-proof and auditable. Live result: dimension A (judgment-heavy) succeeded on a 245-line report; dimension B succeeded only as a mechanical audit after two timeout deaths.
12. **Shared working tree + parallel agents + `git add -A` = accidental merge.** Live incident (2026-08-30): a code reviewer checked out a PR branch into the shared repo working tree (`git checkout origin/<branch> -- .`) to build it; the orchestrator then ran `git add -A && git commit` for a dashboard update and swept the uncommitted PR code into main — merging before the review verdict. Outcome happened to be benign (code passed build+tests and was headed for main anyway), but the gate order (review → merge) was violated. Rules: (a) reviewers/coders that need the code checked out must use a separate `git worktree add` directory, never the shared tree; (b) the orchestrator NEVER runs bare `git add -A` while subagents share the repo — stage files explicitly by path; (c) code-review subagent prompts must say "review via git diff/show; if you must build, use a worktree under /tmp".
13. **Planners can invent invalid syntax — code review is the catch net.** Live case (STORY-002): the impl plan mandated C# raw strings in the form `"""$ … $"""` to protect `$$;` sproc terminators — but that form is invalid C# (the interpolation `$` must PREFIX the quotes: `$"""`). The Copilot coder silently used plain `"""` (which works: `$$;` inside a non-interpolated raw string is fine), and the fresh-context code reviewer flagged the deviation as POSITIVE rather than a violation. Lesson: when plans prescribe exact code syntax, the planner's claim is unverified prose — treat "code deviates from plan in a way that compiles and passes tests" as a data point for plan defects, not coder error. Corollary for planners: only mandate syntax you have compiled or quoted from a working example.
14. **Near-identical test-project names invite false "tests lost" alarms.** Live near-miss (STORY-004 merge verification, first production run): the repo carried BOTH `tests/App.Integration.Tests` (STORY-002's Testcontainers harness) and `tests/App.Infrastructure.Tests` (STORY-004's API-client unit tests). After merging, running the familiar `App.Integration.Tests` showed 24 tests — the 27 new API-client tests appeared "lost" until the right project was run. Rules: (a) verification commands must name the EXACT test project per story, never "the integration tests"; (b) orchestrator merge verification runs the FULL solution test suite (`dotnet test <Solution>.sln`) plus each story's named project, and compares totals against the dashboard's expected counts; (c) planners pick project names that can't be confused (Infrastructure.Tests vs Integration.Tests was one letter class apart).
15. **Emulator-pair Testcontainers suites can hang on boot — isolate with a hard timeout.** Live incident (STORY-010 verification): `dotnet test <solution>` ran four projects green in seconds, then the Testcontainers harness project (Service Bus emulator + SQL Edge pair) hung silently for 38 minutes with its containers gone — no output, no failure, wedged on fixture boot. A solution-level test run gives no signal when one project wedges. Rules: (a) verify emulator/container-pair projects with a standalone `dotnet test <that project>` wrapped in `timeout <N>` (15 min is generous) so a hang converts to a definitive non-zero exit instead of an infinite wait; (b) if the isolated run also hangs, treat it as a FIXTURE DEFECT to route back to the coder (the wait-strategy/healthcheck pair is broken), not a flake to retry; (c) never block a merge on an emulator suite without a definitive PASS — merge-on-assumption is how infra bugs survive. **Root cause found (same incident):** the hang was **Testcontainers Ryuk** (the resource-reaper sidecar) — with `TESTCONTAINERS_RYUK_DISABLED=true` the same fixture booted both containers within ~60s where Ryuk-enabled runs produced zero containers in 38 minutes. The hang sits in Ryuk's handshake with the Docker host on this machine, NOT in the fixture code — the code review had correctly judged the fixture faithful to the proven smoke-compose pattern. Diagnostic technique that cracked it: run the suite with `docker ps` sampled mid-run; containers-present-but-suite-slow vs containers-absent splits "slow tests" from "boot hang" in one observation. Rule: when Testcontainers hangs with zero containers, first retry with Ryuk disabled before blaming the fixture or the code.
16. **MTP test binaries use different CLI flags than VSTest — `--filter` silently matches zero tests.** Live incident (STORY-010 per-class isolation): `dotnet test <proj> --filter "FullyQualifiedName~Class"` ran "successfully" with `total: 0` on every class — MTP forwards nothing it recognizes and reports zero tests rather than erroring, so the isolation runs proved nothing while looking conclusive. Correct forms for MTP executables: run the binary directly (`dotnet exec <test.dll>`) with `--filter-class <ns.Class>` / `--filter-method <ns.Class.Method>` / `--filter-not-class ...` AFTER `--`, plus `--list-tests` to enumerate. `dotnet test <proj> -- <mtp-flags>` also forwards correctly. Rule: any test run reporting `total: 0` when tests were expected is a FILTER SYNTAX BUG, not an empty suite — never draw conclusions from it.
17. **Parallel Copilot sessions that create the same files guarantee merge conflicts — sequence shared-file stories instead.** Live incident (first production run): three stories (queue endpoint, catalog endpoints, observability wiring) all branched from the same main and independently created the same controller, DTO, repository-interface, `Program.cs`, and test-factory files. Every one collided at merge time and required orchestrator-side union resolution in scratch worktrees (the controller needed a 3-endpoint union; the DTO needed keep-one semantics; one union even regressed a route constraint and was only caught because merges are ALWAYS re-verified with the full suite). Rules: (a) plans must declare every file they CREATE; the orchestrator schedules stories that create the same file SEQUENTIALLY, not in parallel; (b) stories that only EXTEND existing files may parallelize; (c) after ANY conflict resolution, run the full suite on the merged tree before pushing — union merges silently change behavior (the `{id:long}` constraint regression was caught this way); (d) keep union-resolution notes in the merge commit so later reviewers know what was combined.

## Local dashboard contract

`factory/DASHBOARD.md` is regenerated after every stage transition (same commit as `stories/STORY-{ID}/state.md`). It is a generated render produced by the kit's renderers — never hand-edited. Combined with GitHub's issue labels (`factory:*`) this gives a kanban view in the Issues tab without any external tooling.
