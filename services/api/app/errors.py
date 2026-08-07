"""RFC 9457 problem details.

Every error carries a stable ``type`` URI and a ``fix`` telling the caller
what to do about it. An error a client cannot act on becomes a support ticket.
See docs/12 section 5.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

BASE = "https://reelsedits.com/errors"


class ProblemDetail(Exception):
    """An error with a machine-readable type and an actionable fix."""

    def __init__(
        self,
        *,
        type_: str,
        title: str,
        status: int,
        detail: str,
        fix: str | None = None,
        headers: dict[str, str] | None = None,
        **extra: Any,
    ) -> None:
        self.type = f"{BASE}/{type_}"
        self.title = title
        self.status = status
        self.detail = detail
        self.fix = fix
        self.headers = headers or {}
        self.extra = extra
        super().__init__(detail)

    def to_dict(self, instance: str) -> dict[str, Any]:
        body = {
            "type": self.type,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "instance": instance,
        }
        if self.fix:
            body["fix"] = self.fix
        body.update(self.extra)
        return body


async def problem_handler(request: Request, exc: ProblemDetail) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content=exc.to_dict(str(request.url.path)),
        headers={"Content-Type": "application/problem+json", **exc.headers},
    )


# ---------------------------------------------------------------------------
# constructors -- one per documented error type
# ---------------------------------------------------------------------------


def invalid_blueprint(detail: str, errors: list[dict] | None = None) -> ProblemDetail:
    return ProblemDetail(
        type_="invalid-blueprint",
        title="Blueprint failed validation",
        status=400,
        detail=detail,
        fix="Validate against schemas/blueprint.schema.json before submitting.",
        errors=errors or [],
    )


def quota_exceeded(used: int, limit: int, period_end: str) -> ProblemDetail:
    return ProblemDetail(
        type_="quota-exceeded",
        title="Render quota exhausted for this period",
        status=402,
        detail=f"Used {used} of {limit} renders.",
        fix="Upgrade the plan, or wait for the quota to reset.",
        quota_used=used,
        quota_limit=limit,
        period_end=period_end,
    )


def licence_required(track_id: str | None = None) -> ProblemDetail:
    """Cannot be worked around by any client.

    Mirrors the schema-level constraint in docs/06 section 3.1: the renderer
    refuses to run without a resolved licence.
    """
    return ProblemDetail(
        type_="licence-required",
        title="Music licence not resolved",
        status=403,
        detail=(
            "The blueprint's music_binding requires a licence and none is attached. "
            "This is a hard constraint and cannot be disabled."
        ),
        fix="POST /v1/music/match to select a licensed catalogue track, or supply "
            "your own track with a rights attestation.",
        track_id=track_id,
    )


def insufficient_coverage(coverage: float, floor: float, gaps: list[dict]) -> ProblemDetail:
    return ProblemDetail(
        type_="insufficient-coverage",
        title="Not enough footage for this style",
        status=409,
        detail=f"Coverage is {coverage:.2f}; the floor for rendering is {floor:.2f}.",
        fix="Add clips covering the listed gaps, or resubmit with "
            "acknowledge_degradation=true to render a compromised edit.",
        coverage=coverage,
        floor=floor,
        gaps=gaps,
    )


def idempotency_conflict() -> ProblemDetail:
    return ProblemDetail(
        type_="idempotency-conflict",
        title="Idempotency key reused with a different body",
        status=409,
        detail="This Idempotency-Key was already used for a different request.",
        fix="Use a fresh Idempotency-Key for a different request body.",
    )


def unsupported_media(codec: str | None, container: str | None) -> ProblemDetail:
    return ProblemDetail(
        type_="unsupported-media",
        title="Unsupported media format",
        status=415,
        detail=f"Cannot decode container={container!r} codec={codec!r}.",
        fix="Re-encode to H.264, H.265 or AV1 in MP4 or MOV.",
        codec=codec,
        container=container,
    )


def source_not_fetchable(domain: str) -> ProblemDetail:
    return ProblemDetail(
        type_="source-not-fetchable",
        title="This source cannot be fetched automatically",
        status=422,
        detail=f"We do not fetch references from {domain}.",
        fix="POST /v1/assets with kind=reference to upload the file directly, "
            "then submit the asset_id.",
        domain=domain,
    )


def renderer_version_too_old(required: str, available: str) -> ProblemDetail:
    return ProblemDetail(
        type_="renderer-version-too-old",
        title="Blueprint requires a newer renderer",
        status=422,
        detail=f"Blueprint needs renderer >= {required}; deployment has {available}.",
        fix="Retry later, or downgrade the blueprint to a compatible version.",
        required=required,
        available=available,
    )


def rate_limited(retry_after: int) -> ProblemDetail:
    return ProblemDetail(
        type_="rate-limited",
        title="Rate limit exceeded",
        status=429,
        detail="Too many requests.",
        fix=f"Retry after {retry_after}s. Use the X-RateLimit-* headers for backpressure.",
        headers={"Retry-After": str(retry_after)},
    )


def capacity(retry_after: int = 30) -> ProblemDetail:
    return ProblemDetail(
        type_="capacity",
        title="GPU capacity temporarily exhausted",
        status=503,
        detail="All render workers are saturated.",
        fix=f"Retry after {retry_after}s.",
        headers={"Retry-After": str(retry_after)},
    )
