import uuid
from datetime import datetime

from pydantic import BaseModel


class ArtifactResponse(BaseModel):
    id: uuid.UUID
    container_id: uuid.UUID
    community_id: uuid.UUID
    title: str | None
    content: str
    author_user_id: uuid.UUID
    proposal_id: uuid.UUID
    prev_artifact_id: uuid.UUID | None
    status: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ArtifactContainerResponse(BaseModel):
    id: uuid.UUID
    community_id: uuid.UUID
    delegated_from_artifact_id: uuid.UUID | None
    title: str
    mission: str | None = None
    status: int
    pending_parent_proposal_id: uuid.UUID | None
    committed_content: str | None
    committed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ContainerWithArtifactsResponse(BaseModel):
    container: ArtifactContainerResponse
    artifacts: list[ArtifactResponse]
