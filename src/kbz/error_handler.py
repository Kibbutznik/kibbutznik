"""Global exception handler + /admin/errors ring buffer.

Pre-fix the default FastAPI behavior on an uncaught exception was to
return the full Python traceback to the caller. On a single-box prod
with anonymous traffic, that leaks file paths, SQLAlchemy connection
strings, and the entire frame stack to strangers.

This module exports `install(app)` so both entrypoints
(`kbz.main:app` and the combined app built in
`agents.run_with_viewer`) register the same handler + the same
`/admin/errors` ring-buffer endpoint. The two app definitions are an
architectural wart we live with for now; the launch-prep plan flags
it for a Tuesday-after-launch cleanup.
"""
from __future__ import annotations

import collections
import logging
import time
import traceback
import uuid as _uuid

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from kbz.auth_deps import get_current_user
from kbz.models.user import User

logger = logging.getLogger(__name__)

# Module-level ring buffer. Single deque shared across all callers
# (we run a single process). Capped at 100 entries — enough to
# diagnose a launch incident, small enough to never bloat memory.
_ERROR_RING: collections.deque = collections.deque(maxlen=100)


def install(app: FastAPI) -> None:
    """Wire the global exception handler + /admin/errors onto the
    given app. Idempotent: calling twice on the same app would
    replace the existing handler (FastAPI behavior) — fine."""

    @app.exception_handler(Exception)
    async def _global_exception_handler(request: Request, exc: Exception):
        """Catch anything not already wrapped as HTTPException.
        Returns a clean JSON 500 to the client; full traceback logged
        + stashed in the ring buffer for /admin/errors inspection."""
        err_id = _uuid.uuid4().hex[:12]
        tb = traceback.format_exc()
        logger.exception(
            "unhandled exception id=%s path=%s method=%s",
            err_id, request.url.path, request.method,
        )
        _ERROR_RING.append({
            "id": err_id,
            "ts": time.time(),
            "path": request.url.path,
            "method": request.method,
            "exc_type": type(exc).__name__,
            "exc_msg": str(exc)[:400],
            "traceback": tb[-2000:],  # last 2KB usually contains the cause
        })
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "error_id": err_id,
            },
        )

    @app.get("/admin/errors")
    async def list_recent_errors(user: User | None = Depends(get_current_user)):
        """Returns the last 100 unhandled-exception events. Admin-gated
        via the existing admin_user_ids list in settings — if the
        list is empty, the endpoint is locked off entirely."""
        from kbz.config import settings as _s
        admin_ids = {
            x.strip() for x in (_s.admin_user_ids or "").split(",") if x.strip()
        }
        if not admin_ids:
            # Hard 403 when no admins are configured — don't expose
            # tracebacks just because someone forgot the env var.
            return JSONResponse(
                status_code=403, content={"detail": "admin not configured"},
            )
        if user is None or str(user.id) not in admin_ids:
            return JSONResponse(
                status_code=403, content={"detail": "forbidden"},
            )
        return {"errors": list(_ERROR_RING)}
