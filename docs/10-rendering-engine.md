# 10 — Rendering Engine

Blueprint + assets → MP4. The unglamorous component that everything else depends on.

---

## 1. The determinism requirement

```
render(blueprint, assets, renderer_version) → bit-identical output, every time
```

This is not a nice-to-have. It is the precondition for:

- **Render caching** — the highest-value cache in the system, worth an entire render
- **The style marketplace** — a blueprint someone buys must produce what they previewed
- **Collaboration** — two people opening the same project must see the same frames
- **The API** — customers building on us need reproducibility
- **Debugging** — "reproduce the bad render" must be possible, or every quality bug is a ghost hunt

**What this rules out:** any sampling model in the render path. No LLM deciding effect parameters at render time, no stochastic grain, no wall-clock-seeded randomness, no "creative variation."

Where variation is genuinely wanted — grain, particles, shake — it comes from a **seeded PRNG whose seed is a function of `(blueprint.id, slot.index, effect_index)`**. Deterministic, and still visually varied across the timeline.

### 1.1 Thread counts are part of the contract

Found the hard way, and worth stating because it is not obvious: **ffmpeg threads the filter graph and the encoder independently, and both default to the host CPU count.**

Unpinned, this produced:

- different bytes from identical inputs on the *same* machine under CPU contention, and
- different bytes between machines with different core counts.

Either would silently corrupt the render cache in a multi-node deployment — where "busy" and "heterogeneous" are the normal state — serving a user a file that is not the one they previewed. There is no error; the cache key matches and the bytes differ.

Three settings are therefore mandatory, and all three are part of `RENDERER_VERSION`:

| Setting | What it fixes |
|---|---|
| `-x264-params deterministic=1` | Thread *scheduling*: interleaving perturbs lookahead decisions |
| `-threads N` (fixed) | Encoder thread *count* |
| `-filter_complex_threads N` / `-filter_threads N` (fixed) | Filter-graph thread count — separate from the encoder, and the one most easily missed |

Pinned to a **fixed count rather than to 1**: determinism requires the count to be *constant*, not to be one. Serialising the filter graph roughly doubled preview render time on a 2-core box, and render seconds are the dominant term in COGS ([docs/14 §2.5](14-cost-model.md)) — paying double for a guarantee a constant already buys would be a bad trade.

Changing any of the three changes the encoded bytes, so each requires a `RENDERER_VERSION` bump; otherwise renders cached under the old value would be served alongside new ones.

CI asserts determinism on an idle machine, which passes trivially. [`tools/stress_determinism.py`](../tools/stress_determinism.py) asserts it under deliberate CPU contention and is run before a renderer release.

The renderer also **asserts its source manifest** before writing a frame: every input asset must be either a user upload or a licensed catalogue item. There is no code path by which reference media can reach the output, and the assertion makes that testable rather than merely intended.

---

## 2. Pipeline

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 1. VALIDATE      schema · invariants · renderer_min_version · licences   │
│                  Fail here, never mid-render.                            │
├──────────────────────────────────────────────────────────────────────────┤
│ 2. RESOLVE       assignments → S3 keys → proxies (preview) or            │
│                  originals (export). Fetch licensed music + fonts.       │
├──────────────────────────────────────────────────────────────────────────┤
│ 3. PLAN          blueprint → execution graph. Compute exact frame        │
│                  ranges, apply time_map warp, resolve cut offsets.       │
├──────────────────────────────────────────────────────────────────────────┤
│ 4. DECODE        NVDEC → CUDA surfaces. Never leaves device memory.      │
├──────────────────────────────────────────────────────────────────────────┤
│ 5. COMPOSITE     per frame:  reframe → speed → grade → effects →         │
│                  transition → text. Order is fixed and matters (§5).     │
├──────────────────────────────────────────────────────────────────────────┤
│ 6. AUDIO         licensed bed (warped) + SFX + ducked source audio       │
├──────────────────────────────────────────────────────────────────────────┤
│ 7. ENCODE        NVENC → H.264/HEVC/AV1, faststart, platform preset      │
├──────────────────────────────────────────────────────────────────────────┤
│ 8. EMIT          S3 · C2PA provenance · degradation report · cost ledger │
└──────────────────────────────────────────────────────────────────────────┘
```

### 2.1 Stage 3 — planning, where the interesting work happens

Converting a declarative blueprint into an execution graph:

```python
def plan(bp: Blueprint, assets) -> ExecutionGraph:
    fps, frame_ms = bp.canvas.fps, 1000 / bp.canvas.fps
    ops = []

    for slot in bp.slots:
        a = slot.assignment
        # Cut offsets are applied HERE, not at analysis time, because they
        # depend on the bound track's warped beat grid.
        t_in  = bp.canvas.snap_to_frame(resolve_cut_time(bp, slot.index))
        t_out = bp.canvas.snap_to_frame(resolve_cut_time(bp, slot.index + 1))

        speed = bp.speed_for(slot.index)
        # A 620ms slot at 0.4x needs 1550ms of SOURCE. If the segment is
        # shorter than that, the ramp is impossible -- catch it now, in
        # planning, not 40 seconds into the render.
        src_needed = source_duration_for(t_out - t_in, speed)
        if src_needed > a.duration_ms:
            speed = flatten_ramp(speed, available=a.duration_ms)
            bp.degradation.add(SPEED_FLATTENED, slot=slot.index)

        ops.append(DecodeOp(asset=a.segment_id, in_ms=a.in_ms,
                            out_ms=a.in_ms + src_needed))
        ops.append(ReframeOp(bp.reframe_for(slot.index), t_in, t_out))
        ops.append(SpeedOp(speed, t_in, t_out))
        ops.append(GradeOp(bp.grade.for_slot(slot.index)))
        ops.extend(EffectOp(e) for e in bp.effects_for(slot.index))

    for cut in bp.cuts:
        if (tr := bp.transition_at(cut.index)):
            ops.append(TransitionOp(tr, cut, align=tr.align))

    ops.append(AudioOp(bp.audio.music_binding, bp.audio.sfx))
    if bp.captions.enabled:
        ops.append(CaptionOp(bp.captions, asr_words(assets)))

    return ExecutionGraph(ops).topologically_sorted()
```

**Everything that can fail must fail in planning.** A render that dies at 80% has burned 40 GPU-seconds and a user's patience. Planning is cheap — validate durations, licences, codec support, VRAM estimates, and speed feasibility there.

---

## 3. Frame-exact timing

The commonest class of subtle bug in video software. Three rules, applied without exception:

**Rule 1 — All timeline times snap to frame boundaries.** `Canvas.snap_to_frame()` is the only place this happens. A transition starting at 12480ms on a 30fps timeline actually starts at frame 374 = 12466.67ms. Inconsistent rounding between the transition start and the underlying clip cut produces a one-frame flash at the seam.

**Rule 2 — Source timestamps are read from presentation timestamps, never computed.** Phone footage is frequently variable-frame-rate. `frame_index × (1/fps)` is wrong for VFR sources and drifts progressively — a 40-second clip can be half a second out by the end. We build an explicit PTS map at probe time and index into it.

**Rule 3 — Transition alignment is explicit.** `align` determines where the cut sits within the transition interval:

```
                 cut at t
                     │
  align=centered     │        outgoing ▓▓▓▓░░░│░░░▓▓▓▓ incoming
                     │                  ├─ d/2 ─┼─ d/2 ─┤
  align=outgoing     │        outgoing ▓▓▓▓░░░░░░│▓▓▓▓ incoming
                     │                  ├──── d ───┤
  align=incoming     │        outgoing ▓▓▓▓│░░░░░░▓▓▓▓ incoming
                                            ├──── d ───┤
```

Getting this wrong shifts everything by half the transition duration. On a 480ms dissolve that is 240ms — a quarter of a second, and glaringly obvious against a beat grid.

---

## 4. Reframing

Turning arbitrary source geometry into 9:16 without centre-cropping the subject out of frame.

```python
def reframe(track: ReframeTrack, frames, masks) -> list[Rect]:
    # 1. Raw per-frame subject centroid
    raw = [centroid(m) if m.any() else frame_center for m in masks]

    # 2. Temporal smoothing -- THE critical step.
    #    SAM 3 masks are excellent per-frame and noisy frame-to-frame.
    #    Driving the crop from raw centroids produces visible jitter, which
    #    is the single commonest failure in auto-reframe products.
    alpha = 1 - track.smoothing            # 0.7 smoothing → alpha 0.3
    path = [raw[0]]
    for p in raw[1:]:
        path.append(alpha * p + (1 - alpha) * path[-1])

    # 3. Velocity clamp -- a virtual camera cannot whip across the frame
    max_step = track.max_pan_speed_pct_per_s / fps / 100 * frame_width
    for i in range(1, len(path)):
        step = path[i] - path[i-1]
        if abs(step) > max_step:
            path[i] = path[i-1] + copysign(max_step, step)

    # 4. Deadband -- a subject wandering within a small region should NOT
    #    move the camera. Without this the frame breathes constantly and
    #    the viewer feels seasick without knowing why.
    for i in range(1, len(path)):
        if abs(path[i] - path[i-1]) < 0.02 * frame_width:
            path[i] = path[i-1]

    # 5. Keep the subject bbox inside the crop with padding, clamp to bounds
    return [build_rect(p, track.padding_pct) for p in path]
```

**Smoothing, velocity clamp and deadband together are what separate a professional-looking auto-reframe from an obviously-automated one.** Each individually is insufficient; the deadband in particular is the one most often omitted and most responsible for the "breathing frame" artefact.

**Fallback ladder** when no subject is detected: `fill_subject` → `fill_center` → `blurred_bars` (fit with a blurred, scaled copy behind). Never letterbox to black on a 9:16 platform — black bars are read by both viewers and platform algorithms as low-effort content.

---

## 5. Composite order

Fixed, and each position is a decision:

```
1. DECODE            source frame → linear-light RGB
2. REFRAME           crop + scale to canvas
3. SPEED             temporal resample (before grade: cheaper, and blend
                     artefacts should be graded, not grade-then-blended)
4. GRADE             primary + secondary correction
5. SLOT EFFECTS      grain, glow, aberration -- AFTER grade, because these
                     are camera/film artefacts that in reality occur
                     downstream of colour, and grading grain looks wrong
6. MOTION            synthetic scale/position/rotation
7. TRANSITION        composite outgoing ⊕ incoming
8. GLOBAL EFFECTS    vignette, letterbox -- applied across the transition
                     so it does not visibly pop at cut points
9. TEXT              captions and titles -- always on top, never graded
10. ENCODE           → target colour space
```

**Two orderings that are counterintuitive and correct:**

Grain *after* grade. Physically, grain is a property of the medium and sits downstream of colour. Applying grain first and then grading it produces a grain structure that shifts with the grade — visible and wrong.

Vignette in *global* effects, after transitions. A vignette applied per-slot pops at every cut as the two shots' vignettes cross-fade against each other. Applied globally, it is continuous across the whole piece, which is what a vignette is.

---

## 6. Implementation

### 6.1 Two backends

| Backend | Use | Why |
|---|---|---|
| **FFmpeg filter graph** | Preview, simple blueprints | Battle-tested, hardware decode/encode, fast to ship |
| **Custom GL/Vulkan compositor** | Export, complex effects | Full control, custom shaders, no filter-graph expressive limits |

MVP ships FFmpeg-only. The blueprint is renderer-agnostic, so the second backend is additive rather than a rewrite — and the two are validated against each other by rendering the golden set through both and comparing SSIM. Divergence beyond tolerance is a bug in one of them.

### 6.2 Filter graph generation

```python
def to_filter_graph(g: ExecutionGraph) -> str:
    parts, labels = [], []
    for i, seg in enumerate(g.segments):
        chain = f"[{i}:v]"
        chain += f"crop={seg.crop.w}:{seg.crop.h}:{seg.crop.x}:{seg.crop.y},"
        chain += f"scale={g.canvas.w}:{g.canvas.h}:flags=lanczos,"
        if seg.speed != 1.0:
            chain += f"setpts={1/seg.speed:.6f}*PTS,"
        chain += f"colorlevels={levels(seg.grade)},eq={eq(seg.grade)},"
        if seg.grade.lut_ref:
            chain += f"lut3d=file={licensed_lut_path(seg.grade.lut_ref)},"
        chain += f"trim=0:{seg.duration_s:.6f},setpts=PTS-STARTPTS[v{i}]"
        parts.append(chain); labels.append(f"[v{i}]")

    for cut in g.cuts:
        if (tr := cut.transition):
            parts.append(xfade_expr(tr, labels))   # xfade or custom GL shader

    return ";".join(parts)
```

`lut3d=file=` loads a **licensed** LUT from our library. It never loads a LUT derived from the reference — there is no such artefact, by construction ([docs/08 §5](08-algorithms.md#5-colour-grade-inversion)).

### 6.3 Custom shaders

FFmpeg's `xfade` covers dissolve, fade, wipe and slide. It does not cover whip pan with directional blur, RGB split, film burn, datamosh, or halation. Those are GLSL fragment shaders in the custom backend:

```glsl
// whip pan: directional motion blur + stretch, driven by transition.direction_deg
uniform sampler2D uOut, uIn;
uniform float uProgress, uIntensity;
uniform vec2  uDirection;

vec4 directionalBlur(sampler2D tex, vec2 uv, vec2 dir, float amount) {
    vec4 acc = vec4(0.0);
    const int N = 24;                        // fixed, not adaptive: determinism
    for (int i = 0; i < N; i++) {
        float t = float(i) / float(N - 1) - 0.5;
        acc += texture(tex, uv + dir * t * amount);
    }
    return acc / float(N);
}

void main() {
    float p = uProgress;
    float blur = uIntensity * 0.14 * sin(p * 3.14159);   // peaks mid-transition
    vec2 uvOut = vUV + uDirection * p * 0.35;
    vec2 uvIn  = vUV - uDirection * (1.0 - p) * 0.35;
    vec4 a = directionalBlur(uOut, uvOut, uDirection, blur);
    vec4 b = directionalBlur(uIn,  uvIn,  uDirection, blur);
    fragColor = mix(a, b, smoothstep(0.35, 0.65, p));
}
```

`const int N = 24` rather than a quality-adaptive sample count: adaptive sampling would make preview and export produce different pixels, breaking the determinism contract in the place users would most notice it.

---

## 7. Partial re-render

The most-travelled path in the product is `preview_ready → edit → preview_ready` ([docs/05 §6](05-data-flow.md#6-state-machine)). Re-rendering 60 seconds because a user swapped one clip is unacceptable — it makes iteration feel expensive and users stop iterating.

```python
def dirty_range(bp_old, bp_new) -> list[tuple[int, int]]:
    dirty = set()
    for i, (a, b) in enumerate(zip(bp_old.slots, bp_new.slots)):
        if a != b:
            dirty.add(i)
            dirty.add(i - 1)   # the incoming transition touches the previous slot
            dirty.add(i + 1)   # and the outgoing one touches the next
    if bp_old.grade != bp_new.grade:      return [(0, len(bp_new.slots))]   # global
    if bp_old.captions != bp_new.captions: dirty |= caption_slots(bp_new)
    return merge_contiguous(sorted(dirty))
```

Rendered segments are cached in S3 keyed by `sha256(slot_spec ‖ asset_id ‖ in/out ‖ renderer_version)`. A single clip swap re-renders 3 slots — roughly 2 seconds instead of 60. Concatenation of cached and fresh segments is stream-copy where the codec parameters match, which they do because we control the encoder settings.

---

## 8. Audio

```
┌─ licensed music bed ── warped by MusicBinding.time_map ─┐
│                                                          │
├─ SFX layer ─── whooshes, impacts, risers on their cuts ─┤──► mix ──► AAC
│                                                          │
└─ source audio ─ ducked -18dB under music, gated ────────┘
```

**Source audio handling.** Kept where the clip has speech (the user's voice matters), gated to silence where it is only wind and handling noise. A user's phone footage of a motorcycle contains mostly wind noise; passing it through unducked ruins an otherwise good edit.

**SFX placement.** `bound_to_cut` anchors an SFX to a cut, so when the cut moves — because the music binding warped the grid — the whoosh moves with it. Unbound SFX use absolute time.

**Loudness normalisation.** `loudnorm` to −14 LUFS integrated, −1 dBTP ceiling. This is the level the major platforms normalise to; delivering louder means they turn it down and it sounds worse than something delivered at target.

---

## 9. Output & provenance

| Preset | Resolution | Codec | Bitrate | Notes |
|---|---|---|---|---|
| Preview | 540×960 | H.264 | 2.5 Mbps | CRF 26, no grain, simplified fx |
| TikTok / Reels / Shorts | 1080×1920 | H.264 High | 10 Mbps | faststart, −14 LUFS |
| 4K | 2160×3840 | HEVC | 45 Mbps | Pro tier, A10G |
| Master | source | ProRes 422 HQ | — | Enterprise |
| Project | — | OTIO / EDL / FCPXML | — | Pro: finish in Resolve/Premiere |

**Every output carries C2PA provenance** recording that it was assembled by ReelsEdits, the blueprint ID, the renderer version, whether any generative content was included, and the music licence ID. This costs nothing, is the right default, and pre-empts a regulatory requirement that is clearly coming ([docs/18 §9](18-legal-ethics.md)).

---

## 10. Performance

| Output | Hardware | Target | Dominant cost |
|---|---|---|---|
| 60s preview @ 540p | L4 | 48s p50 / 88s p95 | Composite |
| 60s export @ 1080p | L4 | 95s p50 / 165s p95 | Composite + encode |
| 60s export @ 4K | A10G | 240s p50 / 420s p95 | Decode + composite |
| Partial (3 slots) | L4 | ~2s | Negligible |

**Optimisations that actually moved the number, in order of impact:**

1. **Zero host round-trips.** NVDEC → CUDA → NVENC without touching host memory. Copying 1080p60 frames to host and back cost more wall-clock than most of the processing. This was worth roughly 2.4× on its own.
2. **Proxy rendering for preview.** 540p preview is ~4× cheaper than 1080p and visually sufficient for judging an edit.
3. **Segment-level caching.** Makes iteration nearly free, which changes user behaviour more than it changes cost.
4. **Batched shader passes.** Multiple effects in one fragment shader rather than one pass each — each extra full-frame pass costs a full read/write of the frame buffer.
5. **Adaptive effect quality on preview.** Grain and particles disabled; blur sample counts reduced. Explicitly *not* applied to export, where determinism must hold.

---

Next: [11 — Database Schema](11-database-schema.md)
