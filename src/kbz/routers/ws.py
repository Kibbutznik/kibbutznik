
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kbz.services.event_bus import event_bus

router = APIRouter()


@router.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
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
