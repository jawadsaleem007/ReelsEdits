# 13 — Scalability

Three scale points, with what actually breaks at each.

---

## 1. What "scale" means here

Not requests per second. The API tier is stateless and could serve a million users on a handful of instances — it never touches media and never blocks on video work.

The constraint is **GPU-seconds per second of wall clock**, and the derived constraint is **queue wait time**, because a user watching a spinner does not care that the fleet is efficiently utilised.

```
Load = (jobs/sec) × (GPU-seconds/job) × (1 − cache_hit_rate)
```

Every scaling strategy in this document attacks one of those three terms. The third is by far the cheapest.

---

## 2. 100 users — validate, don't optimise

**Shape:** ~15 daily active, ~60 renders/day, bursty (evenings, weekends).

```
CloudFront ──► ALB ──► 2× API (t4g.medium)
                        │
                        ├──► RDS Postgres (db.t4g.medium, single-AZ)
                        ├──► ElastiCache Redis (cache.t4g.micro)
                        └──► Temporal Cloud (managed)
                                  │
                                  └──► Modal / RunPod serverless GPU
```

**Everything managed. No Kubernetes. No GPU fleet.**

Serverless GPU costs 2.5–4× reserved capacity per GPU-hour, and at this volume that difference is roughly **$400/month against six weeks of engineering time**. Anyone who builds a GPU autoscaling system before product-market fit has optimised the wrong variable — and will likely rebuild it anyway once the workload profile is real.

pgvector handles vectors. Postgres handles telemetry. Qdrant and ClickHouse are not deployed.

**What breaks first:** nothing technical. The binding constraint is whether the output is good enough, which is what [docs/00](00-executive-summary.md#what-has-to-be-true) schedules for week 14.

| Cost | Monthly |
|---|---|
| Serverless GPU (~40 hrs) | $95 |
| RDS + Redis + ALB | $180 |
| S3 + CloudFront | $40 |
| Temporal Cloud | $100 |
| Frontier LLM (planner) | $25 |
| **Total** | **~$440** |

---

## 3. 10,000 users — the interesting problem

**Shape:** ~1,800 DAU, ~7,000 renders/day, peak 3.5× mean (7–10pm local, and the peak follows the sun across time zones).

```
                              CloudFront + WAF
                                     │
                        ALB ──► 6–20× API (EKS, c7g.xlarge, HPA)
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
              RDS Postgres      ElastiCache      Temporal
              (r7g.2xlarge      (cluster mode)   (self-hosted
               Multi-AZ                           on EKS)
               + 2 replicas)          │
                    │                 ▼
                    │          Redis Streams ──► KEDA
                    │                              │
                    ▼                    ┌─────────┼─────────┐
                 Qdrant                  ▼         ▼         ▼
              (3 replicas)          Analyzer   Indexer   Renderer
                                    2–8× L4    2–12× L4  4–30× L4
                                              (60% spot)
```

### 3.1 GPU scheduling — the core problem

The scheduler must satisfy three objectives that pull against each other:

1. Interactive previews start within 10 seconds
2. GPU utilisation stays above 70% (below that we are burning money)
3. Batch and API work never starves entirely

**Priority streams, not a priority field.** Three separate Redis Streams — `render:p0` (interactive preview), `render:p1` (export), `render:p2` (batch/API). Workers read p0 first, then p1, then p2. A single stream with a priority attribute requires scanning to find high-priority work, which is O(n) at exactly the moment you cannot afford it.

**Scale on estimated GPU-seconds, not message count.** A 4K 90-second render and a 540p 15-second preview are not the same unit, and treating them as one produces a fleet that is simultaneously over- and under-provisioned.

```python
def desired_replicas() -> int:
    backlog = sum(job.estimated_gpu_seconds for job in pending())
    target_wait = 45.0                      # seconds
    per_replica = 0.85                      # realistic utilisation
    return ceil(backlog / (target_wait * per_replica))
```

**Asymmetric scaling.** Up aggressively (30s stabilisation), down conservatively (5min). A GPU node takes 60–90 seconds to become useful even with a warm image cache and mmap'ed weights, so scaling down into a rising peak is expensive in a way scaling up early is not.

**Predictive pre-warming.** Load is highly diurnal and highly predictable. A simple time-of-day + day-of-week model, pre-warming 20 minutes ahead of forecast, removes most cold-start latency at negligible cost. Not machine learning — a lookup table over the last four weeks beats anything fancier here.

### 3.2 Spot instances

60% of GPU capacity on spot with on-demand fallback.

This is safe **because** of the Temporal investment in [docs/03](03-system-architecture.md): a spot interruption costs one re-run of one stage, not a failed job. Concretely, this is where the durability work pays for itself in dollars — roughly **$3,100/month at this scale**.

Interruption handling: the 2-minute warning triggers a graceful drain — stop claiming, finish in-flight work if it fits in the window, otherwise checkpoint and release. Priority p0 (interactive) runs only on on-demand, because a user watching a spinner should never pay for our spot discount.

### 3.3 What breaks first, in order

**1. pgvector recall degrades.** At roughly 5M segment vectors, filtered HNSW search — which is *always* what we do, since matching is scoped to one project — starts returning poor candidates because the filter and the graph traversal fight each other. **Fix:** Qdrant, which does filtered ANN natively. Dual-write for two weeks, then cut reads over.

**2. Postgres write contention on `jobs`.** Thousands of state transitions per second on a hot table. **Fix:** monthly partitioning on `queued_at`, plus moving progress updates out of Postgres entirely — progress goes to Redis and is only persisted at stage boundaries. Most of the write volume was progress noise nobody queries.

**3. Temporal history size.** Long workflows with many activities accumulate large event histories, and replay gets slow. **Fix:** `continue_as_new` at stage boundaries; keep individual workflows under ~200 events.

**4. S3 request cost, not storage cost.** Millions of small `GET`s for proxies and segment artefacts. **Fix:** aggressive CloudFront caching, plus packing per-stage artefacts into a single object per job instead of one per artefact.

**5. Frontier LLM rate limits.** The planner hits provider quotas during peak. **Fix:** the blueprint cache means this is already amortised; add a request queue with a 30-second deadline and fall back to the open-weight planner past it, stamping `planner_tier: fallback`.

### 3.4 Cost

| Item | Monthly | Note |
|---|---|---|
| GPU (reserved baseline + spot) | $18,400 | 62% of total |
| GPU (on-demand burst) | $3,200 | |
| EKS control plane + CPU nodes | $2,100 | |
| RDS (r7g.2xlarge Multi-AZ + replicas) | $1,850 | |
| ElastiCache | $620 | |
| Qdrant on EKS | $780 | |
| ClickHouse | $410 | |
| S3 + CloudFront | $2,400 | Egress dominates |
| Temporal (self-hosted) | $340 | |
| Frontier LLM | $1,100 | Amortised by cache |
| Music licensing minimum | $4,000 | |
| Monitoring | $600 | |
| **Total** | **~$35,800** | |

At ~$210k MRR (10k users, mixed tiers), **gross margin ≈ 83%.**

---

## 4. 1,000,000 users — different company

**Shape:** ~180k DAU, ~700k renders/day, ~8,100 GPU-hours/day.

At this scale three things change qualitatively, not quantitatively.

### 4.1 Multi-region

Not for latency — for **egress cost and data residency**. Video egress is the second-largest line item, and serving EU users from us-east-1 is both slow and expensive.

```
us-east-1 (primary)      eu-west-1              ap-southeast-1
├── full stack           ├── full stack         ├── full stack
├── Postgres primary     ├── read replica       ├── read replica
└── blueprint corpus ────┴── replicated ────────┘
```

**Blueprints replicate globally; media does not.** Blueprints are kilobytes and are the shared asset — a reference analysed in Tokyo should be a cache hit in Berlin. User media stays in its region of origin, which handles GDPR residency and keeps egress local. This split is only possible because the blueprint contains no media, which was a legal decision that turns out to be an architectural one.

### 4.2 Buy GPUs

At 8,100 GPU-hours/day, on-demand cloud GPU costs roughly $2.1M/month. Committed-use contracts cut that by ~45%. Owned hardware in colo cuts it by ~65% at an 18-month payback.

The staged answer: reserved cloud capacity for the predictable baseline (~60%), spot for the middle (~30%), on-demand for the peak (~10%), and a colo evaluation once daily GPU-hours pass ~5,000 for two consecutive quarters. Committing capital to depreciating hardware before the workload is stable is how infrastructure teams destroy a year of runway.

### 4.3 Model distillation

At this volume, inference efficiency is a business strategy rather than an optimisation.

- **Distil the shot-semantics VLM.** A task-specific 2B model trained on our own labelled corpus replaces a 7B general VLM at ~4× throughput and, on our narrow label set, comparable accuracy.
- **Replace the LLM planner with a learned blueprint model.** By this point the corpus is millions of blueprints — enough to train a native generator ([docs/07 §9](07-model-recommendations.md#9-editing-recommendation--agentic-planning)). This removes our only paid inference entirely.
- **Quantise everything.** INT8 for vision models, FP8 where hardware supports it. 2–3× throughput at accuracy losses that do not survive contact with a blind A/B test.

Expected effect: **~50% reduction in GPU-seconds per job.** Against $2.1M/month, that is the single highest-ROI engineering project in the company at that stage.

### 4.4 Cache hit rate becomes the whole game

At a million users, reference selection is extremely power-law distributed. Realistic steady-state hit rate: **80–88%**.

That means only 12–20% of jobs pay for analysis. The economics invert — analysis becomes a rounding error and *rendering* becomes essentially all of COGS, because rendering is inherently uncacheable across users (everyone's footage is different).

Which reframes the optimisation target at scale: **render efficiency, not analysis efficiency.** Concretely — better codec settings, segment-level caching within a user's iteration loop, shared effect passes across concurrent renders on one GPU, and preview quality tuning.

### 4.5 Cost

| Item | Monthly |
|---|---|
| GPU (reserved + spot + owned) | $1,240,000 |
| Storage + CDN egress | $210,000 |
| Databases (multi-region) | $95,000 |
| CPU compute | $68,000 |
| Music licensing | $180,000 |
| Monitoring + ops tooling | $32,000 |
| **Total** | **~$1,825,000** |

At ~$14.5M MRR, **gross margin ≈ 87%.**

---

## 5. Scaling summary

| | 100 users | 10k users | 1M users |
|---|---|---|---|
| Renders/day | 60 | 7,000 | 700,000 |
| GPU-hours/day | 1.3 | 165 | 8,100 |
| Cache hit rate | 15% | 62% | 85% |
| GPU strategy | Serverless | Reserved + 60% spot | Reserved + spot + colo |
| Vector store | pgvector | Qdrant | Qdrant, sharded |
| Regions | 1 | 1 | 3 |
| Infra cost/mo | $440 | $35,800 | $1,825,000 |
| Cost/render | $0.24 | $0.17 | $0.087 |
| Gross margin | negative | 83% | 87% |
| Team | 3 | 14 | 85 |

**Cost per render falls 64% from 100 users to 1M**, driven by cache hit rate, reserved capacity, and model distillation in roughly that order. This is the operating leverage that makes the business model work, and it is why [docs/00](00-executive-summary.md#what-has-to-be-true) lists cache hit rate as a week-18 validation item with a kill criterion.

---

## 6. Load shedding

When capacity is genuinely exhausted, degrade in this order:

1. **Pause p2 (batch/API).** They have SLAs measured in hours.
2. **Downgrade export quality.** 4K exports queue behind 1080p, with a notice.
3. **Reduce preview resolution.** 540p → 360p. Visually acceptable for judging an edit.
4. **Disable optical-flow speed interpolation.** Falls back to blend. Costs ~0.9 GPU-seconds per ramp.
5. **Queue with an honest ETA.** "Approximately 6 minutes" beats a spinner, and beats a 503.
6. **503 with `Retry-After`.** Last resort, and only for new jobs — never for jobs already in flight.

**Never** shed by silently reducing output quality without saying so. That is the same failure as undeclared degradation in [docs/06 §15](06-blueprint-spec.md#15-degradation): the user ships it and blames their own work.

---

Next: [14 — Cost Model](14-cost-model.md)
