"""Builds a human-readable memory context string for injection into LLM prompts.

Target budget: ~600 tokens total.
"""

import logging

from agents.memory import MemoryStore

logger = logging.getLogger(__name__)


class MemoryFormatter:
    """Reads an agent's memories and formats them into a compact prompt section."""

    def __init__(self, memory_store: MemoryStore, users_cache: dict[str, str] | None = None):
        self.store = memory_store
        self.users_cache = users_cache or {}

    def _name(self, user_id: str | None) -> str:
        """Resolve a user_id to a display name, falling back to truncated ID."""
        if not user_id:
            return "unknown"
        return self.users_cache.get(user_id, user_id[:8])

    async def build_memory_context(self, user_id: str, budget_tokens: int = 600) -> str:
        """Assemble the === YOUR MEMORY === block for the LLM prompt.

        Fetches reflections, goals, relationships, and recent episodes in
        parallel-ish fashion, then formats them into a compact string that
        fits within *budget_tokens* (approximate — we use character count
        as a rough proxy, ~4 chars per token).
        """
        char_budget = budget_tokens * 4  # rough token→char ratio

        # Fetch all memory types
        reflection = await self.store.get_latest_reflection(user_id)
        goals = await self.store.get_goals(user_id, active_only=True)
        relationships = await self.store.get_relationships(user_id, limit=7)
        episodes = await self.store.get_recent(user_id, memory_type="episodic", limit=5)

        # If no memories at all, return empty (agent hasn't accumulated anything yet)
        if not reflection and not goals and not relationships and not episodes:
            return ""

        parts: list[str] = ["=== YOUR MEMORY ==="]
        chars_used = 20

        # 1. Reflection (~180 tokens)
        if reflection:
            r_round = reflection.get("round_num", "?")
            r_content = reflection["content"]
            # Truncate reflection to ~700 chars
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
                # Content already includes sentiment info from extractor
                if len(content) > 100:
                    content = content[:100].rsplit(" ", 1)[0] + "..."
                rel_lines.append(f"- {target_name}: {content}")
            section = "\nKEY RELATIONSHIPS:\n" + "\n".join(rel_lines)
            parts.append(section)
            chars_used += len(section)

        # 4. Recent Episodes (~100 tokens) — only if budget allows
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

        # Hard cap: if we exceeded budget, trim the episodes section
        if len(result) > char_budget and len(parts) > 3:
            # Drop episodes
            result = "\n".join(parts[:-1])

        return result
