# 06 — Editing Blueprint Specification (EBP v1)

**Normative schema:** [`schemas/blueprint.schema.json`](../schemas/blueprint.schema.json) (JSON Schema draft 2020-12, validated in CI)
**Reference implementation:** [`services/common/reelsedits_common/blueprint.py`](../services/common/reelsedits_common/blueprint.py) (Pydantic v2)
**Example:** [`schemas/examples/moto-sunset-90bpm.json`](../schemas/examples/moto-sunset-90bpm.json)

---

## 1. What the blueprint is

The Editing Blueprint is the **only** interface between analysis and rendering. It is a complete, timecoded, renderer-agnostic description of an edit's craft, and it is the single most important artefact in the system.

Everything upstream exists to produce one. Everything downstream is a pure function of one.

```
                    ┌───────────────────┐
  reference ───────►│                   │
                    │  EDITING BLUEPRINT│──────► render
  user footage ────►│                   │
                    └───────────────────┘
                       ~18–60 KB JSON
```

### 1.1 Design constraints

Five properties, each of which was a design decision with alternatives that were rejected.

**Contains no reference media.** There is no field in the schema capable of holding an image, a video frame, an audio sample, or a LUT extracted from the reference. This is enforced structurally rather than by policy — `additionalProperties: false` on every object means a future engineer cannot casually add `"reference_thumbnail_b64"`. See [docs/18](18-legal-ethics.md).

**Renderer-agnostic.** Nothing in the blueprint names FFmpeg, a shader, a codec, or a filter. It describes *intent* — "cross dissolve, 320ms, ease-in-out" — not implementation. This is what makes an EDL/OTIO export possible later, and what stops a renderer rewrite from invalidating the corpus.

**Adaptive, not literal.** Slots carry *requirements*, not references to reference shots. Sections carry *target cut densities*, not fixed timestamps. A blueprint extracted from a 128 BPM 45-second reference must render correctly against a 124 BPM 60-second track and different footage. A blueprint that only worked at its original duration would be a template, and templates are what CapCut already has ([docs/02](02-competitive-analysis.md#vs-capcut)).

**Every uncertain value carries a confidence.** Grade, speed, transitions and captions all have confidence fields, because all four are estimated rather than measured. The renderer's behaviour below 0.6 confidence is specified, not incidental.

**Deterministic.** `render(blueprint, assets, renderer_version)` is a pure function. No random seeds, no sampling, no wall-clock, no network-dependent behaviour. This is the precondition for caching, the marketplace, collaboration, the API, and reproducible debugging.

### 1.2 Two states

A blueprint exists in one of two states, distinguished by whether `slots[].assignment` is populated:

| State | `assignment` | Meaning | Where it lives |
|---|---|---|---|
| **Free** | absent | Pure style. Portable across projects and users. | Style library, marketplace |
| **Bound** | present | Style + a specific project's footage assignment | A project |

A free blueprint is the tradeable unit. Binding is what the matcher does.

---

## 2. Top-level structure

```jsonc
{
  "ebp_version": "1.0",
  "id": "bp_7fK2mQx91aBc",
  "parent_id": null,              // set on user edits → immutable version chain
  "created_at": "2026-08-07T09:14:22Z",
  "name": "Golden-hour rolling shots, hard-cut heavy",

  "provenance": { ... },          // §7  what produced this and how sure it is
  "canvas":     { ... },          // §2.1 output geometry
  "audio":      { ... },          // §3  the rhythmic skeleton
  "style":      { ... },          // §4  aggregate signature + the style card
  "slots":      [ ... ],          // §5  holes the matcher fills
  "cuts":       [ ... ],          // §6  where and how cuts land
  "transitions":[ ... ],          // §6.2
  "motion":     [ ... ],          // §8  synthetic camera motion
  "speed":      [ ... ],          // §9  ramps, freezes, timelapse
  "effects":    [ ... ],          // §10
  "grade":      { ... },          // §11
  "captions":   { ... },          // §12
  "text_objects":[ ... ],         // §12.3
  "reframe":    [ ... ],          // §13
  "constraints":{ ... },          // §14 invariants the renderer cannot violate
  "degradation":{ ... }           // §15 what we could not deliver, and why
}
```

### 2.1 Canvas

Output geometry, plus `safe_area_inset_pct` — the region occupied by platform UI chrome (TikTok's right-hand action rail, Instagram's caption bar). Captions and text objects are laid out inside the safe area, which is why our captions do not end up under the share button. Small detail, disproportionately visible in output.

---

## 3. Audio track

**The rhythmic skeleton, never the audio.**

```jsonc
"audio": {
  "bpm": 128.0,
  "bpm_curve": [[0, 128.0], [22400, 128.0], [45000, 132.0]],
  "time_signature": "4/4",
  "beat_grid_ms": [420, 889, 1358, 1827, ...],
  "downbeats_ms": [420, 2296, 4172, ...],
  "sections": [
    {"kind":"intro", "t_in_ms":0,     "t_out_ms":7500,  "energy":0.32, "target_cut_density":0.9},
    {"kind":"build", "t_in_ms":7500,  "t_out_ms":22400, "energy":0.61, "target_cut_density":1.7},
    {"kind":"drop",  "t_in_ms":22400, "t_out_ms":41000, "energy":0.94, "target_cut_density":2.6},
    {"kind":"outro", "t_in_ms":41000, "t_out_ms":45000, "energy":0.40, "target_cut_density":0.7}
  ],
  "energy_curve": { "hz": 20, "values": [0.12, 0.14, ...] },
  "impacts": [ {"t_ms": 22400, "strength": 1.0, "kind": "drop"} ],
  "sfx":     [ {"t_ms": 22280, "class": "riser", "duration_ms": 900, "bound_to_cut": 14} ],
  "mood":  ["driving", "euphoric"],
  "genre": ["electronic", "future_bass"],
  "music_binding": {
    "strategy": "catalogue_match",
    "track_id": "es_9182773",
    "licence_id": "lic_2026_08_a91f",
    "match_score": 0.94,
    "time_map": [[0,0], [22400, 21980], [45000, 44120]]
  }
}
```

### 3.1 `music_binding` — the most consequential field in the schema

The reference's music is almost always a copyrighted master. It cannot be reused. This is not a policy we apply at the end; it is a **structural property of the format**, and this field is how.

The blueprint carries the *structure* — 128 BPM, 4/4, drop at 22.4s, energy curve, downbeat positions. At render time the binding resolves that structure to an actual, licensed track:

| Strategy | Meaning | Licence required |
|---|---|---|
| `catalogue_match` | Retrieved from our licensed catalogue by structural similarity | Yes — `licence_id` mandatory |
| `user_supplied` | The user's own track, with an attestation of rights | User attests |
| `silent` | No music bed; SFX only | No |
| `generated` | AI-generated music matched to the structure (future) | Yes — model licence |

`constraints.require_licensed_audio` is `{"const": true}` in the schema — not a default, not configurable. **The renderer refuses to run without a resolved licence.** A tenant cannot turn this off, a config file cannot override it, and an API caller cannot omit it.

### 3.2 `time_map` — binding structure to a real track

Catalogue tracks will not be exactly 128.000 BPM with the drop at exactly 22.400s. The time map is a piecewise-linear warp from blueprint time to track time, anchored at structural landmarks (section boundaries, the drop, downbeats).

The renderer warps the **edit** to the track, not the track to the edit. Time-stretching licensed audio degrades it and is a licence question in itself; shifting cut points by 20–80ms is imperceptible. Where warp exceeds a threshold the blueprint records `music_tempo_mismatch` in `degradation` and the UI recommends a closer track.

---

## 4. Style profile

Aggregate signature. Feeds the style card ([docs/01 §3.1](01-product-vision.md#31-first-run--make-my-footage-look-like-this)) and the style-similarity search.

```jsonc
"style": {
  "summary": "Fast, hard-cut-driven edit locked tightly to a 128 BPM four-on-the-floor grid...",
  "tags": ["automotive", "golden_hour", "hard_cut", "high_energy"],
  "pacing": {
    "cuts_per_second": 1.87,
    "beat_lock_ratio": 0.84,
    "mean_shot_ms": 535, "median_shot_ms": 469, "shot_ms_stddev": 218,
    "offset_mean_ms": -38.0,        // ← see §6.1
    "offset_stddev_ms": 21.0,
    "acceleration": 0.42
  },
  "shot_scale_mix":  {"wide":0.22, "medium":0.41, "close":0.29, "extreme_close":0.08},
  "transition_mix":  {"hard_cut":0.71, "whip_pan":0.14, "flash":0.09, "zoom_in":0.06},
  "effect_budget":   {"film_grain":1, "light_leak":2, "chromatic_aberration":1},
  "palette": [ {"hex":"#1f3a4d","weight":0.31,"role":"shadow"}, ... ],
  "embedding": [0.031, -0.114, ...]
}
```

### 4.1 `effect_budget` — reproducing restraint

The count, not just the presence, of each effect per section.

A style that uses exactly two light leaks in ninety seconds is characterised as much by the eighty-eight seconds *without* them. Without a budget, a naive renderer that has learned "this style uses light leaks" applies them everywhere and produces something that looks nothing like the reference — it looks like a 2013 wedding video. Restraint is a measurable property and we measure it.

---

## 5. Slots

A slot is a hole in the timeline. It carries **requirements**, not a reference to a reference shot.

```jsonc
{
  "index": 7,
  "t_in_ms": 12480, "t_out_ms": 13100,
  "section": 1,
  "importance": 0.8,
  "requirements": {
    "shot_scale": "close", "shot_scale_tolerance": 1,
    "camera_motion": ["pan_left", "tracking"],
    "camera_height": "low",
    "subject_class": ["mechanical_detail", "vehicle"],
    "narrative_role": "detail",
    "composition": "thirds_left",
    "motion_energy": 0.62, "motion_energy_tolerance": 0.25,
    "motion_direction_deg": -175,
    "min_quality": 0.55,
    "requires_face": false,
    "semantic_vec": [ ... ],
    "semantic_hint": "low-angle detail of a wheel in motion, shallow depth of field"
  },
  "allow_reuse": true, "reuse_penalty": 0.35,
  "droppable": true,
  "assignment": null
}
```

### 5.1 Why requirements rather than references

This is the design decision that makes the whole product work, and it is worth stating plainly.

A naive design stores "slot 7 = reference shot 7" and asks the matcher for "the user clip most similar to reference shot 7." That fails for the exact case the product exists to serve: the reference is a car video and the user shot a motorcycle. Nothing in their footage is similar to a car wheel.

Storing *requirements* — `{close, low, mechanical_detail, motion_energy 0.62, thirds_left}` — turns the question into "which of the user's segments is a low-angle mechanical detail shot with moderate motion?" An exhaust close-up satisfies that. A car-wheel-similarity search does not.

The requirement is the abstraction over the reference shot. `semantic_hint` and `semantic_vec` are retained for VLM re-ranking among candidates that already satisfy the structural requirements — the soft tiebreak, never the hard filter.

### 5.2 `importance` and `droppable`

Under insufficient footage, something has to give. `importance` orders what gives last: the shot on the drop is 1.0; a mid-verse cutaway is 0.3. `droppable: false` marks slots whose removal would break the structure — the hook, the payoff. Degradation is a specified behaviour, not an emergent one.

### 5.3 `motion_direction_deg`

Present because directional transitions need it. A whip-pan transition between slots 7 and 8 only reads correctly if slot 7's outgoing motion and slot 8's incoming motion share a direction. Without this field the renderer would apply a left-to-right whip between two static shots, which looks like a mistake — because it is one.

---

## 6. Cuts and transitions

### 6.1 The cut object, and the negative offset

```jsonc
{
  "index": 14, "t_ms": 22362,
  "mode": "impact",
  "beat_index": 48, "offset_ms": -38,
  "subdivision": "1",
  "hide_in_motion": false,
  "from_slot": 13, "to_slot": 14
}
```

`offset_ms: -38` means the cut lands **38 milliseconds before the beat**.

This is not noise, and it is not an analysis error. It is one of the most valuable things we extract.

Expert editors consistently cut slightly *ahead* of the transient. Visual perception lags auditory perception, and a cut placed exactly on the beat is perceived as arriving late — it feels heavy and slightly behind the music. Placing it 20–60ms early makes the cut and the beat feel simultaneous.

A system that snaps every cut to the grid produces edits that are technically beat-synchronised and feel mechanical. Preserving the reference's own offset *distribution* — mean and standard deviation, sampled per cut — is a large part of why output reads as professionally cut. Full treatment in [docs/08 §2.3](08-algorithms.md#23-the-negative-offset-and-why-it-matters).

**Cut modes:**

| Mode | Meaning |
|---|---|
| `on_beat` | Anchored to a beat, offset applied |
| `subdivided` | Anchored to a subdivision (1/2, 1/3, 1/4, 1/8) |
| `free` | Deliberately off-grid — content-driven |
| `impact` | Anchored to an `audio.impacts` entry, which overrides the beat grid |

Roughly 15% of cuts in good edits are `free`. A blueprint with `beat_lock_ratio: 1.0` describes a robot.

`hide_in_motion` permits the renderer to nudge the cut ±3 frames onto a local motion-energy peak — the classic trick of hiding a cut inside movement.

### 6.2 Transitions

```jsonc
{
  "at_cut": 14,
  "type": "whip_pan",
  "secondary": ["motion_blur"],
  "duration_ms": 180,
  "intensity": 0.75,
  "direction_deg": -180,
  "easing": "ease_in_out",
  "align": "centered",
  "params": { "blur_samples": 24, "stretch": 1.4 },
  "confidence": 0.88,
  "fallback": "flash"
}
```

Three fields deserve comment.

`secondary` exists because real transitions are composites. A whip pan *is* a motion blur. Modelling them as mutually exclusive classes would force the renderer to pick one and lose the other.

`align` says where the cut sits inside the transition interval — centred, entirely on the outgoing clip, or entirely on the incoming clip. Getting this wrong shifts everything by half the transition duration, which on a 400ms dissolve is very visible.

`fallback` is the graceful-degradation path. If no user segment has the lateral motion a whip pan requires, the renderer substitutes `flash` and records a `transition_substituted` compromise — rather than rendering a whip pan between two static shots, which reads as a bug.

---

## 7. Provenance

```jsonc
"provenance": {
  "analyzer_version": "1.4.2",
  "planner_model": "gemini-2.5-pro",
  "planner_tier": "frontier",
  "planner_seed": 42,
  "renderer_min_version": "2.0.0",
  "source_fingerprint": "a91f...",     // perceptual hash, NOT a copy
  "source_duration_ms": 45000,
  "confidence": {
    "overall": 0.83, "beat_grid": 0.96, "structure": 0.91,
    "transitions": 0.79, "grade": 0.58, "speed": 0.64, "captions": 0.88
  },
  "notes": ["Reference bitrate low (1.8 Mbps); grade estimate is approximate."]
}
```

`renderer_min_version` lets a blueprint declare that it uses features an older renderer cannot execute. A renderer below the minimum refuses the job instead of silently ignoring the parts it does not understand — a failure mode that would otherwise produce output that is wrong in ways nobody notices.

Confidence is **per-subsystem** because it varies enormously within one blueprint. Beat grid at 0.96 and grade at 0.58 is the normal case, not an anomaly: rhythm is measurable, grade is inferred. The UI shows "grade: approximate" and offers manual adjustment for exactly this reason.

---

## 8–13. Remaining sections

Fully specified in the schema; summarised here.

**§8 Motion** — synthetic camera motion as keyframed property tracks (`scale`, `pos_x`, `pos_y`, `rotation`). `relative_to_subject: true` anchors on the tracked subject centroid instead of frame centre, which is what makes a slow push-in stay on the subject rather than drifting off them.

**§9 Speed** — `constant`, `ramp` (keyframed speed factor), `freeze`, `reverse`, `timelapse`, `hyperlapse`. `interpolation: optical_flow` produces the smoothest slow motion and is expensive, so it is reserved for Pro export. Carries `confidence`, because speed inference from delivered video is an estimate ([docs/04 stage 3](04-ai-pipeline.md#stage-3--motion-analysis)).

**§10 Effects** — instances scoped `global | section | slot | range`, with keyframable intensity and a blend mode. Governed by `style.effect_budget`.

**§11 Grade** — parametric (`GradeParams`: exposure, contrast+pivot, saturation, vibrance, temp/tint, lift/gamma/gain, shadows/highlights/whites/blacks, split-tone, per-band HSL) plus a `match_target` block of measured colour statistics. The renderer optimises the user's footage *toward the statistics* rather than blindly applying the parameters — because the user's footage has a different starting point, and applying a grade designed for someone else's exposure is how you get crushed blacks. `lut_ref` may point at a **licensed** LUT from our library that approximates the look; it is never a LUT extracted from the reference. See [docs/08 §5](08-algorithms.md#5-colour-grade-inversion).

**§12 Captions** — `mode` (`word_by_word`, `karaoke`, `phrase`, `line`, `static`), `TextStyle`, `TextAnimation` for entry and exit, `active_word_style` for karaoke, and `lead_ms` for offsetting from ASR timestamps. `font_family` is a **classified family** mapped to a licensed font, not an identified typeface — exact font identification from compressed video is unreliable and claiming otherwise would be dishonest.

**§12.3 Text objects** — titles and graphics, distinct from speech captions. `content` is `null` by default with a `placeholder`: **we never copy the reference's words.** The style of the title transfers; the words are the user's.

**§13 Reframe** — how source footage maps into the canvas. `fill_subject` and `track_subject` use segmentation masks rather than centre-crop. `smoothing` (default 0.7) is the virtual camera's laziness; without it, per-frame mask noise produces visible jitter, which is the single most common failure in auto-reframing products.

---

## 14. Constraints

Invariants the renderer must not violate. These are what stop degradation from producing garbage.

```jsonc
"constraints": {
  "min_shot_ms": 180,                    // below this a shot isn't perceived as a shot
  "max_shot_ms": 8000,
  "max_consecutive_same_scale": 2,       // three closeups in a row reads as an error
  "max_consecutive_same_source": 1,      // prevents accidental jump cuts
  "max_segment_reuse": 3,
  "min_reuse_gap_ms": 4000,              // reuse is invisible if far apart, obvious if close
  "forbid_jump_cut_same_source": true,
  "require_licensed_audio": true,        // const in schema; not configurable
  "max_effect_layers": 4
}
```

`min_reuse_gap_ms` encodes something a human editor knows implicitly: reusing a shot is fine if the two uses are far apart and jarring if they are adjacent. With 12 usable segments and 30 slots, reuse is unavoidable — the constraint makes it invisible rather than embarrassing.

---

## 15. Degradation

Populated by the renderer when the blueprint could not be fully realised.

```jsonc
"degradation": {
  "degraded": true,
  "coverage": 0.78,
  "compromises": [
    {"kind":"transition_substituted", "slot":14, "severity":"minor",
     "detail":"No segment with lateral motion for whip pan; used flash."},
    {"kind":"slot_dropped", "slot":22, "severity":"moderate",
     "detail":"No candidate met the wide-shot requirement above quality 0.55."}
  ]
}
```

Surfaced in the UI and stamped into export metadata. **A degraded render that does not say it is degraded is the worst output this system can produce** — worse than a failure, because the user ships it and blames their footage or their own eye.

---

## 16. Versioning and evolution

`ebp_version` is a hard const. Breaking changes bump the major version and ship a migration in `services/common/migrations/`.

**Additive changes (minor):** new optional fields, new enum members with defined fallbacks, new effect types. Old renderers handle these via `renderer_min_version`.

**Breaking changes (major):** field removal, semantic changes, required-field additions. Every stored blueprint is migrated forward in a batch job. Because blueprints are kilobytes and we keep them forever, migration is always feasible — a deliberate consequence of the storage decision in [docs/03 §3.5](03-system-architecture.md#35-data-stores).

The stability of this format is a strategic asset. If EBP becomes the format other tools import and export ([docs/01 §7](01-product-vision.md#7-success-defined)), the schema is the standard and we own it.

---

Next: [07 — Model Recommendations](07-model-recommendations.md)
