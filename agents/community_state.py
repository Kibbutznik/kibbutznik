"""
Community state observer — agents use this to "browse" and understand
what's happening in their community before making decisions.
"""
from dataclasses import dataclass, field
from typing import Any

from agents.api_client import KBZClient


@dataclass
class CommunitySnapshot:
    """Everything an agent needs to know about a community's current state."""
    community: dict = field(default_factory=dict)
    variables: dict = field(default_factory=dict)
    members: list[dict] = field(default_factory=list)
    statements: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    pulses: list[dict] = field(default_factory=list)
    proposals_out_there: list[dict] = field(default_factory=list)
    proposals_on_the_air: list[dict] = field(default_factory=list)
    proposals_draft: list[dict] = field(default_factory=list)
    recent_accepted: list[dict] = field(default_factory=list)
    recent_rejected: list[dict] = field(default_factory=list)
    proposal_comments: dict[str, list[dict]] = field(default_factory=dict)
    action_names: dict[str, str] = field(default_factory=dict)    # action_id -> community name
    action_members: dict[str, list[dict]] = field(default_factory=dict)  # action_id -> members

    @property
    def member_count(self) -> int:
        return self.community.get("member_count", 0)

    @property
    def community_name(self) -> str:
        return self.community.get("name", "Unknown")

    @property
    def next_pulse(self) -> dict | None:
        for p in self.pulses:
            if p["status"] == 0:
                return p
        return None

    @property
    def active_pulse(self) -> dict | None:
        for p in self.pulses:
            if p["status"] == 1:
                return p
        return None

    @property
    def pulse_support_progress(self) -> str:
        np = self.next_pulse
        if not np:
            return "no next pulse"
        return f"{np['support_count']}/{np['threshold']}"

    def member_names(self, users_cache: dict[str, str]) -> list[str]:
        return [users_cache.get(m["user_id"], m["user_id"][:8]) for m in self.members]

    def _threshold_for_type(self, proposal_type: str) -> int:
        """Calculate the support count needed for a proposal type to pass."""
        import math
        mc = self.member_count or 1
        # Map proposal_type → variable name for its threshold
        type_var_map = {
            "AddStatement": "AddStatement", "RemoveStatement": "RemoveStatement",
            "ReplaceStatement": "ReplaceStatement", "ChangeVariable": "ChangeVariable",
            "AddAction": "AddAction", "EndAction": "EndAction",
            "Membership": "Membership", "ThrowOut": "ThrowOut",
            "JoinAction": "JoinAction",
        }
        var_name = type_var_map.get(proposal_type, proposal_type)
        try:
            pct = float(self.variables.get(var_name, "50"))
        except (ValueError, TypeError):
            pct = 50.0
        return math.ceil(mc * pct / 100)

    def _proposal_support_threshold(self) -> int:
        """Support count needed to promote OutThere → OnTheAir."""
        import math
        mc = self.member_count or 1
        try:
            pct = float(self.variables.get("ProposalSupport", "15"))
        except (ValueError, TypeError):
            pct = 15.0
        return math.ceil(mc * pct / 100)

    def summarize(self, my_user_id: str = "", users_cache: dict[str, str] | None = None) -> str:
        """Generate a human-readable summary of the community state for the LLM."""
        users_cache = users_cache or {}
        lines = []
        lines.append(f"## Community: {self.community_name}")
        lines.append(f"Members: {self.member_count}")
        lines.append(f"Pulse progress: {self.pulse_support_progress}")

        max_age = self.variables.get("MaxAge", "2")

        if self.statements:
            lines.append(f"\n### Community Rules / Disclaimer ({len(self.statements)} statements):")
            lines.append("  (Every member implicitly agrees to follow these rules by joining.")
            lines.append("   Violating them is grounds for a ThrowOut proposal!)")
            for i, s in enumerate(self.statements, 1):
                lines.append(f"  {i}. {s['statement_text']}")
        else:
            lines.append("\n### Community Rules: None yet — consider proposing AddStatement to establish community values!")

        if self.actions:
            lines.append(f"\n### Active Actions ({len(self.actions)}):")
            for a in self.actions:
                aid = a['action_id']
                name = self.action_names.get(aid, "Unnamed")
                status_label = "Active" if a.get("status") == 1 else "Ended"
                members = self.action_members.get(aid, [])
                lines.append(
                    f"  - [{name}] id={aid} ({status_label}, {len(members)} members)"
                )

        promote_threshold = self._proposal_support_threshold()

        if self.proposals_out_there:
            lines.append(f"\n### Proposals Gathering Support — need {promote_threshold} to reach OnTheAir ({len(self.proposals_out_there)}):")
            for p in self.proposals_out_there:
                creator = users_cache.get(p["user_id"], p["user_id"][:8])
                accept_threshold = self._threshold_for_type(p["proposal_type"])
                support = p["support_count"]
                age = p.get("age", 0)
                will_promote = "WILL promote to OnTheAir" if support >= promote_threshold else f"needs {promote_threshold - support} more to promote"
                age_warn = f" !! age {age}/{max_age} — will be CANCELED if pulse fires!" if age >= int(max_age) else f" age {age}/{max_age}"
                lines.append(
                    f"  - [{p['proposal_type']}] \"{p['proposal_text']}\" "
                    f"by {creator} | support: {support}/{accept_threshold} | {will_promote} |{age_warn}"
                    f" | id: {p['id']}"
                )
                comments = self.proposal_comments.get(p["id"], [])
                for c in comments[:3]:
                    commenter = users_cache.get(c["user_id"], c["user_id"][:8])
                    lines.append(f"    > {commenter}: \"{c['comment_text'][:80]}\"")

        if self.proposals_on_the_air:
            lines.append(f"\n### Proposals Being Decided (if pulse fires NOW):")
            will_pass = []
            will_fail = []
            for p in self.proposals_on_the_air:
                creator = users_cache.get(p["user_id"], p["user_id"][:8])
                threshold = self._threshold_for_type(p["proposal_type"])
                support = p["support_count"]
                verdict = "WILL PASS" if support >= threshold else "will FAIL"
                if support >= threshold:
                    will_pass.append(p["proposal_type"])
                else:
                    will_fail.append(p["proposal_type"])
                lines.append(
                    f"  - [{p['proposal_type']}] \"{p['proposal_text']}\" "
                    f"by {creator} | support: {support}/{threshold} needed → **{verdict}**"
                    f" | id: {p['id']}"
                )
            if will_pass or will_fail:
                lines.append(f"  >>> PULSE IMPACT: {len(will_pass)} would PASS, {len(will_fail)} would FAIL")

        if self.recent_accepted:
            lines.append(f"\n### Recently Accepted ({len(self.recent_accepted)}):")
            for p in self.recent_accepted[:5]:
                lines.append(f"  - [{p['proposal_type']}] \"{p['proposal_text']}\"")

        if self.recent_rejected:
            lines.append(f"\n### Recently Rejected ({len(self.recent_rejected)}):")
            for p in self.recent_rejected[:3]:
                lines.append(f"  - [{p['proposal_type']}] \"{p['proposal_text']}\"")

        # Actions the agent can still join (not already a member)
        if my_user_id and self.actions:
            joinable = []
            for a in self.actions:
                if a.get("status") != 1:
                    continue  # skip ended actions
                members = self.action_members.get(a["action_id"], [])
                already_member = any(m["user_id"] == my_user_id for m in members)
                if not already_member:
                    name = self.action_names.get(a["action_id"], "Unnamed")
                    joinable.append((name, a["action_id"]))
            if joinable:
                lines.append(f"\n### Actions You Can Join ({len(joinable)}) — propose JoinAction to participate!")
                for name, aid in joinable:
                    lines.append(f"  - [{name}] → JoinAction with val_uuid={aid}")

        # Members list (with user_ids for ThrowOut targeting)
        if self.members:
            lines.append(f"\n### Members ({len(self.members)}):")
            for m in self.members:
                name = users_cache.get(m["user_id"], m["user_id"][:8])
                seniority = m.get("seniority", 0)
                me_marker = " ← you" if m["user_id"] == my_user_id else ""
                lines.append(f"  - {name} (user_id={m['user_id']}, seniority={seniority}){me_marker}")

        # Key variables
        important_vars = ["PulseSupport", "ProposalSupport", "Membership", "ThrowOut", "MaxAge"]
        var_str = ", ".join(f"{k}={self.variables.get(k, '?')}" for k in important_vars)
        lines.append(f"\n### Key Variables: {var_str}")

        # My status
        if my_user_id:
            my_member = next((m for m in self.members if m["user_id"] == my_user_id), None)
            if my_member:
                lines.append(f"\n### My Status: seniority={my_member['seniority']}")
            else:
                lines.append("\n### My Status: NOT A MEMBER")

        return "\n".join(lines)


async def observe_community(client: KBZClient, community_id: str) -> CommunitySnapshot:
    """Fetch the full state of a community — the agent's 'eyes'."""
    snapshot = CommunitySnapshot()

    snapshot.community = await client.get_community(community_id)
    snapshot.variables = await client.get_variables(community_id)
    snapshot.members = await client.get_members(community_id)
    snapshot.statements = await client.get_statements(community_id)
    snapshot.actions = await client.get_actions(community_id)
    snapshot.pulses = await client.get_pulses(community_id)

    # Get proposals by status
    all_proposals = await client.get_proposals(community_id)
    for p in all_proposals:
        if p["proposal_status"] == "OutThere":
            snapshot.proposals_out_there.append(p)
        elif p["proposal_status"] == "OnTheAir":
            snapshot.proposals_on_the_air.append(p)
        elif p["proposal_status"] == "Draft":
            snapshot.proposals_draft.append(p)
        elif p["proposal_status"] == "Accepted":
            snapshot.recent_accepted.append(p)
        elif p["proposal_status"] == "Rejected":
            snapshot.recent_rejected.append(p)

    # Fetch comments on active proposals (for social awareness)
    for p in snapshot.proposals_out_there + snapshot.proposals_on_the_air:
        try:
            comments = await client.get_comments("proposal", p["id"])
            snapshot.proposal_comments[p["id"]] = comments
        except Exception:
            pass

    # Fetch action community names and member lists so agents can reason about joining
    for a in snapshot.actions:
        aid = a["action_id"]
        try:
            comm = await client.get_community(aid)
            snapshot.action_names[aid] = comm.get("name", "Unnamed")
        except Exception:
            pass
        try:
            members = await client.get_members(aid)
            snapshot.action_members[aid] = members
        except Exception:
            pass

    return snapshot
