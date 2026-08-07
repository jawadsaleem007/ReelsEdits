# 12 — API Design

REST over HTTPS, JSON bodies, `Bearer` auth. OpenAPI 3.1 generated from FastAPI at `/openapi.json`. Reference implementation: [`services/api/`](../services/api/).

---

## 1. Principles

**Resources, not RPC.** `POST /v1/renders` rather than `POST /v1/doRender`. The blueprint is a first-class resource because it is the thing users and integrators actually want to manipulate.

**Bytes never transit the API.** Uploads go direct to S3 via presigned multipart. The API issues URLs and registers rows. This is not an optimisation — routing video through the API tier would make it the bottleneck at roughly 200 concurrent users.

**Long operations return a job, immediately.** Nothing blocks. Progress arrives by SSE or webhook.

**Every mutation is idempotent.** `Idempotency-Key` on every `POST`. Video work is expensive; a double-submitted render is a real cost, not just a duplicate row.

**Errors are machine-readable and actionable.** RFC 9457 problem details with a stable `type` URI, plus a `fix` field telling the caller what to do. An error a client cannot act on is a support ticket.

---

## 2. Surface

```
POST   /v1/references                   submit a reference (URL or upload)
GET    /v1/references/{id}              status + blueprint id
GET    /v1/references/{id}/events       SSE analysis progress

GET    /v1/blueprints                   list (filter: tags, bpm, visibility)
GET    /v1/blueprints/{id}              full EBP document
POST   /v1/blueprints/{id}/versions     create an edited version
GET    /v1/blueprints/{id}/diff/{other} structural diff
GET    /v1/blueprints/{id}/style-card   human-readable style summary
POST   /v1/blueprints/search            similarity search by style embedding

POST   /v1/assets                       register + get presigned upload URLs
POST   /v1/assets/batch                 register many at once
POST   /v1/assets/{id}/complete         finalise multipart, trigger indexing
GET    /v1/assets/{id}                  status + MediaProfile
DELETE /v1/assets/{id}

POST   /v1/projects                     create
GET    /v1/projects/{id}
PATCH  /v1/projects/{id}                attach blueprint, add/remove assets
GET    /v1/projects/{id}/coverage       coverage report + actionable gaps
GET    /v1/projects/{id}/events         SSE project-wide progress

POST   /v1/projects/{id}/match          run the matcher (returns assignment)
GET    /v1/projects/{id}/assignment
PATCH  /v1/projects/{id}/assignment     swap/lock a slot  ← the training signal
GET    /v1/projects/{id}/slots/{i}/alternatives   ranked, with reasons

POST   /v1/renders                      preview or export
GET    /v1/renders/{id}
GET    /v1/renders/{id}/download        302 → presigned CDN URL
DELETE /v1/renders/{id}

GET    /v1/music/search                 by bpm, structure, mood, embedding
POST   /v1/music/match                  best catalogue tracks for a blueprint
GET    /v1/music/tracks/{id}/preview

GET    /v1/marketplace/listings
POST   /v1/marketplace/listings
POST   /v1/marketplace/listings/{id}/purchase

GET    /v1/usage                        quota + cost for the period
POST   /v1/webhooks                     register an endpoint
```

---

## 3. Key flows

### 3.1 Reference submission

```http
POST /v1/references
Authorization: Bearer sk_live_...
Idempotency-Key: 018f3a2c-...

{ "source_url": "https://www.tiktok.com/@user/video/123", "name": "moto sunset" }
```

```json
201 Created
{
  "id": "ref_8Kx2mQ",
  "status": "analyzing",
  "cache_hit": false,
  "estimated_ready_in_ms": 52000,
  "events_url": "/v1/references/ref_8Kx2mQ/events"
}
```

On a cache hit — 55–75% of submissions in steady state:

```json
201 Created
{ "id": "ref_9Lm4pR", "status": "ready", "cache_hit": true,
  "blueprint_id": "bp_7fK2mQx91aBc", "estimated_ready_in_ms": 0 }
```

`cache_hit` is exposed deliberately. Integrators building on the API can use it to decide whether to show a progress UI, and it makes our cost structure legible to partners on usage-based pricing.

**URL fetching is policy-gated.** Where a platform's terms or applicable law do not permit fetching, the API returns:

```json
422 Unprocessable Entity
{
  "type": "https://reelsedits.com/errors/source-not-fetchable",
  "title": "This source cannot be fetched automatically",
  "detail": "We don't fetch from this domain. Upload the file directly.",
  "fix": "POST /v1/assets with kind=reference, then reference the asset_id."
}
```

Accepted friction. See [docs/18 §7](18-legal-ethics.md).

### 3.2 Upload

```http
POST /v1/assets/batch
{ "project_id": "prj_...", "assets": [
    { "kind": "clip", "filename": "IMG_4821.MOV", "bytes": 184320000, "sha256": "a91f..." }
]}
```

```json
201 Created
{ "assets": [{
  "id": "ast_3nQ8vB",
  "status": "uploading",
  "dedupe_hit": false,
  "upload": {
    "method": "multipart",
    "upload_id": "2~abc...",
    "part_size": 8388608,
    "parts": [ {"part_number": 1, "url": "https://s3..."}, ... ]
  }
}]}
```

Client `PUT`s parts in parallel, collects ETags, then:

```http
POST /v1/assets/ast_3nQ8vB/complete
{ "parts": [{"part_number": 1, "etag": "\"9b2c...\""}] }
```

`dedupe_hit: true` short-circuits the whole upload when the org has already uploaded that exact file — common when users re-drag the same folder.

### 3.3 Coverage — the honesty endpoint

```http
GET /v1/projects/prj_5Hn2/coverage
```

```json
{
  "overall": 0.78,
  "verdict": "degraded",
  "per_slot": [ {"slot": 0, "coverage": 1.0, "candidates": 7}, ... ],
  "gaps": [
    {
      "slots": [6, 11],
      "severity": "moderate",
      "message": "You need a shot with strong right-to-left motion — the style uses whip-pan transitions at 11.5s and 17.5s.",
      "suggested_action": "shoot",
      "fallback": "We'll substitute flash cuts if you continue."
    }
  ],
  "can_render": true,
  "requires_acknowledgement": true
}
```

`message` is a specific, actionable sentence, not a code. `requires_acknowledgement: true` means the render endpoint will reject without `"acknowledge_degradation": true`. The system will not silently produce a degraded result ([docs/09 §7](09-clip-matching.md#7-insufficiency-and-graceful-degradation)).

### 3.4 The swap endpoint

```http
PATCH /v1/projects/prj_5Hn2/assignment
{ "changes": [ {"slot": 7, "segment_id": "seg_c4", "in_ms": 300, "out_ms": 920, "locked": true} ] }
```

```json
{
  "assignment_id": "asn_2Bx9",
  "changed_slots": [7],
  "recomputed_slots": [6, 8],
  "dirty_ranges": [[6, 9]],
  "overall_confidence": 0.81
}
```

Two things happen server-side, and both matter more than the response:

1. **The swap is logged to ClickHouse** as a preference pair — this is the matcher's training signal ([docs/09 §6](09-clip-matching.md#6-learning-from-swaps)).
2. **Neighbours are recomputed** because the pairwise sequence terms changed; `locked: true` pins the user's choice so the matcher works around it.

`dirty_ranges` drives partial re-render ([docs/10 §7](10-rendering-engine.md)) — the client can request a 3-slot re-render instead of a full one.

### 3.5 Alternatives, with reasons

```http
GET /v1/projects/prj_5Hn2/slots/7/alternatives?limit=5
```

```json
{
  "slot": { "index": 7, "requirements": {...}, "current": "seg_a1" },
  "alternatives": [
    { "segment_id": "seg_c4", "score": 0.84, "rank": 1,
      "reason": "Low-angle mechanical detail with matching leftward motion.",
      "breakdown": {"scale": 0.95, "motion": 0.88, "subject": 1.0, "quality": 0.79} }
  ]
}
```

`breakdown` exposes the per-term fit scores. This is deliberate: a user who can see *why* we ranked something makes a more informed correction, and a more informed correction is a better training label. Explaining the model is not a nicety here — it improves the data.

### 3.6 Render

```http
POST /v1/renders
Idempotency-Key: 018f3a2c-...

{ "project_id": "prj_5Hn2", "preset": "preview",
  "acknowledge_degradation": true, "webhook_url": "https://you/hooks/re" }
```

```json
202 Accepted
{ "id": "rnd_7Qp3", "status": "queued", "cache_hit": false,
  "queue_position": 3, "estimated_ready_in_ms": 61000 }
```

Deterministic rendering means an unchanged blueprint + assignment + assets returns the previous render instantly:

```json
{ "id": "rnd_7Qp3", "status": "complete", "cache_hit": true,
  "download_url": "/v1/renders/rnd_7Qp3/download" }
```

---

## 4. Progress: SSE and webhooks

```
GET /v1/projects/prj_5Hn2/events
Accept: text/event-stream

event: stage
data: {"stage":"analyzing","progress":0.34,"detail":"Detecting shot boundaries"}

event: stage
data: {"stage":"analyzing","progress":0.71,"detail":"Extracting colour grade"}

event: style_card
data: {"blueprint_id":"bp_7fK","summary":"Fast, hard-cut-driven edit...","pacing":{...}}

event: coverage
data: {"overall":0.78,"gaps":[...]}

event: render_progress
data: {"render_id":"rnd_7Qp3","progress":0.62,"eta_ms":24000}

event: complete
data: {"render_id":"rnd_7Qp3","download_url":"..."}
```

`detail` carries a human-readable stage description rather than a percentage alone. During a 60-second analysis, "Extracting colour grade" tells the user the system is doing real, specific work — measurably better for perceived wait than a bar.

**Webhooks** are signed HMAC-SHA256 over the raw body with a timestamp, retried with exponential backoff for 24 hours, and delivered at-least-once with an `event_id` for client-side dedupe.

---

## 5. Errors

RFC 9457, with a `fix`:

```json
409 Conflict
{
  "type": "https://reelsedits.com/errors/insufficient-coverage",
  "title": "Not enough footage for this style",
  "status": 409,
  "detail": "Coverage is 0.41; the floor for rendering is 0.55.",
  "instance": "/v1/renders",
  "fix": "Add clips covering the gaps in GET /v1/projects/{id}/coverage, or resubmit with acknowledge_degradation=true.",
  "coverage": 0.41,
  "gaps": [...]
}
```

| Status | `type` | When |
|---|---|---|
| 400 | `invalid-blueprint` | Fails schema or invariants |
| 402 | `quota-exceeded` | Period render quota exhausted |
| 403 | `licence-required` | Music binding without a resolved licence |
| 409 | `insufficient-coverage` | Below floor without acknowledgement |
| 409 | `idempotency-conflict` | Same key, different body |
| 415 | `unsupported-media` | Codec/container we cannot decode |
| 422 | `source-not-fetchable` | URL fetching not permitted for that domain |
| 422 | `renderer-version-too-old` | `renderer_min_version` exceeds deployment |
| 429 | `rate-limited` | With `Retry-After` |
| 503 | `capacity` | GPU pool saturated; `Retry-After` |

`403 licence-required` cannot be worked around by any client. It reflects the schema-level constraint in [docs/06 §3.1](06-blueprint-spec.md#31-music_binding--the-most-consequential-field-in-the-schema).

---

## 6. Idempotency

```
Idempotency-Key: <client-generated UUID>
```

Stored as `(key, org_id, sha256(body)) → response` in Redis for 24h.

- Same key, same body hash → cached response, `Idempotency-Replayed: true`
- Same key, different body hash → `409 idempotency-conflict`
- In-flight → `409` with `Retry-After: 1`

The body hash is what makes this safe. Keying on the key alone lets a client accidentally reuse a UUID and receive someone else's render.

---

## 7. Rate limits and quotas

Two independent mechanisms, often conflated:

**Rate limits** protect infrastructure. Token bucket per org.

| Tier | Requests/min | Concurrent renders | Uploads/min |
|---|---|---|---|
| Free | 60 | 1 | 10 |
| Creator | 300 | 2 | 60 |
| Pro | 1200 | 5 | 200 |
| Team | 3000 | 12 | 500 |
| Enterprise | negotiated | negotiated | negotiated |

**Quotas** protect margin. Counted in renders and GPU-seconds per billing period, checked transactionally at job creation so concurrent requests cannot race past the limit.

```
X-RateLimit-Limit: 300
X-RateLimit-Remaining: 287
X-Quota-Renders-Remaining: 34
X-Quota-Period-End: 2026-09-01T00:00:00Z
```

Exposing quota headers on every response lets integrators build backpressure instead of discovering the limit by hitting it.

---

## 8. Versioning

URL-versioned (`/v1/`). Additive changes ship in place; breaking changes get `/v2/` with 12 months of `/v1/` support and `Sunset` headers.

The **blueprint schema** versions independently via `ebp_version`. This matters: we may need to evolve the blueprint format faster than the API surface, and coupling them would force unnecessary API majors.

---

## 9. API tier pricing

| Metric | Price | Note |
|---|---|---|
| Reference analysis (cache miss) | $0.35 | Real GPU cost |
| Reference analysis (cache hit) | $0.02 | Cost passed through honestly |
| Clip indexing | $0.02/clip | |
| Match | $0.01 | |
| Render 1080p | $0.60/output-minute | |
| Render 4K | $1.40/output-minute | |
| Storage | $0.02/GB/month | Beyond 50GB included |

Charging differently for cache hits and misses is unusual and correct. It reflects real cost, is legible to partners, and gives integrators an incentive to reuse popular references — which improves our cache hit rate and therefore our margin. Aligned incentives beat flat pricing here.

---

Next: [13 — Scalability](13-scalability.md)
