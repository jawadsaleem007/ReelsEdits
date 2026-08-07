# 00 — Executive Summary

## The thesis

Short-form video is now the dominant medium of the internet, and the bottleneck is no longer cameras, distribution, or even ideas. It is **editing craft**. A creator can shoot excellent footage on a phone and still produce something that feels amateur, because the difference between a video that gets 2,000 views and one that gets 2,000,000 lives almost entirely in decisions that take an experienced editor four hours and a novice editor never: where exactly the cut lands relative to the kick drum, how long the whip-pan blur lasts, whether the speed ramp eases in over 6 frames or 14, how the shadows are lifted and toward which hue.

These decisions are learnable. They are also, critically, **not copyrightable in the form we extract them**. A cut rhythm is not a work of authorship. A tempo map is not a master recording. The specific arrangement of a specific video is protected; the *editing grammar* it demonstrates is closer to a chord progression — the thing every practitioner already imitates by watching and rewinding, thousands of times, badly.

ReelsEdits automates the rewinding.

## What the product does

1. The user uploads a **reference video** — something whose editing they want to emulate.
2. The user uploads their own **raw clips** — footage of an entirely different subject.
3. The system analyses the reference into an **Editing Blueprint**: a timecoded JSON description of every editorial decision, containing no frames and no audio from the reference.
4. The system indexes the user's clips into the same feature space — shot scale, camera motion, subject, motion energy, quality, emotion.
5. A matcher assigns clips to blueprint slots by *semantic and kinetic equivalence*, not by upload order. A reference car-wheel close-up maps to the user's exhaust close-up because both are "detail shot, low camera height, high-frequency motion, mechanical subject" — not because both are the fourth file.
6. A deterministic render engine executes the blueprint: trims, beat-aligned cuts, transitions, speed ramps, zooms, grade, captions, and a **licensed** music bed whose tempo and structure match the reference's rhythmic skeleton.
7. The user previews on a scrubbable timeline, adjusts anything, and exports.

The output is *their* footage, *their* music licence, and *the reference's craft*.

## Why now

Four things became true within roughly the last eighteen months, and all four are required:

**Video-language models can now watch an entire clip, not sample it.** Frontier models — Gemini 2.5/3-class, and open-weight Qwen2.5-VL, InternVL3.5, VideoLLaMA 3 — ingest minutes-to-hours of video at long context and answer grounded temporal questions. Two years ago, "what is happening in shot 7 and why did the editor cut there" was not a question you could ask a model. It is now. ([survey](https://arxiv.org/pdf/2409.18938), [SlowFast-LLaVA-1.5](https://arxiv.org/pdf/2503.18943))

**Concept-level segmentation went zero-shot.** SAM 3 (November 2025, 848M params) takes a noun phrase — "yellow school bus", "person's hand" — and returns masks and stable IDs for every instance across a video, where SAM 1 and 2 returned one object per geometric prompt. Subject tracking for reframing and masking stopped being a per-vertical engineering project. ([SAM 3](https://ai.meta.com/research/publications/sam-3-segment-anything-with-concepts/), [SAM 3.1](https://ai.meta.com/blog/segment-anything-model-3/))

**Beat and structure analysis got good enough to trust blind.** Transformer beat trackers (Beat This!, Beat Transformer) hold up across genres without the DBN post-processing that used to break on tempo changes — which matters enormously, because tempo changes are exactly where edits get interesting. ([Beat This!](https://arxiv.org/pdf/2510.14391) context, [madmom](https://madmom.readthedocs.io/en/v0.16/modules/features/downbeats.html))

**The market proved it wants this, but only got half of it.** OpusClip crossed four million registered users by early 2026 on a much narrower promise — find the good 45 seconds of a long video. Submagic sells captions and B-roll insertion. Nobody sells *editing style itself*. ([market overview](https://www.forasoft.com/learn/ai-for-video-engineering/articles-ai/opus-clip-descript-submagic-captions-ai-video-editor-tools-2026))

## Why this is defensible

Most AI video tools are a thin wrapper over a foundation model and are therefore worth exactly as much as the wrapper. ReelsEdits accrues three assets that do not commoditise:

**The blueprint corpus.** Every reference video analysed produces a structured, labelled record of professional editing decisions. After 100,000 references we hold the largest structured dataset of editing grammar in existence — which lets us train a native blueprint-generation model that no competitor can replicate without redoing the analysis at the same scale. This is the flywheel.

**The matching model.** Clip-to-slot assignment is the component with the least prior art and the most user-visible impact. It is also the one that improves directly from user behaviour: every time a user swaps a chosen clip for a different one, we get a labelled preference pair. This is a ranking problem with a free, high-volume training signal.

**The render determinism.** A blueprint that renders identically on every machine, every time, is unglamorous infrastructure that takes eighteen months to get right and immediately becomes the substrate for templates, marketplaces, collaboration, and an API. Competitors who render through an LLM-in-the-loop cannot offer reproducibility, and reproducibility is what turns a toy into a tool.

## The wedge

We do not launch as "an AI video editor." That market is crowded, undifferentiated, and priced at $15/month.

We launch as **"make my footage look like *that*"** for a single vertical where reference-style imitation is already the dominant behaviour: **automotive and motorsport short-form**. Car and moto creators explicitly study each other's edits, the visual grammar is tight and repeatable (rolling shots, wheel details, exhaust close-ups, drift sequences, sunset b-roll), and the community is loud, concentrated, and willing to pay. Nail one vertical to the point where output is indistinguishable from a $400 freelance edit, then generalise: fitness, travel, food, real estate, product.

## Business model in one table

| Tier | Price | Who | Limits |
|---|---|---|---|
| Free | $0 | Trial | 3 renders/mo, 720p, watermark, catalogue music only |
| Creator | $29/mo | Solo creators | 40 renders/mo, 1080p, no watermark, style library |
| Pro | $79/mo | Full-time creators, small agencies | 150 renders/mo, 4K, custom LUTs, blueprint editing, priority GPU |
| Team | $249/mo (5 seats) | Agencies | Shared style libraries, brand kits, review workflow, SSO |
| Enterprise | from $2k/mo | Brands, media cos | Private deployment options, DPA, custom licensing, SLA |
| API | usage-based | Platforms, tools | $0.60–$1.40 per render-minute |
| Marketplace | 70/30 split | Creators selling styles | Blueprint templates as products |

Detailed unit economics in [docs/14](14-cost-model.md); full model in [docs/17](17-business-model.md).

## Unit economics headline

A 45-second render at 1080p costs roughly **$0.34–$0.52** in GPU, storage, egress and third-party inference at moderate scale, against $29/month for 40 renders — a blended COGS of about **$16/mo against $29 revenue at full utilisation**, and far better in practice because median utilisation runs near 40% of quota. Gross margin lands at **68–78%** once the analysis cache warms (references are heavily repeated — the top 500 reference videos will account for a large share of all jobs, and each is analysed once, ever). See [docs/14](14-cost-model.md).

## What has to be true

This plan is honest about its load-bearing assumptions. Each is stated with how we test it cheaply and early:

| Assumption | Test | When | Kill criterion |
|---|---|---|---|
| Style transfer is perceptually convincing | Blind A/B: our output vs. human editor, 200 raters | Week 14 | <35% preference for ours |
| Clip matching beats naive ordering | Same raters, matched vs. sequential | Week 14 | <60% preference for matched |
| Users accept substituted music | In-product swap rate + churn survey | Week 20 | >40% cite music as reason to churn |
| Reference re-use is high (cache economics) | Distribution of reference hashes | Week 18 | <30% of jobs hit a warm reference |
| Vertical wedge converts | Automotive beta → paid conversion | Week 24 | <8% free→paid |

If the first two fail, the product does not exist and we should know by week 14 for well under $300k.

## The ask

**$4.5M seed** for 24 months of runway.

| Allocation | Amount | Detail |
|---|---|---|
| Engineering (7 FTE) | $2.5M | 2 ML, 2 backend/infra, 1 graphics/render, 1 frontend, 1 founding designer |
| Compute (train + inference) | $900k | Analysis corpus build, matcher training, production GPU |
| Music licensing | $350k | Catalogue partnership minimums + legal |
| GTM | $450k | Creator partnerships, vertical seeding, content |
| Legal, ops, G&A | $300k | Entity, IP, DPA/compliance, accounting |

**Milestones to Series A:** 40k MAU, $180k MRR, blueprint corpus of 250k analysed references, matcher outperforming sequential baseline at >75% preference, and a shipped API with three integration partners.

## Reading order for the sceptical

If you have limited time and want to find the weakest point in this plan, read in this order:

1. [docs/09 — Clip Matching](09-clip-matching.md) — the hardest technical claim
2. [docs/08 §5 — Colour Grade Inversion](08-algorithms.md#5-colour-grade-inversion) — the most over-claimed capability in this category
3. [docs/18 — Legal & Ethics](18-legal-ethics.md) — the thing that kills companies in this space
4. [docs/14 — Cost Model](14-cost-model.md) — whether the margins survive contact with GPU pricing

---

**Sources:** [Long video understanding survey](https://arxiv.org/pdf/2409.18938) · [SlowFast-LLaVA-1.5](https://arxiv.org/pdf/2503.18943) · [SAM 3](https://ai.meta.com/research/publications/sam-3-segment-anything-with-concepts/) · [SAM 3.1](https://ai.meta.com/blog/segment-anything-model-3/) · [Beat tracking as object detection](https://arxiv.org/pdf/2510.14391) · [madmom downbeats](https://madmom.readthedocs.io/en/v0.16/modules/features/downbeats.html) · [AI editing tool landscape 2026](https://www.forasoft.com/learn/ai-for-video-engineering/articles-ai/opus-clip-descript-submagic-captions-ai-video-editor-tools-2026)
