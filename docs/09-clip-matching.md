# 09 — Clip Matching

**The hardest and most valuable component in the system.** It has the least prior art, the largest user-visible impact, and the only training signal that competitors cannot buy.

---

## 1. The problem

The blueprint has 25 slots. The user uploaded 24 clips, which segment into 61 usable ranges. Assign segments to slots.

The naive answer — use clips in order — fails immediately and obviously. But so do most of the sophisticated-sounding answers.

### 1.1 Why nearest-neighbour on embeddings fails

The instinct is: embed each slot's reference shot, embed each user segment, assign each slot its nearest neighbour.

This fails on the **exact case the product exists to serve.**

```
REFERENCE (a car video)            USER FOOTAGE (motorcycles)
  slot 0  car, wide, rolling         seg A  motorcycle, wide, rolling
  slot 1  car interior, close        seg B  helmet POV, close
  slot 2  wheel detail, low          seg C  exhaust detail, low
  slot 3  driver face, medium        seg D  rider, medium
```

CLIP similarity between "car wheel close-up" and "motorcycle exhaust close-up" is **low**. They are different objects. A nearest-neighbour matcher would rather assign the user's *motorcycle wide shot* to the wheel-detail slot, because a motorcycle is more car-like than an exhaust pipe is.

That is exactly backwards. The editorially correct answer is `slot 2 → seg C`, because both are **low-angle mechanical detail shots with high-frequency motion**. Their *editorial role* is identical; their *semantic content* is not.

Semantic similarity is the wrong objective. This is the central insight of the whole component.

### 1.2 Why greedy per-slot assignment fails

Even with the right similarity function, assigning each slot its best candidate independently produces:

- The same excellent segment assigned to eight slots
- Three consecutive close-ups because each individually scored well
- The best action shot burned in slot 3 instead of saved for the drop at slot 14
- Early slots taking the good footage; late slots getting scraps

Editing is a **sequence** problem. A shot's value depends on what precedes it, what follows it, and whether something better is needed later. Greedy assignment cannot see any of that.

---

## 2. Formulation

A **constrained global assignment problem** with sequence-dependent costs.

Given slots `S = {s₀…sₙ}`, candidate segments `C = {c₀…cₘ}`, and an assignment `A: S → C × [in, out]`, maximise:

```
score(A) =  Σ  w_fit  · fit(sᵢ, A(sᵢ))              unary   — does the clip suit the slot
          + Σ  w_seq  · seq(A(sᵢ₋₁), A(sᵢ))          pairwise — do adjacent clips cut together
          + Σ  w_glob · glob(A)                      global  — variety, distribution, reuse
          − Σ  penalty(A)                            constraint violations
```

subject to `constraints` in the blueprint ([docs/06 §14](06-blueprint-spec.md#14-constraints)).

The pairwise term is what makes this hard. With only unary terms it is a linear assignment problem solvable exactly by the Hungarian algorithm in O(n³). With pairwise terms it becomes a quadratic assignment problem — NP-hard in general.

Our structure saves us: **the sequence is a chain, not a graph.** Slot `i` interacts only with `i−1` and `i+1`, never with `i+7`. A chain-structured QAP is solvable exactly by dynamic programming over `(slot, candidate)` states, in O(n · m²).

With n=25 slots and m=40 candidates per slot: 25 × 1600 = 40,000 state transitions. Milliseconds.

---

## 3. Unary fit

How well does one segment suit one slot, ignoring context?

```python
def fit(slot: Slot, seg: Segment) -> float:
    r = slot.requirements

    # --- HARD CONSTRAINTS: fail → candidate excluded entirely ---------------
    if seg.duration_ms < slot.duration_ms:                       return -inf
    if seg.quality < r.min_quality:                              return -inf
    if r.requires_face and not seg.has_face:                     return -inf
    if r.requires_speech and not seg.has_speech:                 return -inf
    if r.shot_scale is not ANY and \
       r.shot_scale.distance(seg.shot_scale) > r.shot_scale_tolerance:
                                                                 return -inf

    # --- SOFT SCORE ---------------------------------------------------------
    s = 0.0

    # Shot scale: exact match best, adjacent bucket acceptable
    s += 0.18 * (1 - r.shot_scale.distance(seg.shot_scale) / 3)

    # Camera motion: class compatibility, not equality
    s += 0.16 * motion_compat(r.camera_motion, seg.camera_motion)

    # Motion energy: the strongest single predictor of whether a clip
    # "feels right" in a slot. Cutting a static shot into a high-energy
    # slot deflates the section audibly.
    d = abs(r.motion_energy - seg.motion_energy)
    s += 0.20 * max(0, 1 - d / max(r.motion_energy_tolerance, 0.05))

    # Subject class: coarse, deliberately. mechanical_detail is the bridge
    # between a car wheel and a motorcycle exhaust.
    s += 0.14 * subject_compat(r.subject_class, seg.subject_class)

    # Composition: consecutive shots that agree compositionally cut cleanly
    s += 0.08 * composition_compat(r.composition, seg.composition)

    s += 0.06 * (1 if r.camera_height in (ANY, seg.camera_height) else 0.3)
    s += 0.10 * seg.quality
    s += 0.08 * cosine(r.semantic_vec, seg.semantic_vec)   # ← soft tiebreak ONLY

    return s
```

**Note the weight on `semantic_vec`: 0.08.** It is a tiebreak among candidates that already satisfy the structural requirements — never a filter. Structure carries 0.92 of the weight. This weighting is the operational expression of §1.1: editorial role dominates semantic content.

### 3.1 Motion energy is the most underrated signal

`motion_energy` — mean optical-flow magnitude normalised per shot — carries 0.20, the largest single weight, and it earns it.

A slot in the drop section wants motion. Assigning a beautiful, perfectly-composed static shot there deflates the section in a way viewers feel immediately without being able to name. Conversely, a frantic handheld shot in a quiet intro slot reads as an error.

Motion energy transfers across content domains perfectly. A car at 60mph and a motorcycle at 60mph have the same motion energy. A wheel spinning and an exhaust vibrating have similar high-frequency motion signatures. **It is the most domain-invariant editorial signal we have** — which is exactly what a cross-domain style-transfer product needs.

### 3.2 Choosing the in/out point

Fit assumes we know *which part* of the segment to use. We do not — so we optimise it jointly:

```python
def best_window(slot, seg) -> tuple[int, int, float]:
    best = (0, slot.duration_ms, -inf)
    for start in range(seg.usable_in, seg.usable_out - slot.duration_ms, 100):
        window = (start, start + slot.duration_ms)
        score = (
            0.5 * mean_quality(seg, window)
          + 0.3 * (1 - abs(mean_motion(seg, window) - slot.requirements.motion_energy))
          + 0.2 * subject_presence(seg, window)
          - 0.4 * crosses_internal_cut(seg, window)   # never straddle a sub-shot boundary
        )
        if score > best[2]:
            best = (*window, score)
    return best
```

`crosses_internal_cut` is a heavy penalty because a user "clip" often contains several shots. Straddling an internal boundary means the rendered slot contains a cut we did not plan — which destroys the beat alignment for that slot and is instantly visible.

---

## 4. Sequence-level objective

How well do two assignments cut together?

```python
def seq(prev: Assignment, curr: Assignment, blueprint) -> float:
    s = 0.0

    # --- CONTRAST: the core principle of shot sequencing --------------------
    # Consecutive shots should differ in scale. Same-scale cuts read as
    # mistakes; a 2+ bucket change reads as deliberate.
    scale_delta = prev.shot_scale.distance(curr.shot_scale)
    if   scale_delta == 0: s -= 0.35
    elif scale_delta == 1: s += 0.05
    elif scale_delta >= 2: s += 0.20

    # --- 30-DEGREE RULE ----------------------------------------------------
    # Cutting between two shots of the same subject from near-identical angles
    # is the definition of a jump cut. Either change angle meaningfully or don't cut.
    if prev.source_clip == curr.source_clip:
        if angular_distance(prev.camera_angle, curr.camera_angle) < 30:
            s -= 0.80

    # --- MOTION CONTINUITY -------------------------------------------------
    # Matching motion direction across a cut makes it invisible. Opposing
    # direction makes it jarring -- which is sometimes exactly what you want
    # on a hard beat, and never what you want mid-phrase.
    if prev.motion_direction and curr.motion_direction:
        agreement = cos(radians(prev.motion_direction - curr.motion_direction))
        transition = blueprint.transition_at(curr.slot_index - 1)
        if transition and transition.type.needs_direction:
            s += 0.30 * agreement          # whip pans REQUIRE agreement
        else:
            s += 0.10 * agreement

    # --- EXPOSURE / COLOUR CONTINUITY --------------------------------------
    # A large luminance jump across a cut reads as an error unless the cut
    # lands on a hard beat, where it reads as intentional.
    luma_jump = abs(prev.mean_luma - curr.mean_luma)
    on_impact = blueprint.cuts[curr.slot_index - 1].mode == CutMode.IMPACT
    s -= (0.10 if on_impact else 0.30) * luma_jump

    # --- SUBJECT CONTINUITY ------------------------------------------------
    if prev.subject_id and prev.subject_id == curr.subject_id:
        s += 0.12                          # following one subject reads as narrative

    return s
```

**The 30-degree rule penalty (−0.80) is nearly disqualifying, and that is correct.** Cutting between two near-identical angles of the same subject is the single most amateur-looking mistake in editing. It is also exactly what a naive matcher does constantly, because two segments from the same clip score similarly on every other axis and therefore look like equally good candidates.

---

## 5. Global terms and the solver

### 5.1 Global objective

Terms that cannot be expressed pairwise:

```python
def glob(assignment, blueprint) -> float:
    s = 0.0

    # Variety: the distribution of shot scales should approximate the
    # reference's own mix, not just avoid local repetition
    s -= 0.25 * kl_divergence(scale_dist(assignment), blueprint.style.shot_scale_mix)

    # Reuse: unavoidable with 12 segments and 30 slots. Make it invisible.
    for seg_id, uses in group_by_segment(assignment):
        if len(uses) > blueprint.constraints.max_segment_reuse: s -= 1.0
        for a, b in zip(uses, uses[1:]):
            gap = b.t_in_ms - a.t_out_ms
            if gap < blueprint.constraints.min_reuse_gap_ms:
                s -= 0.6 * (1 - gap / blueprint.constraints.min_reuse_gap_ms)
            if overlapping_windows(a, b):
                s -= 0.4                    # reusing the SAME frames is very visible

    # Coverage: unfilled slots are costly in proportion to importance
    for slot in blueprint.slots:
        if slot.index not in assignment:
            s -= 1.2 * slot.importance

    # Save the best for the biggest moment. Without this, the strongest shot
    # gets spent in slot 3 and the drop lands on something mediocre.
    peak = max(blueprint.slots, key=lambda x: x.importance)
    if peak.index in assignment:
        s += 0.5 * assignment[peak.index].intrinsic_quality

    return s
```

### 5.2 The solver

```
STAGE 1 — CANDIDATE RETRIEVAL             filtered ANN over Qdrant
  per slot: top-40 by semantic vector, filtered on project, duration,
  quality, compatible scale                                    ~0.3s

STAGE 2 — UNARY SCORING                   fit() for each (slot, candidate)
  25 × 40 = 1,000 evaluations, vectorised                      ~0.1s

STAGE 3 — CHAIN DP                        exact solution of the chain QAP
  V[i][c] = max over c' of ( V[i-1][c'] + seq(c', c) ) + fit(i, c)
  25 × 40 × 40 = 40,000 transitions                            ~0.05s

STAGE 4 — GLOBAL REPAIR                   local search on the DP solution
  DP cannot see global terms (variety, reuse spacing, peak saving).
  Hill-climb with swap and reassign moves until no improvement or 200 iters.
  ~0.15s

STAGE 5 — WINDOW REFINEMENT               best_window() per assigned slot
  ~0.05s

STAGE 6 — VLM RE-RANK (Pro tier only)     top-3 alternatives for the
  highest-importance slots, adjudicated by a VLM against semantic_hint.
  Catches cases where structure agrees but content is wrong -- e.g. a
  technically-perfect detail shot of the wrong object.       ~1.2s
```

**Total: ~0.6s standard, ~1.8s Pro.**

**Why DP then repair, rather than a general QAP solver.** The chain DP gives an exact optimum for the unary+pairwise objective in milliseconds. Global terms are then handled by local search from an already-excellent starting point, which converges in tens of iterations. A general solver would handle all three term types at once, take seconds to minutes, and produce a marginally better answer — a bad trade when a user is waiting.

---

## 6. Learning from swaps

**This is the moat.**

Every time a user opens a slot and swaps clip A for clip B, we log:

```json
{
  "slot_features":   { "shot_scale": "close", "motion_energy": 0.62, ... },
  "rejected":        { "segment_id": "seg_a1", "features": {...}, "our_score": 0.87 },
  "chosen":          { "segment_id": "seg_c4", "features": {...}, "our_score": 0.71 },
  "position_in_ranking": 3,
  "blueprint_id": "bp_...", "user_id": "...", "ts": "..."
}
```

That is a **preference pair labelled by a domain expert at the moment of peak engagement**, generated for free, at volume.

### 6.1 What we train

**Phase 1 — reweight the fit function.** Learn the weights in §3 by logistic regression on preference pairs. Requires ~5k swaps. Immediately better than hand-tuned weights, and fully interpretable, which matters for debugging.

**Phase 2 — learn the projection head.** Train a small MLP on top of frozen CLIP features with a contrastive objective over preference pairs. This produces the **editorial equivalence space** described in [docs/07 §8](07-model-recommendations.md#8-embeddings-for-matching) — where a car wheel and a motorcycle exhaust land close together because they play the same role, not because they look alike. Requires ~50k swaps. This is the step that fixes §1.1 properly rather than working around it.

**Phase 3 — learn the sequence model.** A small transformer over the assignment sequence, trained to predict which full assignment a user would accept without edits. Requires ~200k complete sessions.

### 6.2 Why competitors cannot replicate this

The data does not exist anywhere and cannot be bought. It requires:

1. A product where users assemble edits from their own footage against a specification
2. A UI that makes correcting the machine pleasant enough that people do it
3. Enough users doing it enough times

Anyone starting in month 12 starts at zero swaps against our hundreds of thousands. This is why the swap UI must be *good* rather than merely present, and why every assignment carries a `reason` — an explained choice produces an informative correction, an unexplained one produces a random click.

**Every swap is worth more to us than the render.**

---

## 7. Insufficiency and graceful degradation

When there is genuinely not enough footage, the system must fail loudly. Three tiers:

```python
if coverage >= 0.85:   render normally
elif coverage >= 0.55: DEGRADE, warn, require explicit acceptance
else:                  BLOCK, name what is missing, offer to shoot it
```

### 7.1 Degradation ladder

Applied in this order, each recorded as a `Compromise`:

1. **Relax soft constraints.** Widen scale tolerance to ±2, motion-energy tolerance to 0.4. Cheapest, least visible.
2. **Substitute transitions.** Whip pan → flash where lateral motion is absent ([docs/08 §4.2](08-algorithms.md#42-selection-at-render-time)).
3. **Increase reuse.** Raise `max_segment_reuse`, using different windows and honouring `min_reuse_gap_ms`. Different in/out points of the same segment read as different shots if far enough apart.
4. **Drop low-importance slots.** Only `droppable: true`; merge adjacent slots and extend the survivor. Recompute cuts against the beat grid so rhythm survives the change — this is the step most likely to be done badly, because naively deleting a slot leaves a gap that breaks every subsequent beat alignment.
5. **Compress the blueprint.** Reduce total duration by dropping whole low-energy sections. Structural, and always flagged `major`.

### 7.2 What we never do

**Stretch a clip beyond its usable range.** The shaky half-second where someone pressed record is excluded from `usable_ranges` for a reason. Reaching into it because we are short of footage produces exactly the amateur look the product exists to eliminate.

**Silently repeat a segment back-to-back.** Immediately visible; reads as a bug, not a style.

**Render at coverage < 0.55 without explicit acceptance.** The user gets a specific, actionable list of what to shoot ([docs/08 §9](08-algorithms.md#9-coverage-and-insufficiency-detection)) — and a shoot list is a *feature*, arguably one of the most valuable things the product does. It teaches the user what the style needs.

---

## 8. Evaluation

How we know the matcher works, and what would tell us it does not:

| Metric | Method | Target | Baseline |
|---|---|---|---|
| **Human preference** | Blind A/B, matched vs. sequential, 200 raters | >75% | 50% |
| **Swap rate** | Slots swapped / slots rendered | <15% | — |
| **First-render acceptance** | Exported with zero edits | >45% | — |
| **Top-1 agreement** | Our pick = user's final pick | >70% | — |
| **Constraint violations** | Post-hoc check | 0 | — |
| **Solve time p95** | Instrumented | <2s | — |

**Swap rate is the honest metric and it points two ways.** A low swap rate means either the matcher is good or the UI is bad. We disambiguate by correlating swap rate against export rate: users who swap and then export are engaged; users who neither swap nor export were disappointed and left. The metric that actually matters is *first-render acceptance* — exported with zero edits.

The kill criterion from [docs/00](00-executive-summary.md#what-has-to-be-true) is a blind-rater preference below 60% for matched over sequential at week 14. If intelligent matching cannot beat using clips in order, the central technical claim of the product is false and we should know cheaply.

---

Next: [10 — Rendering Engine](10-rendering-engine.md)
