#!/usr/bin/env python3
"""Verify render determinism under CPU contention.

Run before any renderer release. The CI determinism test asserts the guarantee
on an idle machine, which passes trivially; this asserts it under the condition
where it actually broke.

Background: ffmpeg threads the filter graph and the encoder independently, and
both default to the CPU count. Unpinned, renders diverged under load and would
have differed between machines with different core counts — silently corrupting
the render cache in any multi-node deployment, where "busy" is the normal state.
Both are now pinned to fixed counts (see `ffmpeg_render.X264_THREADS` and
`FILTER_THREADS`), and changing either is a `RENDERER_VERSION` bump because it
changes the encoded bytes.

    python tools/stress_determinism.py reference.mp4 clips/ --renders 4 --load 4
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("reference")
    ap.add_argument("clips")
    ap.add_argument("--renders", type=int, default=4)
    ap.add_argument("--load", type=int, default=4, help="background CPU hogs")
    ap.add_argument("--preset", default="preview")
    args = ap.parse_args()

    from reelsedits_analyzer.audio import analyze_audio
    from reelsedits_analyzer.fusion import build_blueprint
    from reelsedits_analyzer.visual import analyze_visual
    from reelsedits_common import Assignment
    from reelsedits_indexer.index import index_directory
    from reelsedits_matcher import match
    from reelsedits_renderer.ffmpeg_render import (
        FILTER_THREADS,
        RENDERER_VERSION,
        X264_THREADS,
        render,
    )

    ref, clips_dir = Path(args.reference), Path(args.clips)
    print(f"renderer {RENDERER_VERSION}  x264_threads={X264_THREADS}  "
          f"filter_threads={FILTER_THREADS}")

    visual = analyze_visual(ref)
    audio = analyze_audio(ref, visual.profile.duration_ms)
    bp = build_blueprint(audio, visual)

    clips = index_directory(clips_dir)
    paths = {s.id: c.path for c in clips for s in c.segments}
    result = match(bp, [s for c in clips for s in c.segments])
    for x in result.assignments:
        bp.slots[x.slot_index].assignment = Assignment(
            segment_id=x.segment_id, in_ms=x.in_ms, out_ms=x.out_ms, score=x.score
        )
    print(f"{len(result.assignments)} slots bound; rendering {args.renders}x "
          f"under {args.load}x CPU load")

    load = [subprocess.Popen(["sh", "-c", "while :; do :; done"])
            for _ in range(args.load)]
    digests: list[str] = []
    try:
        for i in range(args.renders):
            out = Path(f"/tmp/reelsedits-stress-{i}.mp4")
            t0 = time.time()
            render(bp, paths, out, preset=args.preset)
            digest = hashlib.sha256(out.read_bytes()).hexdigest()
            digests.append(digest)
            print(f"  render {i}: {time.time() - t0:5.1f}s  {digest[:16]}")
            out.unlink(missing_ok=True)
    finally:
        # Kill unconditionally: leaking busy-loops would degrade every
        # subsequent process on the machine.
        for p in load:
            p.kill()
            p.wait()

    if len(set(digests)) == 1:
        print(f"\nDETERMINISTIC across {args.renders} renders under {args.load}x load")
        return 0

    print(f"\nFAILED: {len(set(digests))} distinct outputs from identical inputs")
    for i, d in enumerate(digests):
        print(f"  {i}: {d}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
