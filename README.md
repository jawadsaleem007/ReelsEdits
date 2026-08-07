# ReelsEdits

**Style transfer for video editing.** Upload a reference video whose editing you admire, upload your own raw footage, and get back a finished edit that reproduces the reference's *craft* — its pacing, cut rhythm, transitions, speed ramps, colour grade, caption style, and camera-motion vocabulary — applied to your own clips.

ReelsEdits does not copy the reference video. It copies **how the reference was edited**.

---

## The one-paragraph version

A reference video is analysed into an **Editing Blueprint (EBP)** — a timecoded, renderer-agnostic JSON document describing every editorial decision: where the cuts land relative to the beat grid, what each transition is and how long it lasts, which shot types appear in which narrative slot, how speed and zoom curve across each shot, what the colour grade does, how captions animate. The blueprint contains *no pixels and no audio from the reference*. Your own clips are then indexed into the same feature space, a matcher assigns your clips to the blueprint's slots by semantic and kinetic similarity rather than by filename order, and a deterministic render engine executes the blueprint against your footage.

The blueprint is the product. Everything else is plumbing around it.

---

## Documentation

The full technical and business plan lives in [`docs/`](docs/). Read in order, or jump to what you need.

### Part I — Why

| # | Document | What's in it |
|---|---|---|
| 00 | [Executive Summary](docs/00-executive-summary.md) | The thesis, the wedge, the ask, the numbers |
| 01 | [Product Vision](docs/01-product-vision.md) | User journeys, the "style transfer not content transfer" principle, what we refuse to build |
| 02 | [Competitive Analysis](docs/02-competitive-analysis.md) | OpusClip, Submagic, Captions, Descript, CapCut, Resolve — where the gap actually is |

### Part II — How it works

| # | Document | What's in it |
|---|---|---|
| 03 | [System Architecture](docs/03-system-architecture.md) | Services, GPU worker topology, storage, queues, diagrams |
| 04 | [AI Pipeline](docs/04-ai-pipeline.md) | Stage-by-stage analysis of reference and user footage |
| 05 | [Data Flow](docs/05-data-flow.md) | End-to-end trace of one job, with latency budgets |
| 06 | [Editing Blueprint Spec](docs/06-blueprint-spec.md) | EBP v1 — the core data structure |
| 07 | [Model Recommendations](docs/07-model-recommendations.md) | Every model, why it was chosen, what it costs, what replaces it |
| 08 | [Algorithms](docs/08-algorithms.md) | Beat-grid quantisation, transition classification, grade inversion, ramp fitting |
| 09 | [Clip Matching](docs/09-clip-matching.md) | The assignment problem — the hardest and most valuable component |
| 10 | [Rendering Engine](docs/10-rendering-engine.md) | Blueprint → filter graph → frames → file |

### Part III — Building it

| # | Document | What's in it |
|---|---|---|
| 11 | [Database Schema](docs/11-database-schema.md) | Postgres DDL, pgvector indexes, partitioning |
| 12 | [API Design](docs/12-api-design.md) | REST surface, webhooks, SSE, idempotency, errors |
| 13 | [Scalability](docs/13-scalability.md) | 100 → 10k → 1M users; GPU scheduling, distributed render |
| 14 | [Cost Model](docs/14-cost-model.md) | Unit economics per job, COGS at each scale, gross margin |
| 15 | [Engineering Roadmap](docs/15-engineering-roadmap.md) | Team, phases, hiring plan |
| 16 | [Risks](docs/16-risks.md) | Technical, legal, market, execution — with mitigations |

### Part IV — The business

| # | Document | What's in it |
|---|---|---|
| 17 | [Business Model](docs/17-business-model.md) | Pricing, tiers, marketplace, projections, GTM |
| 18 | [Legal & Ethics](docs/18-legal-ethics.md) | Copyright, music licensing, ToS, moderation, deepfakes |
| 19 | [Future Roadmap](docs/19-future-roadmap.md) | Director mode, prompt editing, generative B-roll |
| 20 | [Implementation Plan](docs/20-implementation-plan.md) | Week-by-week from empty repo to production |

---

## Repository layout

```
ReelsEdits/
├── docs/                    # the plan (see table above)
│   └── diagrams/            # Mermaid sources, render natively on GitHub
├── schemas/
│   └── blueprint.schema.json    # Editing Blueprint v1 — JSON Schema draft 2020-12
├── services/
│   ├── common/              # Pydantic v2 blueprint models, shared types, enums
│   ├── api/                 # FastAPI — auth, uploads, jobs, blueprints, SSE
│   ├── analyzer/            # GPU worker: reference video → Editing Blueprint
│   ├── indexer/             # GPU worker: user clips → ClipFeature + embeddings
│   ├── planner/             # CPU/LLM worker: blueprint synthesis & re-planning
│   ├── matcher/             # CPU worker: clip ↔ slot assignment
│   └── renderer/            # GPU worker: blueprint + clips → MP4
├── infra/                   # docker-compose, Dockerfiles, k8s, Terraform sketch
└── web/                     # Next.js 15 app — upload, timeline, preview, export
```

---

## Status

**Pre-alpha.** This repository currently contains the complete design and a runnable scaffold. Services expose their real API surface and validate real data structures, but the model-backed analysis stages are stubs. See [docs/20](docs/20-implementation-plan.md) for the path from here to a working MVP.

## Three honest caveats

Stated up front because they shape every design decision downstream:

1. **Effect parameter recovery is an under-determined inverse problem.** You cannot exactly recover a LUT, a grain profile, or a glow radius from a compressed, delivered video — the encode has already destroyed the evidence. ReelsEdits estimates perceptually-equivalent parameters and attaches a confidence score to each. Where confidence is low, we say so in the UI rather than silently guessing. See [docs/08](docs/08-algorithms.md#5-colour-grade-inversion).

2. **The reference's music cannot be reused.** It is almost always someone else's copyrighted master. The blueprint stores the reference's *rhythmic and energetic structure* — beat grid, downbeats, section boundaries, energy envelope — and the renderer binds that structure to a **licensed** track from our catalogue with compatible tempo and form. This is a schema-level decision, not a policy bolted on afterwards. See [docs/06](docs/06-blueprint-spec.md#3-audio-track) and [docs/18](docs/18-legal-ethics.md).

3. **Clip matching degrades gracefully or not at all.** If a user uploads twelve seconds of footage against a ninety-second blueprint, no amount of modelling saves the output. The system detects footage insufficiency before rendering and either compresses the blueprint's structure or tells the user what is missing. Silent bad output is the worst failure mode in this product. See [docs/09](docs/09-clip-matching.md#7-insufficiency-and-graceful-degradation).

## Licence

MIT — see [LICENSE](LICENSE).
