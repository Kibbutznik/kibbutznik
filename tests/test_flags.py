"""Tests for the symmetric flag feature.

Covers the four target kinds (comment / proposal / reason / user),
the closeness side effect (positive flag → bumps closeness toward
+1, negative → toward -1, flip reverses the prior delta), the
membership gate, and self-flag rejection.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kbz.models.closeness import Closeness
from sqlalchemy import select

from tests.conftest import create_test_community, create_test_user


async def _login(client, email: str) -> str:
    r = await client.post("/auth/request-magic-link", json={"email": email})
    r = await client.get(r.json()["link"])
    return r.json()["user"]["user_id"]


async def _real_proposal_in(client, community_id, author) -> str:
    """Create a real proposal in `community_id` authored by `author`.
    Comments now require the entity to exist; flag tests that comment
    on a proposal must attach to a real one."""
    resp = await client.post(f"/communities/{community_id}/proposals", json={
        "user_id": author["id"],
        "proposal_type": "AddStatement",
        "proposal_text": f"flag-target-{uuid.uuid4()}",
    })
    return resp.json()["id"]


async def _get_pair_score(
    db_engine, a: str, b: str,
) -> float:
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    a_uuid, b_uuid = uuid.UUID(a), uuid.UUID(b)
    if str(a_uuid) > str(b_uuid):
        a_uuid, b_uuid = b_uuid, a_uuid
    async with sf() as db:
        row = (
            await db.execute(
                select(Closeness.score).where(
                    Closeness.user_id1 == a_uuid,
                    Closeness.user_id2 == b_uuid,
                )
            )
        ).first()
    return float(row.score) if row else 0.0


async def _land_membership(
    client, community_id: str, founder_id: str, joiner_id: str,
) -> None:
    """File + accept a Membership proposal so the joiner becomes an
    active member of the community. Uses founder's pulse-supports."""
    resp = await client.post(f"/communities/{community_id}/proposals", json={
        "user_id": joiner_id,
        "proposal_type": "Membership",
        "proposal_text": "join",
        "val_uuid": joiner_id,
    })
    pid = resp.json()["id"]
    await client.patch(f"/proposals/{pid}/submit")
    await client.post(
        f"/proposals/{pid}/support", json={"user_id": founder_id},
    )
    for _ in range(2):
        await client.post(
            f"/communities/{community_id}/pulses/support",
            json={"user_id": founder_id},
        )


@pytest.mark.asyncio
async def test_positive_flag_on_comment_bumps_closeness(client, db_engine):
    """+1 flag on a comment moves closeness between flagger and the
    comment's author by +FLAG_CLOSENESS_STEP."""
    founder = await create_test_user(client, "flag-founder")
    flagger = await create_test_user(client, "flag-flagger")
    community = await create_test_community(client, founder["id"])
    await _land_membership(client, community["id"], founder["id"], flagger["id"])

    # Founder posts a comment on a proposal entity (any UUID works
    # for the entity_id — comments are polymorphic).
    entity_id = await _real_proposal_in(client, community["id"], founder)
    comment_resp = await client.post(
        f"/entities/proposal/{entity_id}/comments",
        json={"user_id": founder["id"], "comment_text": "what do we think"},
    )
    comment_id = comment_resp.json()["id"]

    pre = await _get_pair_score(db_engine, founder["id"], flagger["id"])

    # Flagger gives a +1 on founder's comment.
    resp = await client.post("/flags", json={
        "user_id": flagger["id"],
        "community_id": community["id"],
        "target_kind": "comment",
        "target_id": comment_id,
        "value": 1,
    })
    assert resp.status_code == 201, resp.text

    post = await _get_pair_score(db_engine, founder["id"], flagger["id"])
    assert post > pre
    assert round(post - pre, 5) == 0.05  # FLAG_CLOSENESS_STEP


@pytest.mark.asyncio
async def test_negative_flag_drops_closeness(client, db_engine):
    """-1 flag should move closeness in the opposite direction."""
    founder = await create_test_user(client, "neg-founder")
    flagger = await create_test_user(client, "neg-flagger")
    community = await create_test_community(client, founder["id"])
    await _land_membership(client, community["id"], founder["id"], flagger["id"])

    entity_id = await _real_proposal_in(client, community["id"], founder)
    cresp = await client.post(
        f"/entities/proposal/{entity_id}/comments",
        json={"user_id": founder["id"], "comment_text": "controversial"},
    )
    comment_id = cresp.json()["id"]

    pre = await _get_pair_score(db_engine, founder["id"], flagger["id"])

    resp = await client.post("/flags", json={
        "user_id": flagger["id"],
        "community_id": community["id"],
        "target_kind": "comment",
        "target_id": comment_id,
        "value": -1,
    })
    assert resp.status_code == 201

    post = await _get_pair_score(db_engine, founder["id"], flagger["id"])
    assert post < pre
    assert round(pre - post, 5) == 0.05


@pytest.mark.asyncio
async def test_flag_flip_reverses_then_applies(client, db_engine):
    """Re-flagging a comment from +1 → -1 should reverse the prior
    +0.05 contribution AND apply -0.05 (net swing of 0.10 from peak)."""
    founder = await create_test_user(client, "flip-founder")
    flagger = await create_test_user(client, "flip-flagger")
    community = await create_test_community(client, founder["id"])
    await _land_membership(client, community["id"], founder["id"], flagger["id"])

    entity_id = await _real_proposal_in(client, community["id"], founder)
    cresp = await client.post(
        f"/entities/proposal/{entity_id}/comments",
        json={"user_id": founder["id"], "comment_text": "let's see"},
    )
    comment_id = cresp.json()["id"]

    pre = await _get_pair_score(db_engine, founder["id"], flagger["id"])

    # +1 first.
    await client.post("/flags", json={
        "user_id": flagger["id"],
        "community_id": community["id"],
        "target_kind": "comment",
        "target_id": comment_id,
        "value": 1,
    })
    after_pos = await _get_pair_score(db_engine, founder["id"], flagger["id"])

    # Flip to -1.
    resp = await client.post("/flags", json={
        "user_id": flagger["id"],
        "community_id": community["id"],
        "target_kind": "comment",
        "target_id": comment_id,
        "value": -1,
    })
    assert resp.status_code == 201
    after_flip = await _get_pair_score(db_engine, founder["id"], flagger["id"])

    # The flip should have moved score back past the original AND
    # then 0.05 below it (net -0.10 from after_pos, -0.05 from pre).
    assert round(after_pos - pre, 5) == 0.05
    assert round(pre - after_flip, 5) == 0.05


@pytest.mark.asyncio
async def test_clear_flag_reverses_closeness(client, db_engine):
    """DELETE /flags reverses the prior contribution."""
    founder = await create_test_user(client, "clr-founder")
    # All setup happens unauthenticated so the session-spoof guards
    # in /communities, /proposals, /comments don't 403 us.
    community = await create_test_community(client, founder["id"])
    entity_id = await _real_proposal_in(client, community["id"], founder)
    cresp = await client.post(
        f"/entities/proposal/{entity_id}/comments",
        json={"user_id": founder["id"], "comment_text": "tbd"},
    )
    comment_id = cresp.json()["id"]
    # Now log in the flagger and land their membership (founder
    # supports via unauth pulse-support since we drop the session
    # cookie just before).
    flagger_id = await _login(client, "clr-flagger@example.com")
    client.cookies.clear()
    await _land_membership(client, community["id"], founder["id"], flagger_id)

    pre = await _get_pair_score(db_engine, founder["id"], flagger_id)
    # Place the flag (still unauth — flagger_id is in body).
    await client.post("/flags", json={
        "user_id": flagger_id,
        "community_id": community["id"],
        "target_kind": "comment",
        "target_id": comment_id,
        "value": 1,
    })

    # Re-login as flagger, then DELETE my own flag (require_user).
    await _login(client, "clr-flagger@example.com")
    resp = await client.delete(f"/flags/comment/{comment_id}")
    assert resp.status_code == 204
    post = await _get_pair_score(db_engine, founder["id"], flagger_id)
    assert round(post - pre, 5) == 0.0


@pytest.mark.asyncio
async def test_re_flag_same_value_is_idempotent(client, db_engine):
    """Setting +1 twice should not double-apply the closeness delta."""
    founder = await create_test_user(client, "idem-founder")
    flagger = await create_test_user(client, "idem-flagger")
    community = await create_test_community(client, founder["id"])
    await _land_membership(client, community["id"], founder["id"], flagger["id"])

    entity_id = await _real_proposal_in(client, community["id"], founder)
    cresp = await client.post(
        f"/entities/proposal/{entity_id}/comments",
        json={"user_id": founder["id"], "comment_text": "stable"},
    )
    comment_id = cresp.json()["id"]

    pre = await _get_pair_score(db_engine, founder["id"], flagger["id"])

    body = {
        "user_id": flagger["id"], "community_id": community["id"],
        "target_kind": "comment", "target_id": comment_id, "value": 1,
    }
    await client.post("/flags", json=body)
    after_first = await _get_pair_score(db_engine, founder["id"], flagger["id"])
    await client.post("/flags", json=body)  # same value → no extra delta
    after_second = await _get_pair_score(db_engine, founder["id"], flagger["id"])
    assert round(after_first - pre, 5) == 0.05
    assert after_first == after_second


@pytest.mark.asyncio
async def test_self_flag_rejected(client):
    user = await create_test_user(client, "self-flag")
    community = await create_test_community(client, user["id"])

    # Founder tries to flag their own comment.
    entity_id = await _real_proposal_in(client, community["id"], user)
    cresp = await client.post(
        f"/entities/proposal/{entity_id}/comments",
        json={"user_id": user["id"], "comment_text": "hi"},
    )
    comment_id = cresp.json()["id"]

    resp = await client.post("/flags", json={
        "user_id": user["id"],
        "community_id": community["id"],
        "target_kind": "comment",
        "target_id": comment_id,
        "value": 1,
    })
    assert resp.status_code == 400
    assert "own content" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_non_member_cannot_flag(client):
    founder = await create_test_user(client, "nm-founder")
    outsider = await create_test_user(client, "nm-outsider")
    community = await create_test_community(client, founder["id"])

    entity_id = await _real_proposal_in(client, community["id"], founder)
    cresp = await client.post(
        f"/entities/proposal/{entity_id}/comments",
        json={"user_id": founder["id"], "comment_text": "members only"},
    )
    comment_id = cresp.json()["id"]

    resp = await client.post("/flags", json={
        "user_id": outsider["id"],
        "community_id": community["id"],
        "target_kind": "comment",
        "target_id": comment_id,
        "value": 1,
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_summary_returns_counts_and_my_value(client):
    founder = await create_test_user(client, "sum-founder")
    community = await create_test_community(client, founder["id"])
    entity_id = await _real_proposal_in(client, community["id"], founder)
    cresp = await client.post(
        f"/entities/proposal/{entity_id}/comments",
        json={"user_id": founder["id"], "comment_text": "for summary"},
    )
    comment_id = cresp.json()["id"]
    flagger_id = await _login(client, "sum-flagger@example.com")
    client.cookies.clear()
    await _land_membership(client, community["id"], founder["id"], flagger_id)

    await client.post("/flags", json={
        "user_id": flagger_id,
        "community_id": community["id"],
        "target_kind": "comment",
        "target_id": comment_id,
        "value": 1,
    })

    # Log in as the flagger so the summary returns my_value.
    await _login(client, "sum-flagger@example.com")
    resp = await client.get(f"/flags/comment/{comment_id}")
    body = resp.json()
    assert body["positive"] == 1
    assert body["negative"] == 0
    assert body["my_value"] == 1


@pytest.mark.asyncio
async def test_user_target_flag_works(client, db_engine):
    """target_kind='user' flags the user directly — author == target."""
    target = await create_test_user(client, "user-target")
    flagger = await create_test_user(client, "user-flagger")
    community = await create_test_community(client, target["id"])
    await _land_membership(client, community["id"], target["id"], flagger["id"])

    pre = await _get_pair_score(db_engine, target["id"], flagger["id"])
    resp = await client.post("/flags", json={
        "user_id": flagger["id"],
        "community_id": community["id"],
        "target_kind": "user",
        "target_id": target["id"],
        "value": 1,
    })
    assert resp.status_code == 201
    post = await _get_pair_score(db_engine, target["id"], flagger["id"])
    assert round(post - pre, 5) == 0.05


@pytest.mark.asyncio
async def test_session_spoof_blocked(client):
    """A logged-in user can't POST a flag with someone else's user_id."""
    other = await create_test_user(client, "spoof-victim")
    # All setup unauthenticated so session-spoof guards don't 403 us.
    community = await create_test_community(client, other["id"])
    entity_id = await _real_proposal_in(client, community["id"], other)
    cresp = await client.post(
        f"/entities/proposal/{entity_id}/comments",
        json={"user_id": other["id"], "comment_text": "victim's comment"},
    )
    comment_id = cresp.json()["id"]
    # Now log in as the attacker.
    await _login(client, "spoof-attacker@example.com")

    resp = await client.post("/flags", json={
        "user_id": other["id"],   # spoof
        "community_id": community["id"],
        "target_kind": "comment",
        "target_id": comment_id,
        "value": 1,
    })
    assert resp.status_code == 403
