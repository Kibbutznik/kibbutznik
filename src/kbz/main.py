import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
