"""FastAPI dependencies for human-user session auth.

Two flavors:

- `get_current_user` — reads the session cookie, returns a User or None.
  Never raises. Use on any route that wants to know "is this request from
  a logged-in human" but still works for anonymous or agent requests.

- `require_user` — same lookup, but raises 401 if no valid session. Use
  on routes that only humans should hit (invite creation, profile).

Agents (server-internal) never set the session cookie, so they simply
return None from `get_current_user`. Existing endpoints that accept
`user_id` in the body remain usable by agents.
"""

from __future__ import annotations

import contextvars
import hmac

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.config import settings
from kbz.database import get_db
from kbz.models.user import User
from kbz.services.auth_service import AuthService

# Set per-request by `install_agent_auth`'s middleware. True iff the
# request carried a valid X-KBZ-Agent-Secret header AND a secret is
# configured. ContextVars are task-local under asyncio, so concurrent
# requests don't bleed into each other.
_agent_authorized: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "kbz_agent_authorized", default=False
)


def install_agent_auth(app) -> None:
    """Register the middleware that flags trusted-agent requests.

    Must be called on EVERY FastAPI app instance that serves the API —
    both kbz.main:app and the combined app built in
    agents/run_with_viewer.py (same pattern as install_error_handler).
    """

    @app.middleware("http")
    async def _agent_auth_mw(request: Request, call_next):
        secret = settings.agent_api_secret
        provided = request.headers.get("X-KBZ-Agent-Secret")
        ok = bool(secret) and bool(provided) and hmac.compare_digest(provided, secret)
        token = _agent_authorized.set(ok)
        try:
            return await call_next(request)
        finally:
            _agent_authorized.reset(token)


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    kbz_session: str | None = Cookie(
        default=None, alias=settings.auth_session_cookie
    ),
    authorization: str | None = Header(default=None),
) -> User | None:
    """Resolve the current user from EITHER a session cookie OR an
    `Authorization: Bearer <api_token>` header.

    Cookies are how the browser-based product authenticates; API tokens
    are how external bots (MCP servers, LangChain, curl scripts,
    custom agents) authenticate. Both look up into the same
    `auth_tokens` table — just different `token_type`.
    """
    svc = AuthService(db)
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1]:
            user = await svc.resolve_api_token(parts[1])
            if user is not None:
                return user
            # Malformed / expired bearer tokens fall through to cookie —
            # we don't want to reject a perfectly good browser session
            # just because an accidental header was set.
    if kbz_session:
        return await svc.resolve_session(kbz_session)
    return None


async def require_user(
    user: User | None = Depends(get_current_user),
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    return user


# The orchestrator creates a single "Big Brother" user account at sim
# bootstrap (see agents/orchestrator.py) — it's the operator account
# the viewer talks through. Big Brother has a session but is NOT a
# member of any community, so endpoints that gate on "must be active
# member" return 403 against the very viewer using them. Tag the BB
# username here so member-only read endpoints can let it pass.
OBSERVER_USER_NAME = "Big Brother"


def is_observer(user: User | None) -> bool:
    """True iff `user` is the simulation observer (Big Brother) account.

    Use as an OR-clause in member-gated read endpoints so the viewer's
    own dashboards don't 403 against the simulation it's running. Read
    endpoints only — write endpoints should still require real
    membership."""
    return user is not None and user.user_name == OBSERVER_USER_NAME


def enforce_session_matches_body(
    body_user_id,
    session_user: User | None,
) -> None:
    """If a session cookie is present, the body's user_id MUST match it.

    Use this at the top of any write endpoint that accepts a `user_id` in
    its request body (support, comment, support_pulse, create_proposal,
    …). Agents — which never carry a session cookie — are unaffected
    because `session_user` will be None.

    Raises 403 if a logged-in human tries to spoof another user.

    Cookieless callers (no session) used to be trusted unconditionally —
    the rationale was "agents have no cookie." But agents authenticate
    with a Bearer token (which populates session_user), so the ONLY
    callers reaching the cookieless branch are genuinely anonymous, and
    trusting them lets anyone POST {"user_id": "<victim>"} and act as that
    victim. When `agent_api_secret` is configured, the cookieless branch
    now requires the trusted-agent header (set by the middleware); the
    real simulation orchestrator carries it, anonymous internet callers
    don't. When the secret is empty (dev/test/legacy) the old permissive
    behavior is preserved so nothing breaks until an operator opts in.
    """
    if session_user is not None:
        # body_user_id may be uuid.UUID or str depending on pydantic model
        try:
            same = str(body_user_id) == str(session_user.id)
        except Exception:
            same = False
        if not same:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="session user does not match body.user_id",
            )
        return

    # Cookieless caller.
    if not settings.agent_api_secret:
        return  # secret disabled → legacy permissive behavior (dev/test)
    if _agent_authorized.get():
        return  # trusted simulation orchestrator
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication required to act as a user",
    )
