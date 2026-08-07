"""ReelsEdits CLI — the whole pipeline, runnable.

    reelsedits analyze  reference.mp4 -o blueprint.json
    reelsedits index    clips/
    reelsedits build    reference.mp4 clips/ -o out.mp4

`build` runs the full chain: analyse the reference, index the footage, match
clips to slots, render an MP4.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from reelsedits_analyzer.audio import analyze_audio
from reelsedits_analyzer.fusion import build_blueprint
from reelsedits_analyzer.visual import analyze_visual
from reelsedits_common import Assignment
from reelsedits_common.enums import MusicStrategy
from reelsedits_indexer.index import index_directory
from reelsedits_matcher import match
from reelsedits_renderer.ffmpeg_render import render

log = logging.getLogger("reelsedits.cli")

DIM, BOLD, GREEN, YELLOW, RED, RESET = (
    "\033[2m", "\033[1m", "\033[32m", "\033[33m", "\033[31m", "\033[0m"
)


def _step(msg: str) -> None:
    print(f"{BOLD}▸{RESET} {msg}", flush=True)


def _detail(msg: str) -> None:
    print(f"  {DIM}{msg}{RESET}", flush=True)


def _warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET} {msg}", flush=True)


# ---------------------------------------------------------------------------


def cmd_analyze(args: argparse.Namespace) -> int:
    ref = Path(args.reference)
    if not ref.exists():
        print(f"{RED}not found:{RESET} {ref}", file=sys.stderr)
        return 1

    _step(f"Analysing {ref.name}")
    visual = analyze_visual(ref)
    _detail(f"{visual.profile.width}×{visual.profile.height} @ {visual.profile.fps}fps, "
            f"{visual.profile.duration_ms / 1000:.1f}s")
    _detail(f"{len(visual.shots)} shots (confidence {visual.sbd_confidence:.2f})")

    audio = analyze_audio(ref, visual.profile.duration_ms)
    _detail(f"{audio.bpm:.1f} BPM, {len(audio.beat_grid_ms)} beats "
            f"(confidence {audio.confidence:.2f})")
    _detail(f"{len(audio.sections)} sections: "
            f"{', '.join(s['kind'] for s in audio.sections)}")
    if audio.impacts:
        _detail(f"{len(audio.impacts)} impacts, strongest at "
                f"{max(audio.impacts, key=lambda i: i['strength'])['t_ms'] / 1000:.1f}s")

    bp = build_blueprint(
        audio, visual,
        name=args.name or ref.stem,
        music_strategy=MusicStrategy(args.music),
        sound_name=args.sound_name,
        platform=args.platform,
    )

    out = Path(args.output or ref.with_suffix(".blueprint.json"))
    out.write_text(bp.model_dump_json(indent=2, by_alias=True, exclude_none=True))

    _step("Style card")
    print(f"\n{bp.style.summary}\n")
    p = bp.style.pacing
    print(f"  {'Pacing':<14} {p.cuts_per_second:.2f} cuts/sec · "
          f"{p.beat_lock_ratio:.0%} beat-locked · offset {p.offset_mean_ms:+.0f}ms")
    print(f"  {'Slots':<14} {len(bp.slots)} · {len(bp.cuts)} cuts · "
          f"{len(bp.transitions)} transitions")
    print(f"  {'Shot mix':<14} " + " · ".join(
        f"{k.replace('_', ' ')} {v:.0%}" for k, v in
        sorted(bp.style.shot_scale_mix.items(), key=lambda kv: -kv[1])[:4]))
    print(f"  {'Tags':<14} {', '.join(bp.style.tags)}")

    weak = bp.low_confidence_subsystems()
    if weak:
        print(f"  {'Approximate':<14} {YELLOW}{', '.join(weak)}{RESET}")

    print(f"\n{GREEN}✓{RESET} blueprint → {out}  ({out.stat().st_size / 1024:.1f} KB)")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    d = Path(args.clips)
    if not d.is_dir():
        print(f"{RED}not a directory:{RESET} {d}", file=sys.stderr)
        return 1

    _step(f"Indexing {d}")
    clips = index_directory(d)
    if not clips:
        print(f"{RED}no usable clips found{RESET}", file=sys.stderr)
        return 1

    total = sum(len(c.segments) for c in clips)
    _detail(f"{len(clips)} clips → {total} segments")
    print()
    print(f"  {'clip':<22}{'segs':>5}{'quality':>9}  {'scales'}")
    for c in clips:
        scales = ", ".join(sorted({s.shot_scale.value for s in c.segments}))
        print(f"  {c.path.name[:21]:<22}{len(c.segments):>5}{c.quality_overall:>9.2f}  "
              f"{DIM}{scales}{RESET}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    ref, clips_dir = Path(args.reference), Path(args.clips)
    out = Path(args.output)

    if not ref.exists():
        print(f"{RED}reference not found:{RESET} {ref}", file=sys.stderr)
        return 1
    if not clips_dir.is_dir():
        print(f"{RED}clips directory not found:{RESET} {clips_dir}", file=sys.stderr)
        return 1

    # 1 --------------------------------------------------------------------
    _step(f"Analysing reference: {ref.name}")
    visual = analyze_visual(ref)
    audio = analyze_audio(ref, visual.profile.duration_ms)
    bp = build_blueprint(
        audio, visual,
        name=args.name or ref.stem,
        music_strategy=MusicStrategy(args.music),
        sound_name=args.sound_name,
        platform=args.platform,
    )
    _detail(f"{len(bp.slots)} slots · {bp.audio.bpm:.0f} BPM · "
            f"{bp.style.pacing.cuts_per_second:.2f} cuts/sec · "
            f"offset {bp.style.pacing.offset_mean_ms:+.0f}ms")

    # 2 --------------------------------------------------------------------
    _step(f"Indexing footage: {clips_dir}")
    clips = index_directory(clips_dir)
    if not clips:
        print(f"{RED}no usable clips{RESET}", file=sys.stderr)
        return 1
    segments = [s for c in clips for s in c.segments]
    path_by_segment = {s.id: c.path for c in clips for s in c.segments}
    _detail(f"{len(clips)} clips → {len(segments)} segments")

    # 3 --------------------------------------------------------------------
    _step("Matching clips to slots")
    result = match(bp, segments)
    _detail(f"coverage {result.coverage:.0%} · confidence "
            f"{result.overall_confidence:.2f} · solved in {result.solve_ms:.0f}ms")

    if result.unfilled:
        _warn(f"{len(result.unfilled)} slots unfilled: {result.unfilled[:8]}")
    for v in result.violations[:4]:
        _warn(v)

    if result.coverage < 0.55 and not args.force:
        print(f"\n{RED}✗ coverage {result.coverage:.0%} is below the 0.55 floor.{RESET}")
        print("  The system will not silently render something bad.")
        print("  Add footage covering the gaps, or pass --force to accept degradation.")
        return 2

    for a in result.assignments:
        bp.slots[a.slot_index].assignment = Assignment(
            segment_id=a.segment_id, in_ms=a.in_ms, out_ms=a.out_ms,
            score=a.score, reason=a.reason,
        )

    if args.show_matches:
        print()
        for a in result.assignments[:12]:
            print(f"  slot {a.slot_index:>2} ← {a.segment_id[:26]:<28}"
                  f"{a.score:.2f}  {DIM}{a.reason}{RESET}")

    # 4 --------------------------------------------------------------------
    _step(f"Rendering ({args.preset})")
    r = render(bp, path_by_segment, out, preset=args.preset)
    _detail(f"{r.width}×{r.height} · {r.duration_ms / 1000:.1f}s · "
            f"{r.bytes / 1024:.0f} KB")
    for c in r.compromises[:5]:
        _warn(c["detail"])

    if args.blueprint:
        bpp = Path(args.blueprint)
        bpp.write_text(bp.model_dump_json(indent=2, by_alias=True, exclude_none=True))
        _detail(f"blueprint → {bpp}")

    # 5 --------------------------------------------------------------------
    binding = bp.audio.music_binding
    if binding and binding.strategy is MusicStrategy.PLATFORM_ATTACH and binding.platform_attach:
        card = binding.platform_attach
        print(f"\n{BOLD}Music — attach in-app{RESET}")
        print(f"  {card.instructions}")
        print(f"  {DIM}This file has no music track. We never redistribute the "
              f"original recording;{RESET}")
        print(f"  {DIM}the platform's own licence covers it when you add it there.{RESET}")

    print(f"\n{GREEN}✓{RESET} {out}")
    return 0


# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="reelsedits",
        description="Style transfer for video editing.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def add_music_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--music", default="platform_attach",
            choices=["platform_attach", "silent"],
            help="platform_attach (default): silent master + instructions to add the "
                 "original sound in-app, under the platform's licence.",
        )
        sp.add_argument("--sound-name", default=None,
                        help="Name of the sound as shown in the app, for the attach card.")
        sp.add_argument("--platform", default="unknown",
                        choices=["tiktok", "instagram", "youtube", "unknown"])

    a = sub.add_parser("analyze", help="Analyse a reference into a blueprint")
    a.add_argument("reference")
    a.add_argument("-o", "--output")
    a.add_argument("--name")
    add_music_args(a)
    a.set_defaults(func=cmd_analyze)

    i = sub.add_parser("index", help="Index a directory of clips")
    i.add_argument("clips")
    i.set_defaults(func=cmd_index)

    b = sub.add_parser("build", help="Reference + footage → finished video")
    b.add_argument("reference")
    b.add_argument("clips")
    b.add_argument("-o", "--output", default="out.mp4")
    b.add_argument("--preset", default="preview", choices=["preview", "1080p", "4k"])
    b.add_argument("--blueprint", help="Also write the bound blueprint here")
    b.add_argument("--show-matches", action="store_true")
    b.add_argument("--force", action="store_true",
                   help="Render even below the coverage floor, accepting degradation")
    b.add_argument("--name")
    add_music_args(b)
    b.set_defaults(func=cmd_build)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return args.func(args)
    except Exception as exc:
        print(f"\n{RED}✗ {exc}{RESET}", file=sys.stderr)
        if args.verbose:
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
