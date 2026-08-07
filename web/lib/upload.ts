/**
 * Direct-to-S3 multipart upload.
 *
 * Bytes never transit the API. Routing video through the API tier would make it
 * the bottleneck at roughly 200 concurrent users. See docs/12 §1.
 */

const PART_SIZE = 8 * 1024 * 1024;
const MAX_PARALLEL = 4;

export interface PreflightResult {
  ok: boolean;
  durationMs?: number;
  width?: number;
  height?: number;
  reason?: string;
}

/**
 * Read duration and dimensions from the file header before uploading.
 *
 * Rejecting a 4GB file *after* the user has waited for it to upload is a
 * terrible experience, and entirely avoidable — the metadata is in the first
 * few kilobytes.
 */
export async function preflight(
  file: File,
  limits: { maxBytes: number; maxDurationMs: number },
): Promise<PreflightResult> {
  if (file.size > limits.maxBytes) {
    return { ok: false, reason: `File is ${(file.size / 1e9).toFixed(1)}GB; limit is ${(limits.maxBytes / 1e9).toFixed(1)}GB.` };
  }

  const url = URL.createObjectURL(file);
  try {
    const meta = await new Promise<{ d: number; w: number; h: number }>((resolve, reject) => {
      const v = document.createElement("video");
      v.preload = "metadata";
      v.onloadedmetadata = () =>
        resolve({ d: v.duration * 1000, w: v.videoWidth, h: v.videoHeight });
      v.onerror = () => reject(new Error("Could not read video metadata"));
      v.src = url;
    });

    if (meta.d > limits.maxDurationMs) {
      return {
        ok: false,
        reason: `Clip is ${(meta.d / 1000).toFixed(0)}s; limit is ${limits.maxDurationMs / 1000}s.`,
      };
    }
    return { ok: true, durationMs: meta.d, width: meta.w, height: meta.h };
  } catch {
    // Unreadable metadata usually means an unusual codec. Let the server's
    // probe give the authoritative answer rather than guessing in the browser.
    return { ok: true, reason: "Could not read metadata locally; server will verify." };
  } finally {
    URL.revokeObjectURL(url);
  }
}

export interface UploadPart {
  part_number: number;
  url: string;
}

/** Upload parts in parallel and return the ETags the API needs to finalise. */
export async function uploadMultipart(
  file: File,
  parts: UploadPart[],
  onProgress?: (fraction: number) => void,
): Promise<{ part_number: number; etag: string }[]> {
  const results: { part_number: number; etag: string }[] = [];
  let done = 0;

  const queue = [...parts];
  const workers = Array.from({ length: Math.min(MAX_PARALLEL, parts.length) }, async () => {
    for (;;) {
      const part = queue.shift();
      if (!part) return;

      const start = (part.part_number - 1) * PART_SIZE;
      const blob = file.slice(start, Math.min(start + PART_SIZE, file.size));

      const res = await fetch(part.url, { method: "PUT", body: blob });
      if (!res.ok) throw new Error(`Part ${part.part_number} failed: ${res.status}`);

      const etag = res.headers.get("ETag");
      if (!etag) throw new Error(`Part ${part.part_number} returned no ETag`);

      results.push({ part_number: part.part_number, etag });
      onProgress?.(++done / parts.length);
    }
  });

  await Promise.all(workers);
  return results.sort((a, b) => a.part_number - b.part_number);
}
