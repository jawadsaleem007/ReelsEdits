"""Constrained global assignment solver.

The objective has unary, pairwise and global terms (docs/09 section 2). With
pairwise terms this is a quadratic assignment problem, NP-hard in general --
but our slot sequence is a *chain*, not a graph: slot i interacts only with
i-1 and i+1. A chain-structured QAP is solvable exactly by dynamic programming
in O(n * m^2).

Global terms (variety, reuse spacing, saving the best shot for the biggest
moment) cannot be expressed pairwise, so they are handled by local search from
the DP optimum -- which converges in tens of iterations because the starting
point is already excellent.

    STAGE 1  candidate retrieval      (caller supplies; ANN in production)
    STAGE 2  unary scoring            O(n*m)
    STAGE 3  chain DP                 O(n*m^2)   <- exact for unary+pairwise
    STAGE 4  global repair            local search
    STAGE 5  window refinement        per assigned slot
"""

from __future__ import annotations

import time
from collections import defaultdict

from reelsedits_common import Blueprint, Slot
from reelsedits_common.enums import CutMode

from .scoring import NEG_INF, fit, seq
from .types import Candidate, MatchResult, Segment, SlotAssignment

DEFAULT_TOP_K = 40
WINDOW_STEP_MS = 100
MAX_REPAIR_ITERS = 200


# ---------------------------------------------------------------------------
# stage 5 (used by stage 2 as well): choose which part of a segment to use
# ---------------------------------------------------------------------------


def best_window(slot: Slot, seg: Segment) -> tuple[int, int, float]:
    """Pick the best sub-window of a segment for a slot.

    Naively using the segment's first N milliseconds wastes the good part of a
    long take. We scan and score, penalising windows that straddle an internal
    sub-shot boundary -- straddling means the rendered slot contains a cut we
    never planned, which destroys that slot's beat alignment.
    """
    need = slot.duration_ms
    lo, hi = seg.usable_in_ms, seg.usable_out_ms - need
    if hi < lo:
        return seg.usable_in_ms, seg.usable_in_ms + need, 0.0

    best = (lo, lo + need, NEG_INF)
    target_energy = slot.requirements.motion_energy

    for start in range(lo, hi + 1, WINDOW_STEP_MS):
        end = start + need
        centre = (start + end) / 2
        span = max(seg.usable_ms, 1)
        # Prefer the middle of the usable range: handheld starts and stops
        # cluster at the edges even after usable_ranges trimming.
        centrality = 1.0 - abs(centre - (seg.usable_in_ms + span / 2)) / (span / 2 + 1e-6)
        score = (
            0.5 * seg.quality
            + 0.3 * (1.0 - abs(seg.motion_energy - target_energy))
            + 0.2 * max(0.0, centrality)
        )
        if score > best[2]:
            best = (start, end, score)
    return best


# ---------------------------------------------------------------------------
# stage 2
# ---------------------------------------------------------------------------


def build_candidates(
    blueprint: Blueprint,
    segments: list[Segment],
    top_k: int = DEFAULT_TOP_K,
) -> dict[int, list[Candidate]]:
    """Score every segment against every slot; keep the top-k per slot.

    In production, stage 1 narrows ``segments`` via filtered ANN before this
    runs. This function is the exhaustive fallback and the test path.
    """
    per_slot: dict[int, list[Candidate]] = {}
    for slot in blueprint.slots:
        cands: list[Candidate] = []
        for seg in segments:
            score, _breakdown, reason = fit(slot, seg)
            if score == NEG_INF:
                continue
            in_ms, out_ms, window_score = best_window(slot, seg)
            cands.append(
                Candidate(
                    segment=seg,
                    in_ms=in_ms,
                    out_ms=out_ms,
                    fit=score + 0.05 * window_score,
                    reason=reason,
                )
            )
        cands.sort(key=lambda c: -c.fit)
        per_slot[slot.index] = cands[:top_k]
    return per_slot


# ---------------------------------------------------------------------------
# stage 3 -- exact chain DP
# ---------------------------------------------------------------------------


def chain_dp(
    blueprint: Blueprint,
    per_slot: dict[int, list[Candidate]],
) -> tuple[dict[int, Candidate], float]:
    """Exact solution of the unary + pairwise objective over the slot chain.

    V[i][c] = fit(i, c) + max over c' of ( V[i-1][c'] + seq(c', c) )

    Slots with no candidates are skipped, and the chain reconnects across the
    gap -- an unfilled slot must not sever the sequence reasoning for the slots
    around it.
    """
    order = [s.index for s in blueprint.slots if per_slot.get(s.index)]
    if not order:
        return {}, 0.0

    first = order[0]
    V: dict[int, list[float]] = {first: [c.fit for c in per_slot[first]]}
    back: dict[int, list[int]] = {first: [-1] * len(per_slot[first])}

    for prev_idx, idx in zip(order, order[1:]):
        prev_cands = per_slot[prev_idx]
        cands = per_slot[idx]

        # Transition context for this junction, if the blueprint defines one.
        cut = next((c for c in blueprint.cuts if c.to_slot == idx), None)
        tr = blueprint.transition_at(cut.index) if cut else None
        tr_type = tr.type if tr else None
        on_impact = cut.mode is CutMode.IMPACT if cut else False

        V[idx] = []
        back[idx] = []
        for c in cands:
            best_val, best_j = NEG_INF, -1
            for j, pc in enumerate(prev_cands):
                val = V[prev_idx][j] + seq(
                    pc, c, transition_type=tr_type, cut_on_impact=on_impact
                )
                if val > best_val:
                    best_val, best_j = val, j
            V[idx].append(best_val + c.fit)
            back[idx].append(best_j)

    last = order[-1]
    end = max(range(len(V[last])), key=lambda k: V[last][k])
    objective = V[last][end]

    chosen: dict[int, Candidate] = {}
    k = end
    for idx in reversed(order):
        chosen[idx] = per_slot[idx][k]
        k = back[idx][k]
        if k < 0:
            break
    return chosen, objective


# ---------------------------------------------------------------------------
# stage 4 -- global terms and repair
# ---------------------------------------------------------------------------


def global_score(
    blueprint: Blueprint, chosen: dict[int, Candidate]
) -> tuple[float, list[str]]:
    """Terms the chain DP structurally cannot see."""
    s = 0.0
    violations: list[str] = []
    cons = blueprint.constraints

    # Variety: the realised scale distribution should approximate the
    # reference's own mix, not merely avoid local repetition.
    realised: dict[str, int] = defaultdict(int)
    for c in chosen.values():
        realised[c.segment.shot_scale.value] += 1
    n = max(len(chosen), 1)
    for scale, target in blueprint.style.shot_scale_mix.items():
        actual = realised.get(scale, 0) / n
        s -= 0.25 * abs(actual - target)

    # Reuse: unavoidable with few segments and many slots. Make it invisible.
    by_segment: dict[str, list[tuple[int, Candidate]]] = defaultdict(list)
    for idx, c in sorted(chosen.items()):
        by_segment[c.segment.id].append((idx, c))

    for seg_id, uses in by_segment.items():
        if len(uses) > cons.max_segment_reuse:
            s -= 1.0 * (len(uses) - cons.max_segment_reuse)
            violations.append(
                f"segment {seg_id} used {len(uses)}x (max {cons.max_segment_reuse})"
            )
        for (i0, _), (i1, _) in zip(uses, uses[1:]):
            gap = blueprint.slots[i1].t_in_ms - blueprint.slots[i0].t_out_ms
            if gap < cons.min_reuse_gap_ms:
                s -= 0.6 * (1 - gap / max(cons.min_reuse_gap_ms, 1))
                violations.append(
                    f"segment {seg_id} reused after only {gap}ms "
                    f"(min {cons.min_reuse_gap_ms}ms)"
                )

    # Consecutive same source: the jump-cut guard, checked globally as well as
    # pairwise because the DP can be pushed into it by strong unary scores.
    run, prev_asset = 1, None
    for idx in sorted(chosen):
        asset = chosen[idx].segment.asset_id
        run = run + 1 if asset == prev_asset else 1
        if run > cons.max_consecutive_same_source:
            s -= 0.5
            violations.append(f"slot {idx}: {run} consecutive shots from one source")
        prev_asset = asset

    # Coverage: unfilled slots cost in proportion to importance.
    for slot in blueprint.slots:
        if slot.index not in chosen:
            s -= 1.2 * slot.importance

    # Save the best shot for the biggest moment. Without this the strongest
    # segment gets spent in slot 3 and the drop lands on something mediocre.
    peak = max(blueprint.slots, key=lambda x: x.importance)
    if peak.index in chosen:
        s += 0.5 * chosen[peak.index].segment.quality

    return s, violations


def repair(
    blueprint: Blueprint,
    per_slot: dict[int, list[Candidate]],
    chosen: dict[int, Candidate],
    base_objective: float,
    max_iters: int = MAX_REPAIR_ITERS,
) -> tuple[dict[int, Candidate], float, list[str]]:
    """Hill-climb on the full objective, starting from the DP optimum."""

    def total(sel: dict[int, Candidate]) -> float:
        unary = sum(c.fit for c in sel.values())
        pairwise = 0.0
        idxs = sorted(sel)
        for a, b in zip(idxs, idxs[1:]):
            cut = next((c for c in blueprint.cuts if c.to_slot == b), None)
            tr = blueprint.transition_at(cut.index) if cut else None
            pairwise += seq(
                sel[a], sel[b],
                transition_type=tr.type if tr else None,
                cut_on_impact=cut.mode is CutMode.IMPACT if cut else False,
            )
        g, _ = global_score(blueprint, sel)
        return unary + pairwise + g

    current = dict(chosen)
    best_val = total(current)

    for _ in range(max_iters):
        improved = False
        for idx, cands in per_slot.items():
            if not cands:
                continue
            incumbent = current.get(idx)
            # Locked slots are the user's choice; the matcher works around them.
            if incumbent is not None and getattr(incumbent, "locked", False):
                continue
            for alt in cands[:8]:
                if incumbent is not None and alt.segment.id == incumbent.segment.id:
                    continue
                trial = dict(current)
                trial[idx] = alt
                val = total(trial)
                if val > best_val + 1e-9:
                    current, best_val, improved = trial, val, True
                    break
            if improved:
                break
        if not improved:
            break

    _, violations = global_score(blueprint, current)
    return current, best_val, violations


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def match(
    blueprint: Blueprint,
    segments: list[Segment],
    *,
    top_k: int = DEFAULT_TOP_K,
    locked: dict[int, str] | None = None,
) -> MatchResult:
    """Assign segments to blueprint slots.

    ``locked`` maps slot index -> segment id for slots the user has pinned.
    """
    t0 = time.perf_counter()

    per_slot = build_candidates(blueprint, segments, top_k=top_k)

    # A locked slot has exactly one candidate, so both the DP and the repair
    # loop are forced to route around it rather than optimise it away.
    if locked:
        for idx, seg_id in locked.items():
            keep = [c for c in per_slot.get(idx, []) if c.segment.id == seg_id]
            if keep:
                per_slot[idx] = keep[:1]

    chosen, objective = chain_dp(blueprint, per_slot)
    chosen, objective, violations = repair(blueprint, per_slot, chosen, objective)

    assignments: list[SlotAssignment] = []
    for slot in blueprint.slots:
        c = chosen.get(slot.index)
        if c is None:
            continue
        in_ms, out_ms, _ = best_window(slot, c.segment)
        _, breakdown, reason = fit(slot, c.segment)
        assignments.append(
            SlotAssignment(
                slot_index=slot.index,
                segment_id=c.segment.id,
                in_ms=in_ms,
                out_ms=out_ms,
                score=round(min(1.0, max(0.0, c.fit)), 4),
                reason=reason,
                breakdown={k: round(v, 3) for k, v in breakdown.items()},
            )
        )

    unfilled = [s.index for s in blueprint.slots if s.index not in chosen]

    if assignments:
        weights = [blueprint.slots[a.slot_index].importance for a in assignments]
        confidence = sum(a.score * w for a, w in zip(assignments, weights)) / sum(weights)
        confidence *= len(assignments) / len(blueprint.slots)
    else:
        confidence = 0.0

    return MatchResult(
        assignments=assignments,
        unfilled=unfilled,
        overall_confidence=round(confidence, 4),
        objective=round(objective, 4),
        solve_ms=round((time.perf_counter() - t0) * 1000, 2),
        violations=violations,
    )
