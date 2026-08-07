# 20 — Implementation Plan

From this repository to a production platform. Week-by-week for phase 0, then coarser.

**Current state:** design complete, blueprint schema frozen and tested, matcher implemented and passing, API contracts defined, services scaffolded. What follows is the order in which the stubs get filled in.

---

## How to read this

Each week has a **deliverable** (something that runs) and an **exit test** (how you know it works). If the exit test fails, do not proceed — the dependency chain is real, and building on a broken shot-boundary detector wastes months in a way that is not obvious for weeks.

---

## Phase 0 — Prove it (weeks 1–14, team of 4)

### Weeks 1–2 — Foundation

**Done in this repository.** Blueprint schema, Pydantic models with cross-field invariants, 45 tests, matcher with 21 tests, CI, docker-compose, service scaffolds.

Remaining:
- Alembic migrations for the schema in [docs/11](11-database-schema.md)
- S3 presigned multipart upload, working end to end
- Temporal workflow skeleton with one real activity

**Exit test:** `docker compose up`, upload a file through a presigned URL, see the row in Postgres, see a Temporal workflow complete.

### Weeks 3–4 — Audio pipeline

The rhythmic backbone, and the most self-contained stage — which makes it the right place to validate the stage architecture before the expensive stages depend on it.

- Demucs v4 source separation
- Transformer beat tracker + madmom DBN, fused with agreement as a confidence signal
- CQT self-similarity → section boundaries, snapped to downbeats
- RMS + spectral flux + LUFS → 20Hz energy curve
- Impact detection; CLAP mood/genre; SFX onsets on the `other` stem
- **The audio deletion step, with a test that asserts it**

**Exit test:** 50 hand-annotated short-form videos. Beat F-measure >0.88. Section boundaries within 1 downbeat on >80%. Deletion assertion passes.

### Weeks 5–6 — Structure and motion

- TransNetV2 + AutoShot ensemble, fused, with gradual-transition *intervals*
- Transition classifier: the hand-designed features in [docs/08 §4.1](08-algorithms.md#41-classification), including Helmholtz decomposition for zoom-vs-spin
- SEA-RAFT flow on the motion proxy
- RANSAC homography fit → camera motion class; residual → subject motion
- Three-estimator speed inference with honest confidence

**Exit test:** shot boundary F1 >0.92 on the annotated set. Transition classification accuracy >0.80 across the six commonest classes. Speed inference: no confident-but-wrong ramps on a 30-clip manual review — a missing ramp is acceptable, a wrong one is not.

### Weeks 7–8 — Semantics

- VLM shot labelling under **constrained decoding** against the shared enums
- SAM 3 concept segmentation, masks, stable IDs, trajectories
- `subject_area_ratio` → `ShotScale` with hysteresis
- Faces: detection, expression, gaze; ephemeral identity only
- Composition: thirds, symmetry, negative space, horizon
- Same code path wired into the **indexer**, because vocabulary parity is the whole point

**Exit test:** shot-scale agreement with human labels >0.85. Zero enum violations across 500 shots. Analyser and indexer produce identical label distributions on the same clip run through both.

### Weeks 9–10 — Matcher and coverage

- Wire the implemented solver to real `Segment` records from the indexer
- Candidate retrieval via pgvector filtered search
- Coverage report with `describe_gap()` — specific, actionable messages
- Swap logging to ClickHouse **from day one**, before any UI exists to generate swaps

**Exit test:** on 20 hand-built (blueprint, footage) pairs, a human editor agrees with our top pick >60% of the time. Solve time p95 <2s. Zero constraint violations.

### Weeks 11–12 — Renderer v0

- FFmpeg filter-graph generation from the execution graph
- Reframe with smoothing, velocity clamp and **deadband** (all three, or the frame breathes)
- Grade application; hard cuts, dissolves, fades, flash
- Speed ramps via `setpts`
- Captions from word-level ASR
- Audio: licensed bed + SFX + ducked source; `loudnorm` to −14 LUFS
- Determinism test in CI: render twice, compare bytes

**Exit test:** 60s render in <120s on an L4. Byte-identical across two runs. Manual review of 20 renders finds no timing errors — no half-frame transition seams, no VFR drift.

### Week 13 — End to end

Thin UI: paste reference → style card → upload clips → coverage → preview → export.

**Exit test:** a person outside the team completes the flow without help.

### Week 14 — The kill gate

**Blind A/B, 200 raters, three arms:**

1. Our output
2. Same footage, same music, edited by a professional human editor
3. Same footage, sequential clip ordering, same blueprint (isolating the matcher)

**Kill criteria:**

| Comparison | Threshold | If it fails |
|---|---|---|
| Ours vs. human | <35% preference | The core claim is false. Stop. |
| Ours vs. sequential | <60% preference | Matching adds nothing. We are a template engine. Reconsider. |

Everything about phase 0's shape exists to reach this week for under $300k.

---

## Phase 1 — Make it real (weeks 15–32, team of 7)

### Weeks 15–17 — Accounts and billing

Clerk OIDC; orgs, RBAC, **row-level security in Postgres**; Stripe subscriptions and webhooks; transactional quota checking; rate limits; the global GPU-second circuit breaker.

**Exit test:** two orgs cannot see each other's data, verified by a test that queries *without* the application-level filter — proving RLS is doing real work rather than being decorative. A quota cannot be exceeded by 50 concurrent requests.

### Weeks 18–20 — Cache and the economics gate

Perceptual fingerprinting; pHash LSH near-duplicate lookup; blueprint cache; render cache; clip-feature cache.

**Week 18 gate:** measure the real hit rate on beta traffic. **Below 30%, [docs/14](14-cost-model.md) is wrong and pricing must change before launch, not after.**

### Weeks 21–23 — Music

Catalogue partnership signed; ingest and analyse the catalogue **with the same pipeline as references** so structural comparison is apples-to-apples; structural matching; `time_map` generation; licence issuance with terms snapshots; the music selection UI.

**Week 20–23 gate:** in-product swap rate on music, plus explicit churn survey. >40% citing music forces a strategic rethink ([docs/16 §R3](16-risks.md)).

### Weeks 24–26 — The editor

Timeline with every cut, transition, ramp and caption as a clickable object. **The swap flow with reasons and score breakdowns.** Blueprint editing. Partial re-render on dirty ranges.

**This is instrumentation disguised as a feature.** Every week it does not exist is a week of matcher training data never collected.

**Exit test:** a swap re-renders in <4s. Every alternative shows a reason. Swap events land in ClickHouse with `chosen_rank` populated.

### Weeks 27–29 — Renderer v1

Custom GL compositor alongside FFmpeg; whip pan with directional blur, RGB split, film burn, halation, light leaks; per-slot grade; subject-tracked reframing; segment-level render caching.

**Exit test:** golden set renders through both backends with SSIM >0.98 — divergence beyond that is a bug in one of them.

### Weeks 30–32 — Closed beta

200 automotive creators. Instrument everything. Weekly calls with 20 of them.

**Exit test:** >45% first-render acceptance (exported with zero edits). >30% week-4 retention.

---

## Phase 2 — Scale the wedge (months 9–14, team of 14)

| Months | Work | Exit test |
|---|---|---|
| 9–10 | EKS migration, GPU fleet, KEDA on estimated GPU-seconds, 60% spot, predictive pre-warm | p95 queue wait <30s at 3.5× mean load; spot interruption costs ≤1 stage |
| 10–11 | Qdrant migration (dual-write, then read cutover); **matcher v1 trained on real swaps** | Matcher v1 beats v0 at >65% on held-out preference pairs |
| 11–12 | Style library, brand kits, Team tier, SSO | — |
| 12–13 | Public API, OpenAPI, three integration partners | A partner ships without talking to us |
| 13–14 | **Second vertical (fitness)** | Expansion is configuration, not engineering |

**The second vertical is the real test.** If it requires retraining models rather than writing SAM 3 concept prompts and re-weighting the matcher, the expansion thesis is wrong and GTM spend should not scale until it is fixed.

---

## Phase 3 — Platform (months 15–20, team of 30)

Marketplace (review queue, Stripe Connect payouts, originality checks). Collaboration. Long-form → short-form. Prompt-based blueprint transforms. Multi-region for egress cost and data residency.

---

## Phase 4 — Compound (months 21–24, team of 55)

Learned blueprint model on the 250k+ corpus — removing the frontier LLM, which is 71% of analysis cost. Model distillation across the vision stack. Generated B-roll with C2PA. Mobile native. EBP published as an open standard.

---

## Critical path

```
Blueprint schema ──► Audio ──► Structure ──► Motion ──► Semantics ──► Matcher ──► Renderer ──► WEEK 14 GATE
      (done)                                                │
                                                            └──► Indexer (shares the vocabulary)
```

**Anything not on this path is deferrable.** Billing, auth, autoscaling, the marketplace, the API, mobile — all well-understood engineering, all worthless if the week-14 gate fails.

---

## Ten things that will go wrong

Written from experience with pipelines of this shape, so they are cheaper the second time:

1. **Variable frame rate.** Phone footage is frequently VFR. `frame_index / fps` drifts progressively — a 40-second clip can be half a second out by the end. Build the PTS map at probe time, in week 1, and index into it everywhere.
2. **Rotation metadata.** Portrait video that reports landscape dimensions with a rotation flag. Handle it at probe or every downstream stage is silently wrong.
3. **Audio/video sync drift.** Long clips with mismatched timebases. Resample audio to a fixed rate at demux.
4. **Colour space.** HDR references tone-mapped inconsistently produce grade estimates that are confidently wrong. Normalise at probe, record the mapping.
5. **Off-by-half-a-frame transitions.** Inconsistent rounding between the transition start and the underlying cut produces a one-frame flash at the seam. One `snap_to_frame` function, used everywhere.
6. **Reframe jitter.** Per-frame mask noise driving the crop directly. Smoothing, velocity clamp *and* deadband — the deadband is the one most often omitted and most responsible for the breathing-frame artefact.
7. **VLM enum drift.** A model update starts emitting a synonym outside the enum. Constrained decoding plus a hard validation failure, not a warning.
8. **Cache key omission.** A cache key without the component version serves stale output indefinitely, with no error anywhere. Version in every key, tested.
9. **Runaway retry cost.** A poison-pill job re-rendering overnight. Global GPU-second circuit breaker, built in week 1, not after the first bill.
10. **Silent model regression.** The highest-likelihood risk in [docs/16](16-risks.md) and the cheapest to mitigate. Golden set from week 6, blocking deploys.

---

## Definition of done, by phase

**Phase 0:** a stranger completes the flow and the week-14 blind A/B passes both thresholds.

**Phase 1:** a stranger pays, renders 40 videos, and returns in week 4.

**Phase 2:** 10,000 users, 83% gross margin, matcher measurably improving from real swap data, second vertical shipped as configuration.

**Phase 3:** the marketplace pays creators meaningfully, and a third party has built on the API without our help.

**Phase 4:** the blueprint corpus trains a model that generates styles rather than transferring them, and EBP is a format someone else imports.

---

← [00 — Executive Summary](00-executive-summary.md) · [README](../README.md)
