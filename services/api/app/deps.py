"""Request dependencies: authentication, tenancy, rate limiting, idempotency."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request

from . import errors
from .config import Settings, get_settings


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller and their tenancy."""

    user_id: str
    org_id: str
    plan: str
    role: str
    scopes: frozenset[str] = frozenset()

    def require(self, scope: str) -> None:
        if scope not in self.scopes and self.role not in ("owner", "admin"):
            raise errors.ProblemDetail(
                type_="forbidden",
                title="Insufficient permissions",
                status=403,
                detail=f"This action requires the '{scope}' scope.",
                fix="Ask an org owner to grant the scope.",
            )


async def require_principal(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Validate the bearer token and establish tenancy.

    The returned ``org_id`` is also set as the Postgres session variable
    ``app.current_org_id`` so row-level security applies. The application
    check is the fast path; RLS is the backstop for the day a worker query
    forgets a WHERE clause. docs/11 section 3.
    """
    # Local single-user mode: one default org, no login. The tenancy boundary
    # is still enforced everywhere (every query filters on org_id), so adding
    # real auth is a change to this function rather than to every handler.
    if settings.environment == "local":
        return Principal(
            user_id="local", org_id="default", plan="pro", role="owner",
            scopes=frozenset({"render", "upload", "publish", "billing"}),
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise errors.ProblemDetail(
            type_="unauthenticated",
            title="Missing or malformed credentials",
            status=401,
            detail="Provide an 'Authorization: Bearer <token>' header.",
            fix="Obtain a token from the dashboard or your OIDC provider.",
        )

    # TODO: verify JWT signature against JWKS, check iss/aud/exp,
    #       resolve org membership and plan
    raise NotImplementedError("JWT verification not yet implemented")


def idempotency_fingerprint(org_id: str, key: str, body: bytes) -> str:
    """Cache key for idempotent replay.

    The body hash is what makes this safe: keying on the client-supplied key
    alone lets an accidentally-reused UUID return someone else's render.
    docs/12 section 6.
    """
    return hashlib.sha256(
        b"|".join([org_id.encode(), key.encode(), hashlib.sha256(body).digest()])
    ).hexdigest()


#: Requests/minute and concurrent renders by plan. Rate limits protect
#: infrastructure; quotas protect margin. They are different mechanisms and
#: conflating them causes outages during legitimate bursts.
RATE_LIMITS: dict[str, dict[str, int]] = {
    "free":       {"rpm": 60,   "concurrent_renders": 1,  "uploads_per_min": 10},
    "creator":    {"rpm": 300,  "concurrent_renders": 2,  "uploads_per_min": 60},
    "pro":        {"rpm": 1200, "concurrent_renders": 5,  "uploads_per_min": 200},
    "team":       {"rpm": 3000, "concurrent_renders": 12, "uploads_per_min": 500},
    "enterprise": {"rpm": 20000,"concurrent_renders": 50, "uploads_per_min": 2000},
}

#: Renders per billing period by plan. See docs/17.
RENDER_QUOTAS: dict[str, int] = {
    "free": 3, "creator": 40, "pro": 150, "team": 600, "enterprise": 100_000,
}
