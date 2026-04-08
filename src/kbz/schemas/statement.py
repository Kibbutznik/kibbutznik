import uuid
from datetime import datetime

from pydantic import BaseModel


class StatementResponse(BaseModel):
    id: uuid.UUID
    community_id: uuid.UUID
    statement_text: str
    status: int
    prev_statement_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}
