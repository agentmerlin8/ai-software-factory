# AI Software Factory

A structured, **runtime-agnostic** process for building software with AI agents — from idea refinement, through comprehension verification, to a completed and proven pull request.

## What This Is

The AI Software Factory is a **process framework** for producing high-quality software with AI coding agents. It solves two problems:

1. **Shared understanding** — every stakeholder (PO, designer, developer, QA) demonstrates understanding of the feature *before* a line of code is written.
2. **Structured production** — specifications run through a verifiable, gated pipeline that leaves an auditable trail from idea to merged, proven code.

This repository is also the home of the **Factory Kit** (v0.1 in active development — design docs in [`kit-v0.1/`](kit-v0.1/prd.md)): the same process packaged so it lives **inside the target repository**, runs on nothing but `git` + `gh` + `python3`, and can be driven by **a human operator or any AI orchestrator**. Every AI runtime sits behind an interchangeable adapter.

## The Core Insight

The biggest failure mode in AI-assisted development isn't the AI — it's that **humans don't read the documents**. A developer skimming a PRD and making assumptions will produce bad code regardless of how good the model is.

The answer is **role-specific comprehension verification** ("Grill Me"): each persona defends their understanding of the relevant documents at the depth their role requires, before any code is generated. Every misconception surfaces either a **persona misunderstanding** (educate) or a **document ambiguity** (fix the document — a closed feedback loop that improves the specs over time).

## How It Works

### Phase 1 — Refinement & Context Bundle

An idea enters (a GitHub issue in the kit model) and is refined — in a live grill with the product owner — into a **context bundle** of four documents:

| Persona | Document |
|---|---|
| BA/PO | **PRD** — requirements, acceptance criteria, explicit scope boundaries |
| UI/UX | **Interface spec** — user flows, states, component contracts |
| DEV | **Architecture** — components, data model, integrations |
| QA | **Test Strategy** — test pyramid, categories, anti-patterns |

Templates and required sections: [`docs/context-bundle.md`](docs/context-bundle.md).

### Phase 2 — Grill Me (Comprehension Verification)

Each persona is grilled by an AI on their specific understanding of the documents:

```
PRD drafted by BA/PO
    │
    ├──▶ [Grill: PRD Quality]      ← BA/PO defends/refines the PRD
    │
    ├──▶ UI/UX reads PRD → spec    → [Grill: UI/UX Understanding]
    │
    ├──▶ DEV reads PRD + spec      → [Grill: DEV Understanding]
    │
    ├──▶ QA reads all docs         → [Grill: QA Understanding]
    │
    ▼
All-persona sign-off → approved → to the production pipeline
```

Each grill produces a structured record — questions asked, scores, misconception root-cause analysis, and triggered document fixes. See [`grill-me/process.md`](grill-me/process.md) and the role-specific prompts in [`grill-me/prompt-template.md`](grill-me/prompt-template.md).

### Phase 3 — Production Pipeline

Approved features are decomposed into stories and run through a gated pipeline:

```
Story decomposition (+ bundle quality gate)
    → test plan → implementation plan
    → gate (mechanical checks) → plan review (fresh context)
    → coder dispatch → build green + named tests green
    → draft PR → two-pass adversarial review (fresh context)
    → fix pass → full suite green → merge-eligible
    → HUMAN MERGE GATE → live proof → closeout (provenance rendered)
```

Principles of the Factory Kit model — every kit runtime holds to these; the enterprise ADO variant differs, keeping its state in work items rather than the repo:

- **The repo is the state.** An append-only event ledger is the single source of truth; dashboards, story-state files, and provenance documents are *generated renders* — never hand-edited.
- **Deterministic bookkeeping.** A controller validates every transition against evidence; gates are mechanical scripts. AI makes the judgment calls; software owns state, checks, and bookkeeping.
- **Fresh-context adversarial review.** Reviewers never see the planner's reasoning (no anchoring bias); re-reviews see their own prior findings so fixes are verified rather than re-litigated.
- **Dispatch packages.** Every role runs from one self-contained package — role instruction, inputs pinned at a recorded SHA, expected outputs, stop rules. Paste it into any AI chat, or feed it to an agent adapter. Same process either way.
- **Ownership lock.** One story has at most one active owner; transfers happen only via recorded handover events. Concurrent work is *prevented*, not merged.
- **Bounded loops.** Max 2 revision loops per stage, then escalation to a human.
- **Human gates only where judgment belongs** — interface approvals (Storybook for UI work), the merge gate, and escalations. A human merging the PR is the final quality gate; the factory never merges autonomously.

The full design of the runtime-agnostic kit — ledger schema, controller, gates, adapters, and the idea-to-PR runbook — is in [`kit-v0.1/architecture.md`](kit-v0.1/architecture.md) and [`kit-v0.1/prd.md`](kit-v0.1/prd.md). The enterprise (ADO) variant of the pipeline is documented in [`docs/architecture.md`](docs/architecture.md) and [`ado/design-spec.md`](ado/design-spec.md).

### Runtimes & Adapters

The process is deliberately independent of any single agent product:

| Mode | Executor | Status |
|---|---|---|
| **Human** | You, with any AI chat of your choice | core to kit v0.1 |
| **Agent runtime** (e.g. Hermes `delegate_task`) | AI orchestrator following the same runbook | first adapter, kit v0.1 |
| **CLI coding agents / API endpoints** | claude-code, codex, any OpenAI-compatible endpoint (locked-down deployments) | in design (v0.2) |

## Current Status

- **Available now**: agent instruction files, Grill Me process, context-bundle templates, onboarding installer, pipeline architecture docs, field notes, benchmark app.
- **Reference implementation**: a private first-party deployment runs the ledger-based pipeline (controller, gates, provenance) in production; its operational lessons are harvested into [`docs/field-notes.md`](docs/field-notes.md) (catalog starts at note 18).
- **In build — Factory Kit v0.1**: design docs are merged in [`kit-v0.1/`](kit-v0.1/prd.md). The build covers the kit skeleton, the deterministic control plane, `human` + `hermes` adapters, the complete idea→PR runbook, and two acceptance events: an end-to-end **dress rehearsal** on `benchmark-app/` and a two-machine **handover drill** — plus the target-repo **readiness checklist** and **scripted verify mode** (fail-closed, CI-gateable). The fresh-repo onboarding hardening test-out follows in v0.1.x.

## Repository Structure

```
ai-software-factory/
├── kit-v0.1/              # Factory Kit v0.1 design docs: PRD, architecture,
│                          #   test strategy, target-repo readiness checklist
├── agents/                # Pipeline role instructions (the source of truth)
├── .github/agents/        # Same instructions, Copilot-discovery path
├── grill-me/              # Comprehension verification: process + prompt templates
├── onboarding/            # Onboarding pipeline: preflight→inspect→propose→apply→verify, readiness registry, templates
├── docs/                  # Context-bundle templates, pipeline architecture,
│                          #   field notes, runtime notes, research, agent evaluations
├── ado/                   # Enterprise variant: ADO work-item-type design + setup script
├── benchmark-app/         # Reference target app for end-to-end dress rehearsals
├── LICENSE                # MIT
└── README.md
```

## Observability & Audit

Every dispatch is recorded: pre-spawn dispatch ID, role, model tier, token usage (or explicit `null` + reason — never invented zeros), outcome, and revision loops — each tagged at the moment it happens with a class (**mechanical / reasoning / contract / nit**) and a one-line root cause. Renders generate per-story state and a **provenance trail** — idea → decisions → dispatches → evidence → PR — and metrics reconcile cost and loop rates (against a frozen baseline where one exists).

In the ADO variant, the same data lives on custom work items — **AI Story** (per story), **AI Verification** (per grill session), **AI Agent Run** (per agent invocation) — where the work item history *is* the audit trail. See [`ado/design-spec.md`](ado/design-spec.md).

## Getting Started

### Adopt the framework (current onboarding)

```bash
git clone https://github.com/agentmerlin8-ops/ai-software-factory
cd ai-software-factory
python3 onboarding/onboard.py preflight /path/to/your/repo
python3 onboarding/onboard.py inspect /path/to/your/repo
python3 onboarding/onboard.py propose /path/to/your/repo
python3 onboarding/onboard.py apply /path/to/your/repo
python3 onboarding/onboard.py verify /path/to/your/repo
```

The pipeline detects greenfield/brownfield/bundled posture, drafts and installs the factory control-home surface (idempotent — never overwrites your files), ensures the pipeline labels, and writes a PASS/FAIL readiness report (`factory/PREFLIGHT.md`). The guided-session brief — every command and every choice — is [`ONBOARDING.md`](ONBOARDING.md); the quick reference is [`onboarding/README.md`](onboarding/README.md).

> The onboarding flow was reworked in kit v0.1 for runtime-agnostic requirements and is **shipped**: the readiness checklist and scripted verify mode (fail-closed, CI-gateable) are in [`kit-v0.1/target-repo-readiness.md`](kit-v0.1/target-repo-readiness.md) + `onboarding/`; the fresh-repo hardening test-out follows in v0.1.x.

### Read the process

1. [`grill-me/process.md`](grill-me/process.md) — comprehension verification (start here as a PO/analyst)
2. [`docs/context-bundle.md`](docs/context-bundle.md) — the four specification documents
3. [`kit-v0.1/prd.md`](kit-v0.1/prd.md) — where the factory is going: the runtime-agnostic kit
4. [`docs/field-notes.md`](docs/field-notes.md) + [`docs/github-native-runtime.md`](docs/github-native-runtime.md) — operational reality from live runs

### Enterprise / locked-down environments

The ADO-based variant (custom work item types as the state machine, no repository-committed artifacts) is documented in [`ado/design-spec.md`](ado/design-spec.md). Model substitution (Azure AI Foundry and other OpenAI-compatible endpoints) and devcontainer setups are covered in [`docs/github-native-runtime.md`](docs/github-native-runtime.md) and the kit's adapter design ([`kit-v0.1/architecture.md`](kit-v0.1/architecture.md)).

## License

MIT — fully open source. Use it, adapt it, contribute back.
