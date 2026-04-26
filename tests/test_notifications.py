"""Tests for the per-user notification inbox."""
import pytest

from tests.conftest import create_test_community


async def _login(client, email: str) -> str:
    """Magic-link login. Creates the human user if not present and
    returns their user_id. Session cookie is left on `client`."""
    r = await client.post("/auth/request-magic-link", json={"email": email})
    assert r.status_code == 200, r.text
    link = r.json()["link"]
    r = await client.get(link)
    assert r.status_code == 200
    return r.json()["user"]["user_id"]


@pytest.mark.asyncio
async def test_proposal_creation_notifies_other_members(client):
    """Filing a proposal in community X drops a `proposal.created`
    notification into every other active member's inbox. The
    author themselves is NOT notified."""
    founder_id = await _login(client, "founder-notif@example.com")
    community = await create_test_community(client, founder_id)

    # Promote a second human via membership flow.
    client.cookies.clear()
    other_id = await _login(client, "other-notif@example.com")
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": other_id,
        "proposal_type": "Membership",
        "proposal_text": "let me in",
        "val_uuid": other_id,
    })
    membership_id = resp.json()["id"]
    await client.patch(f"/proposals/{membership_id}/submit")

    # Founder supports + pulses to land the membership.
    client.cookies.clear()
    await _login(client, "founder-notif@example.com")
    await client.post(
        f"/proposals/{membership_id}/support", json={"user_id": founder_id},
    )
    await client.post(
        f"/communities/{community['id']}/pulses/support",
        json={"user_id": founder_id},
    )
    await client.post(
        f"/communities/{community['id']}/pulses/support",
        json={"user_id": founder_id},
    )

    # The `other` user is now a member and files an AddStatement.
    client.cookies.clear()
    await _login(client, "other-notif@example.com")
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": other_id,
        "proposal_type": "AddStatement",
        "proposal_text": "be excellent to each other",
    })
    assert resp.status_code == 201
    proposal_id = resp.json()["id"]

    # Other (the author) does NOT see a proposal.created for their own.
    resp = await client.get("/users/me/notifications")
    assert resp.status_code == 200
    own_proposal_notes = [
        n for n in resp.json()
        if n["kind"] == "proposal.created"
        and n["payload"].get("proposal_id") == proposal_id
    ]
    assert own_proposal_notes == []

    # Founder DOES.
    client.cookies.clear()
    await _login(client, "founder-notif@example.com")
    resp = await client.get("/users/me/notifications")
    assert resp.status_code == 200
    notes = resp.json()
    found = [
        n for n in notes
        if n["kind"] == "proposal.created"
        and n["payload"].get("proposal_id") == proposal_id
    ]
    assert len(found) == 1
    assert found[0]["payload"]["proposal_type"] == "AddStatement"
    assert "be excellent" in found[0]["payload"]["proposal_text"]


@pytest.mark.asyncio
async def test_proposal_outcome_notifies_author(client):
    """When a proposal is Accepted on a pulse, its author lands
    a `proposal.accepted` notification."""
    user_id = await _login(client, "soloist-notif@example.com")
    community = await create_test_community(client, user_id)

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user_id,
        "proposal_type": "AddStatement",
        "proposal_text": "we keep the kibbutz tidy",
    })
    proposal_id = resp.json()["id"]
    await client.patch(f"/proposals/{proposal_id}/submit")
    await client.post(
        f"/proposals/{proposal_id}/support", json={"user_id": user_id},
    )
    for _ in range(2):
        await client.post(
            f"/communities/{community['id']}/pulses/support",
            json={"user_id": user_id},
        )

    resp = await client.get("/users/me/notifications")
    assert resp.status_code == 200
    accepted = [
        n for n in resp.json()
        if n["kind"] == "proposal.accepted"
        and n["payload"].get("proposal_id") == proposal_id
    ]
    assert len(accepted) == 1


@pytest.mark.asyncio
async def test_age_out_cancellation_notifies_author(client):
    """A proposal that ages out (age > MaxAge → CANCELED) must fire a
    proposal.canceled notification to the author. Pre-fix the
    Accepted/Rejected branch fanned out but the age-cancellation
    branch silently flushed and continued — authors saw their
    proposal disappear from in-flight with no inbox signal."""
    user_id = await _login(client, "ageout-author@example.com")
    community = await create_test_community(client, user_id)

    # Submit a proposal but never support it so it can't promote.
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user_id,
        "proposal_type": "AddStatement",
        "proposal_text": "this will be left to wither",
    })
    proposal_id = resp.json()["id"]
    await client.patch(f"/proposals/{proposal_id}/submit")

    # Trigger 3 pulses (default MaxAge=2; age > 2 → canceled).
    for _ in range(3):
        await client.post(
            f"/communities/{community['id']}/pulses/support",
            json={"user_id": user_id},
        )

    # Sanity: proposal landed in Canceled.
    resp = await client.get(f"/proposals/{proposal_id}")
    assert resp.json()["proposal_status"] == "Canceled"

    # Inbox should now carry exactly one proposal.canceled for it.
    resp = await client.get("/users/me/notifications")
    assert resp.status_code == 200
    canceled = [
        n for n in resp.json()
        if n["kind"] == "proposal.canceled"
        and n["payload"].get("proposal_id") == proposal_id
    ]
    assert len(canceled) == 1, (
        f"expected one proposal.canceled notification, got: "
        f"{[n['kind'] for n in resp.json()]}"
    )


@pytest.mark.asyncio
async def test_self_reply_still_notifies_proposal_author(client):
    """When a commenter replies to their OWN comment on someone
    else's proposal, the proposal author must still get the
    comment.posted notification. Pre-fix the notify chain set
    notify_user_id to the parent comment's author (= the commenter
    themselves), which then short-circuited as a self-notify and
    the proposal author silently got nothing."""
    # Proposal author + a community.
    author_id = await _login(client, "self-reply-author@example.com")
    community = await create_test_community(client, author_id)

    # File a proposal as the author.
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": author_id,
        "proposal_type": "AddStatement",
        "proposal_text": "thread starter",
    })
    proposal_id = resp.json()["id"]

    # Land a second member who will be the commenter.
    client.cookies.clear()
    commenter_id = await _login(client, "self-reply-commenter@example.com")
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": commenter_id,
        "proposal_type": "Membership",
        "proposal_text": "join",
        "val_uuid": commenter_id,
    })
    mid = resp.json()["id"]
    await client.patch(f"/proposals/{mid}/submit")
    client.cookies.clear()
    await _login(client, "self-reply-author@example.com")
    await client.post(
        f"/proposals/{mid}/support", json={"user_id": author_id},
    )
    for _ in range(2):
        await client.post(
            f"/communities/{community['id']}/pulses/support",
            json={"user_id": author_id},
        )

    # Commenter posts a top-level comment (author gets notified, fine).
    client.cookies.clear()
    await _login(client, "self-reply-commenter@example.com")
    r = await client.post(
        f"/entities/proposal/{proposal_id}/comments",
        json={"user_id": commenter_id, "comment_text": "first thought"},
    )
    parent_comment_id = r.json()["id"]

    # Author marks all read so we can isolate the reply signal.
    client.cookies.clear()
    await _login(client, "self-reply-author@example.com")
    await client.post("/users/me/notifications/read-all")

    # Commenter REPLIES to their own comment (parent author == commenter).
    client.cookies.clear()
    await _login(client, "self-reply-commenter@example.com")
    await client.post(
        f"/entities/proposal/{proposal_id}/comments",
        json={
            "user_id": commenter_id,
            "comment_text": "follow-up thought",
            "parent_comment_id": parent_comment_id,
        },
    )

    # Proposal author's inbox must show a NEW comment.posted from
    # the reply — pre-fix it stayed empty.
    client.cookies.clear()
    await _login(client, "self-reply-author@example.com")
    notes = (await client.get("/users/me/notifications?unread_only=true")).json()
    comment_notes = [n for n in notes if n["kind"] == "comment.posted"]
    assert len(comment_notes) == 1, (
        "proposal author must still hear about the reply when the "
        "parent comment author == commenter; got: "
        f"{[n['kind'] for n in notes]}"
    )


@pytest.mark.asyncio
async def test_mark_read_and_unread_count(client):
    """unread-count, mark-one-read and mark-all-read all flip read_at."""
    founder_id = await _login(client, "reader-notif@example.com")
    community = await create_test_community(client, founder_id)

    # Generate at least one notification by seeding a second user
    # and filing a proposal as them.
    client.cookies.clear()
    other_id = await _login(client, "speaker-notif@example.com")
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": other_id,
        "proposal_type": "Membership",
        "proposal_text": "join",
        "val_uuid": other_id,
    })
    membership_id = resp.json()["id"]
    await client.patch(f"/proposals/{membership_id}/submit")

    client.cookies.clear()
    await _login(client, "reader-notif@example.com")
    await client.post(
        f"/proposals/{membership_id}/support", json={"user_id": founder_id},
    )
    for _ in range(2):
        await client.post(
            f"/communities/{community['id']}/pulses/support",
            json={"user_id": founder_id},
        )

    client.cookies.clear()
    await _login(client, "speaker-notif@example.com")
    await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": other_id,
        "proposal_type": "AddStatement",
        "proposal_text": "shared values matter",
    })

    # Founder reads.
    client.cookies.clear()
    await _login(client, "reader-notif@example.com")
    resp = await client.get("/users/me/notifications/unread-count")
    assert resp.status_code == 200
    initial_unread = resp.json()["unread"]
    assert initial_unread >= 1

    # Mark one read.
    notes = (await client.get("/users/me/notifications")).json()
    first_id = notes[0]["id"]
    resp = await client.patch(f"/users/me/notifications/{first_id}/read")
    assert resp.status_code == 200

    after_one = (
        await client.get("/users/me/notifications/unread-count")
    ).json()["unread"]
    assert after_one == initial_unread - 1

    # Mark all.
    resp = await client.post("/users/me/notifications/read-all")
    assert resp.status_code == 200

    final_unread = (
        await client.get("/users/me/notifications/unread-count")
    ).json()["unread"]
    assert final_unread == 0


@pytest.mark.asyncio
async def test_mark_read_404_for_foreign_or_unknown(client):
    """A logged-in user's mark-read against a UUID they don't own
    or that doesn't exist must return 404 — and crucially must not
    distinguish between those two so a fishing client can't probe
    foreign ids."""
    await _login(client, "fisher-notif@example.com")
    bogus = "00000000-0000-0000-0000-000000000077"
    resp = await client.patch(f"/users/me/notifications/{bogus}/read")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_throw_out_target_gets_targets_you_notification(client):
    """A ThrowOut proposal naming user X drops a `proposal.targets_you`
    row in X's inbox the moment it's filed — separate from the
    broadcast `proposal.created` row everyone else gets. This is the
    "thrown out while on vacation" defense."""
    founder_id = await _login(client, "to-founder@example.com")
    community = await create_test_community(client, founder_id)

    client.cookies.clear()
    target_id = await _login(client, "to-target@example.com")
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": target_id,
        "proposal_type": "Membership",
        "proposal_text": "join",
        "val_uuid": target_id,
    })
    membership_id = resp.json()["id"]
    await client.patch(f"/proposals/{membership_id}/submit")

    client.cookies.clear()
    await _login(client, "to-founder@example.com")
    await client.post(
        f"/proposals/{membership_id}/support", json={"user_id": founder_id},
    )
    for _ in range(2):
        await client.post(
            f"/communities/{community['id']}/pulses/support",
            json={"user_id": founder_id},
        )

    # Founder files a ThrowOut targeting `target`.
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": founder_id,
        "proposal_type": "ThrowOut",
        "proposal_text": "they broke the rules",
        "val_uuid": target_id,
    })
    assert resp.status_code == 201

    client.cookies.clear()
    await _login(client, "to-target@example.com")
    resp = await client.get("/users/me/notifications")
    targeted = [n for n in resp.json() if n["kind"] == "proposal.targets_you"]
    assert len(targeted) >= 1
    assert targeted[0]["payload"]["proposal_type"] == "ThrowOut"


@pytest.mark.asyncio
async def test_self_membership_does_not_self_notify(client):
    """A user filing their own Membership shouldn't get a
    `proposal.targets_you` for themselves — that's noise."""
    founder_id = await _login(client, "self-mem-f@example.com")
    community = await create_test_community(client, founder_id)

    client.cookies.clear()
    user_id = await _login(client, "self-mem-u@example.com")
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user_id,
        "proposal_type": "Membership",
        "proposal_text": "letting myself in",
        "val_uuid": user_id,
    })
    assert resp.status_code == 201
    resp = await client.get("/users/me/notifications")
    assert all(n["kind"] != "proposal.targets_you" for n in resp.json())


@pytest.mark.asyncio
async def test_proposal_promoted_to_on_the_air_notifies_non_supporters(client):
    """When OutThere → OnTheAir, every active member who hasn't
    supported it (and isn't the author) gets a `proposal.vote_missing`
    row. Existing supporters and the author are filtered out."""
    founder_id = await _login(client, "vm-founder@example.com")
    community = await create_test_community(client, founder_id)

    client.cookies.clear()
    other_id = await _login(client, "vm-other@example.com")
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": other_id,
        "proposal_type": "Membership",
        "proposal_text": "join",
        "val_uuid": other_id,
    })
    membership_id = resp.json()["id"]
    await client.patch(f"/proposals/{membership_id}/submit")
    client.cookies.clear()
    await _login(client, "vm-founder@example.com")
    await client.post(
        f"/proposals/{membership_id}/support", json={"user_id": founder_id},
    )
    for _ in range(2):
        await client.post(
            f"/communities/{community['id']}/pulses/support",
            json={"user_id": founder_id},
        )

    # Other files AddStatement and supports their own to drive promote.
    client.cookies.clear()
    await _login(client, "vm-other@example.com")
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": other_id,
        "proposal_type": "AddStatement",
        "proposal_text": "we keep things tidy",
    })
    proposal_id = resp.json()["id"]
    await client.patch(f"/proposals/{proposal_id}/submit")
    await client.post(
        f"/proposals/{proposal_id}/support", json={"user_id": other_id},
    )

    # Founder triggers the pulse — promotes OutThere → OnTheAir.
    client.cookies.clear()
    await _login(client, "vm-founder@example.com")
    await client.post(
        f"/communities/{community['id']}/pulses/support",
        json={"user_id": founder_id},
    )

    # Founder didn't support the proposal → must have a vote_missing.
    resp = await client.get("/users/me/notifications")
    vm = [
        n for n in resp.json()
        if n["kind"] == "proposal.vote_missing"
        and n["payload"].get("proposal_id") == proposal_id
    ]
    assert len(vm) == 1

    # The author (other) supported it → must NOT get one.
    client.cookies.clear()
    await _login(client, "vm-other@example.com")
    resp = await client.get("/users/me/notifications")
    own = [
        n for n in resp.json()
        if n["kind"] == "proposal.vote_missing"
        and n["payload"].get("proposal_id") == proposal_id
    ]
    assert own == []


@pytest.mark.asyncio
async def test_notifications_require_session(client):
    """No anonymous inbox."""
    bogus = "00000000-0000-0000-0000-000000000099"
    assert (await client.get("/users/me/notifications")).status_code in (401, 403)
    assert (await client.get("/users/me/notifications/unread-count")).status_code in (401, 403)
    assert (await client.patch(f"/users/me/notifications/{bogus}/read")).status_code in (401, 403)
    assert (await client.post("/users/me/notifications/read-all")).status_code in (401, 403)
