"""Tests for the agent memory system.

Covers:
- Memory API endpoints (CRUD)
- MemoryService (DB layer)
- MemoryFormatter (prompt generation)
- MemoryExtractor (action log → memory extraction)
- Pruning logic
"""

import uuid

import pytest
from tests.conftest import create_test_user


# ── API Endpoint Tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_get_memory(client):
    user = await create_test_user(client)
    uid = user["id"]

    # Create a memory
    resp = await client.post("/memories", json={
        "user_id": uid,
        "memory_type": "episodic",
        "content": "My AddStatement proposal was accepted",
        "importance": 0.8,
        "category": "proposal_outcome",
        "round_num": 5,
    })
    assert resp.status_code == 200
    mem = resp.json()
    assert mem["user_id"] == uid
    assert mem["memory_type"] == "episodic"
    assert mem["content"] == "My AddStatement proposal was accepted"
    assert mem["importance"] == 0.8
    assert mem["round_num"] == 5

    # Get memories
    resp = await client.get(f"/memories/{uid}")
    assert resp.status_code == 200
    memories = resp.json()
    assert len(memories) == 1
    assert memories[0]["id"] == mem["id"]


@pytest.mark.asyncio
async def test_create_memory_rejects_out_of_range_importance(client):
    """The list endpoint bounds min_importance to [0, 1]; write endpoints
    must match, otherwise an agent can write importance=10 and break the
    ordering/threshold semantics downstream."""
    user = await create_test_user(client)
    uid = user["id"]
    for bad in (1.5, -0.1, 99.0):
        resp = await client.post("/memories", json={
            "user_id": uid, "memory_type": "episodic",
            "content": "out of range", "importance": bad,
        })
        assert resp.status_code == 422, f"importance={bad} should 422"


@pytest.mark.asyncio
async def test_update_memory_rejects_out_of_range_importance(client):
    user = await create_test_user(client)
    uid = user["id"]
    created = await client.post("/memories", json={
        "user_id": uid, "memory_type": "episodic",
        "content": "baseline", "importance": 0.5,
    })
    mem_id = created.json()["id"]
    resp = await client.put(f"/memories/{mem_id}", json={"importance": 2.0})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_memories_by_type(client):
    user = await create_test_user(client)
    uid = user["id"]

    # Create different types
    await client.post("/memories", json={
        "user_id": uid, "memory_type": "episodic",
        "content": "Something happened", "importance": 0.5,
    })
    await client.post("/memories", json={
        "user_id": uid, "memory_type": "goal",
        "content": "Write artifact", "importance": 0.7,
    })
    await client.post("/memories", json={
        "user_id": uid, "memory_type": "goal",
        "content": "Join action team", "importance": 0.5,
    })

    # Filter by type
    resp = await client.get(f"/memories/{uid}", params={"memory_type": "goal"})
    goals = resp.json()
    assert len(goals) == 2
    assert all(g["memory_type"] == "goal" for g in goals)

    resp = await client.get(f"/memories/{uid}", params={"memory_type": "episodic"})
    eps = resp.json()
    assert len(eps) == 1


@pytest.mark.asyncio
async def test_get_memories_by_importance(client):
    user = await create_test_user(client)
    uid = user["id"]

    await client.post("/memories", json={
        "user_id": uid, "memory_type": "episodic",
        "content": "Low importance", "importance": 0.2,
    })
    await client.post("/memories", json={
        "user_id": uid, "memory_type": "episodic",
        "content": "High importance", "importance": 0.9,
    })

    resp = await client.get(f"/memories/{uid}", params={"min_importance": 0.5})
    mems = resp.json()
    assert len(mems) == 1
    assert mems[0]["content"] == "High importance"


@pytest.mark.asyncio
async def test_get_memories_ordered_by_importance(client):
    user = await create_test_user(client)
    uid = user["id"]

    for imp in [0.3, 0.9, 0.5, 0.7]:
        await client.post("/memories", json={
            "user_id": uid, "memory_type": "episodic",
            "content": f"importance {imp}", "importance": imp,
        })

    resp = await client.get(f"/memories/{uid}", params={"order_by": "importance"})
    mems = resp.json()
    importances = [m["importance"] for m in mems]
    assert importances == sorted(importances, reverse=True)


@pytest.mark.asyncio
async def test_update_memory(client):
    user = await create_test_user(client)
    uid = user["id"]

    resp = await client.post("/memories", json={
        "user_id": uid, "memory_type": "goal",
        "content": "Write artifact", "importance": 0.7,
    })
    mem_id = resp.json()["id"]

    # Update importance and content
    resp = await client.put(f"/memories/{mem_id}", json={
        "importance": 0.0,
        "content": "COMPLETED: Write artifact",
    })
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["importance"] == 0.0
    assert updated["content"] == "COMPLETED: Write artifact"


@pytest.mark.asyncio
async def test_update_memory_not_found_returns_404(client):
    """Updating a memory that doesn't exist returns 404, not 200."""
    fake_id = str(uuid.uuid4())
    resp = await client.put(f"/memories/{fake_id}", json={"importance": 0.5})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_memory_no_fields_returns_400(client):
    """Updating with an empty body returns 400, not 200."""
    user = await create_test_user(client)
    resp = await client.post("/memories", json={
        "user_id": user["id"], "memory_type": "goal",
        "content": "x", "importance": 0.5,
    })
    mem_id = resp.json()["id"]
    resp = await client.put(f"/memories/{mem_id}", json={})
    assert resp.status_code == 400


async def _login_email(client, email: str) -> str:
    """Magic-link login. Returns user_id; leaves session cookie set."""
    r = await client.post("/auth/request-magic-link", json={"email": email})
    r = await client.get(r.json()["link"])
    return r.json()["user"]["user_id"]


@pytest.mark.asyncio
async def test_create_memory_session_spoof_blocked(client):
    """A logged-in human can't POST a memory with someone else's
    user_id. Pre-fix the endpoint had ZERO ownership binding, so any
    logged-in user could spam memories into another user's account."""
    victim = await create_test_user(client)
    await _login_email(client, "mem-attacker@example.com")
    r = await client.post("/memories", json={
        "user_id": victim["id"],
        "memory_type": "episodic",
        "content": "implanted memory",
        "importance": 0.5,
    })
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_update_memory_session_spoof_blocked(client):
    """A logged-in human can't PUT changes to someone else's memory.
    Pre-fix anyone could rewrite anyone's memories."""
    victim = await create_test_user(client)
    # Victim's memory is created without a session.
    r = await client.post("/memories", json={
        "user_id": victim["id"], "memory_type": "goal",
        "content": "original", "importance": 0.5,
    })
    mem_id = r.json()["id"]
    # Attacker logs in.
    await _login_email(client, "mem-update-attacker@example.com")
    r = await client.put(f"/memories/{mem_id}", json={"content": "hijacked"})
    assert r.status_code == 403
    # Victim's memory is unchanged. (Re-read via GET — public.)
    r = await client.get(f"/memories/{victim['id']}")
    assert r.json()[0]["content"] == "original"


@pytest.mark.asyncio
async def test_prune_session_spoof_blocked(client):
    """DELETE /memories/prune/{user_id} for a foreign user must 403.
    Pre-fix anyone could wipe anyone's memories with one curl."""
    victim = await create_test_user(client)
    await client.post("/memories", json={
        "user_id": victim["id"], "memory_type": "goal",
        "content": "do not delete me", "importance": 0.5,
        "round_num": 0, "expires_at": 1,
    })
    await _login_email(client, "mem-prune-attacker@example.com")
    r = await client.delete(
        f"/memories/prune/{victim['id']}?current_round=99",
    )
    assert r.status_code == 403
    # Victim's memory is unchanged.
    r = await client.get(f"/memories/{victim['id']}")
    assert len(r.json()) == 1


@pytest.mark.asyncio
async def test_create_memory_rejects_unknown_memory_type(client):
    """Typos like `episodc` produce dead rows that no filter retrieves —
    the service only queries against the four canonical types. Schema
    should reject unknowns before they hit the DB."""
    user = await create_test_user(client)
    resp = await client.post("/memories", json={
        "user_id": user["id"],
        "memory_type": "episodc",  # typo
        "content": "lost forever",
        "importance": 0.5,
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_relationship_lookup(client):
    user = await create_test_user(client)
    uid = user["id"]
    target_id = str(uuid.uuid4())

    await client.post("/memories", json={
        "user_id": uid, "memory_type": "relationship",
        "content": "ally — supports my proposals",
        "importance": 0.6, "related_id": target_id,
    })

    # Found
    resp = await client.get(f"/memories/{uid}/relationship/{target_id}")
    assert resp.status_code == 200
    rel = resp.json()
    assert rel["content"] == "ally — supports my proposals"
    assert rel["related_id"] == target_id

    # Not found
    other_id = str(uuid.uuid4())
    resp = await client.get(f"/memories/{uid}/relationship/{other_id}")
    assert resp.status_code == 200
    assert "detail" in resp.json()


@pytest.mark.asyncio
async def test_prune_expired(client):
    user = await create_test_user(client)
    uid = user["id"]

    # Create an expired memory
    await client.post("/memories", json={
        "user_id": uid, "memory_type": "episodic",
        "content": "Old chat message", "importance": 0.2,
        "round_num": 1, "expires_at": 5,
    })
    # Create a non-expired memory
    await client.post("/memories", json={
        "user_id": uid, "memory_type": "episodic",
        "content": "Recent event", "importance": 0.8,
        "round_num": 8,
    })

    # Prune at round 10
    resp = await client.delete(f"/memories/prune/{uid}", params={"current_round": 10})
    assert resp.status_code == 200
    assert resp.json()["deleted"] >= 1

    # Only non-expired should remain
    resp = await client.get(f"/memories/{uid}")
    mems = resp.json()
    assert len(mems) == 1
    assert mems[0]["content"] == "Recent event"


@pytest.mark.asyncio
async def test_prune_caps_per_type(client):
    """Pruning caps episodic memories at 50, keeping highest importance."""
    user = await create_test_user(client)
    uid = user["id"]

    # Create 55 episodic memories
    for i in range(55):
        await client.post("/memories", json={
            "user_id": uid, "memory_type": "episodic",
            "content": f"Event {i}", "importance": i / 100.0,
            "round_num": i,
        })

    resp = await client.get(f"/memories/{uid}", params={"limit": 100})
    assert len(resp.json()) == 55

    # Prune
    resp = await client.delete(f"/memories/prune/{uid}", params={"current_round": 100})
    assert resp.json()["deleted"] >= 5

    # Should be capped at 50
    resp = await client.get(f"/memories/{uid}", params={"limit": 100})
    remaining = resp.json()
    assert len(remaining) <= 50


@pytest.mark.asyncio
async def test_memories_isolated_per_user(client):
    """Each user's memories are isolated."""
    user1 = await create_test_user(client)
    user2 = await create_test_user(client)

    await client.post("/memories", json={
        "user_id": user1["id"], "memory_type": "episodic",
        "content": "User1 event", "importance": 0.5,
    })
    await client.post("/memories", json={
        "user_id": user2["id"], "memory_type": "episodic",
        "content": "User2 event", "importance": 0.5,
    })

    resp1 = await client.get(f"/memories/{user1['id']}")
    resp2 = await client.get(f"/memories/{user2['id']}")

    assert len(resp1.json()) == 1
    assert resp1.json()[0]["content"] == "User1 event"
    assert len(resp2.json()) == 1
    assert resp2.json()[0]["content"] == "User2 event"


@pytest.mark.asyncio
async def test_memory_routes_reject_malformed_uuid_with_422(client):
    """Memory routes used to take `user_id` / `memory_id` as plain str
    and call `uuid.UUID(...)` inside the handler — which raises
    ValueError on a malformed value and surfaces as a 500. The
    endpoints now declare those params as `uuid.UUID`, so FastAPI
    rejects the bad request cleanly with a 422.
    """
    # Path-param UUID: GET /memories/{user_id}
    resp = await client.get("/memories/not-a-uuid")
    assert resp.status_code == 422

    # Path-param UUID: PUT /memories/{memory_id}
    resp = await client.put("/memories/not-a-uuid", json={"importance": 0.3})
    assert resp.status_code == 422

    # Path-param UUID: DELETE /memories/prune/{user_id}
    resp = await client.delete("/memories/prune/not-a-uuid?current_round=1")
    assert resp.status_code == 422

    # Body-field UUID: POST /memories
    resp = await client.post("/memories", json={
        "user_id": "definitely-not-a-uuid",
        "memory_type": "episodic",
        "content": "x",
        "importance": 0.5,
    })
    assert resp.status_code == 422


# ── MemoryService Direct Tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_service_crud(db):
    from kbz.services.memory_service import MemoryService

    svc = MemoryService(db)
    uid = uuid.uuid4()

    # Add
    mem = await svc.add_memory(uid, "episodic", "Test event", importance=0.7, round_num=5)
    assert mem["memory_type"] == "episodic"
    assert mem["importance"] == 0.7

    # Get
    mems = await svc.get_memories(uid, memory_type="episodic")
    assert len(mems) == 1

    # Update
    updated = await svc.update_memory(uuid.UUID(mem["id"]), importance=0.1)
    assert updated["importance"] == 0.1

    # Find relationship (none exists)
    rel = await svc.find_relationship(uid, uuid.uuid4())
    assert rel is None


@pytest.mark.asyncio
async def test_memory_service_find_relationship(db):
    from kbz.services.memory_service import MemoryService

    svc = MemoryService(db)
    uid = uuid.uuid4()
    target = uuid.uuid4()

    await svc.add_memory(
        uid, "relationship", "ally", importance=0.6, related_id=target,
    )

    found = await svc.find_relationship(uid, target)
    assert found is not None
    assert found["content"] == "ally"

    not_found = await svc.find_relationship(uid, uuid.uuid4())
    assert not_found is None


@pytest.mark.asyncio
async def test_find_relationship_handles_duplicate_rows(db):
    """agent_memories has no uniqueness on (user, type=relationship,
    related_id), so an agent that ran the relationship-extractor
    twice for the same target ended up with two rows. Pre-fix
    find_relationship called scalar_one_or_none() and crashed with
    MultipleResultsFound on the next lookup. Now it returns the most
    recently created row instead."""
    from kbz.services.memory_service import MemoryService

    svc = MemoryService(db)
    uid = uuid.uuid4()
    target = uuid.uuid4()

    # Two relationship memories for the same pair (no uniqueness enforced).
    await svc.add_memory(
        uid, "relationship", "ally — old note",
        importance=0.4, related_id=target,
    )
    await svc.add_memory(
        uid, "relationship", "ally — most recent observation",
        importance=0.7, related_id=target,
    )

    # Pre-fix this raised MultipleResultsFound; now it returns the
    # most recently created row (ordered by created_at desc).
    found = await svc.find_relationship(uid, target)
    assert found is not None
    assert "most recent" in found["content"]


# ── MemoryFormatter Tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_formatter_empty_returns_empty(db):
    """Formatter returns empty string when agent has no memories."""
    from kbz.services.memory_service import MemoryService
    from agents.memory_formatter import MemoryFormatter

    uid = uuid.uuid4()

    # Create a mock MemoryStore that reads directly from db
    class DirectStore:
        def __init__(self, svc):
            self.svc = svc
        async def get_latest_reflection(self, user_id):
            mems = await self.svc.get_memories(uuid.UUID(user_id), memory_type="reflection", limit=1)
            return mems[0] if mems else None
        async def get_goals(self, user_id, active_only=True):
            return await self.svc.get_memories(uuid.UUID(user_id), memory_type="goal",
                                                min_importance=0.01 if active_only else 0.0)
        async def get_relationships(self, user_id, limit=10):
            return await self.svc.get_memories(uuid.UUID(user_id), memory_type="relationship", limit=limit)
        async def get_recent(self, user_id, memory_type=None, limit=10):
            return await self.svc.get_memories(uuid.UUID(user_id), memory_type=memory_type, limit=limit)

    svc = MemoryService(db)
    store = DirectStore(svc)
    fmt = MemoryFormatter(store)

    ctx = await fmt.build_memory_context(str(uid))
    assert ctx == ""


@pytest.mark.asyncio
async def test_formatter_produces_sections(db):
    """Formatter includes all sections when data exists."""
    from kbz.services.memory_service import MemoryService
    from agents.memory_formatter import MemoryFormatter

    uid = uuid.uuid4()
    friend = uuid.uuid4()

    class DirectStore:
        def __init__(self, svc):
            self.svc = svc
        async def get_latest_reflection(self, user_id):
            mems = await self.svc.get_memories(uuid.UUID(user_id), memory_type="reflection", limit=1)
            return mems[0] if mems else None
        async def get_goals(self, user_id, active_only=True):
            return await self.svc.get_memories(uuid.UUID(user_id), memory_type="goal",
                                                min_importance=0.01 if active_only else 0.0)
        async def get_relationships(self, user_id, limit=10):
            return await self.svc.get_memories(uuid.UUID(user_id), memory_type="relationship", limit=limit)
        async def get_recent(self, user_id, memory_type=None, limit=10):
            return await self.svc.get_memories(uuid.UUID(user_id), memory_type=memory_type, limit=limit)

    svc = MemoryService(db)
    await svc.add_memory(uid, "reflection", "I need to focus on artifacts.", importance=0.9, round_num=10)
    await svc.add_memory(uid, "goal", "Complete the Charter", importance=0.7, round_num=8)
    await svc.add_memory(uid, "relationship", "strong ally", importance=0.6, related_id=friend, round_num=9)
    await svc.add_memory(uid, "episodic", "My proposal was accepted", importance=0.8, round_num=10)

    store = DirectStore(svc)
    fmt = MemoryFormatter(store, {str(friend): "Dana"})

    ctx = await fmt.build_memory_context(str(uid))
    assert "=== YOUR MEMORY ===" in ctx
    assert "REFLECTION" in ctx
    assert "ACTIVE GOALS" in ctx
    assert "KEY RELATIONSHIPS" in ctx
    assert "RECENT NOTABLE EVENTS" in ctx
    assert "Dana" in ctx
    assert "Charter" in ctx

    # Should be within token budget (~600 tokens ≈ ~2400 chars)
    assert len(ctx) < 3000


# ── MemoryExtractor Tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extractor_create_proposal(db):
    """Extractor creates episodic memory from create_proposal actions."""
    from datetime import datetime, timezone
    from kbz.services.memory_service import MemoryService
    from agents.memory_extractor import MemoryExtractor
    from agents.agent import ActionLog
    from dataclasses import dataclass, field

    uid = uuid.uuid4()
    uid_str = str(uid)

    class DirectStore:
        """Minimal store that writes directly to DB."""
        def __init__(self, svc):
            self.svc = svc
        async def add(self, **kwargs):
            user_id = kwargs.pop("user_id")
            return await self.svc.add_memory(uuid.UUID(user_id), **kwargs)
        async def get_relationship_with(self, user_id, target_user_id):
            return await self.svc.find_relationship(uuid.UUID(user_id), uuid.UUID(target_user_id))
        async def update(self, memory_id, **fields):
            return await self.svc.update_memory(uuid.UUID(memory_id), **fields)

    svc = MemoryService(db)
    store = DirectStore(svc)
    extractor = MemoryExtractor(store)

    # Mock snapshot with empty lists
    @dataclass
    class MockSnapshot:
        proposals_out_there: list = field(default_factory=list)
        proposals_on_the_air: list = field(default_factory=list)
        proposals_draft: list = field(default_factory=list)
        recent_accepted: list = field(default_factory=list)
        recent_rejected: list = field(default_factory=list)

    log = ActionLog(
        timestamp=datetime.now(timezone.utc),
        action_type="create_proposal",
        reason="We need a community rule",
        details='Created [AddStatement] "Members must attend weekly meetings" (id: abc123)',
        success=True,
        ref_id=str(uuid.uuid4()),
    )

    await extractor.extract_from_actions(uid_str, [log], MockSnapshot(), round_num=5)

    mems = await svc.get_memories(uid, memory_type="episodic")
    assert len(mems) >= 1
    assert "community rule" in mems[0]["content"] or "AddStatement" in mems[0]["content"]
    assert "weekly meetings" in mems[0]["content"]
    assert mems[0]["round_num"] == 5


@pytest.mark.asyncio
async def test_extractor_edit_artifact_creates_goal(db):
    """EditArtifact proposals create goal memories."""
    from datetime import datetime, timezone
    from kbz.services.memory_service import MemoryService
    from agents.memory_extractor import MemoryExtractor
    from agents.agent import ActionLog
    from dataclasses import dataclass, field

    uid = uuid.uuid4()
    uid_str = str(uid)

    class DirectStore:
        def __init__(self, svc):
            self.svc = svc
        async def add(self, **kwargs):
            user_id = kwargs.pop("user_id")
            return await self.svc.add_memory(uuid.UUID(user_id), **kwargs)
        async def get_relationship_with(self, user_id, target_user_id):
            return None
        async def update(self, memory_id, **fields):
            return await self.svc.update_memory(uuid.UUID(memory_id), **fields)

    svc = MemoryService(db)
    store = DirectStore(svc)
    extractor = MemoryExtractor(store)

    @dataclass
    class MockSnapshot:
        proposals_out_there: list = field(default_factory=list)
        proposals_on_the_air: list = field(default_factory=list)
        proposals_draft: list = field(default_factory=list)
        recent_accepted: list = field(default_factory=list)
        recent_rejected: list = field(default_factory=list)

    log = ActionLog(
        timestamp=datetime.now(timezone.utc),
        action_type="create_proposal",
        reason="Filling empty artifact",
        details='Created [EditArtifact] "How We Onboard New Members" (id: def456)',
        success=True,
        ref_id=str(uuid.uuid4()),
    )

    await extractor.extract_from_actions(uid_str, [log], MockSnapshot(), round_num=7)

    goals = await svc.get_memories(uid, memory_type="goal")
    assert len(goals) >= 1
    assert "artifact" in goals[0]["content"].lower()


@pytest.mark.asyncio
async def test_extractor_deduplicates_outcomes(db):
    """Extractor doesn't record the same proposal outcome twice."""
    from kbz.services.memory_service import MemoryService
    from agents.memory_extractor import MemoryExtractor
    from dataclasses import dataclass, field

    uid = uuid.uuid4()
    uid_str = str(uid)
    proposal_id = str(uuid.uuid4())

    class DirectStore:
        def __init__(self, svc):
            self.svc = svc
        async def add(self, **kwargs):
            user_id = kwargs.pop("user_id")
            return await self.svc.add_memory(uuid.UUID(user_id), **kwargs)
        async def get_relationship_with(self, user_id, target_user_id):
            return None
        async def update(self, memory_id, **fields):
            return await self.svc.update_memory(uuid.UUID(memory_id), **fields)

    svc = MemoryService(db)
    store = DirectStore(svc)
    extractor = MemoryExtractor(store)

    @dataclass
    class MockSnapshot:
        proposals_out_there: list = field(default_factory=list)
        proposals_on_the_air: list = field(default_factory=list)
        proposals_draft: list = field(default_factory=list)
        recent_accepted: list = field(default_factory=list)
        recent_rejected: list = field(default_factory=list)

    # Simulate the same accepted proposal appearing in multiple rounds
    snapshot = MockSnapshot(recent_accepted=[{
        "id": proposal_id,
        "user_id": uid_str,
        "proposal_type": "AddStatement",
        "proposal_text": "Be kind to each other",
    }])

    # Process twice (simulating two consecutive rounds)
    await extractor.extract_from_actions(uid_str, [], snapshot, round_num=5)
    await extractor.extract_from_actions(uid_str, [], snapshot, round_num=6)

    mems = await svc.get_memories(uid, memory_type="episodic")
    outcome_mems = [m for m in mems if "ACCEPTED" in m["content"]]
    assert len(outcome_mems) == 1  # Only one, not two
