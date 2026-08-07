"""FFmpeg backend — blueprint + assets → a real MP4.

Builds a single filter graph rather than rendering per-slot files and
concatenating. Concatenation forces a re-encode at every join or produces
timestamp discontinuities; one graph gives frame-exact transitions and one
encode pass.

Determinism (docs/10 §1): fixed encoder settings, no wall-clock, no sampling.
The same blueprint plus the same assets produces the same bytes.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from reelsedits_common import Blueprint
from reelsedits_common.enums import MusicStrategy, TransitionType

log = logging.getLogger("reelsedits.renderer")

RENDERER_VERSION = "0.2.0"

PRESETS: dict[str, dict] = {
    "preview": {"w": 540, "h": 960, "crf": 26, "preset": "veryfast", "grain": False},
    "1080p": {"w": 1080, "h": 1920, "crf": 18, "preset": "medium", "grain": True},
    "4k": {"w": 2160, "h": 3840, "crf": 18, "preset": "slow", "grain": True},
}

#: FFmpeg xfade names for the transitions we can express natively. Anything not
#: here degrades to a hard cut and is recorded as a compromise -- rendering a
#: whip pan as an undirected smear reads as a bug, so we would rather not.
XFADE = {
    TransitionType.CROSS_DISSOLVE: "fade",
    TransitionType.FADE_BLACK: "fadeblack",
    TransitionType.FADE_WHITE: "fadewhite",
    TransitionType.SLIDE: "slideleft",
    TransitionType.PUSH: "slideright",
    TransitionType.ZOOM_IN: "zoomin",
}


@dataclass(slots=True)
class RenderResult:
    output: Path
    duration_ms: int
    width: int
    height: int
    bytes: int
    preset: str
    renderer_version: str
    compromises: list[dict] = field(default_factory=list)
    command: str = ""


class RenderError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# filter graph
# ---------------------------------------------------------------------------


def _grade_filters(bp: Blueprint) -> str:
    """Translate the parametric grade into ffmpeg colour filters.

    Nudges the user's footage toward the reference's measured statistics rather
    than applying the reference's absolute values -- the user's footage starts
    from a different exposure, and applying someone else's grade wholesale is
    how you get crushed blacks (docs/08 §5.2).
    """
    target = bp.grade.match_target
    if target is None or bp.grade.confidence < 0.35:
        return ""

    parts = []
    if target.sat_mean is not None:
        # Conservative pull toward the target: full correction on a low-
        # confidence estimate does more harm than good.
        sat = max(0.4, min(1.8, 0.6 + 0.9 * target.sat_mean))
        parts.append(f"saturation={sat:.3f}")

    pct = target.luma_percentiles or {}
    if "p50" in pct:
        mid = float(pct["p50"])
        gamma = max(0.6, min(1.6, 0.45 / max(mid, 0.05)))
        parts.append(f"gamma={gamma:.3f}")

    if not parts:
        return ""
    return "eq=" + ":".join(parts)


def build_filter_graph(
    bp: Blueprint,
    sources: list[dict],
    preset: dict,
) -> tuple[str, list[str], list[dict]]:
    """Return ``(filter_complex, output_labels, compromises)``.

    ``sources`` is one entry per slot: ``{path, in_ms, out_ms}``.
    """
    w, h = preset["w"], preset["h"]
    fps = bp.canvas.fps
    grade = _grade_filters(bp)
    compromises: list[dict] = []
    chains: list[str] = []
    labels: list[str] = []

    for i, src in enumerate(sources):
        dur_s = (src["out_ms"] - src["in_ms"]) / 1000.0
        f = [
            f"[{i}:v]trim=start={src['in_ms'] / 1000:.4f}:duration={dur_s:.4f}",
            "setpts=PTS-STARTPTS",
            f"fps={fps}",
            # scale+crop preserves subject scale and fills the frame. Letterboxing
            # to black on a 9:16 platform reads as low-effort content.
            f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=bicubic",
            f"crop={w}:{h}",
            "setsar=1",
        ]
        if grade:
            f.append(grade)
        f.append("format=yuv420p")
        chains.append(",".join(f) + f"[v{i}]")
        labels.append(f"v{i}")

    # Chain transitions left to right. xfade consumes both inputs and emits one
    # stream, so each step folds the accumulated output with the next slot.
    current = labels[0]
    acc_ms = sources[0]["out_ms"] - sources[0]["in_ms"]

    for i in range(1, len(labels)):
        cut = next((c for c in bp.cuts if c.to_slot == i), None)
        tr = bp.transition_at(cut.index) if cut else None
        slot_ms = sources[i]["out_ms"] - sources[i]["in_ms"]

        xfade_name = XFADE.get(tr.type) if tr else None
        if tr and xfade_name is None:
            compromises.append({
                "kind": "transition_substituted",
                "slot": i,
                "severity": "minor",
                "detail": f"{tr.type.value} not expressible in the ffmpeg backend; used a hard cut.",
            })

        if xfade_name and tr:
            d = min(tr.duration_ms, slot_ms // 2, acc_ms // 2) / 1000.0
            if d < 0.04:
                xfade_name = None

        out = f"x{i}"
        if xfade_name and tr:
            d = min(tr.duration_ms, slot_ms // 2, acc_ms // 2) / 1000.0
            offset = (acc_ms / 1000.0) - d
            chains.append(
                f"[{current}][v{i}]xfade=transition={xfade_name}"
                f":duration={d:.4f}:offset={offset:.4f}[{out}]"
            )
            acc_ms = acc_ms + slot_ms - int(d * 1000)
        else:
            chains.append(f"[{current}][v{i}]concat=n=2:v=1:a=0[{out}]")
            acc_ms += slot_ms
        current = out

    return ";".join(chains), [current], compromises


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------


def _audio_plan(bp: Blueprint, n_sources: int) -> tuple[str, str | None, list[dict]]:
    """Decide the audio track.

    platform_attach and silent both emit a silent master: the creator attaches
    the sound in-app under the platform's licence. The video still gets a real
    (silent) audio stream, because a file with no audio stream at all confuses
    some platform uploaders.
    """
    notes: list[dict] = []
    binding = bp.audio.music_binding

    if binding is None or binding.strategy.emits_silent_master:
        if binding and binding.strategy is MusicStrategy.PLATFORM_ATTACH:
            card = binding.platform_attach
            notes.append({
                "kind": "platform_attach",
                "severity": "minor",
                "detail": (
                    f"Silent master. Attach '{card.sound_name or 'the original sound'}' "
                    f"in-app at {card.trim_start_ms}ms; cuts are on the "
                    f"{card.bpm:.0f} BPM grid." if card else "Silent master."
                ),
            })
        return "anullsrc=channel_layout=stereo:sample_rate=48000", None, notes

    if binding.strategy is MusicStrategy.USER_SUPPLIED and binding.track_id:
        return "", binding.track_id, notes

    raise RenderError(
        f"music strategy '{binding.strategy.value}' requires a resolved licensed "
        "track; the renderer will not run without one"
    )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def render(
    bp: Blueprint,
    clip_paths: dict[str, Path],
    output: Path,
    *,
    preset: str = "preview",
    dry_run: bool = False,
) -> RenderResult:
    """Render a bound blueprint to a video file.

    ``clip_paths`` maps segment_id -> source file.
    """
    if preset not in PRESETS:
        raise RenderError(f"unknown preset {preset!r}; expected one of {list(PRESETS)}")
    cfg = PRESETS[preset]

    if bp.provenance.renderer_min_version > RENDERER_VERSION:
        raise RenderError(
            f"blueprint requires renderer >= {bp.provenance.renderer_min_version}; "
            f"this is {RENDERER_VERSION}. Refusing rather than silently ignoring "
            "features we do not understand."
        )

    sources: list[dict] = []
    for slot in bp.slots:
        a = slot.assignment
        if a is None:
            continue
        path = clip_paths.get(a.segment_id)
        if path is None:
            raise RenderError(f"slot {slot.index}: no source file for {a.segment_id}")
        sources.append({"path": path, "in_ms": a.in_ms, "out_ms": a.out_ms,
                        "slot": slot.index})

    if not sources:
        raise RenderError("blueprint has no bound slots; run the matcher first")

    graph, out_labels, compromises = build_filter_graph(bp, sources, cfg)
    # user_track is returned for the USER_SUPPLIED path, which is not wired
    # into the ffmpeg command yet -- it needs an extra input and a mix stage.
    audio_src, _user_track, audio_notes = _audio_plan(bp, len(sources))
    compromises.extend(audio_notes)

    cmd: list[str] = ["ffmpeg", "-y", "-loglevel", "error"]
    for src in sources:
        cmd += ["-i", str(src["path"])]
    if audio_src:
        cmd += ["-f", "lavfi", "-t", f"{bp.canvas.duration_ms / 1000:.3f}", "-i", audio_src]

    cmd += [
        "-filter_complex", graph,
        "-map", f"[{out_labels[0]}]",
        "-map", f"{len(sources)}:a",
        "-c:v", "libx264",
        "-preset", cfg["preset"],
        "-crf", str(cfg["crf"]),
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
        # --- determinism (docs/10 §1) -------------------------------------
        # Multithreaded libx264 does NOT produce bit-identical output across
        # runs by default: thread scheduling perturbs lookahead decisions, and
        # two renders of identical input differ. This silently breaks render
        # caching, the marketplace guarantee that a purchased blueprint
        # reproduces its preview, and reproducible debugging.
        #
        # x264's `deterministic` flag exists for exactly this. It keeps
        # threading (measured FASTER than -threads 1) while guaranteeing
        # reproducible output. Do not remove without a passing determinism test.
        "-x264-params", "deterministic=1",
        # Strip wall-clock metadata, which would otherwise differ per render.
        "-fflags", "+bitexact", "-flags", "+bitexact",
        "-map_metadata", "-1",
        "-movflags", "+faststart",
        "-shortest",
        str(output),
    ]

    printable = " ".join(shlex.quote(c) for c in cmd)
    if dry_run:
        return RenderResult(output=output, duration_ms=bp.canvas.duration_ms,
                            width=cfg["w"], height=cfg["h"], bytes=0, preset=preset,
                            renderer_version=RENDERER_VERSION,
                            compromises=compromises, command=printable)

    output.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not output.exists():
        raise RenderError(
            f"ffmpeg failed (exit {proc.returncode}):\n{proc.stderr[-2500:]}"
        )

    total_ms = sum(s["out_ms"] - s["in_ms"] for s in sources)
    return RenderResult(
        output=output,
        duration_ms=total_ms,
        width=cfg["w"],
        height=cfg["h"],
        bytes=output.stat().st_size,
        preset=preset,
        renderer_version=RENDERER_VERSION,
        compromises=compromises,
        command=printable,
    )
