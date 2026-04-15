"""Builds a human-readable memory context string for injection into LLM prompts.

Target budget: ~600 tokens total.

When a TKGClient is available, relationships and recent-events sections are
sourced from the Temporal Knowledge Graph with a hard ~300 ms budget each.
On timeout or error we fall back to the legacy MemoryStore path so agent
turns never stall on a slow TKG query.
"""

import asyncio
import logging

from agents.memory import MemoryStore
from agents.tkg_client import TKGClient

logger = logging.getLogger(__name__)


# Hard budget per TKG call. Matches KBZ_TKG_SEMANTIC_TIMEOUT_MS default (300).
_TKG_TIMEOUT_S = 0.3


class MemoryFormatter:
    """Reads an agent's memories and formats them into a compact prompt section."""

    def __init__(
        self,
        memory_store: MemoryStore,
        users_cache: dict[str, str] | None = None,
        tkg_client: TKGClient | None = None,
    ):
        self.store = memory_store
        self.tkg = tkg_client
        self.users_cache = users_cache or {}

    def _name(self, user_id: str | None) -> str:
        """Resolve a user_id to a display name, falling back to truncated ID."""
        if not user_id:
            return "unknown"
        return self.users_cache.get(user_id, user_id[:8])

    async def build_memory_context(
        self, user_id: str, budget_tokens: int = 600,
        current_round: int | None = None,
        query_hint: str | None = None,
    ) -> str:
        """Assemble the === YOUR MEMORY === block for the LLM prompt."""
        char_budget = budget_tokens * 4  # rough token→char ratio

        # Reflections + goals — always from legacy store (textual, not graph)
        reflection = await self.store.get_latest_reflection(user_id)
        goals = await self.store.get_goals(user_id, active_only=True)

        # Relationships — TKG first, fallback to legacy
        relationships = await self._relationships(user_id, current_round)

        # Episodes — TKG semantic search if we have a query hint, else legacy
        episodes = await self._episodes(user_id, query_hint)

        if not reflection and not goals and not relationships and not episodes:
            return ""

        parts: list[str] = ["=== YOUR MEMORY ==="]
        chars_used = 20

        # 1. Reflection (~180 tokens)
        if reflection:
            r_round = reflection.get("round_num", "?")
            r_content = reflection["content"]
            if len(r_content) > 700:
                r_content = r_content[:700].rsplit(" ", 1)[0] + "..."
            section = f"\nREFLECTION (round {r_round}):\n{r_content}"
            parts.append(section)
            chars_used += len(section)

        # 2. Active Goals (~120 tokens)
        if goals:
            goal_lines = []
            for g in goals[:3]:
                r = f" [round {g['round_num']}]" if g.get("round_num") else ""
                content = g["content"]
                if len(content) > 120:
                    content = content[:120].rsplit(" ", 1)[0] + "..."
                goal_lines.append(f"- {content}{r}")
            section = "\nACTIVE GOALS:\n" + "\n".join(goal_lines)
            parts.append(section)
            chars_used += len(section)

        # 3. Relationships (~120 tokens)
        if relationships:
            rel_lines = []
            for r in relationships[:5]:
                target_name = self._name(r.get("related_id"))
                content = r["content"]
                if len(content) > 100:
                    content = content[:100].rsplit(" ", 1)[0] + "..."
                rel_lines.append(f"- {target_name}: {content}")
            section = "\nKEY RELATIONSHIPS:\n" + "\n".join(rel_lines)
            parts.append(section)
            chars_used += len(section)

        # 4. Recent Episodes (~100 tokens)
        if episodes and chars_used < char_budget - 200:
            ep_lines = []
            for e in episodes[:4]:
                r = f"Round {e['round_num']}: " if e.get("round_num") else ""
                content = e["content"]
                if len(content) > 100:
                    content = content[:100].rsplit(" ", 1)[0] + "..."
                ep_lines.append(f"- {r}{content}")
            section = "\nRECENT NOTABLE EVENTS:\n" + "\n".join(ep_lines)
            parts.append(section)
            chars_used += len(section)

        result = "\n".join(parts)

        if len(result) > char_budget and len(parts) > 3:
            result = "\n".join(parts[:-1])
        return result

    # ---------- TKG-backed sections ---------------------------------
    async def _relationships(
        self, user_id: str, current_round: int | None
    ) -> list[dict]:
        """Return relationship-shaped dicts ({related_id, content, round_num}).

        Tries the TKG first with a 300 ms budget. Falls back to legacy
        MemoryStore on error/timeout.
        """
        if self.tkg is not None:
            try:
                rows = await asyncio.wait_for(
                    self.tkg.neighbors(
                        user_id,
                        at_round=current_round,
                        relation="ALLIED_WITH",
                        limit=5,
                    ),
                    timeout=_TKG_TIMEOUT_S,
                )
                if rows:
                    out: list[dict] = []
                    for r in rows:
                        label = r.get("neighbor_label") or ""
                        weight = r.get("weight", 1.0)
                        out.append({
                            "related_id": r.get("dst_id"),
                            "content": f"ally (weight {weight:.0f})" + (f" — {label}" if label else ""),
                            "round_num": r.get("valid_from_round"),
                        })
                    return out
            except (asyncio.TimeoutError, Exception) as e:
                logger.debug("[MemoryFormatter] TKG relationships fallback: %s", e)

        # Legacy fallback
        return await self.store.get_relationships(user_id, limit=7)

    async def _episodes(
        self, user_id: str, query_hint: str | None
    ) -> list[dict]:
        """Return episodic-shaped dicts ({round_num, content}).

        If we have a query hint (e.g. the current prompt context) and a
        TKGClient, run semantic search. Otherwise use legacy recency.
        """
        if self.tkg is not None and query_hint:
            try:
                hits = await asyncio.wait_for(
                    self.tkg.semantic_search(query=query_hint, limit=4),
                    timeout=_TKG_TIMEOUT_S,
                )
                if hits:
                    return [
                        {
                            "round_num": h.get("round_num"),
                            "content": (h.get("content") or "")[:200],
                        }
                        for h in hits
                    ]
            except (asyncio.TimeoutError, Exception) as e:
                logger.debug("[MemoryFormatter] TKG episodes fallback: %s", e)

        return await self.store.get_recent(user_id, memory_type="episodic", limit=5)
