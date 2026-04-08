import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel


class Event(BaseModel):
    event_type: str
    community_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    data: dict[str, Any] = {}
    timestamp: datetime = None

    def model_post_init(self, __context):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


class EventBus:
    """In-memory async event bus for broadcasting governance events."""

    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.remove(queue)

    async def publish(self, event: Event) -> None:
        for queue in self._subscribers:
            await queue.put(event)

    async def emit(
        self,
        event_type: str,
        community_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        **data,
    ) -> None:
        # Convert UUIDs in data to strings for JSON serialization
        serializable_data = {}
        for k, v in data.items():
            serializable_data[k] = str(v) if isinstance(v, uuid.UUID) else v

        event = Event(
            event_type=event_type,
            community_id=community_id,
            user_id=user_id,
            data=serializable_data,
        )
        await self.publish(event)


# Global singleton
event_bus = EventBus()
