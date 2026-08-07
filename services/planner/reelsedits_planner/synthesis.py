"""Blueprint synthesis from fused analysis.

The only non-deterministic component in the system. It sits UPSTREAM of the
blueprint: its output is schema-validated and invariant-checked, then frozen.
Everything downstream is a pure function of the result.

See docs/04-ai-pipeline.md stage 8.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from reelsedits_common import Blueprint

SYSTEM_PROMPT = """\
You are a senior short-form video editor. You are given a structured analysis of
one reference video: its beat grid, section structure, shot boundaries, camera
motion, semantic labels, colour statistics and detected transitions.

Produce an Editing Blueprint that captures HOW this video was edited, in a form
that can be applied to COMPLETELY DIFFERENT footage.

Four rules, in priority order:

1. Describe requirements, never references. Slot 7 is not "the wheel close-up
   from the reference". It is "a close, low-angle shot of a mechanical detail
   with moderate motion energy and the subject on the left third". The
   requirement must be satisfiable by footage of an entirely different subject.

2. Express pacing as rules, not timestamps. "Cut density accelerates from 0.9/s
   to 2.6/s across the build and resolves on the downbeat at the drop" adapts to
   a different-length track. A list of fixed cut times does not.

3. Mark what is load-bearing. Some decisions carry the edit (the shot on the
   drop; the ramp into the chorus). Others are texture (a mid-verse cutaway).
   Set importance accordingly, because low-importance slots are what gets
   dropped when the user has insufficient footage.

4. Say when you are unsure. Where the analysis confidence is low, write
   conservative instructions and record why in provenance.notes. A confident
   wrong instruction is worse than an honest hedge.

Never copy content: not the reference's words, not its music, not its subject
matter. You are transcribing technique, not the performance.
"""


@dataclass(slots=True)
class PlannerConfig:
    model: str = "gemini-2.5-pro"
    tier: Literal["frontier", "fallback"] = "frontier"
    #: Low but non-zero. Zero produces degenerate, repetitive slot requirements;
    #: anything higher makes reanalysis of the same reference non-reproducible.
    temperature: float = 0.2
    seed: int = 42
    max_input_tokens: int = 24_000
    timeout_s: float = 25.0


async def synthesise_blueprint(
    fused_analysis: dict[str, Any],
    config: PlannerConfig,
    *,
    analyzer_version: str,
    renderer_min_version: str,
) -> Blueprint:
    """Turn measurement into an adaptable specification.

    Four things the planner does that pure measurement cannot:

    1. Explains intent, so pacing can adapt to a different track.
    2. Generalises reference shots into slot requirements, so a car reference
       can render onto motorcycle footage.
    3. Separates essential decisions from incidental texture, so degradation
       under insufficient footage is principled.
    4. Flags low confidence, so the UI can be honest about it.

    On frontier-API failure we fall back to an open-weight VLM planner and
    stamp ``planner_tier='fallback'``. Degraded but functional -- the system
    does not go down when an API does.
    """
    # TODO: serialise fused_analysis compactly (target 8-20k tokens)
    # TODO: call the model with constrained JSON decoding against
    #       schemas/blueprint.schema.json
    # TODO: on failure, retry once, then fall back to the open-weight planner
    # TODO: validate -- Blueprint.model_validate() raises on any invariant
    #       violation (overlapping slots, dangling indices, min_shot_ms,
    #       unlicensed music, undeclared degradation)
    # TODO: stamp provenance with model, tier, seed, analyzer_version
    raise NotImplementedError


async def replan(
    blueprint: Blueprint,
    violations: list[str],
    config: PlannerConfig,
) -> Blueprint:
    """Repair a blueprint that failed constraint checks.

    This is the one place an agentic loop belongs: the violation list is a
    concrete, machine-checkable goal, and the schema plus constraint checks are
    the verifier. Bounded, verifiable, and it terminates -- unlike an open-ended
    agentic planner, which would multiply our only paid inference by the number
    of steps.
    """
    # TODO: present violations, request a targeted edit, re-validate, max 3 rounds
    raise NotImplementedError
