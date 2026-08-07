# 07 — Model Recommendations

Every model choice below is stated with **what it does, why it beats the alternatives for our specific case, what it costs, and what replaces it when it is superseded.** The last column matters most: this list has a shelf life of roughly nine months, and the architecture is built so that swapping any single model is a config change plus a golden-set regression run, not a refactor.

**The meta-point:** model access is not a moat. Everyone can call the same models. Our defensibility is in the blueprint corpus, the matcher trained on user swaps, and render determinism ([docs/02 §4](02-competitive-analysis.md#4-where-the-moat-actually-is)). This document is an engineering decision record, not a differentiator.

---

## 1. Selection principles

**Open weights by default; frontier APIs only where quality difference is decisive.** Our margins depend on GPU cost. A model we run costs ~$0.0002 per call at our utilisation; a frontier API call costs 100–1000× that. We pay the premium in exactly one place — the planner — and only because it runs once per *unique reference* and is therefore amortised by the cache.

**Structured output, never free text.** Every model producing a categorical value does so under constrained decoding against the enums in [`services/common/reelsedits_common/enums.py`](../services/common/reelsedits_common/enums.py). Free text produces a long tail of near-synonyms that silently destroys matching.

**Measure what can be measured.** Where a geometric computation exists, use it instead of asking a model. Shot scale is `subject_mask_area / frame_area`, not a VLM's opinion. Models are for perception, not arithmetic.

**Ensembles where the failure modes are uncorrelated.** Two shot-boundary detectors that fail differently give us a confidence signal for free. Two models that fail the same way give us nothing but cost.

**Every model version is recorded in the artefact it produced.** Non-negotiable — it is what makes "which model version produced the outputs users rejected" a query rather than an investigation.

---

## 2. Video understanding & vision-language

### 2.1 Shot-level semantic labelling — **Qwen2.5-VL / InternVL3.5 class (open weights)**

**Task:** For each shot, produce the structured record in [docs/04 stage 4](04-ai-pipeline.md#stage-4--semantic-analysis) — subject, action, scene category, camera height, time of day, emotional tone, narrative role.

**Why open-weight here:** This runs on *every shot of every reference and every user clip* — the highest-volume VLM call in the system by two orders of magnitude. At 25 shots per reference and 24 clips per project, a frontier API would add dollars per job to a product priced at $29/month. Open-weight models on our own vLLM deployment are effectively free at the margin, and the task — categorical labelling from a fixed vocabulary with 4–8 sampled frames — is well within their capability. The open-source field (Qwen2.5-VL, InternVL3/3.5, VideoLLaMA 3, LLaVA-Video, NVILA, Apollo, Tarsier2) has closed most of the gap to frontier models on exactly this kind of grounded, short-horizon task. ([survey](https://arxiv.org/pdf/2409.18938))

**Why not a classifier:** We would need a labelled dataset per attribute, per vertical, and it would not transfer when we expand from automotive to fitness. A VLM with constrained decoding generalises to new verticals for free, which is the whole expansion strategy.

**Cost:** ~0.4 GPU-seconds per shot on an L4 at batch 8.

**Superseded by:** Any open-weight VLM that improves structured-output adherence at equal or lower cost. The interface is a JSON schema, so the swap is a model name and a golden-set run.

### 2.2 Holistic edit reasoning (the planner) — **Gemini 2.5/3-class frontier model**

**Task:** [docs/04 stage 8](04-ai-pipeline.md#stage-8--planner-llm) — take the full fused analysis and produce an *adaptable specification*: explain editorial intent, generalise reference shots into slot requirements, mark essential vs. incidental decisions, flag low confidence.

**Why frontier, and why here specifically:** This is the one task where reasoning quality visibly changes output quality. "The cut density accelerates through the build and resolves on the downbeat at the drop, so under a shorter track compress the build rather than the drop" is a judgement, not a measurement. Open-weight models produce structurally valid but editorially naive plans.

**Why the cost is acceptable:** It runs **once per unique reference, ever** — not per job. With a 55–75% blueprint cache hit rate ([docs/03 §7](03-system-architecture.md#7-caching-strategy)), the amortised cost per job is a fraction of a cent. Frontier models now natively ingest long video at million-token context, so we can additionally hand the model a sampled frame sequence rather than only the serialised analysis, which measurably improves the intent explanations.

**Determinism:** temperature 0.2, fixed seed, model version recorded in `provenance.planner_model`. The planner is the only non-deterministic component and it sits *upstream* of the blueprint; everything downstream is a pure function.

**Fallback:** open-weight VLM planner, blueprint stamped `planner_tier: "fallback"`. Degraded but functional — the system does not go down when an API does.

### 2.3 Long-video understanding for long-form input (future)

For the long-form→short-form feature in [docs/19](19-future-roadmap.md), the relevant capability is hour-scale video QA with clue grounding, benchmarked by Video-MME, MLVU, LongVideoBench, CG-Bench and TemporalBench. Token-efficient architectures (SlowFast-LLaVA-1.5 class) matter more than raw capability here, because cost scales with input duration. Not needed for the MVP. ([SlowFast-LLaVA-1.5](https://arxiv.org/pdf/2503.18943))

---

## 3. Shot boundary detection

### **TransNetV2 + AutoShot ensemble**

**Task:** Every cut position and every gradual-transition *interval* in the reference; sub-shot segmentation in user clips.

**Why both:**

AutoShot outperforms TransNetV2 by **4.2% on the SHOT dataset** and 1.1–1.2% on ClipShots, BBC and RAI. The SHOT margin is the one that matters — SHOT is 853 complete *short videos* with 11,606 shot annotations, which is precisely our domain. AutoShot found its architecture by neural architecture search across 3D ConvNets and transformers. ([AutoShot](https://arxiv.org/abs/2304.06116))

TransNetV2 remains fast, extremely well-tested, and has a mature open implementation using kernel factorisation, batch norm and skip connections. ([TransNetV2](https://github.com/soCzech/TransNetV2))

Running both costs ~6 GPU-seconds for a 60-second reference and buys three things:

1. **Agreement as a confidence signal.** Both fire → high confidence. One fires → medium, resolved by a frame-difference and histogram-distance heuristic at that locus. This confidence propagates into `provenance.confidence.structure`.
2. **Better gradual-transition intervals.** The two models bracket the transition range differently; the union is a better duration estimate than either alone. This matters because a 14-frame cross-dissolve's *duration* is a blueprint field, and collapsing it to a point loses it.
3. **Uncorrelated failure modes.** TransNetV2 over-fires on fast camera motion; AutoShot handles that better but is weaker on very subtle dissolves. Neither failure is silent when you have both.

**Cost:** ~6 GPU-seconds per 60s reference. Cheap and load-bearing — every downstream stage operates per-shot, so a missed boundary corrupts everything after it.

**Superseded by:** Real-time SBD approaches are an active area, and we will re-benchmark quarterly against a held-out set of 500 short-form videos. Any replacement must beat the *ensemble*, not either member. ([faster-than-real-time SBD](https://arxiv.org/pdf/2502.09202))

---

## 4. Segmentation & tracking

### **SAM 3 / SAM 3.1**

**Task:** Concept-prompted segmentation and tracking. Subject masks for reframing, subject-area ratio for shot scale, subject trajectories for tracking-shot detection, occlusion-aware IDs for continuity.

**Why this is a step change and not an increment:** SAM 3 introduced **Promptable Concept Segmentation** — a short noun phrase ("the motorcycle", "the rider's hands") or an image exemplar returns masks and stable IDs for *every* matching instance at once. SAM 1 and 2 predicted one object per geometric prompt. 848M parameters, a DETR-based detector and a tracker sharing a single vision encoder, released November 2025 under the SAM licence with the SA-Co benchmark. SAM 3.1 added multiplexing and global reasoning for faster real-time tracking. ([SAM 3](https://ai.meta.com/research/publications/sam-3-segment-anything-with-concepts/), [SAM 3.1](https://ai.meta.com/blog/segment-anything-model-3/), [overview](https://blog.roboflow.com/what-is-sam3/))

For us this collapses what would otherwise be a per-vertical engineering programme. Automotive needs cars, wheels, exhausts; fitness needs people, weights, equipment; food needs plates and hands. With SAM 3, expanding to a new vertical means **writing new noun phrases**, not training new detectors. That is the difference between a vertical taking a quarter and taking an afternoon.

**Why we still need `smoothing` in the reframe track:** SAM 3 masks are excellent per-frame and still noisy frame-to-frame at the pixel level. Driving a virtual camera directly from raw mask centroids produces visible jitter — the single commonest failure in auto-reframing products. `ReframeTrack.smoothing` (default 0.7) is a temporal low-pass on the camera path, not on the mask. See [docs/10 §4](10-rendering-engine.md).

**Cost:** ~1.2 GPU-seconds per shot at 2fps sampling on an L4.

**Alternative kept warm:** SAM 2 for the fast tier — real-time inference at ~44fps, 6× faster than SAM 1 on images, with strong zero-shot generalisation. Used for preview-quality reframing where mask precision matters less. ([SAM 2](https://ai.meta.com/sam2/), [Ultralytics](https://docs.ultralytics.com/models/sam-2))

---

## 5. Motion estimation

### **SEA-RAFT (accurate tier) / RAFT-small (fast tier)**

**Task:** Dense optical flow, from which we derive global camera motion (via RANSAC-fitted homography), motion magnitude curves, shake severity, subject-vs-camera motion separation, and speed anomaly detection.

**Why SEA-RAFT:** RAFT established the iterative-refinement-over-a-4D-cost-volume approach that dominates supervised optical flow. SEA-RAFT ("Simple, Efficient, Accurate RAFT", ECCV 2024) is state-of-the-art for supervised flow with notably strong cross-domain generalisation — which is exactly our situation, since we run on arbitrary user phone footage rather than a benchmark distribution. It is also meaningfully cheaper than iterative RAFT at equal accuracy, and at 25 shots per reference that difference is real money. ([SEA-RAFT](https://dl.acm.org/doi/abs/10.1007/978-3-031-72667-5_3), [PTLFlow model list](https://ptlflow.readthedocs.io/en/latest/models/models_list.html))

**Why flow at all, rather than a learned camera-motion classifier:** because we need the *magnitude curve*, not just the class. The magnitude curve drives cut micro-placement (finding the motion peak to hide a cut in), speed-ramp detection, transition-direction estimation, and the `motion_energy` requirement that the matcher uses. A classifier gives us one label; flow gives us the label plus four other things we need.

**Cost:** ~12 GPU-seconds per 60s reference on the motion proxy (256px). Running flow at full resolution would roughly 8× this for no gain — motion structure is preserved at low resolution.

**Watch list:** Multi-frame methods exploiting temporal cues (VideoFlow class) and memory-efficient high-resolution training (MEMFOF class) are the likely successors. Hybrid 2D camera-motion bases (CamFlow+ class) are directly relevant to our camera-motion decomposition. ([MEMFOF](https://arxiv.org/pdf/2506.23151), [CamFlow+](https://arxiv.org/pdf/2606.05915))

---

## 6. Audio

### 6.1 Source separation — **Demucs v4 (HTDemucs)**

**Task:** Split into drums / bass / vocals / other before any other audio analysis.

**Why first, before everything:** Beat detection on a full mix with loud vocals and heavy sidechain compression is materially worse than on an isolated drum stem. Structure boundaries are cleaner on separated stems. SFX detection is only possible on the `other` stem, because in a full mix a whoosh is indistinguishable from a synth swell. Demucs costs ~4 GPU-seconds for 60s of audio and improves beat F-measure, boundary precision, and SFX recall simultaneously. Highest-leverage 4 seconds in the pipeline.

### 6.2 Beat & downbeat tracking — **Transformer tracker (Beat This! class) primary, madmom DBN secondary**

**Task:** The beat grid, downbeats, time signature, and tempo curve. This is the backbone of the blueprint.

**Why the transformer is primary:** madmom's DBN post-processor is the long-standing reference and is excellent on steady-tempo material, but it assumes roughly constant tempo and degrades on time-signature and tempo changes. Beat This! (2024) is a transformer beat tracker that achieves high accuracy across diverse styles **without** DBN post-processing, which makes it suitable for pieces with time-signature changes or high tempo variation. ([Beat This! context](https://arxiv.org/pdf/2510.14391), [Carnatic meter tracking](https://arxiv.org/pdf/2509.11241), [madmom](https://madmom.readthedocs.io/en/v0.16/modules/features/downbeats.html))

Tempo changes are exactly where edits get interesting — the half-time breakdown, the build that accelerates. A tracker that breaks there breaks on the most editorially significant moments in the reference.

**Why we still run madmom:** agreement between the two is a free confidence signal, written to `provenance.confidence.beat_grid`. Where they disagree, confidence drops and the planner is instructed to prefer content-driven (`free`) cuts over grid-driven cuts in that region — which is the correct behaviour, because if we cannot find the grid reliably, neither can the viewer.

**Also relevant:** Beat Transformer's time-wise and instrument-wise attention over demixed spectrograms is architecturally aligned with our stem-separation-first approach, and beat-tracking-as-object-detection reframings are producing strong results. ([beat tracking as object detection](https://arxiv.org/pdf/2510.14391))

**Cost:** ~2 GPU-seconds for 60s.

### 6.3 Music structure — **Self-similarity + learned boundary detection**

**Task:** Section segmentation (intro / verse / build / drop / chorus / outro), energy envelope, drop detection.

**Approach:** CQT-based self-similarity matrix, novelty curve, learned boundary classifier, then section-type classification from energy, spectral and repetition features. Boundaries are snapped to downbeats — musical sections change on downbeats, and snapping removes a class of off-by-a-beat errors.

**Why not an off-the-shelf structure model:** available models are trained largely on full-length Western pop and underperform on the 15–90 second, heavily edited, often mashed-up audio in short-form video. The self-similarity approach plus downbeat snapping is more robust on our actual distribution, and — critically — it is the same computation we run over the **licensed music catalogue**, so reference structure and catalogue structure are directly comparable for [`MusicBinding`](../schemas/blueprint.schema.json) matching.

### 6.4 ASR — **WhisperX (alignment) over faster-whisper (inference); Parakeet-TDT for the volume tier**

**Task:** Word-level timestamps for caption generation and caption-style reproduction.

**The requirement is word-level timing, not transcript quality.** Caption style — `word_by_word`, `karaoke` — cannot be reproduced without per-word timestamps. Whisper's native timestamps are segment-level and drift.

- **WhisperX** wraps Whisper with wav2vec2 phoneme forced alignment for genuine per-word timestamps, plus pyannote-audio diarisation. This is the accuracy path.
- **faster-whisper** (CTranslate2 reimplementation) is the fastest path on NVIDIA hardware and is what we run underneath.
- **Whisper large-v3** (1.55B params, 99+ languages) remains the multilingual quality reference.
- **NVIDIA Parakeet-TDT** prioritises inference speed with RTFx >2000 at 1.1B params — dramatically faster than Whisper variants. This changes the cost calculus for the long-form→short-form feature where inputs are 45+ minutes.
- **NVIDIA Canary-Qwen 2.5B** currently leads English accuracy leaderboards.
- **whisper.cpp** for any future on-device path.

([Whisper alternatives 2026](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks), [Gladia comparison](https://www.gladia.io/blog/best-whisper-alternatives-2026), [awesome-whisper](https://github.com/sindresorhus/awesome-whisper))

**Decision:** WhisperX + faster-whisper for MVP (multilingual, alignment quality). Parakeet-TDT as an English-only fast lane once long-form input ships.

### 6.5 Audio embeddings — **CLAP-class**

**Task:** Mood and genre classification; music-to-music similarity for catalogue matching.

Contrastive audio-text pretraining gives zero-shot mood tagging against arbitrary text labels, so "driving", "euphoric", "nostalgic" work without a labelled mood dataset. The same embedding space serves catalogue retrieval, so `MusicBinding.match_score` combines structural similarity (BPM, section layout, energy curve shape) with embedding similarity.

---

## 7. Text, OCR & captions

### **PaddleOCR primary; VLM-based OCR for hard cases**

**Task:** Detect and recognise on-screen text, group into temporal text objects, extract style.

PaddleOCR is fast, handles rotated and curved text, and has strong multilingual coverage. Short-form video text is often stylised, animated, low-contrast, and partially occluded — cases where a VLM's contextual reasoning outperforms a dedicated OCR pipeline. We route by confidence: PaddleOCR first, VLM on regions below threshold.

**Font classification, not font identification.** We classify into nine families ([`FontFamily`](../services/common/reelsedits_common/enums.py)) and map to a licensed font. Exact typeface identification from compressed video is unreliable — the glyph detail needed has been destroyed by the encoder — and a confidently wrong font is worse than an honest family match. Stated in the schema so nobody later mistakes the field for something it is not.

---

## 8. Embeddings for matching

### **SigLIP / CLIP-class visual encoder + learned projection**

**Task:** Semantic vectors for slots and segments, feeding the ANN retrieval that generates matcher candidates.

**The critical design point:** raw CLIP similarity is the *wrong* objective for this product. CLIP would rank a car close-up as most similar to another car close-up — but our entire value proposition is mapping a **car** reference onto **motorcycle** footage. Raw semantic similarity actively fights the goal.

So the pipeline is: frozen CLIP-class backbone → **learned projection head** trained on user swap data ([docs/09 §6](09-clip-matching.md#6-learning-from-swaps)) to produce an *editorial equivalence* space, where a car wheel and a motorcycle exhaust land close together because they play the same editorial role, not because they look alike.

Day one we ship raw CLIP embeddings plus explicit structural constraints (scale, motion, subject class), which works acceptably. The projection head is trained once we have swap volume, and it is the component that compounds.

---

## 9. Editing recommendation & agentic planning

**Phase 1 (now):** rules + constrained optimisation ([docs/08](08-algorithms.md), [docs/09](09-clip-matching.md)) with an LLM planner for high-level intent. Deterministic, debuggable, no training data required.

**Phase 2 (~250k blueprints):** a learned blueprint model trained on the corpus. Input: audio structure + available footage features. Output: a blueprint. This is the flywheel payoff, and the reason blueprints are stored forever.

**Phase 3 (~1M blueprints):** generative style — novel coherent styles rather than transfers of existing ones, conditioned on a text prompt or a brand kit.

**On agentic loops:** the RL-for-video-LLM wave (Video-R1, VideoChat-R1, Video-Thinker, VideoP2R, following DeepSeek-R1) is directly relevant to the planner, where multi-step reasoning over analysis output could plausibly beat single-pass prompting. We are watching it and not betting on it, because an agentic loop in the planner multiplies API cost by the number of steps and the planner is already our only paid inference. ([survey](https://arxiv.org/pdf/2409.18938))

**Where agentic behaviour does belong:** the *repair* loop. When a render produces a blueprint violation (effect budget exceeded, coverage below floor), an agent that inspects the violation and proposes a targeted blueprint edit is a bounded, verifiable use of the pattern — the verification is the schema and the constraint checks, which already exist.

---

## 10. Summary table

| Task | Model | Type | Cost / 60s ref | Why |
|---|---|---|---|---|
| Shot semantics | Qwen2.5-VL / InternVL3.5 | Open | ~10 GPU-s | Highest volume; frontier unaffordable here |
| Edit reasoning | Gemini 2.5/3-class | API | ~$0.02, cached | Judgement, not measurement; amortised by cache |
| Shot boundaries | TransNetV2 + AutoShot | Open | ~6 GPU-s | AutoShot +4.2% on short-form; ensemble → confidence |
| Segmentation | SAM 3 / 3.1 | Open | ~1.2 GPU-s/shot | Concept prompts kill per-vertical detectors |
| Optical flow | SEA-RAFT | Open | ~12 GPU-s | SOTA supervised flow, strong cross-domain |
| Source separation | Demucs v4 | Open | ~4 GPU-s | Improves beat, structure and SFX simultaneously |
| Beat tracking | Beat This! + madmom | Open | ~2 GPU-s | Transformer survives tempo change; DBN = confidence |
| Structure | SSM + learned boundaries | Custom | ~2 GPU-s | Same computation runs on the licensed catalogue |
| ASR | WhisperX + faster-whisper | Open | ~5 GPU-s | Word-level timestamps are non-negotiable |
| ASR (volume tier) | Parakeet-TDT | Open | RTFx >2000 | For long-form input |
| Audio embedding | CLAP-class | Open | ~1 GPU-s | Zero-shot mood; catalogue retrieval |
| OCR | PaddleOCR + VLM fallback | Open | ~3 GPU-s | Speed + robustness on stylised text |
| Visual embedding | SigLIP + learned head | Open+ours | ~0.3 GPU-s/shot | Raw CLIP is the wrong objective; head fixes it |

---

**Sources:** [AutoShot](https://arxiv.org/abs/2304.06116) · [TransNetV2](https://github.com/soCzech/TransNetV2) · [Faster-than-real-time SBD](https://arxiv.org/pdf/2502.09202) · [SAM 3](https://ai.meta.com/research/publications/sam-3-segment-anything-with-concepts/) · [SAM 3.1](https://ai.meta.com/blog/segment-anything-model-3/) · [What is SAM 3](https://blog.roboflow.com/what-is-sam3/) · [SAM 2](https://ai.meta.com/sam2/) · [SEA-RAFT](https://dl.acm.org/doi/abs/10.1007/978-3-031-72667-5_3) · [PTLFlow models](https://ptlflow.readthedocs.io/en/latest/models/models_list.html) · [MEMFOF](https://arxiv.org/pdf/2506.23151) · [CamFlow+](https://arxiv.org/pdf/2606.05915) · [Beat tracking as object detection](https://arxiv.org/pdf/2510.14391) · [madmom](https://madmom.readthedocs.io/en/v0.16/modules/features/downbeats.html) · [Carnatic meter tracking](https://arxiv.org/pdf/2509.11241) · [Open-source STT 2026](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks) · [Whisper alternatives](https://www.gladia.io/blog/best-whisper-alternatives-2026) · [awesome-whisper](https://github.com/sindresorhus/awesome-whisper) · [Long video MLLM survey](https://arxiv.org/pdf/2409.18938) · [SlowFast-LLaVA-1.5](https://arxiv.org/pdf/2503.18943)

Next: [08 — Algorithms](08-algorithms.md)
