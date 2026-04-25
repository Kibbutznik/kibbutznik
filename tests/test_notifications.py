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
async def test_notifications_require_session(client):
    """No anonymous inbox."""
    bogus = "00000000-0000-0000-0000-000000000099"
    assert (await client.get("/users/me/notifications")).status_code in (401, 403)
    assert (await client.get("/users/me/notifications/unread-count")).status_code in (401, 403)
    assert (await client.patch(f"/users/me/notifications/{bogus}/read")).status_code in (401, 403)
    assert (await client.post("/users/me/notifications/read-all")).status_code in (401, 403)
