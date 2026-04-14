import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.database import get_db
from kbz.enums import ArtifactStatus, ContainerStatus
from kbz.models.action import Action
from kbz.models.artifact import Artifact
from kbz.models.artifact_container import ArtifactContainer
from kbz.schemas.artifact import (
    ArtifactContainerResponse,
    ArtifactResponse,
    ContainerWithArtifactsResponse,
)
from kbz.services.artifact_service import ArtifactService

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("/containers/community/{community_id}", response_model=list[ContainerWithArtifactsResponse])
async def list_containers_for_community(
    community_id: uuid.UUID,
    include_history: int = 0,
    db: AsyncSession = Depends(get_db),
):
    svc = ArtifactService(db)
    containers = await svc.list_containers(community_id)
    out: list[ContainerWithArtifactsResponse] = []
    for c in containers:
        artifacts = await svc.list_artifacts(c.id, include_history=bool(include_history))
        out.append(
            ContainerWithArtifactsResponse(
                container=ArtifactContainerResponse.model_validate(c),
                artifacts=[ArtifactResponse.model_validate(a) for a in artifacts],
            )
        )
    return out


@router.get("/containers/{container_id}", response_model=ContainerWithArtifactsResponse)
async def get_container(
    container_id: uuid.UUID,
    include_history: int = 0,
    db: AsyncSession = Depends(get_db),
):
    svc = ArtifactService(db)
    container = await svc.get_container(container_id)
    if not container:
        raise HTTPException(status_code=404, detail="Container not found")
    artifacts = await svc.list_artifacts(container.id, include_history=bool(include_history))
    return ContainerWithArtifactsResponse(
        container=ArtifactContainerResponse.model_validate(container),
        artifacts=[ArtifactResponse.model_validate(a) for a in artifacts],
    )


@router.get("/{artifact_id}/history", response_model=list[ArtifactResponse])
async def get_artifact_history(artifact_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = ArtifactService(db)
    chain = await svc.get_history(artifact_id)
    if not chain:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return [ArtifactResponse.model_validate(a) for a in chain]


@router.get("/communities/{community_id}/work_tree")
async def get_work_tree(community_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Recursive view: containers in this community, each with its artifacts,
    each artifact's child containers (delegations into sub-Actions), and so on.
    """
    svc = ArtifactService(db)
    visited: set[uuid.UUID] = set()

    async def render_container(c: ArtifactContainer) -> dict:
        if c.id in visited:
            return {"id": str(c.id), "title": c.title, "cycle": True}
        visited.add(c.id)
        artifacts = await svc.list_artifacts(c.id, include_history=False)
        artifact_nodes = []
        for a in artifacts:
            # Look for child containers delegated from this artifact.
            res = await db.execute(
                select(ArtifactContainer).where(
                    ArtifactContainer.delegated_from_artifact_id == a.id
                )
            )
            child_containers = list(res.scalars().all())
            children = [await render_container(cc) for cc in child_containers]
            artifact_nodes.append(
                {
                    "id": str(a.id),
                    "title": a.title,
                    "content": a.content,
                    "author_user_id": str(a.author_user_id),
                    "proposal_id": str(a.proposal_id) if a.proposal_id else None,
                    "is_plan": getattr(a, 'is_plan', False),
                    "status": a.status,
                    "delegated_to": children,
                }
            )
        return {
            "id": str(c.id),
            "community_id": str(c.community_id),
            "title": c.title,
            "mission": c.mission,
            "status": c.status,
            "delegated_from_artifact_id": str(c.delegated_from_artifact_id)
            if c.delegated_from_artifact_id
            else None,
            "committed_content": c.committed_content,
            "artifacts": artifact_nodes,
        }

    containers = await svc.list_containers(community_id)
    return [await render_container(c) for c in containers]
