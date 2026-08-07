# 16 — Risks

Ordered by expected damage, not by likelihood. Each risk states the failure mode concretely, the early warning signal, the mitigation, and — where relevant — what would make us stop.

---

## Tier 1 — Existential

### R1. The output is not good enough

**The risk:** Style transfer produces something that is technically correct and viscerally wrong. Cuts land on beats, transitions fire, the grade matches — and it reads as machine-made. Users try it once and do not come back.

This is the risk. Everything else in this document is survivable.

**Why it is plausible:** editing quality is perceptual and holistic. A system can satisfy every measurable constraint and still produce output that a viewer recognises as automated within three seconds, for reasons neither they nor we can articulate.

**Early signal:** first-render acceptance below 25%. Users who preview and never export. High swap rates *combined with* low export rates — that combination means the output is wrong in ways users cannot fix by swapping clips.

**Mitigation:**
- Week 14 blind A/B against human editors, before spending real money ([docs/15 §2](15-engineering-roadmap.md#2-phase-0--prove-it-weeks-114))
- The negative cut offset ([docs/08 §2.3](08-algorithms.md#23-the-negative-offset-and-why-it-matters)) and offset *variance*, which is a large part of the mechanical/performed distinction
- Contrast constraints and the 30-degree rule in the sequence objective
- `usable_ranges`, which removes the shaky record-button half-second that reads as amateur
- Effect budgets, so restraint transfers along with vocabulary

**Kill criterion:** <35% blind preference at week 14.

### R2. Nobody wants this

**The risk:** Creators say they want to imitate edits, and when given the tool, do not. The observed behaviour — scrubbing frame-by-frame through someone else's Reel — turns out to be *learning*, which people enjoy, rather than *labour*, which they want automated.

**Why it is plausible:** the identity of "editor" is part of why creators do this. Automating the craft may remove the part they value.

**Early signal:** high signup, low second-session return. Users who complete one render and never start another.

**Mitigation:**
- Position as *craft acceleration*, not replacement — the blueprint is inspectable and editable, so the user remains the author
- The coverage report doubles as a shoot list, which teaches rather than replaces
- Vertical wedge into automotive, where the imitation behaviour is most explicit and most social

**Kill criterion:** <8% free→paid conversion in the automotive beta at week 24.

### R3. Music substitution is unacceptable

**The risk:** Users want *that song*. The rhythmic skeleton bound to a different licensed track feels like a downgrade, and they churn.

**Why this is the assumption I am least confident about:** short-form video culture is substantially *about* specific sounds. A trending audio is often the entire reason a format works. "Same structure, different track" may be a category error.

**Early signal:** users requesting the original track. Low completion after the music step. Explicit churn citations.

**Mitigation, in order of effect:**
- **`platform_attach` is the default** ([docs/18 §4](18-legal-ethics.md)). The user gets the *actual* track by attaching it in-app under the platform's licence, and because we cut to that track's real beat grid it syncs exactly. This addresses the objection directly rather than working around it: the complaint was never "I want a different song", it was "I want *that* song."
- Structural matching so a substitute, where wanted, genuinely *fits* the edit — same BPM, same section layout, drop in the same place
- Deep catalogue partnership, so the alternatives are good rather than merely legal
- `user_supplied` strategy with a rights attestation, for users who have their own licence
- Long-term: platform partnerships where the platform's own licensed library can be bound

**What we will not do:** pass through the reference's audio. There is no version of that which is worth the company. See [docs/18 §4](18-legal-ethics.md).

**Kill criterion:** >40% of churning users cite music at week 20. This would not kill the company but would force a strategic rethink — most likely toward platform partnership or a narrower launch market.

---

## Tier 2 — Severe

### R4. A rights holder sues

**The risk:** A music label, a platform, or a creator sues over reference analysis, arguing that extracting editorial structure is a derivative work or that URL fetching breaches terms.

**Assessment:** Our legal position is reasonable but untested. Editing structure — cut rhythm, tempo maps, transition vocabulary — is much closer to unprotectable method than to protected expression, and the blueprint contains no reference media by construction. But "reasonable and untested" is where litigation risk lives, and a well-funded plaintiff can impose enormous cost without winning.

**Mitigation:**
- No reference media in the blueprint, enforced structurally by `extra="forbid"` and testable at render time by `source_manifest_ok()`
- URL fetching gated per domain; hard 24-hour deletion of fetched references
- All music licensed with per-render licence records and terms snapshots
- Specialist IP counsel engaged before launch, not after a letter arrives
- Insurance and a legal reserve in the seed budget

**Full treatment:** [docs/18](18-legal-ethics.md).

### R5. An incumbent ships it

**The risk:** Submagic adds "style from URL." CapCut adds intelligent assembly. We lose on distribution.

**Assessment:** Real, and Submagic is the most likely. Their obstacle is economics rather than capability — per-reference GPU analysis does not fit a $16/month decoration product without repricing, and repricing is harder than shipping. CapCut has every capability and inverted incentives ([docs/02 §6](02-competitive-analysis.md#6-why-the-incumbents-probably-wont-do-this)).

**Window: 18–24 months.**

**Mitigation:** the compounding assets — blueprint corpus, swap-trained matcher, render determinism. A competitor starting in month 12 starts at zero on all three. Plus vertical depth: being dramatically better for automotive beats being marginally better for everyone.

### R6. GPU costs make it unviable

**The risk:** GPU prices rise, or usage patterns are heavier than modelled, and gross margin collapses.

**Assessment:** [docs/14 §7](14-cost-model.md#7-sensitivity) models the compound worst case — GPU +40%, usage 2×, cache hit 30%, licensing doubled — at 49% gross margin. Survivable, with the levers in §6 mostly untouched.

**Mitigation:** hard per-org and global GPU-second budgets with a circuit breaker; reserved capacity and spot; the distillation path; and a cost ledger on every job so margin erosion is visible in a query rather than in a quarterly surprise.

### R7. Model regression ships silently

**The risk:** A model update subtly degrades output. Nobody notices for weeks. Trust erodes, and by the time it is visible in retention metrics the cause is three deploys back.

**Why this is more dangerous than it sounds:** unlike code, models fail *gradually*. There is no exception, no 500, no alert. Output just gets slightly worse.

**Mitigation:**
- **Golden-set regression on every analyzer deploy** — 200 fixed references, blueprints diffed structurally, deploy blocked on cut-count drift >5%, transition-distribution shift, or grade drift beyond tolerance. This is the single most valuable piece of test infrastructure in the system.
- Determinism tests: render the golden set twice, any byte difference fails CI
- Every artefact records the version that produced it, so attribution is a join
- Feature-flagged model swaps with a 5% canary

---

## Tier 3 — Manageable

### R8. Clip matching does not generalise beyond the launch vertical

Automotive has tight, repeatable visual grammar. Fitness, food and travel may not decompose as cleanly into the slot requirements in [docs/06 §5](06-blueprint-spec.md#5-slots).

**Mitigation:** coarse subject classes with explicit bridges ([`SUBJECT_BRIDGES`](../services/matcher/reelsedits_matcher/scoring.py)); motion energy as the most domain-invariant signal; per-vertical matcher weight tuning; and treating the second vertical as an explicit phase-2 test ([docs/15 §4](15-engineering-roadmap.md#4-phase-2--scale-the-wedge-months-914)).

### R9. Grade transfer disappoints

Users expect exact colour matching; we deliver an approximation with a confidence score.

**Mitigation:** honesty, from the schema up. `grade.confidence` is a first-class field, the UI says "approximate" below 0.6, and manual controls are always available. [docs/08 §5](08-algorithms.md#5-colour-grade-inversion) explains why exactness is not achievable, and a user who understands the constraint is far less disappointed than one who was promised precision.

### R10. Platform policy changes

TikTok/Instagram block automated download, restrict API access, or penalise tool-assisted content.

**Mitigation:** upload-first as the primary path; URL fetching is already a policy-gated convenience rather than a dependency. Platform-agnostic output. C2PA provenance, which positions us as compliant ahead of a requirement that is clearly coming.

### R11. Key-person concentration

Early ML and render work concentrates in two or three heads.

**Mitigation:** documentation-first (this repository is the artefact of that practice); pairing on every load-bearing component; no single-owner subsystems past month 6.

### R12. Deepfake and misuse pressure

The product takes two videos as input, and there will be pressure — from users and from the market — to combine identities.

**Mitigation:** architectural refusal. The system is *incapable* of face swapping or voice cloning, not merely unwilling ([docs/01 §6](01-product-vision.md#6-what-we-refuse-to-build)). Face identity embeddings are ephemeral and never persisted. C2PA on every output. This is a case where the right answer is to make the capability absent rather than restricted.

---

## Risk matrix

| ID | Risk | Likelihood | Damage | Detected by |
|---|---|---|---|---|
| R1 | Output not good enough | Medium | **Fatal** | Week 14 blind A/B |
| R2 | Nobody wants it | Medium | **Fatal** | Week 24 conversion |
| R3 | Music rejected | Low-medium | Severe | Week 20 churn survey |
| R4 | Rights holder litigation | Low-medium | Severe | Legal review; incident |
| R5 | Incumbent ships it | Medium | Severe | Competitive monitoring |
| R6 | GPU economics | Low | Severe | Cost ledger, weekly |
| R7 | Silent model regression | **High** | Moderate | Golden-set CI |
| R8 | Matching does not generalise | Medium | Moderate | Second-vertical test |
| R9 | Grade disappoints | High | Low | Support volume |
| R10 | Platform policy shift | Medium | Moderate | Policy monitoring |
| R11 | Key-person | Medium | Moderate | Bus-factor audit |
| R12 | Misuse pressure | Medium | Moderate | Abuse reports |

**R7 is the highest-likelihood risk in the table and the one most often left unmitigated.** It is also the cheapest to mitigate — a golden set and a diff. Most teams building on models discover this after the third silent regression.

**R3 was the risk I would most have wanted an outside opinion on**, and `platform_attach` materially reduces it — the user gets the real track, so the market question ("does short-form culture permit substitution at all?") no longer has to be answered in the affirmative for the product to work. The residual risk is narrower: whether the extra step of attaching the sound in-app is friction users tolerate. That is testable in the beta rather than existential.

---

Next: [17 — Business Model](17-business-model.md)
