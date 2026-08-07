# 11 — Database Schema

PostgreSQL 16 + pgvector. DDL in [`services/api/migrations/`](../services/api/migrations/).

---

## 1. Principles

**Job state lives in Postgres, not in a queue.** Queues lose messages, get replayed, and cannot answer "what is the state of job X." The queue is a dispatch mechanism; the database is the source of truth. This distinction is what makes the retry logic in [docs/03](03-system-architecture.md) safe.

**Blueprints are immutable and versioned.** A user edit creates a new row with `parent_id` pointing at the previous version. Never `UPDATE`. This gives undo, diff, and provenance for free, and it means a marketplace blueprint someone purchased cannot change under them.

**Row-level security on every tenant table.** Enforced in the database, not only in application code. The application check is the fast path; RLS is what saves you the day a worker query forgets a `WHERE org_id`.

**Blueprints are never deleted.** They are the corpus ([docs/02 §4](02-competitive-analysis.md#4-where-the-moat-actually-is)) and they are kilobytes. Deleting one destroys training data permanently to save nothing.

**High-volume telemetry goes to ClickHouse, not Postgres.** Swap logs and render events arrive at a rate that would make Postgres the bottleneck. They go to ClickHouse; Postgres holds only what needs transactional integrity.

---

## 2. Core tables

### 2.1 Identity

```sql
CREATE TABLE orgs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    plan            TEXT NOT NULL DEFAULT 'free'
                    CHECK (plan IN ('free','creator','pro','team','enterprise')),
    stripe_customer_id TEXT UNIQUE,
    -- Denormalised quota counters. Updated transactionally on job creation so
    -- a burst of concurrent requests cannot exceed quota via a read-then-write race.
    renders_used_period      INT NOT NULL DEFAULT 0,
    renders_quota_period     INT NOT NULL DEFAULT 3,
    gpu_seconds_used_period  BIGINT NOT NULL DEFAULT 0,
    period_start    TIMESTAMPTZ NOT NULL DEFAULT date_trunc('month', now()),
    settings        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         CITEXT NOT NULL UNIQUE,
    external_id   TEXT UNIQUE,                    -- Clerk/Auth0 subject
    display_name  TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ,
    deleted_at    TIMESTAMPTZ
);

CREATE TABLE org_members (
    org_id  UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role    TEXT NOT NULL DEFAULT 'member'
            CHECK (role IN ('owner','admin','member','viewer')),
    PRIMARY KEY (org_id, user_id)
);
CREATE INDEX ON org_members (user_id);
```

### 2.2 Assets

```sql
CREATE TYPE asset_kind AS ENUM ('reference','clip','audio','lut','font','overlay');
CREATE TYPE asset_status AS ENUM
    ('pending','uploading','probing','indexing','ready','failed','deleted');

CREATE TABLE assets (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    uploaded_by   UUID REFERENCES users(id),
    kind          asset_kind NOT NULL,
    status        asset_status NOT NULL DEFAULT 'pending',

    s3_key        TEXT,
    s3_bucket     TEXT,
    bytes         BIGINT,
    sha256        TEXT,                -- exact dedupe within an org

    -- MediaProfile from ffprobe
    container     TEXT,
    video_codec   TEXT,
    audio_codec   TEXT,
    width         INT,
    height        INT,
    fps           NUMERIC(7,4),
    is_vfr        BOOLEAN DEFAULT FALSE,   -- see docs/10 section 3, rule 2
    duration_ms   INT,
    rotation      INT DEFAULT 0,
    color_primaries TEXT,
    has_audio     BOOLEAN,

    -- Retention. URL-fetched references get 24h; see docs/05 section 2.4.
    retention_class TEXT NOT NULL DEFAULT 'standard'
                    CHECK (retention_class IN ('standard','ephemeral_24h','permanent')),
    expires_at    TIMESTAMPTZ,

    error_code    TEXT,
    error_detail  TEXT,
    metadata      JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
CREATE INDEX ON assets (org_id, kind, status);
CREATE INDEX ON assets (sha256) WHERE sha256 IS NOT NULL;
CREATE INDEX ON assets (expires_at) WHERE expires_at IS NOT NULL;
```

`expires_at` with a partial index drives the retention sweeper. Hard deletion of URL-fetched references within 24 hours is a legal commitment ([docs/18](18-legal-ethics.md)) and needs to be a cheap, reliable, indexed query — not a full-table scan someone eventually disables because it is slow.

### 2.3 References and blueprints

```sql
CREATE TABLE references (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            UUID REFERENCES orgs(id) ON DELETE SET NULL,
    asset_id          UUID REFERENCES assets(id) ON DELETE SET NULL,

    -- Perceptual, NOT byte-level. The whole cost model depends on the same
    -- video at different bitrates producing the same value. docs/08 section 1.
    fingerprint       TEXT NOT NULL,
    phash_sequence    BYTEA,           -- for near-duplicate LSH lookup
    audio_fingerprint BYTEA,
    duration_bucket   INT NOT NULL,

    source_url        TEXT,
    source_platform   TEXT,
    provenance_note   TEXT,            -- how we obtained it; audit trail

    blueprint_id      UUID,            -- FK added after blueprints table
    analyzer_version  TEXT NOT NULL,
    analysis_ms       INT,
    gpu_seconds       NUMERIC(10,3),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- THE cache index. A miss here costs 35-70 GPU-seconds; a hit costs ~2s.
CREATE UNIQUE INDEX references_fingerprint_key
    ON references (fingerprint, analyzer_version);
CREATE INDEX ON references (duration_bucket);


CREATE TABLE blueprints (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        UUID REFERENCES orgs(id) ON DELETE SET NULL,
    reference_id  UUID REFERENCES references(id) ON DELETE SET NULL,
    parent_id     UUID REFERENCES blueprints(id),   -- immutable version chain

    ebp_version   TEXT NOT NULL DEFAULT '1.0',
    name          TEXT,
    doc           JSONB NOT NULL,        -- the full validated blueprint
    doc_sha256    TEXT NOT NULL,         -- render cache key component

    -- Denormalised for querying and ranking without parsing JSONB
    duration_ms       INT NOT NULL,
    slot_count        INT NOT NULL,
    cut_count         INT NOT NULL,
    bpm               NUMERIC(6,2),
    cuts_per_second   NUMERIC(5,2),
    beat_lock_ratio   NUMERIC(4,3),
    confidence_overall NUMERIC(4,3),
    tags              TEXT[] NOT NULL DEFAULT '{}',

    style_embedding   vector(768),       -- style similarity search

    visibility    TEXT NOT NULL DEFAULT 'private'
                  CHECK (visibility IN ('private','org','marketplace','public')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    -- deliberately no deleted_at: blueprints are never deleted
);
ALTER TABLE references
    ADD CONSTRAINT references_blueprint_fk
    FOREIGN KEY (blueprint_id) REFERENCES blueprints(id);

CREATE INDEX ON blueprints (org_id, created_at DESC);
CREATE INDEX ON blueprints (parent_id);
CREATE INDEX ON blueprints USING GIN (tags);
CREATE INDEX ON blueprints USING GIN (doc jsonb_path_ops);
CREATE INDEX ON blueprints USING hnsw (style_embedding vector_cosine_ops)
    WHERE visibility IN ('marketplace','public');
```

**Why `doc JSONB` rather than fully normalised tables.** The blueprint is validated by JSON Schema before it lands, it is read atomically by the renderer, and it evolves. Normalising 25 slots × 8 requirement fields into relational tables would mean a 12-way join to render one video, and a migration for every schema addition. The denormalised columns give us the query surface we actually need; the GIN index handles the rest.

### 2.4 Clip features and segments

```sql
CREATE TABLE clip_features (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id        UUID NOT NULL UNIQUE REFERENCES assets(id) ON DELETE CASCADE,
    org_id          UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    indexer_version TEXT NOT NULL,

    quality_overall NUMERIC(4,3),
    sharpness       NUMERIC(4,3),
    exposure_score  NUMERIC(4,3),
    shake_severity  NUMERIC(4,3),
    noise_level     NUMERIC(4,3),

    has_face        BOOLEAN DEFAULT FALSE,
    has_speech      BOOLEAN DEFAULT FALSE,
    is_indoor       BOOLEAN,
    scene_category  TEXT,
    time_of_day     TEXT,
    weather         TEXT,

    transcript      JSONB,           -- word-level, with timestamps
    features        JSONB NOT NULL DEFAULT '{}',
    gpu_seconds     NUMERIC(10,3),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON clip_features (org_id, quality_overall DESC);


-- The matcher's actual unit of work. Users upload long takes; the good
-- three seconds are in the middle.
CREATE TABLE segments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id        UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    org_id          UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,

    t_in_ms         INT NOT NULL,
    t_out_ms        INT NOT NULL,
    usable_in_ms    INT NOT NULL,     -- trimmed of shake, focus hunt, boundaries
    usable_out_ms   INT NOT NULL,

    shot_scale      TEXT NOT NULL,
    camera_motion   TEXT NOT NULL,
    camera_height   TEXT,
    subject_class   TEXT NOT NULL,
    composition     TEXT,
    narrative_role  TEXT,

    motion_energy      NUMERIC(4,3) NOT NULL,
    motion_direction_deg NUMERIC(6,2),
    quality            NUMERIC(4,3) NOT NULL,
    subject_area_ratio NUMERIC(5,4),
    mean_luma          NUMERIC(4,3),
    subject_track_id   TEXT,

    semantic_vec    vector(768),
    motion_vec      vector(128),

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (t_out_ms > t_in_ms),
    CHECK (usable_out_ms > usable_in_ms)
);

-- Composite index matching the matcher's hard-constraint filter exactly
CREATE INDEX segments_match_idx ON segments
    (org_id, shot_scale, camera_motion, quality DESC)
    INCLUDE (motion_energy, t_in_ms, t_out_ms);
CREATE INDEX ON segments USING hnsw (semantic_vec vector_cosine_ops);
CREATE INDEX ON segments (asset_id);
```

**pgvector now, Qdrant later.** HNSW in pgvector is fine to roughly 5M vectors with our filter selectivity. Beyond that, filtered ANN — which is *always* what we do, since matching is scoped to one project — degrades, and Qdrant's native filtered search becomes necessary. The migration is a dual-write followed by a read cutover; the schema above stays.

### 2.5 Projects, jobs, renders

```sql
CREATE TYPE job_state AS ENUM (
    'draft','reference_pending','reference_ready','indexing','ready_to_render',
    'matching','insufficient_footage','rendering','preview_ready','exporting',
    'complete','failed','cancelled');

CREATE TABLE projects (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    created_by    UUID REFERENCES users(id),
    name          TEXT NOT NULL DEFAULT 'Untitled',
    blueprint_id  UUID REFERENCES blueprints(id),
    state         job_state NOT NULL DEFAULT 'draft',
    coverage      NUMERIC(4,3),
    settings      JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
CREATE INDEX ON projects (org_id, updated_at DESC) WHERE deleted_at IS NULL;

CREATE TABLE project_assets (
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    asset_id   UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    role       TEXT NOT NULL DEFAULT 'clip',
    position   INT,
    PRIMARY KEY (project_id, asset_id)
);

CREATE TABLE jobs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    project_id     UUID REFERENCES projects(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL
                   CHECK (kind IN ('analyze','index','match','render','export')),
    state          job_state NOT NULL DEFAULT 'draft',
    priority       SMALLINT NOT NULL DEFAULT 1,   -- 0 interactive, 1 export, 2 batch

    workflow_id    TEXT,                          -- Temporal
    idempotency_key TEXT,

    input          JSONB NOT NULL DEFAULT '{}',
    output         JSONB,
    -- Per-job cost ledger. Makes margin-per-job a query, not a spreadsheet.
    cost_ledger    JSONB NOT NULL DEFAULT '{}',

    attempts       INT NOT NULL DEFAULT 0,
    error_code     TEXT,
    error_detail   TEXT,

    queued_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at     TIMESTAMPTZ,
    finished_at    TIMESTAMPTZ
);
CREATE UNIQUE INDEX ON jobs (org_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX ON jobs (state, priority, queued_at) WHERE state NOT IN ('complete','failed','cancelled');
CREATE INDEX ON jobs (project_id, kind, queued_at DESC);


CREATE TABLE renders (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id     UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    org_id         UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    blueprint_id   UUID NOT NULL REFERENCES blueprints(id),
    job_id         UUID REFERENCES jobs(id),

    preset         TEXT NOT NULL,
    width          INT NOT NULL,
    height         INT NOT NULL,
    duration_ms    INT NOT NULL,
    s3_key         TEXT,
    bytes          BIGINT,

    -- Determinism contract: same key => byte-identical output.
    -- sha256(blueprint_doc || assignment || asset_ids || renderer_version)
    render_cache_key TEXT NOT NULL,
    renderer_version TEXT NOT NULL,

    assignment     JSONB NOT NULL,
    degradation    JSONB NOT NULL DEFAULT '{}',
    gpu_seconds    NUMERIC(10,3),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at     TIMESTAMPTZ
);
CREATE UNIQUE INDEX ON renders (render_cache_key);
CREATE INDEX ON renders (project_id, created_at DESC);
```

### 2.6 Music licensing

```sql
CREATE TABLE music_tracks (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider       TEXT NOT NULL,
    provider_track_id TEXT NOT NULL,
    title          TEXT NOT NULL,
    artist         TEXT,
    duration_ms    INT NOT NULL,

    -- Analysed with the SAME pipeline as references, so structural
    -- comparison is apples-to-apples. docs/07 section 6.3.
    bpm            NUMERIC(6,2),
    time_signature TEXT,
    sections       JSONB,
    energy_curve   JSONB,
    downbeats_ms   INT[],
    mood           TEXT[],
    genre          TEXT[],
    audio_embedding vector(512),

    licence_terms  JSONB NOT NULL,
    territories    TEXT[],
    active         BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (provider, provider_track_id)
);
CREATE INDEX ON music_tracks (bpm) WHERE active;
CREATE INDEX ON music_tracks USING hnsw (audio_embedding vector_cosine_ops);


-- Every use of a licensed track. Non-negotiable: this is the record we
-- produce if a rights holder ever asks.
CREATE TABLE music_licences (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID NOT NULL REFERENCES orgs(id),
    track_id     UUID NOT NULL REFERENCES music_tracks(id),
    render_id    UUID REFERENCES renders(id),
    issued_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ,
    terms_snapshot JSONB NOT NULL,     -- terms AS THEY WERE at issue time
    reported_to_provider_at TIMESTAMPTZ
);
CREATE INDEX ON music_licences (org_id, issued_at DESC);
CREATE INDEX ON music_licences (track_id);
```

`terms_snapshot` stores the licence terms as they stood at issue time, not a reference to the current terms. When a provider changes their agreement, every previously-issued licence must still be evidenced under the terms that applied. A foreign key to a mutable terms row would silently rewrite history.

### 2.7 Marketplace

```sql
CREATE TABLE style_listings (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    blueprint_id  UUID NOT NULL REFERENCES blueprints(id),
    seller_org_id UUID NOT NULL REFERENCES orgs(id),
    title         TEXT NOT NULL,
    description   TEXT,
    price_cents   INT NOT NULL DEFAULT 0,
    currency      TEXT NOT NULL DEFAULT 'usd',
    preview_render_id UUID REFERENCES renders(id),
    status        TEXT NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft','review','live','suspended')),
    purchases     INT NOT NULL DEFAULT 0,
    rating_sum    INT NOT NULL DEFAULT 0,
    rating_count  INT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON style_listings (status, purchases DESC) WHERE status = 'live';

CREATE TABLE style_purchases (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id  UUID NOT NULL REFERENCES style_listings(id),
    buyer_org_id UUID NOT NULL REFERENCES orgs(id),
    price_cents INT NOT NULL,
    payout_cents INT NOT NULL,          -- 70% to seller
    stripe_payment_intent TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (listing_id, buyer_org_id)
);
```

---

## 3. Row-level security

```sql
ALTER TABLE assets   ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE segments ENABLE ROW LEVEL SECURITY;
ALTER TABLE renders  ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs     ENABLE ROW LEVEL SECURITY;

CREATE POLICY org_isolation ON assets
    USING (org_id = current_setting('app.current_org_id', true)::uuid);
-- repeated for each tenant table

-- Workers need cross-org read for cache lookups; they get an explicit,
-- narrowly-scoped role rather than BYPASSRLS on the application role.
CREATE ROLE reelsedits_worker;
GRANT SELECT ON references, blueprints, music_tracks TO reelsedits_worker;
```

The application sets `app.current_org_id` per transaction from the validated JWT. Application-level checks remain the fast path; RLS is the backstop.

---

## 4. ClickHouse — telemetry and training data

```sql
-- The matcher's training set. This table IS the moat. docs/09 section 6.
CREATE TABLE swap_events (
    ts              DateTime64(3),
    org_id          UUID,
    project_id      UUID,
    blueprint_id    UUID,
    slot_index      UInt16,
    slot_features   String,     -- JSON
    rejected_segment_id UUID,
    rejected_features   String,
    rejected_score      Float32,
    chosen_segment_id   UUID,
    chosen_features     String,
    chosen_score        Float32,
    chosen_rank         UInt8,  -- position in OUR ranking; 1 = we were right
    matcher_version String
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(ts)
ORDER BY (matcher_version, ts);

CREATE TABLE render_events (
    ts             DateTime64(3),
    org_id         UUID,
    render_id      UUID,
    preset         LowCardinality(String),
    duration_ms    UInt32,
    gpu_seconds    Float32,
    queue_wait_ms  UInt32,
    renderer_version LowCardinality(String),
    degraded       UInt8,
    compromise_kinds Array(LowCardinality(String)),
    estimated_cost_usd Float32
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(ts)
ORDER BY (ts, org_id);
```

`chosen_rank` is the single most informative column. Rank 1 means our top pick was accepted. A rising mean rank across a matcher version is a regression, visible in one query.

---

## 5. Partitioning and retention

| Table | Strategy | Retention |
|---|---|---|
| `jobs` | Monthly range partition on `queued_at` | Detach after 90d → S3 |
| `renders` | Monthly on `created_at` | 180d, then metadata only |
| `segments` | None initially; hash on `org_id` past ~50M | With parent asset |
| `blueprints` | None | **Forever** |
| `references` | None | Forever (fingerprint + blueprint only) |
| ClickHouse | Monthly | 24 months |

---

## 6. Critical queries

**Blueprint cache lookup** — runs on every reference submission, must be sub-millisecond:

```sql
SELECT b.id, b.doc
FROM references r JOIN blueprints b ON b.id = r.blueprint_id
WHERE r.fingerprint = $1 AND r.analyzer_version = $2;
-- Index Scan using references_fingerprint_key  (cost=0.42..8.44 rows=1)
```

**Matcher candidate retrieval** — per slot, 25× per render:

```sql
SELECT s.id, s.semantic_vec <=> $1 AS dist, s.motion_energy, s.quality
FROM segments s
JOIN project_assets pa ON pa.asset_id = s.asset_id AND pa.project_id = $2
WHERE s.shot_scale = ANY($3)
  AND s.quality >= $4
  AND (s.usable_out_ms - s.usable_in_ms) >= $5
ORDER BY s.semantic_vec <=> $1
LIMIT 40;
```

The filter runs *before* the vector scan because the project join is highly selective — 61 segments, not 5 million. This is the query whose degradation at scale motivates the Qdrant migration.

**Margin per job:**

```sql
SELECT date_trunc('day', queued_at) AS d,
       count(*) AS jobs,
       sum((cost_ledger->>'estimated_cost_usd')::numeric) AS cogs,
       sum((cost_ledger->'gpu_seconds'->>'render')::numeric) AS render_gpu_s
FROM jobs
WHERE finished_at > now() - interval '30 days'
GROUP BY 1 ORDER BY 1;
```

---

Next: [12 — API Design](12-api-design.md)
