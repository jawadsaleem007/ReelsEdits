"""Determinism guarantees and the source-manifest assertion.

    render(blueprint, assets, renderer_version) -> bit-identical output

This is the precondition for render caching, the style marketplace,
collaboration, the API, and reproducible debugging. See docs/10 section 1.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def render_cache_key(
    blueprint_doc: dict[str, Any],
    assignment: list[dict[str, Any]],
    asset_ids: list[str],
    renderer_version: str,
    preset: str,
) -> str:
    """Content-addressed identity of a render.

    Equal keys must imply byte-identical output. ``renderer_version`` is
    mandatory: ship a better compositor and every cached render must miss.
    Omit it and you serve output from a superseded renderer indefinitely,
    which is the kind of bug discovered three months later by a confused user.
    """
    payload = json.dumps(
        {
            "blueprint": blueprint_doc,
            "assignment": sorted(assignment, key=lambda a: a["slot"]),
            "assets": sorted(asset_ids),
            "renderer": renderer_version,
            "preset": preset,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def effect_seed(blueprint_id: str, slot_index: int, effect_index: int) -> int:
    """Deterministic PRNG seed for effects that need visual variation.

    Grain, particles and shake should vary across the timeline but must not
    vary between two renders of the same blueprint. Seeding from position
    rather than from the clock gives both.
    """
    h = hashlib.sha256(f"{blueprint_id}:{slot_index}:{effect_index}".encode())
    return int.from_bytes(h.digest()[:8], "big")


class SourceManifestViolation(RuntimeError):
    """Raised when a render's inputs include anything other than user uploads
    and licensed catalogue assets."""


def source_manifest_ok(
    manifest: list[dict[str, Any]],
    *,
    org_id: str,
    licensed_asset_ids: set[str],
) -> bool:
    """Assert that no reference media can reach the output.

    The architectural rule from docs/01 section 1 is that no pixel and no audio
    sample from the reference ever reaches the output. The blueprint schema
    makes it structurally impossible to CARRY such data; this function makes it
    testable at render time, which is the difference between an intention and a
    guarantee.

    Raises rather than returning False, because a violation is a bug that must
    stop the render, not a condition to branch on.
    """
    for item in manifest:
        origin = item.get("origin")
        if origin == "user_upload":
            if item.get("org_id") != org_id:
                raise SourceManifestViolation(
                    f"asset {item.get('id')} belongs to another org"
                )
        elif origin == "licensed_catalogue":
            if item.get("id") not in licensed_asset_ids:
                raise SourceManifestViolation(
                    f"catalogue asset {item.get('id')} has no resolved licence"
                )
        elif origin == "generated":
            if not item.get("provenance"):
                raise SourceManifestViolation(
                    f"generated asset {item.get('id')} lacks C2PA provenance"
                )
        else:
            raise SourceManifestViolation(
                f"asset {item.get('id')} has disallowed origin {origin!r}; "
                "only user_upload, licensed_catalogue and generated may reach a render"
            )
    return True
