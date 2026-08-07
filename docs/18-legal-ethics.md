# 18 — Legal & Ethics

> **Not legal advice.** This document sets out the analysis and the engineering commitments that follow from it. It is written to be reviewed by specialist IP and media counsel, not to substitute for them. Several positions below are reasonable but untested, and they are labelled as such.

---

## 1. The central legal question

**Does extracting the editing structure of a video, and applying that structure to different footage, infringe?**

Our position: **no**, for the reasons below. But the honest framing is that this is a reasonable position rather than a settled one, and reasonable-but-untested is exactly where litigation risk lives.

### 1.1 The argument

**Copyright protects expression, not method.** A specific video is a protected work: its frames, its audio, its particular arrangement of particular shots. The *technique* it demonstrates — cutting at 1.9 cuts per second, landing cuts 38ms ahead of the beat, alternating aerial wides with low-angle detail, using a whip pan where lateral motion permits — is closer to unprotectable method, procedure or system.

The analogy that holds up: a chord progression, a cinematographic convention, a rhetorical structure. You can write down that a guitarist used a triplet feel, palm-muted the low E, and modulated up a semitone at the bridge. Writing that down is not distributing the song.

**No protected element is copied.** The Editing Blueprint contains no frames, no audio samples, no LUT extracted from the reference, and none of its words. It contains numbers and enumerated values. Its size is measured in tens of kilobytes. What it describes could be — and routinely is — derived by a human editor watching the video and taking notes.

**The output shares nothing with the reference except technique.** Different footage, different subject, different music, different words.

### 1.2 Where the argument is weakest

Three places, stated plainly because pretending otherwise would be useless to counsel:

**Very short, very distinctive references.** A 10-second video with 4 cuts and one unusual transition — the "structure" and the "expression" converge, and a blueprint of it is close to a description of the whole work.

**Highly stylised, recognisable formats.** Some creators' edits are so distinctive that a faithful structural reproduction reads as an imitation of *them*, and the claim may sound in something other than copyright — passing off, unfair competition, or personality rights depending on jurisdiction.

**Aggregate rather than individual analysis.** Analysing one video for one user is defensible. Systematically ingesting a catalogue to build a corpus for training raises text-and-data-mining questions that vary substantially by jurisdiction (EU TDM exceptions with opt-out, UK's narrower research exception, US fair use analysis).

### 1.3 Engineering commitments that follow

Not policies. Structural properties, enforced in code:

| Commitment | Enforcement |
|---|---|
| No reference media can enter a blueprint | `extra="forbid"` on every model in [`blueprint.py`](../services/common/reelsedits_common/blueprint.py); no field can hold binary data |
| No reference media can reach a render | [`source_manifest_ok()`](../services/renderer/reelsedits_renderer/determinism.py) raises unless every input is a user upload or a licensed asset |
| Reference words are never copied | `TextObject.content` defaults to `None`; the style transfers, the words are the user's |
| We never redistribute the reference's recording | `MusicStrategy` has no member that muxes it into an export; `platform_attach` gives the user the same track via the platform's own licence. Adding a passthrough member is a breaking schema change, and a test asserts the member set. |
| Fetched references are deleted within 24h | `assets.retention_class = 'ephemeral_24h'` + indexed `expires_at` sweeper |
| Extracted audio is deleted at end of analysis | Explicit, tested step in [`AudioStage`](../services/analyzer/reelsedits_analyzer/pipeline.py) |

The distinction matters: a policy is something an engineer can accidentally violate; a schema that cannot represent the violation is something they cannot.

---

## 2. Very short and highly distinctive references

Mitigations for §1.2:

**Minimum duration.** References under 8 seconds are rejected. Below that, structure and expression are not meaningfully separable.

**Complexity floor.** A blueprint with fewer than 6 slots is not stored as a reusable style — it is too close to a description of the specific work.

**Abstraction is mandatory, not optional.** Slots carry *requirements*, never references to reference shots ([docs/06 §5.1](06-blueprint-spec.md#51-why-requirements-rather-than-references)). This exists for product reasons and has the useful property of being the abstraction step that separates method from expression.

**Creator opt-out.** A public registry where creators can request their content be excluded from analysis, honoured by perceptual fingerprint. Costs us little and is the right default.

**Marketplace originality review.** Blueprints listed for sale are reviewed for excessive structural similarity to a single identifiable source.

---

## 3. Music — the largest exposure

**The reference's music is almost always a copyrighted master.** Using it would require both a sync licence and a master licence, per track, per use. Not obtainable at our scale or price point.

### 3.1 The design response

This is handled at the **schema level**, not by policy:

```
Reference audio ──► [ ANALYSIS ] ──► rhythmic skeleton ──► [ BINDING ] ──► licensed track
                         │                                       │
                    audio deleted                         licence_id required
                    at end of stage 1                     or render refuses
```

The blueprint stores BPM, beat grid, downbeats, section structure, and the energy curve — **facts about the recording, not the recording**. Facts are not copyrightable. Tempo is a measurement.

The `time_map` then warps the *edit* to the licensed track rather than time-stretching the track ([docs/06 §3.2](06-blueprint-spec.md#32-time_map--binding-structure-to-a-real-track)). Stretching licensed audio degrades it and raises its own licensing question.

**`constraints.require_licensed_audio` is `{"const": true}` in the schema.** Not a default. No tenant, config file, or API caller can disable it, and the API returns `403 licence-required` with no workaround.

### 3.2 Licensing strategy

**Primary: catalogue partnership.** Epidemic Sound has the most developed API infrastructure among the major royalty-free providers — a Partner API gated behind a partnership agreement, with credentials issued through a developer portal and authentication via API key, partner token, or OAuth 2.0 (Epidemic Sound Connect). Their all-inclusive licence covers mechanical, sync and public performance rights across a catalogue of 55,000+ tracks and 250,000+ sound effects. ([Epidemic Sound APIs](https://apis.io/providers/epidemic-sound/), [catalogue detail](https://github.com/api-evangelist/epidemic-sound))

**Important caveat on alternatives:** Artlist states that its standard licences do **not** cover music use inside an app, and directs developers to enterprise sales. Soundstripe requires confirming whether a subscription includes embedded app use. Neither is usable under a standard subscription for our case — this is exactly the kind of detail that sinks a launch if discovered late. ([alternatives comparison](https://www.soundstripe.com/blogs/epidemic-sound-alternatives), [developer guidance](https://meditationmusiclibrary.com/blogs/wednesday-wisdom-blog/music-licensing-for-apps))

**Secondary:** direct label relationships for a premium tier; user-supplied tracks with a rights attestation.

**Per-use records are non-negotiable.** Every render writes a `music_licences` row with a **snapshot of the terms as they stood at issue time** ([docs/11 §2.6](11-database-schema.md#26-music-licensing)) — not a pointer to current terms, which would silently rewrite history when a provider changes their agreement. This is the record we produce if a rights holder ever asks.

---

## 4. Reference audio: what we will not do

**We do not extract the reference's master recording and mux it into a user's export.** Not as a paid feature, not for enterprise, not where a platform API might technically make it available. That is distributing a copyrighted sound recording without a licence, and there is no version of it worth the company.

**What we do instead — `platform_attach`, and it is the default.** The user exports a silent master and attaches the original sound *inside* TikTok or Instagram, where the platform's own blanket licence covers it. Because we cut the edit to that track's real beat grid, it re-syncs exactly, and we hand the creator the trim offset so it lines up first time.

This is worth being precise about, because it looks like a workaround and is not:

| | Extract and mux | `platform_attach` |
|---|---|---|
| Who distributes the recording | **We do** | The platform, under its existing licence |
| What the user gets | The track | The same track |
| Sync quality | Exact | Exact (same beat grid) |
| Our licensing exposure | **Severe** | None |

The user outcome is identical. The legal position is not remotely. Where a workaround exists that is *both* safer and equally good for the user, taking it is not a compromise.

The temptation will be real — users will ask for the file to just contain the song. See [docs/16 §R3](16-risks.md), which treats music rejection as a top-tier risk precisely *because* we will not take the easy escape. `platform_attach` substantially defuses that risk: the objection was never "I want a different track", it was "I want *that* track", and this gives them that track.

---

## 5. Privacy

**Faces.** Detected, tracked within a job for continuity, and identity embeddings are **never persisted beyond the job**. We need to know "the same person appears in shots 3, 7 and 11." We do not need a face database, and building one would create obligations under BIPA, GDPR Article 9, and equivalents that are entirely avoidable.

**Speech.** Transcribed for captions. Transcripts are user content, deleted with the project.

**User media.** Encrypted at rest (SSE-KMS, per-org keys on Enterprise) and in transit. Org-isolated by S3 prefix policy, scoped STS credentials per job, and Postgres row-level security as a backstop ([docs/11 §3](11-database-schema.md#3-row-level-security)).

**GDPR/CCPA.** Export and deletion endpoints; DPA available; sub-processor list published; data residency by region from month 17 ([docs/13 §4.1](13-scalability.md#41-multi-region)).

**Training data.** We train the matcher on swap events and — with consent — on blueprints. **We do not train on user video.** Swap events are structured feature vectors, not media, and users can opt out with no loss of function.

**Blueprint sharing across tenants.** The cost model assumes a shared global blueprint cache ([docs/14 §6](14-cost-model.md#6-the-five-levers-ranked-by-impact)). This is only clean because a blueprint contains no user media and no reference media — it is derived structural data about a third-party video, not either party's content. This must be explicit in the terms of service, and it is worth flagging to counsel as a deliberate design choice with commercial consequences.

---

## 6. Deepfakes and misuse

**Architectural refusal, not policy refusal.**

The product takes two videos as input. There will be commercial and user pressure to combine identities. The answer is that the system is *incapable*:

- No face swapping, face reenactment, or voice cloning — no model, no code path, no feature flag
- Face identity embeddings are ephemeral by construction
- Generative features ([docs/19](19-future-roadmap.md)) generate *backgrounds, textures and transitions*, never people
- Every output carries C2PA provenance recording that it was assembled by ReelsEdits, from which blueprint, and whether any generative content was included

A capability that does not exist cannot be misused, leaked, or gradually relaxed under commercial pressure. This is a stronger guarantee than a policy, and it is cheaper to maintain.

---

## 7. Platform terms of service

**URL fetching is a policy-gated convenience, never a dependency.**

Automated download from TikTok, Instagram or YouTube generally conflicts with their terms. So:

- Fetching runs only for domains on an explicit allowlist (`fetchable_domains`, empty by default in [`config.py`](../services/api/app/config.py))
- `robots.txt` is respected; provenance is recorded per fetch
- Where fetching is not permitted, the API returns `422 source-not-fetchable` with instructions to upload directly

**This is deliberate friction and we accept it.** The alternative — building a core product flow on something a platform can terminate with a ToS update — is a worse trade than asking users to upload a file they already have.

**Output side.** We produce standard MP4 with no platform-specific hooks, and we do not automate posting. Users publish through the platforms' own apps.

---

## 8. Content moderation

Runs **at upload, before any GPU spend** — moderating after analysis wastes money on content we will reject.

| Category | Action |
|---|---|
| CSAM | Hash matching (PhotoDNA-class); immediate block, preserve, report to NCMEC |
| Non-consensual intimate imagery | Block, account review |
| Graphic violence | Block for public/marketplace, permit private with a warning |
| Extremist content | Block, review |
| Copyright (rights-holder report) | Notice-and-takedown, counter-notice process |

Automated first pass, human review on appeal, published transparency reporting from month 12.

**Marketplace listings get human review before going live.** A public storefront is a different risk surface from private renders, and a moderation failure there is far more visible.

---

## 9. AI disclosure and provenance

**C2PA Content Credentials on every export.** Records that the video was assembled by ReelsEdits, the blueprint ID, the renderer version, whether any generative content was included, and the music licence ID.

This costs essentially nothing, is the right default, and pre-empts a regulatory requirement that is clearly coming — the EU AI Act's transparency obligations and equivalent state-level rules in the US are moving in one direction.

**Generative content is always disclosed**, in the UI and in the file metadata. A user should never be unable to tell which parts of their video were generated.

---

## 10. Terms of service — key provisions

**Users represent** they own or have rights to their uploaded footage, and that they have rights to any user-supplied music.

**Users grant** us a limited licence to process their content for the purpose of providing the service. Not a broad content licence, not a licence to train on their video.

**We disclaim** responsibility for the user's choice of reference video and for the user's use of the output. Style transfer is a tool; the user directs it.

**We commit** to: no training on user video; deletion on request; no reference media in output; per-render music licensing; and C2PA provenance on every export.

**Marketplace sellers** warrant originality and grant a licence to distribute; buyers get a perpetual licence to use the blueprint, not to resell it.

---

## 11. Practical risk reduction

Ranked by cost-effectiveness:

1. **Specialist IP counsel before launch, not after a letter.** A written opinion on the style/content distinction costs $25–50k and is the highest-value legal spend available.
2. **Structural enforcement over policy.** Everything in §1.3 — cheap to build, and it converts "we promise" into "it cannot".
3. **Licensed catalogue from day one.** No period where unlicensed music is technically possible.
4. **Creator opt-out registry.** Costs little; removes the sympathetic-plaintiff scenario.
5. **Minimum duration and complexity floors.** Removes the hardest edge case at negligible product cost.
6. **Per-use licence records with terms snapshots.** The difference between a documented position and a scramble.
7. **Insurance and legal reserve.** Budgeted in the seed ([docs/17 §7](17-business-model.md#7-funding)).
8. **Transparency reporting.** Reduces regulatory friction, and is the kind of thing that is much easier to start early than to retrofit.

---

**Sources:** [Epidemic Sound APIs](https://apis.io/providers/epidemic-sound/) · [Epidemic Sound catalogue and licensing](https://github.com/api-evangelist/epidemic-sound) · [Royalty-free alternatives compared](https://www.soundstripe.com/blogs/epidemic-sound-alternatives) · [Music licensing for apps — developer guide](https://meditationmusiclibrary.com/blogs/wednesday-wisdom-blog/music-licensing-for-apps)

Next: [19 — Future Roadmap](19-future-roadmap.md)
