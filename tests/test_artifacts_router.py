"""Artifact HTTP-endpoint regression tests.

Covers a recent 500 caused by the ArtifactResponse schema requiring
`proposal_id: UUID` while the model column is nullable — every
community is auto-seeded with a "Plan" artifact that has NULL
proposal_id, so the very first /artifacts/containers/community/{id}
read after community creation 500'd.
"""
from __future__ import annotations

import pytest

from tests.conftest import create_test_user, create_test_community


@pytest.mark.asyncio
async def test_list_containers_for_fresh_community_does_not_500(client):
    """A brand-new community has its seeded "Plan" artifact (no
    originating proposal). Listing containers must serialize that
    artifact cleanly — proposal_id can be null on the wire."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    r = await client.get(f"/artifacts/containers/community/{community['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list)
    # The seeded plan must be present and visible.
    all_artifacts = [a for c in body for a in c["artifacts"]]
    assert len(all_artifacts) >= 1
    # And the seeded artifact's proposal_id is null on the wire,
    # not a 500.
    seeded = next(
        (a for a in all_artifacts if a["proposal_id"] is None),
        None,
    )
    assert seeded is not None, (
        "expected at least one artifact with proposal_id=null "
        "(the seeded community Plan); got: "
        f"{[a['proposal_id'] for a in all_artifacts]}"
    )
