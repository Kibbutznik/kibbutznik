import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from kbz.config import settings
from kbz.database import async_session
from kbz.services.auth_service import AuthService
from kbz.services.event_bus import event_bus

router = APIRouter()


@router.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """Real-time event stream — every governance event across the
    platform.

    Pre-fix this was unauthenticated. Any client could connect and
    receive every event in the system: proposal text, comment text,
    support, pulse outcomes, member identity changes — i.e. real-time
    exfiltration of every private community deliberation. Now require
    a valid session (cookie OR `?token=` query param for clients that
    can't send cookies on a WS upgrade) before subscribing. Connections
    without a valid session are closed with policy-violation 1008.
    """
    raw = (
        websocket.cookies.get(settings.auth_session_cookie)
        or websocket.query_params.get("token")
    )
    if not raw:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    async with async_session() as db:
        svc = AuthService(db)
        user = await svc.resolve_session(raw)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    queue = event_bus.subscribe()
    try:
        while True:
            event = await queue.get()
            await websocket.send_text(event.model_dump_json())
    except WebSocketDisconnect:
        event_bus.unsubscribe(queue)
    except Exception:
        event_bus.unsubscribe(queue)
