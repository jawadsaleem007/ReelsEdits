# 01 — Product Vision

## 1. The core principle: style, not content

Everything in this system follows from one distinction, and getting it wrong — technically, legally, or in the UI — destroys the product.

**Content** is what the reference video *is*: its frames, its audio, its subject, its script, its specific arrangement of specific shots. Content is authored, owned, and protected.

**Style** is *how the reference was assembled*: the statistical and structural signature of its editing decisions. Cut density. The relationship between cut points and the beat grid. The transition vocabulary and its frequency distribution. Speed-ramp curve shapes. Colour-grade direction. Caption kinetics. The order in which shot scales appear across a narrative arc.

An analogy that holds up under pressure: a reference video is a *performance*, and the blueprint is a *transcription of the performance's technique* — not a recording of it. You can write down that a guitarist used a triplet feel, palm-muted the low E, and modulated up a semitone at the bridge. Writing that down is not distributing the song.

The engineering consequence is a hard architectural rule:

> **No pixel and no audio sample from the reference video ever reaches the output, ever appears in the blueprint, and ever leaves the analysis worker.**

The blueprint is numbers and enums. Its size is measured in tens of kilobytes. This is enforced by schema — the EBP has no field capable of holding image or audio data — and by a render-time assertion that the render's source manifest contains only user-uploaded and licensed-catalogue assets. See [docs/06](06-blueprint-spec.md) and [docs/18 §3](18-legal-ethics.md).

## 2. Who this is for

### Primary: the ambitious solo creator

Twenty-three, posts three to five short videos a week to Instagram Reels, TikTok and YouTube Shorts. Shoots on a phone or an entry-level mirrorless. Has 8k–90k followers and is trying to get to the point where this is a job. Currently edits in CapCut, spends 90–180 minutes per video, and knows their edits are the weak link — they can *see* that a creator they follow has better rhythm and cannot articulate why.

This person has already tried to reverse-engineer edits manually. They have scrubbed frame by frame through someone else's Reel. The product is not teaching them a new behaviour; it is automating one they already perform badly.

**What they'll pay:** $29/month, immediately, if the output is convincingly better than what they make by hand. They will not pay for marginal improvement.

### Secondary: the small agency / social team

Three to fifteen people producing short-form for brand clients. Their problem is not craft — they have craft — it is **consistency at volume**. A client's brand has a look, and every editor on the team renders it slightly differently. A blueprint is a *specification*, which means brand style becomes enforceable rather than aspirational.

**What they'll pay:** $249/month for a team, growing to enterprise, because the alternative is a headcount.

### Tertiary: platforms and tools

Anyone with a video product who needs an editing engine but does not want to build one. This is the API tier, and it becomes meaningful once the render engine is deterministic and the blueprint format is stable enough to be a public contract.

### Explicitly not for

Narrative filmmakers, documentary editors, anyone whose edit is driven by story logic rather than kinetic and rhythmic structure. The blueprint models pacing and craft, not meaning. A three-minute documentary sequence about grief is not a beat-synced pacing problem, and the product should not pretend otherwise.

## 3. The user journey

### 3.1 First run — "make my footage look like this"

```
 ┌──────────────────────────────────────────────────────────────────┐
 │ 1. PASTE OR UPLOAD REFERENCE                                     │
 │    A TikTok/Reel URL, or a file. 5–120s.                         │
 │    → "Analysing this edit…"  (~35–70s, or ~2s if cached)         │
 └──────────────────────────────────────────────────────────────────┘
                              ↓
 ┌──────────────────────────────────────────────────────────────────┐
 │ 2. STYLE CARD                                                    │
 │    Not a progress bar — a *readable description of the style*:   │
 │                                                                   │
 │      Pacing        Fast · 1.9 cuts/sec · 84% beat-locked         │
 │      Structure     4 sections · builds to drop at 0:22           │
 │      Transitions   Hard cut 71% · whip pan 14% · flash 9%        │
 │      Motion        Heavy push-in, 6 speed ramps                  │
 │      Grade         Teal shadows, warm highlights, +18% contrast  │
 │      Captions      Word-by-word, centre-low, bounce-in           │
 │      Shot mix      Wide 22% · medium 41% · detail 37%            │
 │                                                                   │
 │    [Use this style]     [Save to my library]                     │
 └──────────────────────────────────────────────────────────────────┘
```

**The style card is the product's most important screen.** It is where the user decides whether to trust the system. It proves we understood the reference in terms they recognise, and it does so *before* they have invested any footage. It also reframes the interaction from "black box magic" to "here is a specification you can inspect and edit" — which is what makes the later editing affordances feel natural rather than like error correction.

```
                              ↓
 ┌──────────────────────────────────────────────────────────────────┐
 │ 3. UPLOAD YOUR CLIPS                                             │
 │    Drag 5–40 clips. Live coverage meter as they index:           │
 │                                                                   │
 │      Coverage for this style          ████████░░  78%            │
 │      ✓ 6 detail shots    ✓ 4 wide     ✓ 9 medium                 │
 │      ⚠ No shots with strong lateral motion — the style uses      │
 │        3 whip-pan transitions that need them. Add one, or        │
 │        we'll substitute flash cuts.        [Add clip] [Substitute]│
 └──────────────────────────────────────────────────────────────────┘
```

The coverage meter is the honesty mechanism from [README caveat 3](../README.md). It runs *during* upload, before any render is committed, and it names the specific missing thing rather than a generic warning. A user who is told "you need a shot that moves left-to-right fast" can go shoot one in ten minutes. A user who is told "insufficient footage" churns.

```
                              ↓
 ┌──────────────────────────────────────────────────────────────────┐
 │ 4. MUSIC                                                          │
 │    We can't use the reference's track. Here are 6 licensed        │
 │    tracks with the same tempo (128 BPM) and structure             │
 │    (16-bar build → drop at 0:22 → outro):                         │
 │      ▸ "Nightline"  128 BPM  drop 0:21  ████████░ 94% match      │
 │      ▸ "Vector"     127 BPM  drop 0:23  ███████░░ 89% match      │
 │      … or upload your own licensed track                          │
 └──────────────────────────────────────────────────────────────────┘
                              ↓
 ┌──────────────────────────────────────────────────────────────────┐
 │ 5. PREVIEW  (~40–90s render)                                      │
 │    Scrubbable timeline. Every cut, transition, ramp and caption   │
 │    is a clickable object that opens exactly one control.          │
 │    Swap any clip: click the slot → ranked alternatives from your  │
 │    footage, with the reason each was ranked.                      │
 └──────────────────────────────────────────────────────────────────┘
                              ↓
 ┌──────────────────────────────────────────────────────────────────┐
 │ 6. EXPORT   1080×1920 · 4K · platform presets · project file      │
 └──────────────────────────────────────────────────────────────────┘
```

### 3.2 The important detail about swapping clips

When a user clicks a slot and swaps clip A for clip B, we log `(slot_features, clip_A_features, clip_B_features, chose_B)`. That is a preference pair, generated by a domain expert, for free, at the exact moment they are most engaged. It is the training signal for the matcher described in [docs/09 §6](09-clip-matching.md).

This is why the swap UI must be *pleasant* rather than merely present. Every swap is worth more to us than the render.

## 4. What "an expert human editor" actually means here

The brief asks for the AI to "behave like an expert human video editor." That phrase is doing a lot of work, so here is the concrete decomposition we build against. An expert editor:

| Expert behaviour | Our mechanism | Doc |
|---|---|---|
| Feels where a cut should land | Beat-grid quantisation with learned offset distribution — expert cuts land 20–60ms *before* the transient, not on it | [08 §2](08-algorithms.md#2-beat-grid-and-cut-quantisation) |
| Varies pace deliberately | Section-level cut-density model tied to the audio energy envelope | [08 §3](08-algorithms.md#3-pacing-model) |
| Chooses shots for contrast | Matcher penalises consecutive same-scale, same-motion assignments | [09 §4](09-clip-matching.md#4-sequence-level-objective) |
| Hides cuts in motion | Cut-point refinement searches ±3 frames for a motion-energy peak | [08 §2.4](08-algorithms.md#24-micro-placement-refinement) |
| Matches transition to content | Transition selection conditioned on outgoing/incoming motion direction | [08 §4](08-algorithms.md#4-transition-modelling) |
| Doesn't repeat themselves | Global effect-budget constraint per blueprint section | [08 §6](08-algorithms.md#6-effect-budget) |
| Knows when to break the grid | Explicit `free` cut-mode on ~15% of cuts, sampled from the reference's own off-grid distribution | [06 §5](06-blueprint-spec.md) |
| Cuts to the *subject*, not the frame | Reframing driven by SAM 3 subject masks, not centre-crop | [10 §4](10-rendering-engine.md) |

The claim is not that a model has taste. The claim is that a large fraction of what reads as taste in short-form editing is **structural regularity that can be measured in a reference and reproduced under constraints**. The residual — the genuinely authorial 20% — is what the swap UI and blueprint editing exist to let the human supply.

## 5. Design principles

**The blueprint is always visible and always editable.** Nothing the system decides is hidden. Every automatic decision has a control. Users who never open them still benefit from their existence, because the product reads as a tool rather than a slot machine.

**Fail before rendering, never after.** Coverage checks, music availability, resolution mismatches, and moderation all run pre-render. A user who waits 90 seconds for a bad render has been robbed twice.

**Deterministic renders.** The same blueprint plus the same assets produces a bit-identical output. This is required for caching, for the marketplace, for collaboration, for the API, and for debugging. It rules out putting a sampling LLM anywhere in the render path — the LLM sits in the *planner*, produces a blueprint, and then gets out of the way.

**Degrade to something usable.** When we cannot do the sophisticated thing, we do the simple thing and label it. A missing whip-pan becomes a flash cut with a note, not a crash and not a silent substitution.

**Speed is a feature with a hard number.** Reference analysis under 75 seconds cold and under 5 seconds warm. Preview render under 90 seconds for 60 seconds of output. Beyond that, users leave the tab.

## 6. What we refuse to build

Stated as product policy because each of these is a plausible-sounding feature that would damage the company:

**Frame-level reproduction of the reference.** No feature that outputs the reference's actual footage, upscaled, restyled, or interpolated. This is the line between style transfer and infringement and there is no version of crossing it that is worth the revenue.

**Reference audio passthrough.** No "use original sound" toggle, even where a platform's own API might make it technically available. See [docs/18 §4](18-legal-ethics.md).

**Face swapping, face reenactment, or voice cloning of a person from the reference.** Not because the technology is unavailable but because a tool that takes "a video of a person" plus "a video of another person" as its two inputs must be architecturally incapable of combining their identities. See [docs/18 §6](18-legal-ethics.md).

**Engagement-optimising content suggestions.** We optimise craft, not attention capture. We will not ship "add a hook here to increase watch time" — that is a different product with different externalities.

**Unlabelled generative footage.** When we eventually generate B-roll ([docs/19](19-future-roadmap.md)), it carries C2PA provenance and is disclosed in the UI and in the exported file's metadata.

## 7. Success, defined

**Six months:** A creator in our beta vertical uses ReelsEdits weekly and stops opening CapCut for the edits it covers. Blind raters prefer our output to their own previous work at >70%.

**Eighteen months:** "ReelsEdits it" is how creators in at least two verticals describe the act of restyling footage. The style marketplace has 500+ paid blueprints and pays out meaningfully to their authors.

**Three years:** The Editing Blueprint is a format other tools import and export, because it is the only structured, renderer-agnostic representation of an edit that anyone has bothered to standardise.

---

Next: [02 — Competitive Analysis](02-competitive-analysis.md)
