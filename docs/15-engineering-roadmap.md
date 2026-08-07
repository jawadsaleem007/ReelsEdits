# 15 — Engineering Roadmap

Twenty-four months, five phases. Week-by-week detail for phases 0–2 is in [docs/20](20-implementation-plan.md); this document is the shape, the team, and the sequencing logic.

---

## 1. Sequencing principle

**Build the thing that can be wrong first.**

The riskiest claims in this plan are that style transfer is perceptually convincing and that intelligent matching beats sequential ordering. Both are testable at week 14 with a team of four. Everything else — scaling, billing, the marketplace, the API — is well-understood engineering that we know how to do and that is worthless if the first two claims are false.

So the roadmap front-loads *falsification*, not features.

---

## 2. Phase 0 — Prove it (weeks 1–14)

**Goal:** answer "does this work at all" for under $300k.

**Team: 4.** Two ML engineers, one backend/infra, one founding designer who also writes frontend.

| Weeks | Deliverable | Why here |
|---|---|---|
| 1–2 | Blueprint schema, Pydantic models, tests, CI | The blueprint is the interface; everything else depends on it existing first |
| 3–4 | Audio pipeline: Demucs, beat, structure, energy | The rhythmic backbone. Also the most self-contained, so it validates the stage architecture cheaply |
| 5–6 | Structure + motion: SBD ensemble, SEA-RAFT, camera decomposition | Every downstream stage is per-shot |
| 7–8 | Semantics: VLM under constrained decoding, SAM 3, composition | Produces the shared vocabulary the matcher needs |
| 9–10 | Matcher v0: chain DP + repair, coverage report | The riskiest component |
| 11–12 | Renderer v0: FFmpeg filter graph, transitions, grade, captions | Enough to look at output |
| 13 | End-to-end: paste reference → upload clips → preview | First moment the thing exists |
| **14** | **Blind A/B evaluation, 200 raters** | **The kill gate** |

**Kill criteria at week 14** (from [docs/00](00-executive-summary.md#what-has-to-be-true)):

- Blind raters prefer our output to a human editor's <35% of the time → the core claim is false
- Matched assignment beats sequential <60% of the time → [docs/09](09-clip-matching.md) is wrong and the product is a template engine

Either result means stopping, and finding out for $300k rather than $4.5M is the entire point of this phase's shape.

**Deliberately not built:** billing, auth beyond a hardcoded org, multi-tenancy, autoscaling, the marketplace, 4K, the API, mobile. Serverless GPU throughout — no fleet management.

---

## 3. Phase 1 — Make it real (weeks 15–32)

**Goal:** a product a stranger can pay for.

**Team: 7.** +1 backend, +1 graphics/render engineer, +1 frontend.

| Weeks | Deliverable |
|---|---|
| 15–17 | Auth, orgs, RBAC, row-level security, Stripe billing, quotas |
| 18–20 | Blueprint cache with perceptual fingerprinting; measure the real hit rate |
| 21–23 | Music: catalogue partnership, structural matching, licence issuance |
| 24–26 | Editor UI: timeline, swap flow with reasons, blueprint editing |
| 27–29 | Renderer v1: custom GL backend, whip pan, RGB split, film burn, halation |
| 30–32 | Closed beta with 200 automotive creators |

**Week 18 gate — cache hit rate.** If under 30% of jobs hit a warm reference, [docs/14](14-cost-model.md) is wrong and pricing must change before launch, not after.

**Week 20 gate — music acceptance.** If >40% of beta users cite substituted music as a reason to churn, the music strategy needs rethinking. This is the assumption I am least confident about in the whole plan.

**The swap UI is a phase-1 priority, not a phase-2 one.** Every week it does not exist is a week of matcher training data we never collect. It is instrumentation disguised as a feature.

---

## 4. Phase 2 — Scale the wedge (months 9–14)

**Goal:** dominate one vertical, then prove the second.

**Team: 14.** +2 ML, +2 backend/infra, +1 designer, +1 data engineer, +1 DevRel.

| Months | Deliverable |
|---|---|
| 9–10 | Migrate to EKS, GPU worker fleet, KEDA autoscaling, spot |
| 10–11 | Qdrant migration; matcher v1 trained on the first ~50k swaps |
| 11–12 | Style library, saved styles, brand kits; Team tier |
| 12–13 | Public API + three integration partners |
| 13–14 | Second vertical (fitness), validating that expansion is a config change |

**The second vertical is the real test.** If expanding from automotive to fitness requires retraining models rather than writing new SAM 3 concept prompts and re-weighting the matcher, then [docs/07 §4](07-model-recommendations.md#4-segmentation--tracking) is wrong about zero-shot segmentation and the expansion strategy needs rework.

**Matcher v1 is the first compounding artefact.** Learned fit weights from ~5k preference pairs, then the learned projection head at ~50k ([docs/09 §6](09-clip-matching.md#6-learning-from-swaps)). This is where the moat starts existing rather than being asserted.

---

## 5. Phase 3 — Platform (months 15–20)

**Team: 30.**

- **Marketplace.** Creators sell blueprints, 70/30 split. Requires review, moderation, payouts, and a stable blueprint format — which is why the format was frozen in phase 0.
- **Collaboration.** Multiple editors on one project. Only tractable because renders are deterministic.
- **Long-form → short-form.** Different input shape, reuses the whole analysis stack.
- **Prompt-based editing.** "Make it faster and moodier" as a blueprint transform.
- **Multi-region.** EU and APAC, for egress cost and data residency.

---

## 6. Phase 4 — Compound (months 21–24)

**Team: 55.**

- **Learned blueprint model** trained on 250k+ corpus. Removes the frontier LLM from the critical path — which [docs/14 §2.1](14-cost-model.md) shows is 71% of analysis cost.
- **Model distillation** across the vision stack. Up to 50% GPU reduction.
- **Generative B-roll and transitions**, with C2PA provenance and UI disclosure.
- **Mobile native**, on-device preview.

---

## 7. Hiring plan

| | M0 | M6 | M12 | M18 | M24 |
|---|---:|---:|---:|---:|---:|
| ML / research | 2 | 3 | 5 | 8 | 14 |
| Backend / infra | 1 | 3 | 5 | 9 | 16 |
| Graphics / render | 0 | 1 | 2 | 4 | 7 |
| Frontend | 1 | 2 | 3 | 5 | 9 |
| Design | 1 | 1 | 2 | 3 | 4 |
| Data / analytics | 0 | 0 | 1 | 2 | 3 |
| DevRel / support | 0 | 0 | 1 | 3 | 5 |
| GTM | 0 | 1 | 3 | 8 | 15 |
| Ops / finance / legal | 0 | 1 | 2 | 3 | 5 |
| **Total** | **5** | **12** | **24** | **45** | **78** |

**Two hires that are easy to get wrong:**

**The graphics/render engineer (month 7).** Hiring this person too late is a common and expensive mistake. Someone who has shipped a real-time compositor or a game engine renderer will build in three months what a generalist backend engineer builds in nine, and the difference shows up directly in output quality — which is the product.

**The data engineer (month 11).** Not for dashboards. For the swap-log pipeline that feeds the matcher. Every month this role is unfilled is a month of degraded training data, and the data is the moat.

---

## 8. Engineering practices

**Golden-set regression on every analyzer deploy.** 200 fixed reference videos re-analysed, blueprints diffed structurally against the known-good baseline. Cut-count drift >5%, transition-distribution shift, or grade drift beyond tolerance blocks the deploy. **This is the single most valuable piece of test infrastructure in the system** — model updates silently changing output is the failure mode that erodes user trust fastest, and it is invisible without this.

**Determinism tests in CI.** Render the golden set twice; any byte difference fails the build. Determinism is a contract ([docs/10 §1](10-rendering-engine.md#1-the-determinism-requirement)) and contracts need enforcement.

**The blueprint example is generated, not hand-written.** [`build_example.py`](../schemas/examples/build_example.py) regenerates it in CI and fails on any diff, so the published example cannot drift from the models.

**Every artefact records the version that produced it.** Non-negotiable. It turns "which model version produced the outputs users rejected" from an investigation into a join.

**Feature flags on every model swap.** New shot-boundary detector ships behind a flag, canaries at 5%, compares golden-set output, then ramps. Model changes are riskier than code changes because they fail gradually rather than loudly.

---

## 9. Technical debt we are taking deliberately

Stated up front so it is a decision rather than an accident:

| Debt | Why | Repay by |
|---|---|---|
| FFmpeg-only renderer | Ships 4 months faster than a custom compositor | Month 8 |
| pgvector instead of Qdrant | One less system before PMF | Month 10 |
| Serverless GPU | Removes an entire operational surface | Month 9 |
| Hand-tuned matcher weights | No training data exists yet | Month 11 |
| No multi-region | Complexity without users to justify it | Month 17 |
| Single blueprint version | Migration machinery before it's needed is waste | As needed |

Everything on this list is a **contained** shortcut — each is behind an interface that makes the replacement a swap rather than a rewrite. That containment is the actual discipline; taking shortcuts is easy, taking ones that do not metastasise is the skill.

---

Next: [16 — Risks](16-risks.md)
