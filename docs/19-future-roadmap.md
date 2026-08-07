# 19 — Future Roadmap

Ambitious features, each with an honest assessment of feasibility and what it depends on. Ordered by when the prerequisites exist rather than by how exciting they sound.

---

## Near term (months 12–18)

### One-click viral remake

Paste a URL that is currently trending; we analyse it, match against footage the user has already indexed, and produce a first cut with no further input.

**Feasible now** — it is the existing pipeline with the interaction removed. The work is in the trend-detection layer (what is trending, per platform, per vertical) rather than in the editing engine.

**The real design question is restraint.** A tool that makes chasing trends frictionless is a tool that pushes creators toward homogeneity, which is bad for them and eventually bad for us. Framing it as "this format, your subject" rather than "copy this" is the difference between a useful accelerant and a slop machine.

### Automatic highlight detection

For users with long footage: find the moments worth cutting to. Motion peaks, subject prominence, expression changes, audio events, quality windows.

**Feasible** — most of the signal already exists in [`usable_ranges`](../services/indexer/reelsedits_indexer/pipeline.py). The addition is *interestingness*, which is genuinely harder than *usability* and where the swap logs help: users implicitly rank interestingness every time they choose one segment over another.

### Social media optimisation

Platform-specific presets: safe areas, duration targets, hook timing, caption placement, loudness. Partly shipped in `Canvas.safe_area_inset_pct`.

**Feasible and unglamorous.** Mostly a data-maintenance problem — platform specs change and someone has to keep up.

### Long-form → short-form

Podcast, vlog or webinar in; clips out, styled by a blueprint.

**Feasible**, and it is where the ASR cost calculus changes: Parakeet-TDT at RTFx >2000 makes a 45-minute input cheap, where Whisper large-v3 would not be ([docs/07 §6.4](07-model-recommendations.md#64-asr--whisperx-alignment-over-faster-whisper-inference-parakeet-tdt-for-the-volume-tier)).

**Note this is OpusClip's core product.** We would enter it with a differentiator — style transfer applied to the excerpt — but should be clear-eyed that it is their home ground.

---

## Medium term (months 18–30)

### Prompt-based editing

"Make it faster." "More cinematic, fewer cuts." "Punch up the drop."

**Feasible, and more tractable than it sounds**, because the blueprint gives the LLM a structured object to *transform* rather than a video to *generate*. "Make it faster" becomes: raise `target_cut_density` per section, tighten `min_shot_ms`, shift the transition mix toward hard cuts, steepen `acceleration`. Every transform is validated by the schema and the invariant checks before it renders.

**This is the payoff for the blueprint being a first-class, editable object.** A system that edits video directly would have to regenerate; we mutate a 40KB JSON document and re-render three slots.

### AI director mode

Describe the video you want; the system generates a blueprint from scratch rather than transferring one.

**Requires the learned blueprint model** — roughly 250k corpus entries ([docs/07 §9](07-model-recommendations.md#9-editing-recommendation--agentic-planning)). This is the flywheel's first real payoff and the point at which we stop needing a reference at all.

### Personalised editing styles

Learn an individual creator's style from their own back catalogue, so "edit like me" works without a reference.

**Feasible, and strategically strong** — it turns the tool from an imitation aid into an identity aid, which addresses [docs/16 §R2](16-risks.md) (creators may not want to imitate) head-on. A creator's own style, applied consistently and quickly, is a different and less fraught value proposition.

### Multi-camera editing

Synchronised multi-angle footage; automatic angle selection driven by speech, motion and subject prominence.

**Feasible.** Sync via audio cross-correlation is well-understood; angle selection is the matcher with a different constraint set (same moment, choose the angle, respect the 30-degree rule). A natural Team/Enterprise feature.

### Collaborative editing

Multiple editors on one project, with comments, versions and approval.

**Only tractable because renders are deterministic and blueprints are immutably versioned.** Two people opening the same project see the same frames; a review comment anchors to a blueprint version and a slot index. Retrofitting this onto a non-deterministic renderer would be very hard, which is a good illustration of why [docs/10 §1](10-rendering-engine.md#1-the-determinism-requirement) mattered.

---

## Long term (months 30+)

### AI-generated B-roll

Generate footage to fill coverage gaps: a sunset, an establishing wide, an abstract texture.

**Technically feasible; deliberately constrained.**

- Generates **backgrounds, textures, environments and abstract elements — never people**
- Always C2PA-labelled and disclosed in the UI
- Offered as a *gap filler* with the shoot-list alternative presented alongside, never as the default

The constraint is not squeamishness. A tool that silently generates footage stops being a tool for creators and becomes a content mill, and the creators who are our customers will notice that before we do.

### AI-generated transitions and motion graphics

Novel transitions synthesised to match a reference's aesthetic; lower-thirds and title cards generated in a brand's visual language.

**Feasible.** Transitions are a constrained generation problem — two known endpoint frames, a fixed duration, a target aesthetic. Determinism is the engineering challenge: a generated transition must render identically every time, which means the generation happens once and is cached as an asset, not re-run per render.

### AI-generated sound effects

Whooshes, impacts and risers synthesised to match a specific transition's motion profile rather than pulled from a library.

**Feasible and genuinely useful.** A whoosh whose duration and spectral sweep match the actual whip-pan velocity sounds markedly better than a stock sample stretched to fit. Small feature, disproportionate quality effect.

### Voice-controlled editing

"Cut there." "Slower." "Use the sunset shot instead."

**Feasible; uncertain demand.** Editing is a visual, spatial task and voice is a poor interface for precision. Plausibly valuable for accessibility and for mobile, where precise touch interaction is hard. Worth prototyping before committing.

### Real-time collaborative preview

Multiple users scrubbing a shared timeline with live updates.

**Hard.** Requires streaming render infrastructure rather than file-based render, which is a different architecture. Justified only by strong Enterprise demand.

### The Editing Blueprint as an open standard

Publish EBP as an open specification; build importers and exporters for Premiere, Resolve, Final Cut and CapCut.

**The most strategically interesting item on this list, and the least technical.**

If EBP becomes the format other tools import and export, we own the interchange layer for editing style. That is a much larger position than being one video editor among many — and it is achievable precisely because nobody else has bothered to standardise a renderer-agnostic representation of an edit.

The path: stabilise the format (done — `ebp_version` is a hard const), publish the schema (done), build the marketplace so third parties want to consume it, then publish reference importers. The format's value to others grows with the corpus, which grows with our usage.

---

## What we will not build

Restated from [docs/01 §6](01-product-vision.md#6-what-we-refuse-to-build), because roadmap pressure is exactly when these get quietly reconsidered:

**Face swapping, face reenactment, voice cloning.** Architecturally absent, not policy-restricted.

**Frame-level reproduction of a reference.** Upscaled, restyled, interpolated — no version of this is worth the company.

**Reference audio passthrough.** Not as a premium feature, not for Enterprise, not where a platform API makes it technically available.

**Engagement-optimising suggestions.** "Add a hook here to increase watch time" is a different product with different externalities. We optimise craft.

**Unlabelled generative content.** Everything generated is disclosed, in the UI and in the file.

---

## Prioritisation

| Feature | Value | Effort | Depends on | Phase |
|---|---|---|---|---|
| One-click viral remake | High | Low | Trend detection | 12–15 |
| Highlight detection | High | Medium | — | 12–18 |
| Social optimisation | Medium | Low | — | 12–15 |
| Long-form → short-form | High | Medium | Parakeet ASR path | 15–18 |
| Prompt-based editing | **Very high** | Medium | Blueprint transforms | 18–24 |
| Personalised styles | **Very high** | High | Corpus + per-user training | 20–26 |
| Multi-camera | Medium | Medium | Audio sync | 20–26 |
| Collaboration | High | High | Determinism (done) | 18–24 |
| AI director mode | **Very high** | Very high | 250k corpus | 24–30 |
| Generated B-roll | Medium | High | Video gen + C2PA | 30+ |
| Generated transitions | Medium | High | Deterministic caching | 30+ |
| Generated SFX | Medium | Medium | Audio gen | 30+ |
| Voice control | Low | Medium | — | 30+ |
| EBP open standard | **Very high** | Low | Marketplace traction | 24+ |

**The two highest-leverage items are prompt-based editing and the open standard**, and both are cheap relative to their impact — because both are consequences of decisions already made in [docs/06](06-blueprint-spec.md). Designing the blueprint as a stable, inspectable, renderer-agnostic object was the enabling choice for most of this roadmap.

---

Next: [20 — Implementation Plan](20-implementation-plan.md)
