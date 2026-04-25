import pytest
from tests.conftest import create_test_user, create_test_community


async def _accept_proposal(client, community_id, user_id, proposal_id):
    """Helper: submit, support, and run 2 pulses to accept a proposal."""
    await client.patch(f"/proposals/{proposal_id}/submit")
    await client.post(f"/proposals/{proposal_id}/support", json={"user_id": user_id})
    await client.post(f"/communities/{community_id}/pulses/support", json={"user_id": user_id})
    await client.post(f"/communities/{community_id}/pulses/support", json={"user_id": user_id})


@pytest.mark.asyncio
async def test_add_statement_via_proposal(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "All members are equal",
    })
    await _accept_proposal(client, community["id"], user["id"], resp.json()["id"])

    resp = await client.get(f"/communities/{community['id']}/statements")
    assert len(resp.json()) == 1
    assert resp.json()[0]["statement_text"] == "All members are equal"


@pytest.mark.asyncio
async def test_remove_statement_via_proposal(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Add a statement first
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Temporary rule",
    })
    await _accept_proposal(client, community["id"], user["id"], resp.json()["id"])

    # Get the statement ID
    resp = await client.get(f"/communities/{community['id']}/statements")
    statement_id = resp.json()[0]["id"]

    # Remove it
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "RemoveStatement",
        "proposal_text": "Remove temporary rule",
        "val_uuid": statement_id,
    })
    await _accept_proposal(client, community["id"], user["id"], resp.json()["id"])

    resp = await client.get(f"/communities/{community['id']}/statements")
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_remove_statement_cannot_target_foreign_community(client):
    """An accepted RemoveStatement in community A whose val_uuid points
    at a statement in community B must NOT remove B's statement.
    Without the community_id guard in the executor, A could delete
    B's text just by accepting a proposal with a foreign val_uuid."""
    user = await create_test_user(client)
    a = await create_test_community(client, user["id"], name="Alpha")
    b = await create_test_community(client, user["id"], name="Bravo")

    # Land a statement in B.
    resp = await client.post(f"/communities/{b['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Bravo's rule",
    })
    await _accept_proposal(client, b["id"], user["id"], resp.json()["id"])
    b_stmt_id = (await client.get(f"/communities/{b['id']}/statements")).json()[0]["id"]

    # File a RemoveStatement in A that points at B's statement.
    resp = await client.post(f"/communities/{a['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "RemoveStatement",
        "proposal_text": "Remove Bravo's rule from inside Alpha",
        "val_uuid": b_stmt_id,
    })
    await _accept_proposal(client, a["id"], user["id"], resp.json()["id"])

    # B's statement must still be active — A cannot delete it.
    b_stmts = (await client.get(f"/communities/{b['id']}/statements")).json()
    assert len(b_stmts) == 1
    assert b_stmts[0]["id"] == b_stmt_id


@pytest.mark.asyncio
async def test_replace_statement_cannot_target_foreign_community(client):
    """ReplaceStatement with a foreign val_uuid must abort entirely —
    don't remove B's statement, and don't insert a stub successor in
    A pointing at B's id either."""
    user = await create_test_user(client)
    a = await create_test_community(client, user["id"], name="Alpha")
    b = await create_test_community(client, user["id"], name="Bravo")

    resp = await client.post(f"/communities/{b['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Bravo original",
    })
    await _accept_proposal(client, b["id"], user["id"], resp.json()["id"])
    b_stmt_id = (await client.get(f"/communities/{b['id']}/statements")).json()[0]["id"]

    resp = await client.post(f"/communities/{a['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "ReplaceStatement",
        "proposal_text": "Try to hijack Bravo's statement",
        "val_uuid": b_stmt_id,
        "val_text": "Hijacked text",
    })
    await _accept_proposal(client, a["id"], user["id"], resp.json()["id"])

    # B's statement still active and unchanged.
    b_stmts = (await client.get(f"/communities/{b['id']}/statements")).json()
    assert len(b_stmts) == 1
    assert b_stmts[0]["statement_text"] == "Bravo original"
    # A did NOT gain a phantom statement referencing B's row.
    a_stmts = (await client.get(f"/communities/{a['id']}/statements")).json()
    assert all(s["statement_text"] != "Hijacked text" for s in a_stmts)


@pytest.mark.asyncio
async def test_replace_statement_via_proposal(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Add a statement
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Version 1",
    })
    await _accept_proposal(client, community["id"], user["id"], resp.json()["id"])

    stmt_id = (await client.get(f"/communities/{community['id']}/statements")).json()[0]["id"]

    # Replace it
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "ReplaceStatement",
        "proposal_text": "Replace Version 1",
        "val_uuid": stmt_id,
        "val_text": "Version 2",
    })
    await _accept_proposal(client, community["id"], user["id"], resp.json()["id"])

    resp = await client.get(f"/communities/{community['id']}/statements")
    statements = resp.json()
    assert len(statements) == 1
    assert statements[0]["statement_text"] == "Version 2"
    assert statements[0]["prev_statement_id"] == stmt_id
