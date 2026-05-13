import asyncio
import collections
import logging
import time
import traceback
import uuid as _uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from kbz.database import async_session
from kbz.routers import (
    actions,
    artifacts,
    audit,
    auth,
    closeness,
    comments,
    export as export_router,
    communities,
    flags,
    highlights,
    invites,
    me,
    members,
    memory,
    metrics,
    notifications,
    proposals,
    pulses,
    reasons,
    search,
    statements,
    tkg,
    users,
    wallet_webhook,
    wallets,
    ws,
)
from kbz.services.artifact_service import ArtifactService
from kbz.services.event_bus import event_bus
from kbz.services.tkg_ingestor import TKGIngestor

logger = logging.getLogger(__name__)

app = FastAPI(
    title="KBZ - Kibutznik Governance Platform",
    description="Pulse-based direct democracy governance API",
    version="0.1.0",
)

# CORS: previously `allow_origins=["*"]` blanket-allowed every browser
# origin. We never set `allow_credentials=True` so the wildcard never
# combined with cookies — but keeping `*` was a latent footgun: any
# future change to enable credentialed CORS would have instantly turned
# every state-changing route into a CSRF target. Drive the origin list
# from config and default to the empty string (no CORS header sent),
# which is safe for an API consumed only by same-origin pages and
# server-to-server clients.
from kbz.config import settings as _cors_settings  # avoid shadow at top
_origins = [o.strip() for o in (_cors_settings.cors_allow_origins or "").split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,  # explicit; flip to True only with a CSRF token
    )

app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(communities.router, prefix="/communities", tags=["communities"])
app.include_router(members.router, tags=["members"])
app.include_router(proposals.router, tags=["proposals"])
app.include_router(pulses.router, tags=["pulses"])
app.include_router(reasons.router, tags=["reasons"])
app.include_router(statements.router, tags=["statements"])
app.include_router(actions.router, tags=["actions"])
app.include_router(comments.router, tags=["comments"])
app.include_router(closeness.router, tags=["closeness"])
app.include_router(audit.router, tags=["audit"])
app.include_router(artifacts.router)
app.include_router(memory.router, tags=["memory"])
app.include_router(tkg.router)
app.include_router(metrics.router)
app.include_router(export_router.router, tags=["export"])
app.include_router(search.router, tags=["search"])
app.include_router(flags.router, tags=["flags"])
app.include_router(highlights.router)
app.include_router(auth.router)
app.include_router(invites.router)
app.include_router(me.router)
app.include_router(notifications.router)
app.include_router(wallets.router)
app.include_router(wallet_webhook.router)
app.include_router(ws.router, tags=["websocket"])


@app.get("/health")
async def health():
    return {"status": "ok"}


# ─── Global error handling (HN launch hardening) ──────────────────
#
# FastAPI's default behavior for an uncaught exception is to return
# the full Python traceback to the caller. On a single 4GB box with
# Plausible analytics watching and an HN crowd inspecting our every
# response, that means a single Postgres hiccup leaks file paths,
# SQLAlchemy connection strings, and stack frames to anonymous
# strangers.
#
# Below: replace that with a clean JSON 500 + a small in-memory
# ring buffer of the last 100 5xx events, queryable from
# /admin/errors (admin-gated). Full traceback still goes to
# journalctl via logger.exception.

_ERROR_RING: collections.deque = collections.deque(maxlen=100)


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    """Catch anything not already wrapped as HTTPException. Returns
    a clean JSON 500 to the client; full traceback logged + stashed
    in the ring buffer for /admin/errors inspection."""
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
        "traceback": tb[-2000:],  # last 2KB — usually contains the actual cause
    })
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "error_id": err_id,
        },
    )


from fastapi import Depends as _Depends
from kbz.auth_deps import get_current_user as _get_current_user
from kbz.models.user import User as _User


@app.get("/admin/errors")
async def list_recent_errors(user: _User | None = _Depends(_get_current_user)):
    """Returns the last 100 unhandled-exception events. Admin-gated
    via the existing admin_user_ids list in settings — if the list
    is empty, the endpoint is locked off entirely. Each event
    includes path, exc_type, message, and the last 2KB of traceback
    (enough to identify the cause without dumping the entire frame
    stack)."""
    from kbz.config import settings as _s
    admin_ids = {x.strip() for x in (_s.admin_user_ids or "").split(",") if x.strip()}
    if not admin_ids:
        # Hard 403 when no admins are configured — don't expose
        # tracebacks just because someone forgot to set the env var.
        return JSONResponse(status_code=403, content={"detail": "admin not configured"})
    if user is None or str(user.id) not in admin_ids:
        return JSONResponse(status_code=403, content={"detail": "forbidden"})
    return {"errors": list(_ERROR_RING)}


# ---- Artifact cascade subscriber ----
# Listens on the event bus for proposal.accepted / proposal.rejected events.
# When the proposal is the auto-generated parent EditArtifact for some
# sub-action's pending container, flips that container accordingly.

async def _artifact_cascade_loop() -> None:
    queue = event_bus.subscribe()
    try:
        while True:
            event = await queue.get()
            if event.event_type not in ("proposal.accepted", "proposal.rejected"):
                continue
            proposal_id_str = event.data.get("proposal_id")
            if not proposal_id_str:
                continue
            try:
                import uuid as _uuid
                proposal_id = _uuid.UUID(str(proposal_id_str))
            except (ValueError, TypeError):
                continue
            try:
                async with async_session() as session:
                    svc = ArtifactService(session)
                    if event.event_type == "proposal.accepted":
                        await svc.on_parent_proposal_accepted(proposal_id)
                    else:
                        await svc.on_parent_proposal_rejected(proposal_id)
                    await session.commit()
            except Exception as e:
                logger.exception("Artifact cascade handler failed: %s", e)
    except asyncio.CancelledError:
        event_bus.unsubscribe(queue)
        raise


@app.on_event("startup")
async def _start_artifact_cascade() -> None:
    app.state._artifact_cascade_task = asyncio.create_task(_artifact_cascade_loop())


@app.on_event("shutdown")
async def _stop_artifact_cascade() -> None:
    task = getattr(app.state, "_artifact_cascade_task", None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ---- TKG ingestor -----------------------------------------------------
# Subscribes to event_bus and writes nodes/edges into the temporal knowledge
# graph in real time. Embeddings are offloaded to an internal queue so the
# hot ingest path never blocks on Ollama.

@app.on_event("startup")
async def _start_tkg_ingestor() -> None:
    ingestor = TKGIngestor(async_session)
    await ingestor.start()
    app.state._tkg_ingestor = ingestor


@app.on_event("shutdown")
async def _stop_tkg_ingestor() -> None:
    ingestor: TKGIngestor | None = getattr(app.state, "_tkg_ingestor", None)
    if ingestor is not None:
        await ingestor.stop()
