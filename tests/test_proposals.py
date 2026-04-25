import pytest
from tests.conftest import create_test_user, create_test_community


@pytest.mark.asyncio
async def test_create_proposal(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "We believe in open governance",
    })
    assert resp.status_code == 201
    proposal = resp.json()
    assert proposal["proposal_type"] == "AddStatement"
    assert proposal["proposal_status"] == "Draft"
    assert proposal["support_count"] == 0


@pytest.mark.asyncio
async def test_proposal_pitch_round_trip(client):
    """A proposal's `pitch` (the proposer's "why") persists and comes
    back on GET and on the enriched list endpoint — it's a separate
    column from proposal_text, not a prefix/suffix of it."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Open governance is a shared value.",
        "pitch": "We repeatedly argue past each other in pulses. "
                 "Naming 'open governance' as a shared value gives us a "
                 "canonical phrase to point to when we disagree on process.",
    })
    assert resp.status_code == 201
    created = resp.json()
    assert created["pitch"].startswith("We repeatedly argue past each other")
    assert created["proposal_text"] == "Open governance is a shared value."

    # List endpoint should also carry the pitch through enrich().
    rlist = await client.get(f"/communities/{community['id']}/proposals")
    assert rlist.status_code == 200
    rows = rlist.json()
    assert any(p["id"] == created["id"] and p["pitch"].startswith("We repeatedly") for p in rows)


@pytest.mark.asyncio
async def test_proposal_pitch_optional(client):
    """Creating without a pitch is fine — the column is nullable and
    the response field comes back as None. This keeps old clients
    (and legacy rows) from breaking."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "No-pitch proposal",
    })
    assert resp.status_code == 201
    assert resp.json()["pitch"] is None


@pytest.mark.asyncio
async def test_list_proposals_respects_limit_and_offset(client):
    """The list endpoint was unbounded — a community with thousands of
    proposals would dump them all plus run enrichment per row. Paginate
    with limit/offset, defaulting to a generous cap but never unlimited."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    for i in range(5):
        await client.post(f"/communities/{community['id']}/proposals", json={
            "user_id": user["id"],
            "proposal_type": "AddStatement",
            "proposal_text": f"statement {i}",
        })

    # limit=2 returns only 2 of the 5
    r = await client.get(
        f"/communities/{community['id']}/proposals", params={"limit": 2},
    )
    assert r.status_code == 200
    assert len(r.json()) == 2

    # offset slides the window; limit=2 offset=2 returns the next 2
    r = await client.get(
        f"/communities/{community['id']}/proposals",
        params={"limit": 2, "offset": 2},
    )
    assert len(r.json()) == 2

    # Over-the-top limits are rejected rather than silently letting
    # callers pin the db with one request.
    r = await client.get(
        f"/communities/{community['id']}/proposals", params={"limit": 10000},
    )
    assert r.status_code == 422


async def test_per_member_proposal_cap_blocks_sixth_in_flight(client):
    """Default ProposalRateLimit is 5 — the 6th in-flight proposal
    by the same author 429s until earlier ones land or get canceled."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    for i in range(5):
        resp = await client.post(f"/communities/{community['id']}/proposals", json={
            "user_id": user["id"],
            "proposal_type": "AddStatement",
            "proposal_text": f"draft #{i}",
        })
        assert resp.status_code == 201

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "one too many",
    })
    assert resp.status_code == 429
    assert "in-flight" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_change_variable_for_rate_limit_bypasses_cap(client):
    """A capped member must always be able to file the ONE proposal
    that would unstick the community — ChangeVariable targeting
    ProposalRateLimit itself. Without this escape hatch, a single-
    member community that hits its own cap is permanently stuck."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    # Fill the cap with 5 in-flight AddStatements.
    for i in range(5):
        resp = await client.post(f"/communities/{community['id']}/proposals", json={
            "user_id": user["id"],
            "proposal_type": "AddStatement",
            "proposal_text": f"capper #{i}",
        })
        assert resp.status_code == 201

    # A regular ChangeVariable still 429s.
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "ChangeVariable",
        "proposal_text": "MaxAge",
        "val_text": "10",
    })
    assert resp.status_code == 429

    # ChangeVariable targeting ProposalRateLimit goes through.
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "ChangeVariable",
        "proposal_text": "ProposalRateLimit\nbumping the cap so we can move",
        "val_text": "20",
    })
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_proposal_rate_limit_can_be_voted_higher(client):
    """A community can change ProposalRateLimit through the normal
    governance flow; once the new value lands the cap moves. We
    verify by voting the limit to 10 and then filing a 6th proposal
    that would have been refused under the default."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Land a ChangeVariable that bumps the cap to 10.
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "ChangeVariable",
        "proposal_text": "ProposalRateLimit",
        "val_text": "10",
    })
    pid = resp.json()["id"]
    await client.patch(f"/proposals/{pid}/submit")
    await client.post(f"/proposals/{pid}/support", json={"user_id": user["id"]})
    for _ in range(2):
        await client.post(
            f"/communities/{community['id']}/pulses/support",
            json={"user_id": user["id"]},
        )

    # Cap is now 10. File 6 AddStatements — all succeed.
    for i in range(6):
        resp = await client.post(f"/communities/{community['id']}/proposals", json={
            "user_id": user["id"],
            "proposal_type": "AddStatement",
            "proposal_text": f"under-the-new-cap #{i}",
        })
        assert resp.status_code == 201, f"#{i}: {resp.text}"


@pytest.mark.asyncio
async def test_proposal_rate_limit_zero_disables_cap(client):
    """Setting ProposalRateLimit to '0' disables the cap entirely.
    Useful for trusted bot-driven communities that intentionally
    want unbounded queueing."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Vote the limit to 0.
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "ChangeVariable",
        "proposal_text": "ProposalRateLimit",
        "val_text": "0",
    })
    pid = resp.json()["id"]
    await client.patch(f"/proposals/{pid}/submit")
    await client.post(f"/proposals/{pid}/support", json={"user_id": user["id"]})
    for _ in range(2):
        await client.post(
            f"/communities/{community['id']}/pulses/support",
            json={"user_id": user["id"]},
        )

    # File 12 — none get capped.
    for i in range(12):
        resp = await client.post(f"/communities/{community['id']}/proposals", json={
            "user_id": user["id"],
            "proposal_type": "AddStatement",
            "proposal_text": f"unbounded #{i}",
        })
        assert resp.status_code == 201, f"#{i}: {resp.text}"


@pytest.mark.asyncio
async def test_throw_out_cooldown_blocks_repeat_against_same_target(client):
    """After a ThrowOut against user X is Canceled (decided), a new
    ThrowOut against the same X can't be filed for 24h. Stops the
    repeated-pitchfork pattern. We use Withdraw to land it in
    CANCELED — the cooldown counts decided proposals regardless of
    outcome."""
    founder = await create_test_user(client, "throw-cooldown-f")
    target = await create_test_user(client, "throw-cooldown-t")
    community = await create_test_community(client, founder["id"])

    # Land target as a member.
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": target["id"],
        "proposal_type": "Membership",
        "proposal_text": "join",
        "val_uuid": target["id"],
    })
    membership_id = resp.json()["id"]
    await client.patch(f"/proposals/{membership_id}/submit")
    await client.post(
        f"/proposals/{membership_id}/support", json={"user_id": founder["id"]},
    )
    for _ in range(2):
        await client.post(
            f"/communities/{community['id']}/pulses/support",
            json={"user_id": founder["id"]},
        )

    # File first ThrowOut and immediately withdraw it (→ CANCELED).
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": founder["id"],
        "proposal_type": "ThrowOut",
        "proposal_text": "first try",
        "val_uuid": target["id"],
    })
    first_id = resp.json()["id"]
    resp = await client.post(f"/proposals/{first_id}/withdraw", json={
        "user_id": founder["id"],
    })
    assert resp.status_code == 200

    # A fresh ThrowOut against the same target now 429s due to
    # cooldown (the existing DEDUPE_RULES wouldn't catch it because
    # the prior one is now CANCELED, not in-flight).
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": founder["id"],
        "proposal_type": "ThrowOut",
        "proposal_text": "second try same day",
        "val_uuid": target["id"],
    })
    assert resp.status_code == 429
    assert "cooldown" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_throw_out_cooldown_anchors_on_decision_time_not_creation(client, db):
    """Repro for the cooldown anchoring bug: a ThrowOut filed long ago
    that was decided MOMENTS ago must still trigger the cooldown.

    The buggy implementation filtered on `Proposal.created_at >= cutoff`,
    so a 25h-old proposal that decided 5 minutes ago slipped through —
    the pitchfork could swing again instantly. Fix anchors on
    coalesce(decided_at, created_at) so decision time wins when present.
    """
    import uuid as _uuid
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select as _select

    from kbz.enums import ProposalStatus, ProposalType
    from kbz.models.member import Member
    from kbz.models.proposal import Proposal

    founder = await create_test_user(client, "cd-anchor-f")
    target = await create_test_user(client, "cd-anchor-t")
    community = await create_test_community(client, founder["id"])

    # Land target as a member so a fresh ThrowOut against them is
    # the right shape (existing-member target).
    db.add(
        Member(
            community_id=_uuid.UUID(community["id"]),
            user_id=_uuid.UUID(target["id"]),
            seniority=0,
        )
    )
    await db.commit()

    # Inject a previously-decided ThrowOut directly: created 25h ago
    # (before the cutoff window), but decided just now (inside it).
    # Without the fix, the created_at check kicks it out of the
    # cooldown query → fresh ThrowOut succeeds.
    now = datetime.now(timezone.utc)
    db.add(
        Proposal(
            id=_uuid.uuid4(),
            community_id=_uuid.UUID(community["id"]),
            user_id=_uuid.UUID(founder["id"]),
            proposal_type=ProposalType.THROW_OUT,
            proposal_status=ProposalStatus.REJECTED,
            proposal_text="ancient grudge",
            val_uuid=_uuid.UUID(target["id"]),
            val_text="",
            age=0,
            support_count=0,
            created_at=now - timedelta(hours=25),
            decided_at=now - timedelta(minutes=5),
        )
    )
    await db.commit()

    # Fresh ThrowOut against the same target must 429 — decision was
    # 5 minutes ago, well inside the 24h cooldown window.
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": founder["id"],
        "proposal_type": "ThrowOut",
        "proposal_text": "second swing same hour",
        "val_uuid": target["id"],
    })
    assert resp.status_code == 429, resp.text
    assert "cooldown" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_proposal_support_race_returns_409_not_500(client, monkeypatch):
    """Race-window safety net: if two concurrent same-user supports
    of the same proposal both pass the dedupe SELECT before either
    commits, the loser must see 409 (not a 500 IntegrityError
    crash). Force the loser's path by monkey-patching the dedupe
    SELECT inside the service to always return None, while keeping
    a real prior Support row in the DB. Symmetric to the
    pulse-support race fix already shipped."""
    from kbz.services import support_service as _ss

    user = await create_test_user(client, "race-prop-mp")
    community = await create_test_community(client, user["id"])
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "race-target",
    })
    pid = resp.json()["id"]
    await client.patch(f"/proposals/{pid}/submit")

    # Legitimate first support; row lands in `supports`.
    resp = await client.post(
        f"/proposals/{pid}/support", json={"user_id": user["id"]},
    )
    assert resp.status_code == 201

    # Monkey-patch the dedupe SELECT so the second request thinks no
    # prior Support exists — the INSERT then violates the (user_id,
    # proposal_id) PK on autoflush/commit.
    real_execute = _ss.AsyncSession.execute

    async def execute_skipping_dupcheck(self, stmt, *args, **kwargs):
        s = str(stmt)
        if (
            "FROM supports" in s
            and "WHERE" in s
            and "supports.user_id" in s
        ):
            class _Empty:
                def scalar_one_or_none(self):
                    return None
            return _Empty()
        return await real_execute(self, stmt, *args, **kwargs)

    monkeypatch.setattr(_ss.AsyncSession, "execute", execute_skipping_dupcheck)

    resp = await client.post(
        f"/proposals/{pid}/support", json={"user_id": user["id"]},
    )
    assert resp.status_code == 409, (
        f"expected 409, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_amend_proposal_creates_successor_and_cancels_original(client):
    """Amending a Draft/OutThere proposal moves the original to
    CANCELED and creates a successor whose parent_proposal_id chains
    back. Version increments. The successor lands as DRAFT — the
    author has to re-submit and re-collect support, same way /edit
    forces re-evaluation."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "we publish weekly digests",
        "pitch": "transparency builds trust",
    })
    original = resp.json()
    assert original["version"] == 1
    assert original["parent_proposal_id"] is None

    resp = await client.post(f"/proposals/{original['id']}/amend", json={
        "user_id": user["id"],
        "proposal_text": "we publish biweekly digests",
        "pitch": "biweekly is more sustainable than weekly",
    })
    assert resp.status_code == 201, resp.text
    successor = resp.json()
    assert successor["proposal_text"] == "we publish biweekly digests"
    assert successor["pitch"] == "biweekly is more sustainable than weekly"
    assert successor["parent_proposal_id"] == original["id"]
    assert successor["version"] == 2
    assert successor["proposal_status"] == "Draft"

    # Original got CANCELED.
    resp = await client.get(f"/proposals/{original['id']}")
    assert resp.json()["proposal_status"] == "Canceled"


@pytest.mark.asyncio
async def test_amend_only_by_author(client):
    """Strangers can't amend — only the original author."""
    founder = await create_test_user(client, "amend-author")
    intruder = await create_test_user(client, "amend-intruder")
    community = await create_test_community(client, founder["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": founder["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "x",
    })
    pid = resp.json()["id"]

    resp = await client.post(f"/proposals/{pid}/amend", json={
        "user_id": intruder["id"],
        "proposal_text": "y",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_amend_rejected_after_on_the_air(client):
    """Once a proposal is OnTheAir / Accepted / Rejected the train
    has left — /amend must 400 with a clear reason."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "we move fast",
    })
    pid = resp.json()["id"]
    await client.patch(f"/proposals/{pid}/submit")
    await client.post(
        f"/proposals/{pid}/support", json={"user_id": user["id"]},
    )
    # One pulse promotes OutThere → OnTheAir.
    await client.post(
        f"/communities/{community['id']}/pulses/support",
        json={"user_id": user["id"]},
    )

    resp = await client.post(f"/proposals/{pid}/amend", json={
        "user_id": user["id"],
        "proposal_text": "we move slow",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_amend_with_no_changes_rejected(client):
    """An amend that touches nothing is almost certainly a client
    bug. Refuse so we don't bloat the chain with empty successors."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "x",
    })
    pid = resp.json()["id"]
    resp = await client.post(f"/proposals/{pid}/amend", json={
        "user_id": user["id"],
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_amend_chain_returned_oldest_first(client):
    """GET /proposals/{id}/versions returns the chain ending at the
    given id, oldest-first. Useful for rendering "v1 → v2 → v3 (you
    are here)" without N+1 walks."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "v1",
    })
    v1 = resp.json()

    resp = await client.post(f"/proposals/{v1['id']}/amend", json={
        "user_id": user["id"],
        "proposal_text": "v2",
    })
    v2 = resp.json()

    resp = await client.post(f"/proposals/{v2['id']}/amend", json={
        "user_id": user["id"],
        "proposal_text": "v3",
    })
    v3 = resp.json()

    resp = await client.get(f"/proposals/{v3['id']}/versions")
    assert resp.status_code == 200
    chain = resp.json()
    texts = [p["proposal_text"] for p in chain]
    assert texts == ["v1", "v2", "v3"]
    versions = [p["version"] for p in chain]
    assert versions == [1, 2, 3]


@pytest.mark.asyncio
async def test_amend_404_for_unknown(client):
    user = await create_test_user(client)
    bogus = "00000000-0000-0000-0000-000000000099"
    resp = await client.post(f"/proposals/{bogus}/amend", json={
        "user_id": user["id"],
        "proposal_text": "into the void",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_invalid_proposal_type(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "InvalidType",
        "proposal_text": "Bad proposal",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_non_member_cannot_propose(client):
    user1 = await create_test_user(client, "founder")
    user2 = await create_test_user(client, "outsider")
    community = await create_test_community(client, user1["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user2["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "I'm not a member",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_submit_proposal(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Create proposal
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Test statement",
    })
    proposal = resp.json()
    assert proposal["proposal_status"] == "Draft"

    # Submit it
    resp = await client.patch(f"/proposals/{proposal['id']}/submit")
    assert resp.status_code == 200
    assert resp.json()["proposal_status"] == "OutThere"


@pytest.mark.asyncio
async def test_submit_proposal_rejects_non_author_session(client):
    """A logged-in user cannot promote someone else's draft."""
    # Create community + draft as the founder
    await client.post("/auth/request-magic-link", json={"email": "author@ex.com"})
    r = await client.get((await client.post(
        "/auth/request-magic-link", json={"email": "author@ex.com"},
    )).json()["link"])
    author_id = r.json()["user"]["user_id"]
    community = (await client.post("/communities", json={
        "name": "Kib", "founder_user_id": author_id,
    })).json()
    proposal = (await client.post(
        f"/communities/{community['id']}/proposals",
        json={
            "user_id": author_id,
            "proposal_type": "AddStatement",
            "proposal_text": "work-in-progress",
        },
    )).json()
    # Log out, log in as stranger, try to submit
    client.cookies.clear()
    r = await client.post(
        "/auth/request-magic-link", json={"email": "stranger@ex.com"},
    )
    await client.get(r.json()["link"])
    r = await client.patch(f"/proposals/{proposal['id']}/submit")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_submit_proposal_404_for_unknown_id(client):
    import uuid as _uuid
    r = await client.patch(f"/proposals/{_uuid.uuid4()}/submit")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_support_proposal(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Create and submit proposal
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Support me",
    })
    proposal = resp.json()
    await client.patch(f"/proposals/{proposal['id']}/submit")

    # Add support
    resp = await client.post(f"/proposals/{proposal['id']}/support", json={
        "user_id": user["id"],
    })
    assert resp.status_code == 201

    # Check support count
    resp = await client.get(f"/proposals/{proposal['id']}")
    assert resp.json()["support_count"] == 1

    # Regression: /supporters must return the row, not 500.
    # Prior bug: BotProfile outerjoin referenced Proposal.community_id
    # before Proposal was in the FROM clause, crashing the query.
    resp = await client.get(f"/proposals/{proposal['id']}/supporters")
    assert resp.status_code == 200
    supporters = resp.json()
    assert len(supporters) == 1
    assert supporters[0]["user_id"] == user["id"]


@pytest.mark.asyncio
async def test_supporters_404_on_unknown_proposal(client):
    """`GET /proposals/{id}/supporters` used to return 200 + [] for a
    bogus proposal id. That's indistinguishable from a real proposal
    nobody has supported, so a typo silently looks like success."""
    bogus = "00000000-0000-0000-0000-000000000099"
    resp = await client.get(f"/proposals/{bogus}/supporters")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_support_rejected(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "No duplicates",
    })
    proposal = resp.json()
    await client.patch(f"/proposals/{proposal['id']}/submit")

    await client.post(f"/proposals/{proposal['id']}/support", json={"user_id": user["id"]})
    resp = await client.post(f"/proposals/{proposal['id']}/support", json={"user_id": user["id"]})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_remove_support(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Unsupport me",
    })
    proposal = resp.json()
    await client.patch(f"/proposals/{proposal['id']}/submit")
    await client.post(f"/proposals/{proposal['id']}/support", json={"user_id": user["id"]})

    # Remove support
    resp = await client.delete(f"/proposals/{proposal['id']}/support/{user['id']}")
    assert resp.status_code == 200

    # Check count is back to 0
    resp = await client.get(f"/proposals/{proposal['id']}")
    assert resp.json()["support_count"] == 0


@pytest.mark.asyncio
async def test_cannot_support_draft(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Still a draft",
    })
    proposal = resp.json()

    # Try to support while still Draft
    resp = await client.post(f"/proposals/{proposal['id']}/support", json={"user_id": user["id"]})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_proposals_by_status(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Create two proposals, submit one
    resp1 = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Draft one",
    })
    resp2 = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Submitted one",
    })
    await client.patch(f"/proposals/{resp2.json()['id']}/submit")

    # List all
    resp = await client.get(f"/communities/{community['id']}/proposals")
    assert len(resp.json()) == 2

    # List only OutThere
    resp = await client.get(f"/communities/{community['id']}/proposals?status=OutThere")
    assert len(resp.json()) == 1
    assert resp.json()[0]["proposal_text"] == "Submitted one"


@pytest.mark.asyncio
async def test_membership_proposal_by_non_member(client):
    """Membership proposals can be created by non-members (they propose themselves).

    Also exercises the app's apply-to-join flow end to end: pitch persists,
    the proposal appears in the community's proposal list, and a second
    duplicate apply is rejected (409) so the UI can show a meaningful
    error instead of silently creating a ghost row.
    """
    user1 = await create_test_user(client, "founder")
    user2 = await create_test_user(client, "applicant")
    community = await create_test_community(client, user1["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user2["id"],
        "proposal_type": "Membership",
        "proposal_text": "applicant applied to join",
        "pitch": "I organise weekly co-op meetings and can onboard newcomers.",
        "val_uuid": user2["id"],
    })
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["pitch"] == "I organise weekly co-op meetings and can onboard newcomers."
    assert str(created["user_id"]) == user2["id"]
    assert str(created["val_uuid"]) == user2["id"]

    # The proposal must show up in the community's proposal list so the
    # viewer + app can render it. This was the exact symptom the user
    # reported: "I saw no new membership proposal in the simulated community."
    listing = (await client.get(f"/communities/{community['id']}/proposals")).json()
    ids = [p["id"] for p in listing]
    assert created["id"] in ids, f"membership proposal {created['id']} missing from list"

    # The applicant must be able to submit their own membership proposal.
    # Otherwise it sits as Draft forever and is invisible to UIs that
    # filter to OutThere/OnTheAir (which is most of them).
    sub = await client.patch(f"/proposals/{created['id']}/submit")
    assert sub.status_code == 200, sub.text
    assert sub.json()["proposal_status"] == "OutThere"

    # Duplicate apply must 409, not silently 201 a second row.
    dup = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user2["id"],
        "proposal_type": "Membership",
        "proposal_text": "applicant applied to join",
        "pitch": "retry",
        "val_uuid": user2["id"],
    })
    assert dup.status_code == 409, dup.text


@pytest.mark.asyncio
async def test_create_proposal_rejects_megabyte_text(client):
    """Proposal free-text columns are TEXT (unbounded) at the DB layer;
    without an upstream cap, any anonymous caller can bloat the
    proposals table with multi-MB rows, and the EditArtifact quote-
    check goes O(N*M) on oversized text."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    # Just over the 10,000-char limit defined in schemas/proposal.py.
    too_long = "x" * 10_001
    for field in ("proposal_text", "pitch", "val_text"):
        body = {
            "user_id": user["id"],
            "proposal_type": "AddStatement",
            "proposal_text": "fine",
        }
        body[field] = too_long
        r = await client.post(
            f"/communities/{community['id']}/proposals", json=body,
        )
        assert r.status_code == 422, f"{field}: {r.status_code} {r.text}"
