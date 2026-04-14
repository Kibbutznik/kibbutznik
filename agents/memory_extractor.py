"""Deterministic memory extraction from agent action logs.

After each agent turn, this module examines the executed actions and the
community snapshot to produce structured memory entries — no LLM calls.
"""

import logging
from typing import TYPE_CHECKING

from agents.memory import MemoryStore

if TYPE_CHECKING:
    from agents.agent import ActionLog
    from agents.community_state import CommunitySnapshot

logger = logging.getLogger(__name__)


class MemoryExtractor:
    """Extracts memories from action logs and community state changes."""

    def __init__(self, memory_store: MemoryStore, users_cache: dict[str, str] | None = None):
        self.store = memory_store
        self.users_cache = users_cache or {}
        # Track proposal IDs we've already recorded outcomes for (prevents duplicates
        # when recent_accepted/recent_rejected persist across multiple rounds)
        self._recorded_outcomes: set[str] = set()

    def _name(self, user_id: str | None) -> str:
        if not user_id:
            return "unknown"
        return self.users_cache.get(user_id, user_id[:8])

    async def extract_from_actions(
        self,
        user_id: str,
        action_logs: list["ActionLog"],
        snapshot: "CommunitySnapshot",
        round_num: int,
    ) -> None:
        """Examine action logs and snapshot, write new memories to the store.

        Called once per agent turn, after all actions have been executed.
        """
        for log in action_logs:
            if not log.success:
                continue

            try:
                await self._process_action(user_id, log, snapshot, round_num)
            except Exception as e:
                logger.debug(f"[MemoryExtractor] Error processing {log.action_type}: {e}")

        # Observe community-level events from the snapshot
        await self._observe_snapshot(user_id, snapshot, round_num)

    async def _process_action(
        self,
        user_id: str,
        log: "ActionLog",
        snapshot: "CommunitySnapshot",
        round_num: int,
    ) -> None:
        """Extract memories from a single successful action."""

        if log.action_type == "create_proposal":
            # Determine proposal type from details
            ptype = self._extract_proposal_type(log.details)
            short_text = self._extract_quote(log.details, 80)

            if ptype == "EditArtifact":
                # Check if this targets a Plan artifact
                is_plan_edit = self._is_plan_edit(log.details, snapshot)
                if is_plan_edit:
                    await self.store.add(
                        user_id=user_id,
                        memory_type="goal",
                        content=f"Updated Plan: {short_text}",
                        importance=0.9,
                        category="plan",
                        round_num=round_num,
                        related_id=log.ref_id,
                    )
                else:
                    # Goal: working on a regular artifact
                    await self.store.add(
                        user_id=user_id,
                        memory_type="goal",
                        content=f"Working on artifact content: {short_text}",
                        importance=0.7,
                        category="artifact_work",
                        round_num=round_num,
                        related_id=log.ref_id,
                    )
            elif ptype == "AddAction":
                await self.store.add(
                    user_id=user_id,
                    memory_type="episodic",
                    content=f"I proposed creating a new action: {short_text}",
                    importance=0.5,
                    category="governance",
                    round_num=round_num,
                    related_id=log.ref_id,
                )
            elif ptype == "AddStatement":
                await self.store.add(
                    user_id=user_id,
                    memory_type="episodic",
                    content=f"I proposed a new community rule: {short_text}",
                    importance=0.5,
                    category="governance",
                    round_num=round_num,
                    related_id=log.ref_id,
                )
            elif ptype in ("DelegateArtifact", "CommitArtifact"):
                await self.store.add(
                    user_id=user_id,
                    memory_type="episodic",
                    content=f"I proposed {ptype}: {short_text}",
                    importance=0.6,
                    category="artifact_work",
                    round_num=round_num,
                    related_id=log.ref_id,
                )
            elif ptype == "JoinAction":
                await self.store.add(
                    user_id=user_id,
                    memory_type="goal",
                    content=f"Joining action to contribute: {short_text}",
                    importance=0.5,
                    category="participation",
                    round_num=round_num,
                    related_id=log.ref_id,
                )
            else:
                # Generic proposal
                await self.store.add(
                    user_id=user_id,
                    memory_type="episodic",
                    content=f"I proposed [{ptype}]: {short_text}",
                    importance=0.4,
                    category="governance",
                    round_num=round_num,
                    related_id=log.ref_id,
                )

        elif log.action_type == "support_proposal":
            # Track who we support — build relationship memory
            proposal_author = self._find_proposal_author(log.ref_id, snapshot)
            if proposal_author and proposal_author != user_id:
                author_name = self._name(proposal_author)
                ptype = self._find_proposal_type(log.ref_id, snapshot)
                existing = await self.store.get_relationship_with(user_id, proposal_author)
                if existing:
                    # Update: increment support count in content
                    old_content = existing["content"]
                    old_importance = existing.get("importance", 0.3)
                    new_importance = min(0.9, old_importance + 0.05)
                    await self.store.update(
                        existing["id"],
                        content=f"ally — I frequently support their proposals ({ptype})",
                        importance=new_importance,
                    )
                else:
                    await self.store.add(
                        user_id=user_id,
                        memory_type="relationship",
                        content=f"I supported their {ptype} proposal",
                        importance=0.3,
                        category="social",
                        round_num=round_num,
                        related_id=proposal_author,
                    )

        elif log.action_type == "send_chat":
            # Low-importance episodic — only record if it seems meaningful
            msg = self._extract_quote(log.details, 60)
            if len(msg) > 20:  # skip trivially short chat
                await self.store.add(
                    user_id=user_id,
                    memory_type="episodic",
                    content=f"I said in chat: \"{msg}\"",
                    importance=0.2,
                    category="social",
                    round_num=round_num,
                    expires_at=round_num + 15,  # chat memories expire quickly
                )

        elif log.action_type == "comment":
            await self.store.add(
                user_id=user_id,
                memory_type="episodic",
                content=f"I commented on proposal {(log.ref_id or '')[:8]}: {self._extract_quote(log.details, 60)}",
                importance=0.3,
                category="social",
                round_num=round_num,
                related_id=log.ref_id,
                expires_at=round_num + 20,
            )

    async def _observe_snapshot(
        self,
        user_id: str,
        snapshot: "CommunitySnapshot",
        round_num: int,
    ) -> None:
        """Extract memories from community state changes visible in the snapshot.

        This catches events the agent didn't cause but should remember:
        - Recently accepted/rejected proposals (especially our own)
        - New members joining
        - Pulse firing
        """
        # Check recently accepted proposals — did any of ours pass?
        for p in snapshot.recent_accepted:
            pid = p.get("id", "")
            if p.get("user_id") == user_id and pid not in self._recorded_outcomes:
                self._recorded_outcomes.add(pid)
                ptype = p.get("proposal_type", "?")
                ptext = (p.get("proposal_text") or "")[:60]
                await self.store.add(
                    user_id=user_id,
                    memory_type="episodic",
                    content=f"My {ptype} proposal was ACCEPTED: \"{ptext}\"",
                    importance=0.8,
                    category="proposal_outcome",
                    round_num=round_num,
                    related_id=pid,
                )
                # If it was an EditArtifact, mark the goal as progressing
                if ptype == "EditArtifact":
                    is_plan = self._is_plan_edit_from_proposal(p)
                    await self.store.add(
                        user_id=user_id,
                        memory_type="goal",
                        content=f"Plan accepted: \"{ptext}\"" if is_plan else f"Artifact content accepted: \"{ptext}\"",
                        importance=0.9 if is_plan else 0.8,
                        category="plan" if is_plan else "artifact_work",
                        round_num=round_num,
                        related_id=pid,
                    )

        # Check recently rejected proposals — did any of ours fail?
        for p in snapshot.recent_rejected:
            pid = p.get("id", "")
            if p.get("user_id") == user_id and pid not in self._recorded_outcomes:
                self._recorded_outcomes.add(pid)
                ptype = p.get("proposal_type", "?")
                ptext = (p.get("proposal_text") or "")[:60]
                await self.store.add(
                    user_id=user_id,
                    memory_type="episodic",
                    content=f"My {ptype} proposal was REJECTED: \"{ptext}\"",
                    importance=0.7,
                    category="proposal_outcome",
                    round_num=round_num,
                    related_id=pid,
                )

    # ---- helpers ----

    @staticmethod
    def _extract_proposal_type(details: str) -> str:
        """Extract proposal type from action log details like 'Created [EditArtifact] ...'"""
        if "[" in details and "]" in details:
            start = details.index("[") + 1
            end = details.index("]")
            return details[start:end]
        return "Unknown"

    @staticmethod
    def _extract_quote(details: str, max_len: int = 60) -> str:
        """Extract the quoted text from action log details."""
        if '"' in details:
            parts = details.split('"')
            if len(parts) >= 2:
                text = parts[1]
                if len(text) > max_len:
                    return text[:max_len] + "..."
                return text
        # Fallback: return the details truncated
        text = details[:max_len]
        if len(details) > max_len:
            text += "..."
        return text

    @staticmethod
    def _find_proposal_author(proposal_id: str | None, snapshot: "CommunitySnapshot") -> str | None:
        """Find the author (user_id) of a proposal from the snapshot."""
        if not proposal_id:
            return None
        all_proposals = (
            snapshot.proposals_out_there
            + snapshot.proposals_on_the_air
            + snapshot.proposals_draft
            + snapshot.recent_accepted
            + snapshot.recent_rejected
        )
        for p in all_proposals:
            if p.get("id") == proposal_id:
                return p.get("user_id")
        return None

    @staticmethod
    def _is_plan_edit(details: str, snapshot: "CommunitySnapshot") -> bool:
        """Detect if an EditArtifact action targets a Plan artifact.

        Heuristics: look for 'Plan' in the artifact title referenced in the
        action details, or check the snapshot's containers for matching artifact.
        """
        # Check details text for plan references
        lower = details.lower()
        if "plan" in lower and ("📋" in details or "is_plan" in lower):
            return True
        # Check if the val_uuid (artifact being edited) is a plan artifact
        # Details format: 'Created [EditArtifact] val_uuid=XXXXXXXX ...'
        if "val_uuid=" in details:
            uuid_part = details.split("val_uuid=")[1].split()[0].strip('"').strip("'")
            # Search snapshot containers for this artifact
            for c in getattr(snapshot, "containers", []):
                for a in c.get("artifacts", []):
                    aid = a.get("id", "")
                    if aid.startswith(uuid_part) or uuid_part.startswith(aid[:8]):
                        if a.get("is_plan") or a.get("title", "").lower() == "plan":
                            return True
        return False

    @staticmethod
    def _is_plan_edit_from_proposal(proposal: dict) -> bool:
        """Detect if an accepted EditArtifact proposal targeted a Plan artifact."""
        ptext = (proposal.get("proposal_text") or "").lower()
        return "plan" in ptext and ("📋" in (proposal.get("proposal_text") or "") or "is_plan" in ptext)

    @staticmethod
    def _find_proposal_type(proposal_id: str | None, snapshot: "CommunitySnapshot") -> str:
        """Find the proposal_type from the snapshot."""
        if not proposal_id:
            return "?"
        all_proposals = (
            snapshot.proposals_out_there
            + snapshot.proposals_on_the_air
            + snapshot.proposals_draft
            + snapshot.recent_accepted
            + snapshot.recent_rejected
        )
        for p in all_proposals:
            if p.get("id") == proposal_id:
                return p.get("proposal_type", "?")
        return "?"
