# Test Strategy: Factory Kit v0.1

> Status: DRAFT — direction folded 2026-10-23 (parity clarification, config/workspace schema tests, D-1 both legs; see `architecture.md`, `build-plan.md`, `grill-disposition.md`). Drafted 2026-09-28.
> Companion docs: `prd.md`, `architecture.md`, `target-repo-readiness.md`.

## Testing Philosophy

The kit's core value is **determinism you can trust**: the control plane must be testable without any AI in the loop. Tests are written before implementation (TDD), failures are reported honestly, and nothing in the control plane is "verified" by an LLM's opinion. AI sits behind dispatch packages; the packages' *contracts* (paths, shapes, stop rules) are what we test — never an AI's free-form output.

Two principles carried from the reference implementation:

1. **Mechanical before judgment** — every gate script must pass (or fail) deterministically before any LLM review runs.
2. **Honest records** — tests assert the null+reason convention and correction-as-event discipline; the ledger is never hand-edited, including in tests.

## Test Pyramid

| Layer | Share (indicative) | What it covers |
|---|---|---|
| **Unit** (deterministic) | ~70% | controller refusals, ledger schema, gates, prep/template generators, renders (`--check`), routing, usage reconciliation |
| **Integration** (deterministic) | ~20% | full transition chains on fixture repos: ledger + git + gh-free paths (forge mocked or fixture-backed) |
| **Drills** (human-in-the-loop) | ~10% | the two acceptance drills: dress rehearsal + handover drill; conformance runs of `onboard verify` |

No network in unit/integration tests. Forge interactions are either fixture-backed or run in the drills.

## Test Categories & Examples

### 1. Controller refusal tests (the refusal *is* the feature)

- Missing evidence key on a transition → refusal naming that check, exit non-zero.
- Skip a state (e.g. CODING → DRAFT_OPENED without BUILD_GREEN) → refusal naming order.
- **Claim lock**: transition without a held claim → refusal naming the lock; claim over an open claim → refusal + handover path.
- **Handover**: flagged abrupt takeover passes and records `flagged: true`.
- **Override**: valid committed override → passes + `override_used` annotation with blob SHA; placeholder reason → refusal naming the field; override never bypasses a missing-evidence check (narrowness test).

### 2. Ledger schema tests

- Every event type: required fields enforced; `<field>` null requires `<field>_reason` non-empty; reason present with a value → rejected.
- **Config + workspace schema** (build-plan S1): `workspace.toml` and `environments/*.toml` schema-validate; unknown keys refuse; `repo@sha` pins resolve against each repo's object database.
- Corrections: an edit attempt (in-place change) is detectable; correction events validate.
- Render determinism: `render_state --check`, `render_dashboard --check`, `render_provenance --check` match ledger byte-for-byte after regeneration.

### 3. Gate script tests

- Fixture-driven: known-good plan passes; each seeded defect (missing contract seam, uncovered named test, cross-story collision) fails with the specific message.
- Gates are exercised in CI on PRs (advisory workflow).

### 4. Generator tests (dispatch packages, handoffs, bundles)

- `make_context_bundle` inlines every pinned input at the recorded SHA; a missing input fails loudly.
- Dispatch package contains all six sections; inputs are SHA-pinned; stop rules present.
- Handoff renders acceptance criteria, CREATE/EXTEND manifest, verify commands, resume preamble.

### 5. Parity test (the runtime-agnostic promise)

- Mechanical **contract test** (CI): the same dispatch package fed to stub executors on both adapter paths → artifacts at identical paths with identical contract shape. This proves the **contract**, never live models.
- **Live parity** is measured in D-1 (both legs), not here. **Contract shape** = required artifact paths + required sections/front-matter keys per role output (defined in `agents/`).

### 6. Conformance tests (`onboard verify`)

- Compliant fixture repo → all CORE checks PASS, exit 0.
- Non-compliant fixtures, one defect per run (missing labels, missing board field, no ledger seed, no protected main, adapter prerequisite missing) → the specific check FAILs with remedy text, exit non-zero.

## Drills (the acceptance events — not CI, recorded as evidence)

### D-1 — Dress rehearsal (end-to-end)

One small idea → merged PR on the `benchmark-app` target, operated via kit + runbook only. **Both legs execute the same package** for at least one role dispatch (the parity unit): (a) **human** — operator drives the control plane and role work via pasted packages; (b) **hermes** — that same package executed through the hermes adapter via the standard dispatch mechanism, run locally (small token cost — no cloud), recorded as "live dispatch, non-sealed mechanism"; the sealed-mechanism re-run follows when the execution seam's launch increment is authorized — nothing is called sealed-grade before then. Recorded: provenance trail, render outputs, board Stage at each transition, PR link, merge decision, **and the mechanical parity comparison (required artifact paths, sections, front-matter keys — identical across legs; a mismatch fails the drill)**. **Pass = the runbook needed no outside knowledge:** a fresh-context agent or uninstructed human given only the clone + runbook completes D-1 with zero clarification requests; every request is recorded as a `finding`/`human_gate` with a defect flag.

### D-2 — Handover drill (two machines)

Machine 1 works a story to mid-flight → push. Machine 2 (fresh clone) resumes with zero questions. Four variants must all pass:

| Variant | Expected result |
|---|---|
| (a) Transition without claim | Refused, names the lock |
| (b) Cooperative handover | Passes; record shows from/to/reason |
| (c) Abrupt takeover (owner unresponsive) | Passes; `flagged: true` |
| (d) Override path | Passes when valid+committed; annotated; delete→relock verified |

## Test Data Management

- **Fixture ledgers/stories** live under `factory/gates/fixtures/` and a `tests/fixtures/` tree; committed, minimal, purpose-built.
- **No local-run artifacts** (caches, db files) ever committed — `.gitignore` is part of the onboarding check.
- Drill evidence lands in `stories/<id>/` + provenance renders (committed); sensitive outputs (tokens, hostnames) are excluded by convention already in place.

## Code Quality Tools

- `python3 -m unittest` (stdlib only — no test dependencies, NFR-1).
- Gate scripts double as quality tooling at plan-review time.
- Ruff/formatting: optional and advisory; do not add non-stdlib runtime deps to satisfy linting.

## Test Execution Commands

```bash
# control-plane unit + integration suite
python3 -m unittest discover -s factory/tools -p 'test_*.py'

# gate fixtures
python3 factory/gates/pre_review_gate.py plan <story-dir>

# render check
python3 factory/tools/render_state.py <story> --check
python3 factory/tools/render_dashboard.py --check

# conformance
python3 onboarding/onboard.py verify <target>
```

## Anti-Patterns (explicitly rejected)

- Testing LLM outputs for exact content — test **contracts** (paths, shapes, stop rules) instead.
- Hand-editing the ledger or generated renders in any test or fix — append events, regenerate.
- Silent skips: a check that cannot run must fail or explicitly attest, never pass by default.
- Network calls in unit tests.
- "It worked on my machine": tests that assume a specific home path, hostname, or runtime.

## Traceability (PRD → tests)

| PRD | Test coverage |
|---|---|
| FR-4, FR-5 | Categories 1, 2 |
| FR-6, FR-7 | Category 1 (lock/override), D-2 |
| FR-3, FR-8 | Categories 4, 5 |
| FR-9, FR-10 | Category 2 (ledger records) + metrics fixtures |
| FR-11 | Category 6 |
| Build S1 (config + ledger schema) | Category 2 (config/workspace schema) |
| FR-2, FR-12, G4 | D-1, D-2 (acceptance drills) |