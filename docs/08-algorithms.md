# 08 — Algorithms

The non-obvious computations. Models handle perception; these handle the editorial reasoning that sits between perception and the blueprint.

---

## 1. Perceptual fingerprinting

**Problem:** The same TikTok, downloaded twice at different bitrates from different mirrors, must hit the same blueprint cache entry. Byte-level hashing gives 0% hit rate on exactly the case where re-use is highest, and the whole cost model depends on that hit rate ([docs/14](14-cost-model.md)).

```python
def fingerprint(video: Path, analyzer_version: str) -> str:
    frames = decode_at(video, fps=1.0, size=(64, 64), grayscale=True)
    # DCT-based pHash per frame: robust to bitrate, scale, and mild crop
    phashes = [dct_phash(f) for f in frames]

    # Quantise duration to 100ms so re-encodes with a trailing frame still match
    duration_bucket = round(probe_duration_ms(video) / 100)

    # Chromaprint over the first 30s: survives transcode, catches audio-identical
    # videos with different visual crops (very common with reposts)
    audio_fp = chromaprint(extract_audio(video, max_sec=30))

    return sha256(b"|".join([
        b"".join(phashes),
        str(duration_bucket).encode(),
        audio_fp,
        analyzer_version.encode(),   # ← mandatory
    ])).hexdigest()
```

**Near-duplicate handling.** Exact fingerprint match is the fast path. On miss we run a second lookup: Hamming distance over the pHash sequence against an LSH index. Distance below 12% of bits across ≥80% of aligned frames counts as a near-duplicate and reuses the blueprint. This catches the very common case of the same video reposted with a different intro card or a 2% crop.

**`analyzer_version` in the key is mandatory, not defensive.** Ship a better shot-boundary detector and every old blueprint must miss. Omit it and you serve blueprints from a superseded model indefinitely — a bug discovered three months later by a confused user, with no error anywhere.

---

## 2. Beat grid and cut quantisation

### 2.1 Building the grid

Two trackers, fused:

```python
beats_t = transformer_tracker(drums_stem)      # Beat This! class
beats_d = dbn_tracker(drums_stem)              # madmom

matched = [(bt, bd) for bt in beats_t
           if (bd := nearest(beats_d, bt)) and abs(bt - bd) < 40]

agreement = len(matched) / max(len(beats_t), len(beats_d))
grid = beats_t                                  # transformer is primary
confidence = 0.55 + 0.45 * agreement
```

Where agreement is low, `provenance.confidence.beat_grid` drops and the planner is instructed to prefer `free` cuts in that region. This is the correct response, not a hedge: if we cannot find the grid reliably, neither can the viewer, and forcing cuts onto an uncertain grid produces edits that feel *off* in a way nobody can name.

### 2.2 Mapping cuts to the grid

For each detected cut at time `t`:

```python
i, raw_offset = nearest_beat(t)          # signed; negative = cut precedes beat
ibi = mean_inter_beat_interval()
offset_in_beats = raw_offset / ibi

for sub in (1, 1/2, 1/3, 1/4, 1/8, 2/3, 3/4):
    if abs(offset_in_beats % sub) < 0.08 or abs(offset_in_beats % sub - sub) < 0.08:
        best_sub = sub
        break

if abs(raw_offset) < 45:                 mode = ON_BEAT
elif best_sub and abs(raw_offset) < ibi * 0.35:  mode = SUBDIVIDED
elif near_any(impacts, t, tol=120):      mode = IMPACT
else:                                    mode = FREE
```

### 2.3 The negative offset, and why it matters

**This is the most valuable number the analyser extracts, and the easiest to accidentally throw away.**

Across professionally-cut short-form video, the distribution of `offset_ms` is not centred on zero. It is centred around **−20 to −60 milliseconds** — the cut lands *before* the transient.

The reason is perceptual. Visual processing latency exceeds auditory processing latency by roughly 30–50ms for a suprathreshold stimulus. A cut placed exactly on the beat is *perceived* as arriving after it: the edit feels heavy, slightly behind, dragging. Editors converge on the early cut by feel, without necessarily knowing why.

A system that quantises every cut to the grid produces edits that are, measurably, perfectly beat-synchronised — and that feel mechanical. This is a large part of why template-based tools produce output that is recognisably template-based.

So:

```python
# Extract the reference's OWN offset distribution, per section
offsets_by_section = defaultdict(list)
for cut in cuts:
    if cut.mode in (ON_BEAT, SUBDIVIDED):
        offsets_by_section[cut.section].append(cut.offset_ms)

style.pacing.offset_mean_ms   = mean(all_offsets)     # e.g. -36.4
style.pacing.offset_stddev_ms = stdev(all_offsets)    # e.g. 10.8
```

At render time, each cut's offset is **sampled from that distribution**, not set to the mean:

```python
offset = clamp(gauss(mu=section_mean, sigma=section_stddev), -120, 40)
t_render = beat_grid[cut.beat_index] + offset
```

The variance matters as much as the mean. Every cut at exactly −36ms is a different kind of mechanical. Human editors are consistent-with-variance, and reproducing the variance is what makes the rhythm feel performed rather than computed.

### 2.4 Micro-placement refinement

When `cut.hide_in_motion` is set, the renderer may nudge the cut within ±3 frames to the local motion-energy peak:

```python
window = range(t - 3*frame_ms, t + 3*frame_ms, frame_ms)
best = argmax(window, key=lambda u: motion_energy(outgoing_clip, u))
if abs(best - t) <= 3 * frame_ms and motion_energy(best) > 1.4 * motion_energy(t):
    t = best
```

The `1.4×` threshold prevents nudging for a marginal gain — moving a cut off a carefully-chosen beat offset to gain 5% more motion is a net loss. Only a *clear* motion peak justifies the move.

---

## 3. Pacing model

The blueprint must adapt to a different-length track. Fixed timestamps would make it a template.

**Extraction.** Cut density in a 4-second sliding window, correlated against the audio energy envelope:

```python
density = [cuts_in_window(t, 4.0) / 4.0 for t in timeline]
energy  = [energy_curve.at(t) for t in timeline]
r = pearson(density, energy)              # typically 0.6–0.85 in good edits
```

Per section we store `target_cut_density` — cuts per second, not a cut list.

**Adaptation.** Given a new track of different duration and section layout:

```python
for new_section in new_track.sections:
    ref_section = match_by_kind_and_position(new_section, blueprint.sections)
    n_cuts = round(new_section.duration_s * ref_section.target_cut_density)

    # Place on beats within the section, spaced to match the reference's own
    # inter-cut-interval distribution rather than uniformly
    positions = sample_beat_positions(
        beats_in(new_section),
        n=n_cuts,
        interval_dist=ref_section.ici_distribution,
    )
```

**Non-uniform spacing is the point.** Uniform placement produces a metronome. Real edits cluster — three quick cuts then a hold, then two more. Sampling from the reference's own inter-cut-interval distribution reproduces the clustering, which is a large part of what "rhythm" means here beyond mere beat-locking.

**Compression priority.** When the new track is shorter, sections compress in order of `1 − energy`: intros and outros give way first, drops last. When longer, low-importance slots are duplicated with different assignments rather than existing shots being held longer — holding a shot past its natural length is far more visible than an extra cut.

---

## 4. Transition modelling

### 4.1 Classification

The gradual-transition *interval* comes from the SBD ensemble. Classification uses hand-designed features, because the classes are physically distinct and the parameters (duration, intensity, direction) matter as much as the class:

```python
def classify(frames, i0, i1):
    n = i1 - i0
    if n <= 1:
        return HARD_CUT, {}

    luma  = [mean_luma(f) for f in frames[i0:i1]]
    flow  = [flow_field(frames[k], frames[k+1]) for k in range(i0, i1-1)]
    hist  = [histogram(f) for f in frames[i0:i1]]

    # Fade: monotonic luma convergence to an extreme
    if monotonic(luma) and (luma[-1] < 0.05 or luma[-1] > 0.95):
        return (FADE_BLACK if luma[-1] < 0.5 else FADE_WHITE), {}

    # Flash: brief spike above BOTH neighbours
    if n <= 4 and max(luma) > 1.6 * max(luma[0], luma[-1]):
        return FLASH, {"peak": max(luma)}

    # Whip pan: extreme unidirectional flow + directional blur
    mag, coherence, direction = flow_stats(flow)
    if mag > 28 and coherence > 0.82:
        return WHIP_PAN, {"direction_deg": direction, "magnitude": mag}

    # Zoom vs spin: divergence-dominated vs curl-dominated flow
    div, curl = helmholtz(flow)
    if abs(div) > 3 * abs(curl) and abs(div) > 0.14:
        return (ZOOM_IN if div > 0 else ZOOM_OUT), {"rate": abs(div)}
    if abs(curl) > 3 * abs(div) and abs(curl) > 0.14:
        return SPIN, {"rate": curl}

    # Cross dissolve: linear histogram blend, low flow
    if linear_blend_score(hist) > 0.85 and mag < 6:
        return CROSS_DISSOLVE, {}

    # RGB split: per-channel phase-correlation misregistration
    shift = channel_misregistration(frames[i0:i1])
    if shift > 1.8:
        return RGB_SPLIT, {"max_shift_px": shift}

    return CROSS_DISSOLVE, {}     # honest default
```

Multi-label output is permitted and expected. A whip pan *is* a motion blur; `secondary: [MOTION_BLUR]` carries the second label rather than forcing a choice that loses information.

**Helmholtz decomposition** — splitting the flow field into divergence (expansion/contraction → zoom) and curl (rotation → spin) — is the clean discriminator between zoom and spin transitions, which are otherwise easy to confuse and look completely different when rendered.

### 4.2 Selection at render time

The reference's transition at a given cut may not be *possible* with the user's footage. A whip pan between two static shots reads as a bug.

```python
def select(planned: Transition, out_seg, in_seg) -> tuple[Transition, Compromise | None]:
    if planned.type.needs_motion:
        if out_seg.motion_energy < 0.35 or in_seg.motion_energy < 0.35:
            return planned.fallback_or(FLASH), Compromise(
                kind=TRANSITION_SUBSTITUTED,
                detail=f"No motion for {planned.type}; used {fallback}.")

    if planned.type.needs_direction:
        d_out, d_in = out_seg.motion_direction, in_seg.motion_direction
        if d_out is None or angular_distance(d_out, d_in) > 55:
            return planned.fallback_or(FLASH), Compromise(...)
        planned.direction_deg = circular_mean(d_out, d_in)

    return planned, None
```

Every substitution is recorded in `degradation.compromises` and surfaced. Silent substitution is how a user ends up confused about why their output does not match the style card.

---

## 5. Colour grade inversion

**This is the section where products in this category over-claim, so this section is deliberately blunt about what is and is not possible.**

### 5.1 What you cannot do

You cannot recover the LUT applied to the reference. Three independent reasons, each individually sufficient:

**The "before" does not exist.** Grade inversion is `find T such that T(original) = graded`. You have `graded`. You do not have `original` — the camera-native footage was never published. This is not a hard problem; it is an under-determined one. Infinitely many `(original, T)` pairs produce the same `graded`.

**Compression destroyed the evidence.** Delivered short-form video is 4:2:0 chroma-subsampled at 1–5 Mbps. Chroma resolution is already halved in both dimensions and quantised hard. The fine colour relationships that a 33³ LUT encodes have been thrown away by the encoder before you ever see the file.

**Clipping is not invertible.** Any pixel the grade pushed to 0 or 255 has lost its pre-grade value permanently. In high-contrast graded footage this is a substantial fraction of pixels — and disproportionately the shadows and highlights, which is exactly where the grade's character lives.

A product claiming "extracts the exact LUT" is claiming something that is not achievable from a delivered video. We do not claim it, and the schema field is named `lut_ref` pointing at a **licensed** LUT from our library, not an extracted one.

### 5.2 What we do instead

**Step 1 — Measure the delivered look.** Not to invert it; to describe it.

```python
target = {
  "luma_percentiles": {p: percentile(luma, p) for p in (1, 5, 25, 50, 75, 95, 99)},
  "mean_lab":         mean(to_lab(pixels)),
  "sat_mean":         mean(saturation),
  "sat_p95":          percentile(saturation, 95),
  # The split-tone signature: the single most recognisable element of most
  # short-form grades. Mean hue of darkest 15% vs brightest 15%.
  "shadow_hue_deg":    circular_mean(hue[luma < percentile(luma, 15)]),
  "highlight_hue_deg": circular_mean(hue[luma > percentile(luma, 85)]),
  "hue_vs_hue":        hue_transfer_curve(),
  "hue_vs_sat":        sat_by_hue_curve(),
}
```

**Step 2 — Fit parameters by optimisation.** Find `GradeParams` minimising the distance between the *user's* graded footage statistics and the reference's target statistics:

```python
def loss(params, user_frames, target):
    out = apply_grade(user_frames, params)
    return (
        2.0 * l2(percentiles(out.luma), target["luma_percentiles"])
      + 1.5 * delta_e_2000(mean_lab(out), target["mean_lab"])
      + 1.0 * abs(mean_sat(out) - target["sat_mean"])
      + 2.5 * circular_dist(shadow_hue(out), target["shadow_hue_deg"])
      + 2.5 * circular_dist(highlight_hue(out), target["highlight_hue_deg"])
      + 0.4 * skin_tone_penalty(out)     # skin must stay plausible
    )

params = minimize(loss, x0=identity_grade(), method="L-BFGS-B", bounds=GRADE_BOUNDS)
```

**Optimising the user's footage toward the reference's statistics — rather than applying parameters derived from the reference — is the key move.** The user's footage starts from a different exposure, a different white balance, a different camera. Applying a grade authored for someone else's starting point is precisely how you get crushed blacks and magenta skin. Matching *statistics* adapts automatically.

`skin_tone_penalty` is a hard-won detail: unconstrained statistical matching will happily push skin tones green or magenta to hit a global hue target. A penalty term keeping detected skin regions inside a plausible hue band costs nothing and prevents the single most noticeable grading failure.

**Step 3 — Report confidence honestly.**

```python
confidence = (
    0.30 * min(1.0, bitrate_mbps / 6.0)         # low bitrate → low confidence
  + 0.25 * (1 - clipped_pixel_fraction)          # clipping → unrecoverable
  + 0.25 * shot_consistency                      # per-shot variance
  + 0.20 * (1 - loss_at_optimum / baseline_loss) # did the fit actually converge
)
```

Below 0.6 the UI says **"grade: approximate"** and offers manual controls. The example blueprint ships with `grade.confidence = 0.58` for exactly this reason — a 1.8 Mbps reference does not support a confident grade estimate, and pretending otherwise would be dishonest.

### 5.3 Per-shot vs global

Editors grade globally and then adjust shots. We fit a global grade first, then per-shot residuals, and keep residuals only where they exceed a threshold — otherwise per-shot noise masquerades as intentional variation and the output flickers between shots.

---

## 6. Effect budget

Reproducing a style's *restraint* is as important as reproducing its vocabulary.

```python
budget = {}
for section in sections:
    for effect_type in detected_effects:
        n = count_instances(effect_type, section)
        budget[effect_type] = max(budget.get(effect_type, 0), n)
```

At render time, `Blueprint.check_effect_budget()` reports violations and the renderer drops the lowest-`intensity` excess instances first.

Without this, a renderer that has learned "this style uses light leaks" applies them at every opportunity and produces something that looks nothing like the reference. It looks like a 2013 wedding video. The count is the style.

---

## 7. Speed inference

You cannot directly observe that a shot was captured at 120fps and played at 24. Three weak estimators, combined:

**Motion blur vs. displacement.** Real-time footage at a given shutter angle has a predictable relationship between per-frame displacement and motion blur extent. Slow motion has *displacement without proportional blur* — the frames were captured with a short exposure at high rate.

```python
blur_extent = estimate_blur_length(frame, direction=flow_direction)
expected    = displacement * SHUTTER_ANGLE / 360
ratio       = blur_extent / max(expected, 1e-3)
speed_est_1 = clamp(ratio, 0.1, 1.0)      # ratio << 1 suggests slow motion
```

**Temporal frequency content.** Natural motion has characteristic high-frequency content — micro-jitter, vibration, subject detail. Slow motion attenuates it; timelapse amplifies it.

**Semantic expectation.** Ask the VLM: "a person walking should cross this frame in roughly N seconds." Compare to observed. Crude, and surprisingly effective for the large factors (4× slow motion, 20× timelapse) that the other two estimators handle least well.

```python
speed  = weighted_median([speed_est_1, speed_est_2, speed_est_3],
                         weights=[0.45, 0.30, 0.25])
spread = max(ests) / max(min(ests), 1e-3)
confidence = 1.0 / (1.0 + 2.0 * log(spread))

if confidence < 0.6:
    speed, note = 1.0, "speed ambiguous; rendered at native rate"
```

**Below 0.6 confidence we record `speed: 1.0` rather than guessing.** A wrong speed ramp is far more visible in the output than a missing one — the audience does not know what they did not get, but they absolutely notice motion that looks wrong.

**Ramp fitting.** For non-constant speed, fit a monotone piecewise-cubic (PCHIP) to the estimated speed curve, then simplify to the fewest keyframes reproducing it within tolerance. Editors use 2–4 keyframes; a 40-keyframe ramp is overfitting to estimator noise and will render as stutter.

---

## 8. Composition analysis

```python
def compose(mask, frame_shape) -> dict:
    cy, cx = centroid(mask)
    h, w = frame_shape
    nx, ny = cx / w, cy / h

    power_points = [(1/3, 1/3), (2/3, 1/3), (1/3, 2/3), (2/3, 2/3)]
    thirds_score = 1 - min(hypot(nx-px, ny-py) for px, py in power_points) / 0.5

    left  = mask[:, :w//2].sum()
    right = mask[:, w//2:].sum()
    symmetry = 1 - abs(left - right) / max(left + right, 1)

    subject_bbox = bounding_box(mask)
    negative_space = 1 - area(subject_bbox) / (h * w)

    horizon = detect_horizon(frame)          # Hough + gradient orientation

    return {"thirds": thirds_score, "symmetry": symmetry,
            "negative_space": negative_space, "subject_x": nx, "subject_y": ny,
            "horizon_y": horizon.y, "horizon_angle": horizon.angle}
```

Composition matters for matching, not just aesthetics: a reference shot with the subject hard left and negative space right should ideally map to a user segment with the same layout. Consecutive shots that agree compositionally cut together cleanly; ones that disagree produce a jarring jump the viewer reads as an error.

---

## 9. Coverage and insufficiency detection

Runs **before** any render is committed.

```python
def coverage(blueprint, segments) -> Report:
    per_slot = {}
    for slot in blueprint.slots:
        cands = [s for s in segments if satisfies_hard_constraints(slot, s)]
        per_slot[slot.index] = min(1.0, len(cands) / slot.min_candidates)

    weights  = [s.importance for s in blueprint.slots]
    overall  = weighted_mean(per_slot.values(), weights)

    gaps = []
    for slot in blueprint.slots:
        if per_slot[slot.index] < 0.5:
            gaps.append(describe_gap(slot))      # ← the important part
    return Report(overall, per_slot, gaps)


def describe_gap(slot) -> str:
    r = slot.requirements
    parts = []
    if r.shot_scale is not ShotScale.ANY: parts.append(f"a {r.shot_scale.value} shot")
    if r.motion_direction_deg is not None:
        d = "left-to-right" if r.motion_direction_deg > 0 else "right-to-left"
        parts.append(f"with strong {d} motion")
    if r.camera_height is not CameraHeight.ANY: parts.append(f"from a {r.camera_height.value} angle")
    return "You need " + " ".join(parts) + f" — the style uses one at {slot.t_in_ms/1000:.1f}s."
```

`describe_gap` is disproportionately important. "You need a shot with strong left-to-right motion — the style uses a whip pan at 12.2s" sends a user out to shoot for ten minutes. "Insufficient footage" churns them. Same information, entirely different outcome.

---

## 10. Complexity

| Algorithm | Complexity | 60s ref, 24 clips |
|---|---|---|
| Fingerprint | O(n frames) | 1.4s |
| Beat fusion | O(b log b) | 0.2s |
| Cut→grid mapping | O(c log b) | <0.1s |
| Transition classification | O(t · w) | 1.1s |
| Grade fit (L-BFGS-B) | O(iters · pixels) | 3.2s |
| Speed inference | O(shots · frames) | 2.8s |
| Composition | O(shots) | 0.4s |
| Coverage | O(slots · segments) | 0.3s |
| Assignment | see [docs/09](09-clip-matching.md) | 0.6s |

---

Next: [09 — Clip Matching](09-clip-matching.md)
