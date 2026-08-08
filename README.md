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
│   ├── matcher/             # clip ↔ slot assignment (chain DP + repair)
│   ├── renderer/            # blueprint + clips → MP4 (FFmpeg, deterministic)
│   ├── api/                 # FastAPI + job runner + web UI (app/static)
│   └── cli/                 # `reelsedits analyze | index | build`
├── infra/                   # docker-compose, Dockerfiles, k8s, Terraform sketch
├── tools/                   # check_links.py and other repo utilities
└── web/                     # Next.js 15 app — upload, timeline, preview, export
```

## Development

```bash
make install     # venv + all packages, editable
make run         # http://localhost:8000
make test-fast   # schema + matcher, seconds
make test        # full suite, renders real video
make lint
```

Both the UI and the CLI refuse to render below 0.55 coverage unless you explicitly
accept degradation, and they name the footage that is missing. Silent bad output is
the worst failure mode in this product, so it is not a default.

---

## Run it

You need **Python 3.10+** and **ffmpeg** (with libx264) on your PATH. Nothing else.

```bash
./run.sh                 # macOS / Linux
.\run.ps1                # Windows
```

Then open **http://localhost:8000** and drag in a reference video and some clips. First run builds a virtualenv and takes a couple of minutes; after that it starts instantly.

There is also a CLI, if you prefer:

```bash
reelsedits analyze reference.mp4                            # style card + blueprint.json
reelsedits index   my-clips/                                # segments, quality, shot scales
reelsedits build   reference.mp4 my-clips/ -o out.mp4       # the whole thing
```

## Status

**Alpha — it is a working application.** Upload a reference and footage in a browser, watch the analysis progress, read the style card, see which shots you're missing, render, swap clips you don't like, download the file.

| | |
|---|---|
| **Working** | Web UI · REST API · background jobs with live progress · SQLite/Postgres persistence · blueprint caching (~19× faster on a repeat reference) · coverage report with an actionable shoot list · clip swapping with preference logging · deterministic rendering · quota enforcement |
| **v0 baseline** | The analysis *models*. librosa stands in for Demucs + a transformer beat tracker; histogram differencing for the TransNetV2 + AutoShot ensemble; Farneback flow for SEA-RAFT. Semantic labelling is pluggable but ships with no weights, so `subject_class` is unset out of the box (see below). |
| **Not built** | Accounts and login (single local org), S3 and multi-node deployment (interfaces exist, local disk is wired), the marketplace, the licensed-music catalogue. |

Because the blueprint schema is frozen, swapping the real models in changes function bodies rather than contracts. [docs/07](docs/07-model-recommendations.md) specifies each replacement; [docs/20](docs/20-implementation-plan.md) has the order.

### Turning on cross-domain matching

The product's central claim is that a car reference can render onto motorcycle footage, because both a wheel and an exhaust are *low-angle mechanical detail*. That works through subject labels — and without a semantic model every subject is `any`, so the bridge never fires and matching runs on shot scale, camera motion and quality alone.

```bash
pip install -e "services/analyzer[vlm]"
export REELSEDITS_VLM_MODEL=Qwen/Qwen2.5-VL-3B-Instruct   # or [clip], cheaper
```

`GET /readyz` reports which backend is live and whether cross-domain matching is active, so this is never a silent difference. Backends are selected automatically and only claim availability when weights are already cached — downloading several GB during a user's first analysis would present as a hang.

What works with no weights at all: camera height (measured from horizon position — geometry, not semantics) and a perceptual shot descriptor for the matcher's similarity tiebreak.

## Three honest caveats

Stated up front because they shape every design decision downstream:

1. **Effect parameter recovery is an under-determined inverse problem.** You cannot exactly recover a LUT, a grain profile, or a glow radius from a compressed, delivered video — the encode has already destroyed the evidence. ReelsEdits estimates perceptually-equivalent parameters and attaches a confidence score to each. Where confidence is low, we say so in the UI rather than silently guessing. See [docs/08](docs/08-algorithms.md#5-colour-grade-inversion).

2. **We never redistribute the reference's recording — but you still get the track.** The master is almost always someone else's copyright, so we cannot mux it into an export. The default `platform_attach` mode renders a *silent* master and hands the creator the trim offset to attach the original sound inside TikTok/Instagram, where the platform's own blanket licence covers it. Because the edit was cut to that track's real beat grid, it re-syncs exactly. Same track, same sync, no redistribution. Licensed-catalogue substitution remains available for off-platform publishing. See [docs/06](docs/06-blueprint-spec.md#3-audio-track) and [docs/18](docs/18-legal-ethics.md).

3. **Clip matching degrades gracefully or not at all.** If a user uploads twelve seconds of footage against a ninety-second blueprint, no amount of modelling saves the output. The system detects footage insufficiency before rendering and either compresses the blueprint's structure or tells the user what is missing. Silent bad output is the worst failure mode in this product. See [docs/09](docs/09-clip-matching.md#7-insufficiency-and-graceful-degradation).

## Licence

MIT — see [LICENSE](LICENSE).
