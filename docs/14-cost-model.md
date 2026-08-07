# 14 — Cost Model

Unit economics from GPU-seconds up. Every figure below is derived from the latency budgets in [docs/05](05-data-flow.md) and the per-stage GPU costs in [docs/04](04-ai-pipeline.md), not estimated top-down.

---

## 1. Input assumptions

| Input | Value | Source |
|---|---|---|
| L4 GPU, on-demand (g6.xlarge) | $0.805/hr = **$0.000224/GPU-s** | AWS list |
| L4, 1-yr reserved | $0.52/hr = $0.000144/GPU-s | ~35% discount |
| L4, spot | $0.29/hr = $0.000081/GPU-s | ~64% discount, varies |
| A10G (4K work) | $1.006/hr | |
| S3 Standard | $0.023/GB/mo | |
| CloudFront egress | $0.085/GB | Tiered down at volume |
| Frontier LLM planner call | ~$0.021 | ~15k in / 4k out |
| CPU worker (c7i.2xlarge) | $0.357/hr | |

**Blended GPU rate used throughout: $0.000131/GPU-s** — a 20/60/20 mix of on-demand, reserved and spot, matching the fleet composition in [docs/13 §3.2](13-scalability.md#32-spot-instances).

---

## 2. Cost per job

### 2.1 Reference analysis (cache MISS)

| Stage | GPU-s | Cost |
|---|---:|---:|
| Probe & proxies (CPU) | — | $0.0002 |
| Audio (Demucs, beat, structure, mood, SFX) | 8 | $0.00105 |
| Structure (SBD ensemble + transition classification) | 6 | $0.00079 |
| Motion (SEA-RAFT, camera decomposition, speed) | 12 | $0.00157 |
| Semantics (VLM ×25 shots, SAM 3, faces) | 18 | $0.00236 |
| Grade & effects (fit + detectors) | 7 | $0.00092 |
| Text (OCR + WhisperX) | 9 | $0.00118 |
| Fusion (CPU) | — | $0.0001 |
| **Planner (frontier LLM)** | — | **$0.0210** |
| S3 writes + storage | — | $0.0018 |
| **Total** | **60** | **$0.0296** |

**The planner is 71% of analysis cost** and every GPU stage combined is 26%. This is worth staring at: the instinct is that GPU dominates, and for *analysis* it does not. It is why the planner runs once per unique reference and never per job — and why replacing it with a learned blueprint model ([docs/07 §9](07-model-recommendations.md#9-editing-recommendation--agentic-planning)) is the single biggest analysis-cost lever available.

### 2.2 Reference analysis (cache HIT)

| Item | Cost |
|---|---:|
| Fingerprint (1.4 CPU-s) | $0.0001 |
| Postgres lookup + blueprint fetch | $0.00002 |
| **Total** | **$0.00012** |

**A cache hit costs 0.4% of a miss.** This ratio is the entire cost model.

### 2.3 Clip indexing

| Stage | GPU-s/clip | Cost/clip |
|---|---:|---:|
| Probe + quality + usable ranges | 0.4 | $0.00005 |
| Sub-shot segmentation | 0.8 | $0.00010 |
| Semantics (VLM + SAM 3) | 2.6 | $0.00034 |
| Motion (flow) | 1.4 | $0.00018 |
| Colour + composition | 0.5 | $0.00007 |
| ASR (if speech) | 0.9 | $0.00012 |
| Embeddings | 0.3 | $0.00004 |
| **Per clip** | **6.9** | **$0.00090** |
| **× 24 clips (typical project)** | 166 | **$0.0217** |

### 2.4 Matching

CPU only. 0.6s on a c7i.2xlarge = **$0.00006**. Negligible, and worth noting because matching is the most *valuable* component and the cheapest to run — the cost is in acquiring the training data, not in inference.

### 2.5 Rendering — where the money goes

**60s preview @ 540p:**

| Item | GPU-s | Cost |
|---|---:|---:|
| Decode (NVDEC) | 6 | $0.00079 |
| Composite (reframe, grade, effects, transitions) | 34 | $0.00445 |
| Captions | 5 | $0.00066 |
| Audio mix | 2 | $0.00026 |
| Encode (NVENC) | 4 | $0.00052 |
| **Total** | **51** | **$0.0067** |

**60s export @ 1080p:**

| Item | GPU-s | Cost |
|---|---:|---:|
| Decode | 14 | $0.00183 |
| Composite (full effects, film grain) | 62 | $0.00812 |
| Captions | 9 | $0.00118 |
| Audio | 3 | $0.00039 |
| Encode | 11 | $0.00144 |
| S3 write (180MB) | — | $0.0009 |
| **Total** | **99** | **$0.0139** |

**60s export @ 4K (A10G):** ~248 GPU-s = **$0.0432** plus $0.0031 storage.

### 2.6 Storage and egress per project

| Item | Size | Monthly |
|---|---:|---:|
| Originals (24 clips, ~1.2GB), 30d Standard → IA | 1.2 GB | $0.0193 |
| Proxies (7d) | 0.18 GB | $0.0010 |
| Stage artefacts (14d) | 0.09 GB | $0.0009 |
| Renders (2 previews + 1 export, 180d) | 0.31 GB | $0.0051 |
| **Storage** | | **$0.0263** |
| CDN egress (~3 views of preview + export) | 0.62 GB | **$0.0527** |

**Egress is twice storage**, and grows with engagement rather than with usage. A user who previews eight times costs more in bandwidth than in GPU — which is a genuinely counterintuitive result and drives the aggressive preview-resolution defaults in [docs/10 §9](10-rendering-engine.md#9-output--provenance).

---

## 3. Full job cost

**Typical job:** 1 reference, 24 clips, 2 preview renders, 1 export at 1080p, 60 seconds of output.

| Component | Cache MISS | Cache HIT |
|---|---:|---:|
| Reference analysis | $0.0296 | $0.0001 |
| Clip indexing (24) | $0.0217 | $0.0217 |
| Matching (×3 iterations) | $0.0002 | $0.0002 |
| Preview renders (×2) | $0.0134 | $0.0134 |
| Export 1080p | $0.0139 | $0.0139 |
| Storage (monthly) | $0.0263 | $0.0263 |
| CDN egress | $0.0527 | $0.0527 |
| Postgres / Redis / Qdrant share | $0.0080 | $0.0080 |
| Monitoring, CPU orchestration | $0.0042 | $0.0042 |
| **Total** | **$0.170** | **$0.141** |

**With overhead allocation** (idle GPU capacity, control plane, staging environments) at a 1.55× multiplier reflecting realistic 68% fleet utilisation:

| | Cache MISS | Cache HIT |
|---|---:|---:|
| **Fully loaded cost per job** | **$0.264** | **$0.219** |

At a 62% cache hit rate: **blended $0.236 per job.**

---

## 4. Margin by tier

| Tier | Price | Quota | Median use | COGS at median | COGS at cap | Margin (median) |
|---|---:|---:|---:|---:|---:|---:|
| Free | $0 | 3 | 1.4 | $0.33 | $0.71 | −$0.33 |
| Creator | $29 | 40 | 17 | $4.01 | $9.44 | **86%** |
| Pro | $79 | 150 | 61 | $16.42 | $40.4 | **79%** |
| Team | $249 | 600 | 210 | $56.6 | $161.6 | **77%** |
| Enterprise | $2,000+ | — | — | negotiated | — | 70–80% |

**Median use is ~42% of quota.** This is the standard pattern in usage-tiered SaaS and it is the difference between a viable and non-viable price point — margin at the cap (Pro: 49%) would be uncomfortable, and margin at median (79%) is healthy.

**Worst case:** every user maxes their quota. Creator margin falls to 67%, Pro to 49%, Team to 35%. Survivable, and the hard GPU-second budget in [`config.py`](../services/api/app/config.py) exists precisely so a pathological account cannot exceed that ceiling.

**Free tier costs $0.33/user/month.** At a 9% free→paid conversion, each converting user must cover roughly 11 non-converting ones — $3.63 against $29 revenue. Comfortable, and the 720p watermarked cap keeps the downside bounded.

---

## 5. Cost at each scale

| | 100 users | 10k users | 1M users |
|---|---:|---:|---:|
| Renders/mo | 1,800 | 210,000 | 21,000,000 |
| Cache hit rate | 15% | 62% | 85% |
| Cost/render | $0.24 | $0.17 | $0.087 |
| Infra cost/mo | $440 | $35,800 | $1,825,000 |
| MRR | $1,200 | $210,000 | $14,500,000 |
| **Gross margin** | **−167%** | **83%** | **87%** |

Negative margin at 100 users is correct and expected — fixed costs (RDS, Temporal, ALB) dominate at that volume, and optimising them would be optimising the wrong thing.

---

## 6. The five levers, ranked by impact

**1. Cache hit rate — 15% → 85% saves 41% of analysis cost.**
Highest-leverage single number in the company. Driven by: perceptual (not byte) fingerprinting, near-duplicate matching via pHash LSH, and — critically — a *shared global cache*, so a reference analysed for one user is free for the next. The cost model in this document assumes cross-tenant blueprint sharing, which is only legally clean because the blueprint contains no reference media.

**2. Model distillation — up to 50% of render+index GPU.**
A distilled 2B shot-semantics model at ~4× throughput, INT8 quantisation across the vision stack, and replacing the LLM planner with a learned blueprint model. Only worth doing past ~5,000 GPU-hours/day; before that, engineering time is more valuable elsewhere.

**3. Reserved capacity and spot — 45% of GPU cost.**
Requires predictable load, which requires scale. Blended $0.000131/GPU-s versus $0.000224 on-demand is a 42% reduction, and it is available as soon as the baseline is stable.

**4. Egress optimisation — 30% of CDN cost.**
Lower default preview bitrate, better CDN cache headers on repeated previews, regional serving. Underrated: egress is the second-largest line item and grows with engagement rather than usage.

**5. Segment-level render caching — 60% of iteration cost.**
A clip swap re-renders 3 slots instead of 60 seconds ([docs/10 §7](10-rendering-engine.md#7-partial-re-render)). This changes user behaviour more than it changes cost — cheap iteration produces more swaps, and swaps are the matcher's training data.

---

## 7. Sensitivity

What breaks the model, and by how much:

| Scenario | Effect on gross margin (10k users) |
|---|---|
| Cache hit rate 62% → 30% | 83% → 78% |
| GPU prices rise 40% | 83% → 74% |
| Users render 2× more than assumed | 83% → 71% |
| Music licensing doubles | 83% → 81% |
| Frontier LLM cost 5× | 83% → 82% (cache absorbs it) |
| **All of the above simultaneously** | **83% → 49%** |

Even the compound worst case leaves a viable business. The model is not fragile — the margin has room, and the levers in §6 are mostly untouched at that scale.

**The genuine risk is not cost, it is retention.** A user who renders once and churns has cost us $0.26 and returned $0. The unit economics are excellent *conditional on* the product being good enough to keep people, which is what [docs/00](00-executive-summary.md#what-has-to-be-true) tests at week 14 and what [docs/16](16-risks.md) treats as the primary risk.

---

## 8. Instrumentation

Every job writes a cost ledger ([docs/03 §9](03-system-architecture.md#9-observability)):

```json
{
  "gpu_seconds": {"analyze": 0.0, "index": 165.6, "match": 0.0, "render": 149.0},
  "external_tokens": {"planner_in": 0, "planner_out": 0},
  "s3_put_bytes": 194000000,
  "cdn_egress_bytes": 620000000,
  "cache_hits": {"blueprint": true, "clips": 4, "render": false},
  "estimated_cost_usd": 0.141
}
```

Rolled up hourly into ClickHouse. **Margin per job is a query, not a spreadsheet exercise** — and cost-per-render trending is alerted on at 20% week-over-week, because the way infrastructure costs run away is gradually and then suddenly.

---

Next: [15 — Engineering Roadmap](15-engineering-roadmap.md)
