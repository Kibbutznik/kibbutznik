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

    # The canceled-by-amend row must show up in /audit with a
    # non-NULL decided_at — every other terminal transition stamps
    # it; pre-fix amend was the odd one out so the row sorted to
    # the bottom of the audit log with NULL decision time.
    audit = (await client.get(f"/communities/{community['id']}/audit")).json()
    matching = [e for e in audit if e["proposal_id"] == original["id"]]
    assert len(matching) == 1
    assert matching[0]["proposal_status"] == "Canceled"
    assert matching[0]["decided_at"] is not None, (
        "amend() must stamp decided_at on the canceled original — "
        "audit log sorts by decided_at desc nullslast and the row "
        "would otherwise sink to the bottom."
    )


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
async def test_change_variable_rejects_non_numeric_for_numeric_var(client):
    """ChangeVariable("PulseSupport", "soon") used to land Accepted
    AND THEN crash the next pulse cycle with ValueError because
    pulse_service does int(float("soon")). Now refused at create
    time so the author sees their bad input immediately and the
    community's pulse cycle stays healthy."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    for bad in ("soon", "abc", "Infinity", "NaN", ""):
        r = await client.post(f"/communities/{community['id']}/proposals", json={
            "user_id": user["id"],
            "proposal_type": "ChangeVariable",
            "proposal_text": "PulseSupport",
            "val_text": bad,
        })
        assert r.status_code == 422, f"val_text={bad!r} should 422; got {r.status_code}"


@pytest.mark.asyncio
async def test_change_variable_accepts_numeric_for_numeric_var(client):
    """The honest path: numeric val_text on a numeric variable still
    creates the proposal (proves we didn't over-tighten). Vary the
    variable name per call so DEDUPE_RULES doesn't 409 us — the rule
    keys on proposal_text."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    cases = [
        ("PulseSupport", "60"),
        ("MaxAge", "5"),
        ("ProposalSupport", "30"),
        ("ProposalRateLimit", "10"),
    ]
    for var_name, ok in cases:
        r = await client.post(f"/communities/{community['id']}/proposals", json={
            "user_id": user["id"],
            "proposal_type": "ChangeVariable",
            "proposal_text": var_name,
            "val_text": ok,
        })
        assert r.status_code == 201, (
            f"var={var_name} val_text={ok!r} should 201; got {r.text}"
        )


@pytest.mark.asyncio
async def test_change_variable_rejects_negative_for_numeric_var(client):
    """Negative values on numeric variables make no sense:
    - PulseSupport=-5 (% threshold) breaks the >= ceil(n*pct/100) math
      and lets every proposal auto-pass with zero support.
    - MaxAge=-3 (rounds before age-out) cancels every proposal before
      it can age in.
    - membershipFee=-1 (Decimal money) would mint credits to applicants.
    Reject at create time. ProposalRateLimit's "off" sentinel is 0
    (still allowed), not negative."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    cases = [
        ("PulseSupport", "-5"),
        ("MaxAge", "-1"),
        ("MinCommittee", "-2"),
        ("membershipFee", "-1"),
    ]
    for var_name, bad in cases:
        r = await client.post(f"/communities/{community['id']}/proposals", json={
            "user_id": user["id"],
            "proposal_type": "ChangeVariable",
            "proposal_text": var_name,
            "val_text": bad,
        })
        assert r.status_code == 422, (
            f"var={var_name} val_text={bad!r} should 422; got {r.status_code}: {r.text}"
        )
        assert "non-negative" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_change_variable_unknown_var_rejected(client):
    """A ChangeVariable proposal naming a variable that isn't in
    DEFAULT_VARIABLES used to silently no-op at execute time
    (UPDATE 0 rows). Now refused at create time so the author
    sees their typo immediately."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    r = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "ChangeVariable",
        "proposal_text": "TotallyMadeUpVariable",
        "val_text": "42",
    })
    assert r.status_code == 422
    assert "TotallyMadeUpVariable" in r.json()["detail"]


@pytest.mark.asyncio
async def test_change_variable_empty_text_rejected(client):
    """proposal_text is parsed to extract the var name (first line);
    empty text → no name → silent no-op. Refuse at create time."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    r = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "ChangeVariable",
        "proposal_text": "",
        "val_text": "42",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_throw_out_without_val_uuid_rejected_at_create(client):
    """Pre-fix the executor silently skipped (`if proposal.val_uuid:`)
    when val_uuid was missing — ThrowOut got accepted, the audit log
    showed it landed, and nothing happened. Surface the error at
    create time so the author sees it BEFORE wasting a pulse cycle."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    r = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "ThrowOut",
        "proposal_text": "no target — should 422",
    })
    assert r.status_code == 422
    assert "val_uuid" in r.json()["detail"]


@pytest.mark.asyncio
async def test_end_action_without_val_uuid_rejected_at_create(client):
    """Same shape — EndAction without val_uuid would also no-op
    silently in the executor."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    r = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "EndAction",
        "proposal_text": "no target action",
    })
    assert r.status_code == 422


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
    await client.post(f"/communities/{community['id']}/proposals", json={
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


@pytest.mark.asyncio
async def test_remove_support_refused_after_decision(client):
    """Once a proposal lands a terminal status (Accepted/Rejected/
    Canceled), DELETE /proposals/{id}/support/{uid} must 400. The
    audit log re-queries the Support table at read time, so deleting
    a Support row after the decision rewrites who-supported-what
    after the fact — a governance integrity hole. Pre-decision,
    supporters can change their mind freely; post-decision, the
    historical record is frozen."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Soon to be accepted",
    })
    pid = resp.json()["id"]
    await client.patch(f"/proposals/{pid}/submit")
    await client.post(f"/proposals/{pid}/support", json={"user_id": user["id"]})
    # Drive two pulses to land it.
    for _ in range(2):
        await client.post(
            f"/communities/{community['id']}/pulses/support",
            json={"user_id": user["id"]},
        )

    # Confirm landed.
    decided = await client.get(f"/proposals/{pid}")
    assert decided.json()["proposal_status"] == "Accepted"

    # The audit log shows the supporter at the moment of decision.
    audit_before = (await client.get(f"/communities/{community['id']}/audit")).json()
    matching_before = [e for e in audit_before if e["proposal_id"] == pid]
    assert len(matching_before) == 1
    assert any(
        s["user_id"] == user["id"] for s in matching_before[0]["supporters"]
    ), f"expected user as supporter in audit, got: {matching_before[0]['supporters']}"

    # Now try to retroactively withdraw support — must 400.
    resp = await client.delete(f"/proposals/{pid}/support/{user['id']}")
    assert resp.status_code == 400, resp.text
    assert "decided" in resp.json()["detail"].lower()

    # Audit log still shows the supporter — history is preserved.
    audit_after = (await client.get(f"/communities/{community['id']}/audit")).json()
    matching_after = [e for e in audit_after if e["proposal_id"] == pid]
    assert len(matching_after) == 1
    assert any(
        s["user_id"] == user["id"] for s in matching_after[0]["supporters"]
    ), "audit log lost the supporter — history was rewritten"


@pytest.mark.asyncio
async def test_add_statement_requires_non_empty_text(client):
    """AddStatement with empty proposal_text used to land Accepted and
    insert a blank Statement row into the community rulebook. The
    schema only capped max_length; no min. Require non-empty at create
    time so the bad row never gets a chance to land."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    for bad in ("", "   ", "\n\t  "):
        r = await client.post(f"/communities/{community['id']}/proposals", json={
            "user_id": user["id"],
            "proposal_type": "AddStatement",
            "proposal_text": bad,
        })
        assert r.status_code == 422, f"proposal_text={bad!r} should 422; got {r.status_code}"
        assert "non-empty" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_artifact_requires_non_empty_content(client):
    """CreateArtifact's proposal_text becomes the artifact body.
    Empty text → blank artifact in the community's container. Reject
    at create."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    r = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "CreateArtifact",
        "proposal_text": "",
        "val_text": "Just a title",
    })
    assert r.status_code == 422
    assert "non-empty" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_replace_statement_requires_text(client):
    """ReplaceStatement uses val_text (new statement) or
    proposal_text. Both empty → blank replacement. Reject at create."""
    import uuid as _uuid
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    r = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "ReplaceStatement",
        "proposal_text": "",
        "val_text": "",
        "val_uuid": str(_uuid.uuid4()),
    })
    assert r.status_code == 422
    assert "non-empty" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_membership_rejects_nonexistent_applicant(client):
    """Membership where val_uuid points at a user_id that doesn't
    exist must 422. Pre-fix the proposal was accepted, the executor
    ran member_svc.create(community_id, bogus_user_id), and an
    orphan member row landed in the roster — NULL user_name in
    /members AND a permanent +1 to member_count that inflated every
    threshold computation forever (the orphan can never vote)."""
    import uuid as _uuid
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    bogus = str(_uuid.uuid4())

    r = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "Membership",
        "proposal_text": "smuggled-in member",
        "val_uuid": bogus,
    })
    assert r.status_code == 422, r.text
    assert "not a known user" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_membership_rejects_nonexistent_self_applicant(client):
    """Same shape but with no val_uuid — Membership uses user_id as
    the applicant. A bogus user_id (e.g. an agent posting with a
    fabricated id) must also 422."""
    import uuid as _uuid
    founder = await create_test_user(client)
    community = await create_test_community(client, founder["id"])
    bogus = str(_uuid.uuid4())  # no user row for this id

    r = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": bogus,
        "proposal_type": "Membership",
        "proposal_text": "ghost applicant",
    })
    assert r.status_code == 422, r.text
    assert "not a known user" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_throw_out_rejects_nonexistent_user(client):
    """Pre-fix ThrowOut(val_uuid=<bogus>) landed Accepted, the
    executor's member_svc.throw_out silently no-op'd, and the
    create-time fanout dropped a `proposal.targets_you` notification
    in the DB for a user_id that doesn't exist in the users table —
    an orphan row. Same shape as PR #62/#64. Reject at create."""
    import uuid as _uuid
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    bogus = str(_uuid.uuid4())

    r = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "ThrowOut",
        "proposal_text": "throw out a ghost",
        "val_uuid": bogus,
    })
    assert r.status_code == 422, r.text
    assert "not an active member" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_throw_out_rejects_real_user_who_isnt_a_member(client):
    """Same defense for a real user who's never joined this
    community — the proposal can't have any effect, and the target
    notification just confuses someone who has no business being in
    the inbox of this community."""
    user = await create_test_user(client, "founder-throw")
    community = await create_test_community(client, user["id"])
    outsider = await create_test_user(client, "outsider-throw")

    r = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "ThrowOut",
        "proposal_text": "throw out a stranger",
        "val_uuid": outsider["id"],
    })
    assert r.status_code == 422, r.text
    assert "not an active member" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_throw_out_against_real_member_still_works(client):
    """The honest path: ThrowOut against an actual active member
    creates the proposal as before. Membership setup uses
    Membership-then-pulse to land the second user as a real
    member, then we file ThrowOut against them."""
    founder = await create_test_user(client, "founder-honest")
    target = await create_test_user(client, "target-honest")
    community = await create_test_community(client, founder["id"])

    # Land the target as a member.
    mid = (
        await client.post(f"/communities/{community['id']}/proposals", json={
            "user_id": target["id"],
            "proposal_type": "Membership",
            "proposal_text": "join",
            "val_uuid": target["id"],
        })
    ).json()["id"]
    await client.patch(f"/proposals/{mid}/submit")
    await client.post(
        f"/proposals/{mid}/support", json={"user_id": founder["id"]},
    )
    for _ in range(2):
        await client.post(
            f"/communities/{community['id']}/pulses/support",
            json={"user_id": founder["id"]},
        )

    # Now ThrowOut against the real member should succeed.
    r = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": founder["id"],
        "proposal_type": "ThrowOut",
        "proposal_text": "valid removal request",
        "val_uuid": target["id"],
    })
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_edit_rejects_empty_text_for_add_statement(client):
    """Edit re-runs the same content validation as create. Pre-fix
    edit_text bypassed it entirely — an author could file a valid
    Draft AddStatement and edit it down to "". The bad row would
    then sail into pulse-time and land a blank Statement in the
    rulebook if accepted."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    pid = (
        await client.post(f"/communities/{community['id']}/proposals", json={
            "user_id": user["id"],
            "proposal_type": "AddStatement",
            "proposal_text": "valid initial",
        })
    ).json()["id"]

    r = await client.patch(f"/proposals/{pid}/edit", json={
        "user_id": user["id"], "proposal_text": "",
    })
    assert r.status_code == 422, r.text
    assert "non-empty" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_edit_rejects_unknown_change_variable_name(client):
    """Same shape — edit must enforce the ChangeVariable name check
    that PR #58 added at create time."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    pid = (
        await client.post(f"/communities/{community['id']}/proposals", json={
            "user_id": user["id"],
            "proposal_type": "ChangeVariable",
            "proposal_text": "PulseSupport",
            "val_text": "60",
        })
    ).json()["id"]

    r = await client.patch(f"/proposals/{pid}/edit", json={
        "user_id": user["id"], "proposal_text": "NotARealVariable",
    })
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_edit_rejects_negative_change_variable_value(client):
    """Edit must enforce the negative-value check from PR #63."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    pid = (
        await client.post(f"/communities/{community['id']}/proposals", json={
            "user_id": user["id"],
            "proposal_type": "ChangeVariable",
            "proposal_text": "MaxAge",
            "val_text": "5",
        })
    ).json()["id"]

    r = await client.patch(f"/proposals/{pid}/edit", json={
        "user_id": user["id"], "val_text": "-3",
    })
    assert r.status_code == 422, r.text
    assert "non-negative" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_amend_rejects_empty_text_for_add_statement(client):
    """Same defense, applied to amend (which builds a successor row).
    Pre-fix amend skipped content validation, so an author could amend
    a valid v1 down to "" and the v2 Draft would carry blank content."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    pid = (
        await client.post(f"/communities/{community['id']}/proposals", json={
            "user_id": user["id"],
            "proposal_type": "AddStatement",
            "proposal_text": "v1 valid",
        })
    ).json()["id"]

    r = await client.post(f"/proposals/{pid}/amend", json={
        "user_id": user["id"], "proposal_text": "",
    })
    assert r.status_code == 422, r.text
    assert "non-empty" in r.json()["detail"].lower()
