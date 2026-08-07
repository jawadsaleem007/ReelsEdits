-- Telemetry and matcher training data. See docs/11 section 4.
CREATE DATABASE IF NOT EXISTS reelsedits;

-- This table IS the moat: every row is a preference pair labelled by a domain
-- expert at the moment of peak engagement. docs/09 section 6.
CREATE TABLE IF NOT EXISTS reelsedits.swap_events (
    ts                  DateTime64(3),
    org_id              UUID,
    project_id          UUID,
    blueprint_id        UUID,
    slot_index          UInt16,
    slot_features       String,
    rejected_segment_id UUID,
    rejected_features   String,
    rejected_score      Float32,
    chosen_segment_id   UUID,
    chosen_features     String,
    chosen_score        Float32,
    chosen_rank         UInt8,
    matcher_version     LowCardinality(String)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(ts)
ORDER BY (matcher_version, ts);

CREATE TABLE IF NOT EXISTS reelsedits.render_events (
    ts                 DateTime64(3),
    org_id             UUID,
    render_id          UUID,
    preset             LowCardinality(String),
    duration_ms        UInt32,
    gpu_seconds        Float32,
    queue_wait_ms      UInt32,
    renderer_version   LowCardinality(String),
    degraded           UInt8,
    compromise_kinds   Array(LowCardinality(String)),
    estimated_cost_usd Float32
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(ts)
ORDER BY (ts, org_id);
