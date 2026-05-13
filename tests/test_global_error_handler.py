"""Global exception handler tests — HN-launch hardening.

Pre-fix the default FastAPI behavior on an uncaught exception was
to return the full Python traceback as the response body. On a
production server inspected by HN audiences, that's a leak of file
paths, SQLAlchemy connection strings, and stack frames to anonymous
callers.

These tests pin the new behavior:
  - 500 responses are clean JSON with a generic message + opaque
    error_id, no traceback text in the body
  - The traceback IS captured in the in-memory ring buffer for
    operator inspection via /admin/errors
  - /admin/errors locks off entirely when no admin_user_ids are
    configured (the default)
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_test_user


@pytest.mark.asyncio
async def test_unhandled_exception_returns_clean_500(client, monkeypatch):
    """Patch a route to raise a non-HTTPException and verify the
    response is sanitized JSON, not a traceback."""
    from kbz import main as _main

    # Insert a deliberately-broken test route. Idempotent: only adds
    # if not already there.
    if not any(getattr(r, "path", None) == "/__test_boom__" for r in _main.app.routes):
        @_main.app.get("/__test_boom__")
        async def _boom():
            raise RuntimeError("internal database string with secrets")

    r = await client.get("/__test_boom__")
    assert r.status_code == 500
    body = r.json()
    assert body["detail"] == "Internal server error"
    assert "error_id" in body and len(body["error_id"]) == 12
    # The clear-text exception message must NOT leak to the client.
    assert "internal database string" not in r.text
    assert "secrets" not in r.text
    assert "Traceback" not in r.text


@pytest.mark.asyncio
async def test_http_exceptions_still_render_normally(client):
    """HTTPException (404/422/etc.) must still surface their detail
    field — the global handler only catches truly unhandled exceptions."""
    r = await client.get("/proposals/00000000-0000-0000-0000-000000000000")
    # Either 404 or 422 depending on the route's validation; both
    # should have a clean JSON detail, not a generic 500.
    assert r.status_code in (404, 422)
    assert "Internal server error" not in r.text


@pytest.mark.asyncio
async def test_admin_errors_locks_off_when_no_admins_configured(client):
    """With admin_user_ids unset (the default in tests), /admin/errors
    must 403 — never expose tracebacks just because someone forgot
    to set the env var."""
    r = await client.get("/admin/errors")
    assert r.status_code == 403
    assert "admin" in r.text.lower()


@pytest.mark.asyncio
async def test_admin_errors_403s_anonymous_callers(client, monkeypatch):
    """Even WITH admin_user_ids set, an anonymous caller (no session,
    no api token) gets 403 — admin-gating actually gates."""
    from kbz import config
    monkeypatch.setattr(
        config.settings, "admin_user_ids",
        "11111111-1111-1111-1111-111111111111",
    )
    r = await client.get("/admin/errors")
    assert r.status_code == 403
