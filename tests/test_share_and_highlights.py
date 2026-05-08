"""Public sharing surface — Phase 2a + 2b.

Covers:
  - GET /artifacts/{id}/share renders HTML with OG/Twitter meta
    populated from the artifact's title and content excerpt.
  - The share page is visibility-gated: artifacts whose root
    community has Visibility=private must 404, even with a
    valid id.
  - GET /highlights returns recent accepted proposals from public
    root communities only, omits private ones, dedupes consecutive
    edits to the same artifact, and is cached at the response level.
"""
from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import update as _upd

from kbz.enums import ProposalStatus, ProposalType, ArtifactStatus
from kbz.models.artifact import Artifact
from kbz.models.artifact_container import ArtifactContainer
from kbz.models.community import Community
from kbz.models.proposal import Proposal
from kbz.models.variable import Variable
from tests.conftest import create_test_user, create_test_community


# ── /artifacts/{id}/share ───────────────────────────────────────

@pytest.mark.asyncio
async def test_share_page_renders_with_og_meta(client, db):
    """Happy path: a public community + an active artifact yields
    HTML with og:title, og:description, twitter:card meta tags."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"], "ShareCo")
    cid = _uuid.UUID(community["id"])

    # Inject a container + active artifact directly.
    container_id = _uuid.uuid4()
    db.add(ArtifactContainer(
        id=container_id, community_id=cid, status=1,
        title="Handbook", delegated_from_artifact_id=None,
    ))
    art_id = _uuid.uuid4()
    db.add(Artifact(
        id=art_id, container_id=container_id, community_id=cid,
        title="Onboarding", content="Welcome new members.\n\n## Steps\n\n- Buddy assignment\n- First pulse",
        author_user_id=_uuid.UUID(user["id"]),
        proposal_id=None,
        status=int(ArtifactStatus.ACTIVE),
        is_plan=False,
    ))
    await db.commit()

    r = await client.get(f"/artifacts/{art_id}/share")
    assert r.status_code == 200, r.text
    body = r.text
    assert "<title>Onboarding — ShareCo on Kibbutznik</title>" in body
    assert 'property="og:title" content="Onboarding"' in body
    assert 'property="og:description"' in body
    assert "Welcome new members" in body  # excerpt
    assert 'name="twitter:card"' in body
    assert "## Steps" not in body  # markdown should be rendered, not raw
    assert "<h2>Steps</h2>" in body
    assert "Buddy assignment" in body  # list item rendered


@pytest.mark.asyncio
async def test_share_page_404_for_private_community(client, db):
    """Setting Visibility=private on the root must hide the artifact
    completely from the public share endpoint — 404, no leakage."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"], "Hidden")
    cid = _uuid.UUID(community["id"])

    # Flip root visibility to private.
    await db.execute(
        _upd(Variable).where(
            Variable.community_id == cid,
            Variable.name == "Visibility",
        ).values(value="private")
    )

    container_id = _uuid.uuid4()
    db.add(ArtifactContainer(
        id=container_id, community_id=cid, status=1,
        title="Internal docs", delegated_from_artifact_id=None,
    ))
    art_id = _uuid.uuid4()
    db.add(Artifact(
        id=art_id, container_id=container_id, community_id=cid,
        title="Secret plan", content="don't tell anyone",
        author_user_id=_uuid.UUID(user["id"]),
        proposal_id=None,
        status=int(ArtifactStatus.ACTIVE),
        is_plan=False,
    ))
    await db.commit()

    r = await client.get(f"/artifacts/{art_id}/share")
    assert r.status_code == 404
    assert "Secret plan" not in r.text
    assert "don't tell anyone" not in r.text


@pytest.mark.asyncio
async def test_share_page_works_for_unlisted(client, db):
    """Visibility=unlisted → page renders for anyone with the URL,
    plus a small 'unlisted' note in the body."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"], "ULC")
    cid = _uuid.UUID(community["id"])

    await db.execute(
        _upd(Variable).where(
            Variable.community_id == cid, Variable.name == "Visibility",
        ).values(value="unlisted")
    )

    container_id = _uuid.uuid4()
    db.add(ArtifactContainer(
        id=container_id, community_id=cid, status=1,
        title="Notes", delegated_from_artifact_id=None,
    ))
    art_id = _uuid.uuid4()
    db.add(Artifact(
        id=art_id, container_id=container_id, community_id=cid,
        title="Friend group plans", content="Trip to Iceland in May.",
        author_user_id=_uuid.UUID(user["id"]),
        proposal_id=None,
        status=int(ArtifactStatus.ACTIVE),
        is_plan=False,
    ))
    await db.commit()

    r = await client.get(f"/artifacts/{art_id}/share")
    assert r.status_code == 200
    assert "unlisted" in r.text.lower()
    assert "Iceland" in r.text


@pytest.mark.asyncio
async def test_share_page_404_for_unknown_artifact(client):
    bogus = _uuid.uuid4()
    r = await client.get(f"/artifacts/{bogus}/share")
    assert r.status_code == 404


# ── /highlights ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_highlights_returns_recent_accepted_proposals(client, db):
    """A handful of accepted interesting-type proposals across public
    communities show up in /highlights, ordered most-recent-first."""
    # Reset the module-level cache between test runs; otherwise
    # the cached payload from a sibling test bleeds into this one.
    from kbz.routers.highlights import _CACHE
    _CACHE["payload"] = None

    user = await create_test_user(client)
    community = await create_test_community(client, user["id"], "HiglightCo")
    cid = _uuid.UUID(community["id"])
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)

    # Three accepted proposals of interesting types.
    for i, (ptype, text) in enumerate([
        (ProposalType.ADD_STATEMENT, "We respond within one round."),
        (ProposalType.ADD_ACTION, "Onboarding Writers"),
        (ProposalType.CHANGE_VARIABLE, "MaxAge"),
    ]):
        db.add(Proposal(
            id=_uuid.uuid4(),
            community_id=cid,
            user_id=_uuid.UUID(user["id"]),
            proposal_type=ptype,
            proposal_status=ProposalStatus.ACCEPTED,
            proposal_text=text,
            val_text="3" if ptype == ProposalType.CHANGE_VARIABLE else None,
            age=0,
            support_count=5,
            decided_at=now - timedelta(minutes=i),
        ))
    # And a non-interesting type that should be filtered.
    db.add(Proposal(
        id=_uuid.uuid4(),
        community_id=cid,
        user_id=_uuid.UUID(user["id"]),
        proposal_type=ProposalType.MEMBERSHIP,
        proposal_status=ProposalStatus.ACCEPTED,
        proposal_text="join",
        val_text=None,
        age=0,
        support_count=5,
        decided_at=now,
    ))
    await db.commit()

    r = await client.get("/highlights?limit=5")
    assert r.status_code == 200
    payload = r.json()
    types = [h["type"] for h in payload["highlights"]]
    assert "AddStatement" in types
    assert "AddAction" in types
    assert "ChangeVariable" in types
    assert "Membership" not in types  # filtered uninteresting type
    # decided_at desc — first item is the most recent
    assert payload["highlights"][0]["type"] == "AddStatement"


@pytest.mark.asyncio
async def test_highlights_excludes_private_communities(client, db):
    """A private root community's accepted proposals must NOT appear
    in the public highlights stream."""
    from kbz.routers.highlights import _CACHE
    _CACHE["payload"] = None

    user = await create_test_user(client)
    pub = await create_test_community(client, user["id"], "PublicHL")
    priv = await create_test_community(client, user["id"], "PrivateHL")
    pub_cid = _uuid.UUID(pub["id"])
    priv_cid = _uuid.UUID(priv["id"])

    # Mark the second one private.
    await db.execute(
        _upd(Variable).where(
            Variable.community_id == priv_cid, Variable.name == "Visibility",
        ).values(value="private")
    )

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for cid, marker in [(pub_cid, "public-marker"), (priv_cid, "private-marker")]:
        db.add(Proposal(
            id=_uuid.uuid4(),
            community_id=cid,
            user_id=_uuid.UUID(user["id"]),
            proposal_type=ProposalType.ADD_STATEMENT,
            proposal_status=ProposalStatus.ACCEPTED,
            proposal_text=marker,
            age=0,
            support_count=3,
            decided_at=now,
        ))
    await db.commit()

    r = await client.get("/highlights?limit=10")
    payload = r.json()
    titles = " ".join(h["title"] for h in payload["highlights"])
    assert "public-marker" in titles
    assert "private-marker" not in titles  # private MUST be excluded


@pytest.mark.asyncio
async def test_highlights_link_for_artifact_proposals_points_to_share_page(client, db):
    """An EditArtifact highlight should link to /artifact/<val_uuid>
    (the public share page), not the live viewer."""
    from kbz.routers.highlights import _CACHE
    _CACHE["payload"] = None

    user = await create_test_user(client)
    community = await create_test_community(client, user["id"], "ArtCo")
    cid = _uuid.UUID(community["id"])
    art_id = _uuid.uuid4()
    from datetime import datetime, timezone
    db.add(Proposal(
        id=_uuid.uuid4(),
        community_id=cid,
        user_id=_uuid.UUID(user["id"]),
        proposal_type=ProposalType.EDIT_ARTIFACT,
        proposal_status=ProposalStatus.ACCEPTED,
        proposal_text="Filled in the onboarding section.",
        val_uuid=art_id,
        age=0,
        support_count=4,
        decided_at=datetime.now(timezone.utc),
    ))
    await db.commit()

    r = await client.get("/highlights?limit=5")
    edits = [h for h in r.json()["highlights"] if h["type"] == "EditArtifact"]
    assert edits, "no EditArtifact highlight returned"
    assert edits[0]["link"] == f"/artifact/{art_id}"
