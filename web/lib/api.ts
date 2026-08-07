/**
 * Typed client for the ReelsEdits API.
 * Mirrors docs/12-api-design.md. Regenerate from /openapi.json once the API
 * surface stabilises.
 */

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface ProblemDetail {
  type: string;
  title: string;
  status: number;
  detail: string;
  /** What the caller should do about it. An error you cannot act on is a support ticket. */
  fix?: string;
  [k: string]: unknown;
}

export class ApiError extends Error {
  constructor(readonly problem: ProblemDetail) {
    super(problem.detail);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      // Every mutation is idempotent: video work is expensive, and a
      // double-submitted render is a real cost rather than a duplicate row.
      ...(init.method && init.method !== "GET"
        ? { "Idempotency-Key": crypto.randomUUID() }
        : {}),
      ...init.headers,
    },
  });
  if (!res.ok) throw new ApiError((await res.json()) as ProblemDetail);
  return (await res.json()) as T;
}

export interface StyleCard {
  blueprint_id: string;
  summary: string;
  pacing: Record<string, number>;
  transition_mix: Record<string, number>;
  shot_scale_mix: Record<string, number>;
  palette: { hex: string; weight: number; role: string }[];
  tags: string[];
  confidence: Record<string, number>;
  /** Subsystems the UI must label "approximate" rather than present as fact. */
  low_confidence_subsystems: string[];
}

export interface CoverageGap {
  slots: number[];
  severity: "minor" | "moderate" | "major";
  /** Specific and actionable. "You need a shot with strong left-to-right motion." */
  message: string;
  suggested_action: "shoot" | "upload" | "substitute" | "accept";
  fallback?: string;
}

export interface CoverageReport {
  overall: number;
  verdict: "good" | "degraded" | "insufficient";
  gaps: CoverageGap[];
  can_render: boolean;
  requires_acknowledgement: boolean;
}

export interface Alternative {
  segment_id: string;
  score: number;
  rank: number;
  reason: string;
  breakdown: Record<string, number>;
}

export const api = {
  createReference: (body: { source_url?: string; asset_id?: string; name?: string }) =>
    request<{ id: string; status: string; cache_hit: boolean; blueprint_id?: string }>(
      "/v1/references",
      { method: "POST", body: JSON.stringify(body) },
    ),

  styleCard: (blueprintId: string) =>
    request<StyleCard>(`/v1/blueprints/${blueprintId}/style-card`),

  coverage: (projectId: string) =>
    request<CoverageReport>(`/v1/projects/${projectId}/coverage`),

  alternatives: (projectId: string, slot: number) =>
    request<{ alternatives: Alternative[] }>(
      `/v1/projects/${projectId}/slots/${slot}/alternatives`,
    ),

  swap: (projectId: string, slot: number, segmentId: string, locked = true) =>
    request<{ dirty_ranges: [number, number][]; overall_confidence: number }>(
      `/v1/projects/${projectId}/assignment`,
      {
        method: "PATCH",
        body: JSON.stringify({ changes: [{ slot, segment_id: segmentId, locked }] }),
      },
    ),

  render: (projectId: string, preset = "preview", acknowledgeDegradation = false) =>
    request<{ id: string; status: string; cache_hit: boolean }>("/v1/renders", {
      method: "POST",
      body: JSON.stringify({
        project_id: projectId,
        preset,
        acknowledge_degradation: acknowledgeDegradation,
      }),
    }),

  /** Server-sent progress. `detail` carries a human stage description, not just a %. */
  events(projectId: string, onEvent: (type: string, data: unknown) => void): EventSource {
    const es = new EventSource(`${BASE}/v1/projects/${projectId}/events`);
    for (const t of ["stage", "style_card", "coverage", "render_progress", "complete"]) {
      es.addEventListener(t, (e) => onEvent(t, JSON.parse((e as MessageEvent).data)));
    }
    return es;
  },
};
