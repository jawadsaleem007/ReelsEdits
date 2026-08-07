"""Blueprint to execution graph.

Everything that can fail must fail HERE, in planning. A render that dies at 80%
has burned 40 GPU-seconds and a user's patience. Planning is cheap: validate
durations, licences, codec support, VRAM estimates and speed feasibility before
decoding a single frame.

See docs/10 section 2.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from reelsedits_common import Blueprint, Slot, SpeedTrack
from reelsedits_common.enums import CompromiseKind, SpeedMode

#: Fixed composite order. Each position is a decision -- see docs/10 section 5.
#: Grain sits AFTER grade because grain is a property of the medium and lives
#: downstream of colour; grading grain looks wrong. Vignette sits in global
#: effects AFTER transitions, because a per-slot vignette pops at every cut.
COMPOSITE_ORDER = (
    "decode", "reframe", "speed", "grade", "slot_effects",
    "motion", "transition", "global_effects", "text", "encode",
)


@dataclass(slots=True)
class Op:
    kind: str
    slot: int | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionGraph:
    ops: list[Op]
    canvas: dict[str, Any]
    estimated_vram_mb: int
    estimated_gpu_seconds: float
    compromises: list[dict[str, Any]] = field(default_factory=list)

    def sorted_ops(self) -> list[Op]:
        order = {k: i for i, k in enumerate(COMPOSITE_ORDER)}
        return sorted(self.ops, key=lambda o: (o.slot or 0, order.get(o.kind, 99)))


def source_duration_for(output_ms: int, speed: SpeedTrack | None) -> int:
    """How much SOURCE a slot consumes at its speed setting.

    A 620ms slot at 0.4x needs 1550ms of source. Discovering that the segment
    is shorter than that at frame 300 of the render is the failure mode this
    exists to prevent.
    """
    if speed is None or speed.mode is SpeedMode.CONSTANT:
        factor = speed.factor if speed else 1.0
        return int(output_ms * factor)
    if speed.mode is SpeedMode.RAMP and speed.keyframes:
        # Integrate the speed curve: source consumed = sum(dt * speed(t))
        total = 0.0
        for a, b in zip(speed.keyframes, speed.keyframes[1:]):
            dt = b.t_ms - a.t_ms
            total += dt * (a.value + b.value) / 2.0
        return int(total)
    if speed.mode is SpeedMode.FREEZE:
        return int(output_ms - (speed.freeze_hold_ms or 0))
    return output_ms


def plan(
    blueprint: Blueprint,
    assets: dict[str, dict[str, Any]],
    *,
    preset: Literal["preview", "1080p", "4k", "master"] = "preview",
    renderer_version: str = "2.1.0",
) -> ExecutionGraph:
    """Compile a blueprint into an execution graph, or fail loudly."""
    if blueprint.provenance.renderer_min_version > renderer_version:
        raise ValueError(
            f"blueprint requires renderer >= {blueprint.provenance.renderer_min_version}; "
            f"this is {renderer_version}. Refusing rather than silently ignoring "
            "features we do not understand."
        )

    binding = blueprint.audio.music_binding
    if binding is not None and binding.strategy.requires_licence and not binding.licence_id:
        raise ValueError(
            "music_binding requires a licence and none is resolved; "
            "constraints.require_licensed_audio is not configurable"
        )

    ops: list[Op] = []
    compromises: list[dict[str, Any]] = []

    for slot in blueprint.slots:
        a = slot.assignment
        if a is None:
            continue

        speed = next((s for s in blueprint.speed if s.slot == slot.index), None)
        needed = source_duration_for(slot.duration_ms, speed)

        if needed > a.duration_ms:
            # Flatten the ramp rather than fail: a missing ramp is far less
            # visible than a render that dies.
            compromises.append({
                "kind": CompromiseKind.SPEED_FLATTENED.value,
                "slot": slot.index,
                "severity": "moderate",
                "detail": f"Ramp needs {needed}ms of source; only {a.duration_ms}ms available.",
            })
            speed = None
            needed = slot.duration_ms

        ops.append(Op("decode", slot.index,
                      {"asset": a.segment_id, "in_ms": a.in_ms, "out_ms": a.in_ms + needed}))

        reframe = next((r for r in blueprint.reframe if r.slot == slot.index), None)
        ops.append(Op("reframe", slot.index, {"track": reframe}))

        if speed is not None:
            ops.append(Op("speed", slot.index, {"track": speed}))

        ops.append(Op("grade", slot.index, {"params": _grade_for(blueprint, slot)}))

        for i, eff in enumerate(e for e in blueprint.effects if e.slot == slot.index):
            ops.append(Op("slot_effects", slot.index, {"effect": eff, "index": i}))

        for m in (m for m in blueprint.motion if m.slot == slot.index):
            ops.append(Op("motion", slot.index, {"track": m}))

    for cut in blueprint.cuts:
        tr = blueprint.transition_at(cut.index)
        if tr is not None:
            ops.append(Op("transition", cut.to_slot, {"transition": tr, "cut": cut}))

    for eff in (e for e in blueprint.effects if e.scope == "global"):
        ops.append(Op("global_effects", None, {"effect": eff}))

    if blueprint.captions.enabled:
        ops.append(Op("text", None, {"captions": blueprint.captions}))

    ops.append(Op("encode", None, {"preset": preset}))

    return ExecutionGraph(
        ops=ops,
        canvas=blueprint.canvas.model_dump(),
        estimated_vram_mb=_estimate_vram(blueprint, preset),
        estimated_gpu_seconds=_estimate_gpu_seconds(blueprint, preset),
        compromises=compromises,
    )


def _grade_for(blueprint: Blueprint, slot: Slot) -> dict[str, Any]:
    override = next((g for g in blueprint.grade.per_slot if g.slot == slot.index), None)
    params = override.params if override else blueprint.grade.global_
    return params.model_dump()


_PRESET_SCALE = {"preview": 0.25, "1080p": 1.0, "4k": 4.0, "master": 6.0}


def _estimate_vram(blueprint: Blueprint, preset: str) -> int:
    px = blueprint.canvas.width * blueprint.canvas.height * _PRESET_SCALE.get(preset, 1.0)
    frame_mb = px * 4 / 1_048_576                    # RGBA32
    layers = min(blueprint.constraints.max_effect_layers, 4) + 3   # in/out/work buffers
    return int(frame_mb * layers * 8 + 1024)         # 8-frame lookahead + model overhead


def _estimate_gpu_seconds(blueprint: Blueprint, preset: str) -> float:
    seconds = blueprint.canvas.duration_ms / 1000
    base = seconds * 0.8 * _PRESET_SCALE.get(preset, 1.0)
    effect_cost = 0.04 * len(blueprint.effects) * seconds
    flow_cost = 0.9 * sum(
        1 for s in blueprint.speed if s.interpolation == "optical_flow"
    )
    return round(base + effect_cost + flow_cost, 2)
