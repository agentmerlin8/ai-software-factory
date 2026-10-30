**S3.5c — Dual-model review panel** (third increment of S3.5; owner decision 2026-10-30: cross-family double review, wired into the lane now, measured on the next real PR)

Owner decision, 2026-10-30: with Copilot review retired, the owned lane must be thorough on its own. The panel currently runs three lenses on ONE model (`qwen3.8-max`); the bench shows the union is what works (0.93 blocker/major recall) while no single lens is trustworthy (contract 0.14, security 0.43, adversary 0.64). A second model family is the same diversity lever one level up. Chosen pair: `qwen3.8-max` + `deepseek-v4-pro-0813` (in-pool, no new credential).

**Deliverables**

- **Panel shape** — every internal lens runs once per model, both blind, same package hash, separate findings documents. Structurally enforced: the record refuses a panel whose models cover different lens sets (panel asymmetry), and refuses two documents with the same (lens, model).
- **CLI** — `--findings ROLE@MODEL=FILE` (repeatable) as the unambiguous form; legacy `--findings ROLE=FILE` + `--model ROLE=MODEL` still renders a single-model panel. Refuse mixing the two forms.
- **Record schema `review.record.v2`** (lane version 2) — each finding gains `model`; record-level finding ids are namespaced `<lens-id>@<model>` so two models' `RC-001` cannot collide; each finding carries `location_key` (artifact path + dimension digest) and `agreement` (distinct models raising at that location). Reviewer entries keep role/model/document hash. `check` accepts stored `review.record.v1` records under v1 semantics and re-derives v2 records field-by-field, including panel symmetry and agreement counts.
- **Verdict over the union** — any blocker/major from either model forces CHANGES REQUESTED; a second model can add a finding, never cancel one. Zero-undispositioned still enforced; dispositions key on the namespaced id.
- **Render** — Panel section (models, lens coverage matrix, per-model finding counts) and findings table columns for model and agreement (`2/2`, `1/2`) so duplicate clusters are triaged as pairs.
- **Docs** — `kit-v0.1/architecture.md` review-lane section states the two-model panel and the namespacing rule.

**Acceptance**

- A 6-document dual panel renders a v2 record; the same defect raised by both models appears twice with `agreement: 2`, and each entry is dispositioned separately.
- Panel asymmetry (one model missing a lens) and duplicate (lens, model) are named refusals.
- A stored v1 record still passes `check` unchanged.
- `check` catches a dropped, downgraded, or re-attributed finding, and a panel-symmetry violation introduced into stored JSON.
- Unit suite green (`python3 -m unittest`), kit-ci green.

**Out of scope (named deferrals)**

- Bench A/B of the second model — owner chose to measure on the next real PR instead; the bench gains a per-model/union block in a later slice.
- Required CI check and state-machine cutover — that is S3.5b, which now builds against record v2.
- Re-recording PR #23 at its head (carried item O1 on #25) — unchanged by this slice.
