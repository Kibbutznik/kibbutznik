"""
Community state observer — agents use this to "browse" and understand
what's happening in their community before making decisions.
"""
import logging
from dataclasses import dataclass, field
from typing import Any

from agents.api_client import KBZClient

logger = logging.getLogger(__name__)


def _proposal_display_text(p: dict) -> str:
    """Return a human-readable description for a proposal.

    For artifact proposals (CreateArtifact, EditArtifact, etc.) the meaningful
    text is often in val_text (the title) rather than proposal_text (which may
    be empty for title-only CreateArtifact).  Fall back gracefully.
    """
    ptype = p.get("proposal_type", "")
    text = (p.get("proposal_text") or "").strip()
    val = (p.get("val_text") or "").strip()
    if ptype == "CreateArtifact":
        return val or text or "(untitled artifact)"
    if ptype in ("EditArtifact", "DelegateArtifact", "CommitArtifact", "RemoveArtifact"):
        return val or text or f"({ptype})"
    return text or val or "(no description)"


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
    action_activity: dict[str, dict] = field(default_factory=dict)  # action_id -> {pulses, active_proposals, accepted, rejected}
    chat_messages: list[dict] = field(default_factory=list)  # recent community chat (newest first)
    containers: list[dict] = field(default_factory=list)  # ArtifactContainer dicts owned by THIS community
    container_artifacts: dict[str, list[dict]] = field(default_factory=dict)  # container_id -> list of active artifacts
    delegations_out: dict[str, dict] = field(default_factory=dict)  # artifact_id -> {action_community_id, action_name, child_container_id, child_status, child_artifact_count} for artifacts of THIS community delegated to a child Action

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
            lines.append("   Violating them is grounds for a ThrowOut proposal!")
            lines.append("   Outdated or harmful rules can be retired via RemoveStatement,")
            lines.append("   or rewritten in place via ReplaceStatement — both target the rule by id.)")
            for i, s in enumerate(self.statements, 1):
                lines.append(f"  {i}. [id={s['id']}] {s['statement_text']}")
        else:
            lines.append("\n### Community Rules: None yet — consider proposing AddStatement to establish community values!")

        if self.actions:
            active_actions = [a for a in self.actions if a.get("status") == 1]
            ended_actions = [a for a in self.actions if a.get("status") != 1]
            if active_actions:
                lines.append(f"\n### Active Actions ({len(active_actions)}):")
                for a in active_actions:
                    aid = a['action_id']
                    name = self.action_names.get(aid, "Unnamed")
                    members = self.action_members.get(aid, [])
                    activity = self.action_activity.get(aid, {})
                    pulses = activity.get("pulses", 0)
                    active_props = activity.get("active_proposals", 0)
                    accepted = activity.get("accepted", 0)
                    rejected = activity.get("rejected", 0)
                    # Flag as idle if there's no current work AND either some
                    # history exists (so it's not just freshly created) or the
                    # action is a zombie shell (no pulses ever, no proposals,
                    # only the founder still in it).
                    has_history = pulses >= 1 or accepted >= 1 or rejected >= 1
                    is_zombie = pulses == 0 and accepted == 0 and rejected == 0 and len(members) <= 1
                    idle = active_props == 0 and (has_history or is_zombie)
                    status_tag = (
                        " 💤 IDLE — consider proposing EndAction to close it"
                        if idle else ""
                    )
                    lines.append(
                        f"  - [{name}] id={aid} ({len(members)} members, "
                        f"{pulses} pulses fired, {active_props} active proposals, "
                        f"{accepted} accepted / {rejected} rejected){status_tag}"
                    )
                    if idle:
                        lines.append(
                            f"    → To close: create EndAction proposal with val_uuid={aid}"
                        )
            if ended_actions:
                lines.append(f"\n### Ended Actions ({len(ended_actions)}): (already closed, no action needed)")
                for a in ended_actions:
                    aid = a['action_id']
                    name = self.action_names.get(aid, "Unnamed")
                    lines.append(f"  - [{name}] id={aid}")

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
                desc = _proposal_display_text(p)
                lines.append(
                    f"  - [{p['proposal_type']}] \"{desc}\" "
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
                desc = _proposal_display_text(p)
                lines.append(
                    f"  - [{p['proposal_type']}] \"{desc}\" "
                    f"by {creator} | support: {support}/{threshold} needed → **{verdict}**"
                    f" | id: {p['id']}"
                )
            if will_pass or will_fail:
                lines.append(f"  >>> PULSE IMPACT: {len(will_pass)} would PASS, {len(will_fail)} would FAIL")

        if self.recent_accepted:
            lines.append(f"\n### Recently Accepted ({len(self.recent_accepted)}):")
            for p in self.recent_accepted[:5]:
                lines.append(f"  - [{p['proposal_type']}] \"{_proposal_display_text(p)}\"")

        if self.recent_rejected:
            lines.append(f"\n### Recently Rejected ({len(self.recent_rejected)}):")
            for p in self.recent_rejected[:3]:
                lines.append(f"  - [{p['proposal_type']}] \"{_proposal_display_text(p)}\"")

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

        # Community chat — recent messages (or new since last read)
        if self.chat_messages:
            lines.append(f"\n### Recent Chat ({len(self.chat_messages)} new messages)")
            # Show in chronological order (oldest first); the list arrives newest-first
            for m in reversed(self.chat_messages):
                author = users_cache.get(m.get("user_id", ""), m.get("user_id", "")[:8])
                text = (m.get("comment_text") or "")[:200]
                lines.append(f"  {author}: {text}")
            lines.append("  (Use send_chat to participate in the community discussion.)")

        # Artifact containers (the productive layer — what this community is BUILDING)
        if self.containers:
            lines.append(f"\n### Artifact Containers ({len(self.containers)}) — what this community is producing:")
            lines.append("  (CreateArtifact = plan a SLOT (title only, empty body);")
            lines.append("   EditArtifact = FILL the body of an existing artifact;")
            lines.append("   DelegateArtifact = hand an artifact to a child Action to work on;")
            lines.append("   CommitArtifact = seal the container and ship its contents upward.)")
            status_label = {1: "OPEN", 2: "PENDING_PARENT (frozen)", 3: "COMMITTED"}
            for c in self.containers:
                cid = c["id"]
                st = status_label.get(c.get("status"), str(c.get("status")))
                origin = " (root)" if not c.get("delegated_from_artifact_id") else f" (delegated from artifact {c['delegated_from_artifact_id'][:8]} in parent)"
                lines.append(f"\n  Container \"{c.get('title','')}\" [id={cid}] — {st}{origin}")
                mission = (c.get("mission") or "").strip()
                if mission:
                    lines.append(f"    >>> MISSION: {mission}")
                    lines.append(
                        "    >>> CreateArtifact here = title-only slot for a section of the deliverable. "
                        "EditArtifact fills the body. Slogans/principles belong in AddStatement."
                    )
                else:
                    lines.append(
                        "    >>> MISSION: (none set — ask the community what concrete deliverable "
                        "this container is for, then fill it with real sections, not slogans.)"
                    )
                arts = self.container_artifacts.get(cid, [])
                if not arts:
                    lines.append("    (empty — no artifacts yet, propose CreateArtifact to add one)")
                for a in arts:
                    aid = a["id"]
                    author = users_cache.get(a["author_user_id"], a["author_user_id"][:8])
                    title = a.get("title") or "(untitled)"
                    preview = (a.get("content") or "").replace("\n", " ")[:120]
                    deleg = self.delegations_out.get(aid)
                    if deleg:
                        lines.append(
                            f"    [{aid}] \"{title}\" by {author} — DELEGATED to action "
                            f"\"{deleg['action_name']}\" "
                            f"({deleg['child_artifact_count']} child artifacts, container status: {deleg['child_status']})"
                        )
                    elif not (a.get("content") or "").strip():
                        # In root containers, nudge toward delegation; in child containers, nudge toward EditArtifact
                        if not c.get("delegated_from_artifact_id"):
                            lines.append(f"    [{aid}] \"{title}\" by {author} — EMPTY (needs DelegateArtifact to an Action, or AddAction first)")
                        else:
                            lines.append(f"    [{aid}] \"{title}\" by {author} — EMPTY (needs EditArtifact to fill body)")
                    else:
                        lines.append(f"    [{aid}] \"{title}\" by {author} — {preview}")
                if c.get("status") == 1 and arts:
                    lines.append(
                        f"    → To commit: CommitArtifact with val_uuid={cid} "
                        f"and val_text=JSON list of artifact ids in chosen order"
                    )
                if c.get("status") == 2:
                    lines.append("    → Frozen pending parent verdict — no mutations allowed.")

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


async def observe_community(
    client: KBZClient,
    community_id: str,
    chat_after: str | None = None,
) -> CommunitySnapshot:
    """Fetch the full state of a community — the agent's 'eyes'.

    `chat_after` is an ISO timestamp; only chat messages newer than this
    are returned. Pass None to get the last 15 messages (initial read).
    """
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

    # Fetch action community names, member lists, and activity stats so agents can
    # reason about joining and about whether an action is finished/idle and ready
    # to be ended via an EndAction proposal in the parent community.
    #
    # PERF: cap the number of actions we fetch details for. With hundreds of
    # actions, doing 3-4 HTTP calls per action per agent per round grinds the
    # system to a halt.  We fetch the first MAX_ACTION_DETAIL actions and
    # only store names (1 HTTP call) for the rest.
    MAX_ACTION_DETAIL = 10
    active_actions = [a for a in snapshot.actions if a.get("status") == 1]
    detail_actions = active_actions[:MAX_ACTION_DETAIL]
    name_only_actions = active_actions[MAX_ACTION_DETAIL:]

    for a in detail_actions:
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
        try:
            child_proposals = await client.get_proposals(aid)
            child_pulses = await client.get_pulses(aid)
            active = sum(
                1 for p in child_proposals
                if p.get("proposal_status") in ("OutThere", "OnTheAir")
            )
            accepted = sum(
                1 for p in child_proposals
                if p.get("proposal_status") == "Accepted"
            )
            rejected = sum(
                1 for p in child_proposals
                if p.get("proposal_status") == "Rejected"
            )
            # Pulses that have actually fired (Active=1 or Done=2). Next-pulses (status=0)
            # are pending and don't represent elapsed time.
            pulses_fired = sum(1 for p in child_pulses if p.get("status", 0) >= 1)
            snapshot.action_activity[aid] = {
                "pulses": pulses_fired,
                "active_proposals": active,
                "accepted": accepted,
                "rejected": rejected,
            }
        except Exception:
            pass

    # For remaining actions, only fetch the name (1 HTTP call each, no proposals/pulses).
    for a in name_only_actions:
        aid = a["action_id"]
        try:
            comm = await client.get_community(aid)
            snapshot.action_names[aid] = comm.get("name", "Unnamed")
        except Exception:
            pass

    # Artifact containers — load the work tree for this community.
    # The endpoint returns each container with its artifacts; each artifact
    # carries any child containers it has been delegated to (recursive).
    try:
        tree = await client.get_work_tree(community_id)
        logger.debug("observe_community: work_tree returned %d containers", len(tree))
        for c in tree:
            container_dict = {
                "id": c["id"],
                "community_id": c.get("community_id"),
                "title": c.get("title"),
                "mission": c.get("mission"),
                "status": c.get("status"),
                "delegated_from_artifact_id": c.get("delegated_from_artifact_id"),
                "committed_content": c.get("committed_content"),
            }
            snapshot.containers.append(container_dict)
            artifacts_flat: list[dict] = []
            for a in c.get("artifacts", []):
                artifacts_flat.append({
                    "id": a["id"],
                    "title": a.get("title"),
                    "content": a.get("content", ""),
                    "author_user_id": a.get("author_user_id"),
                    "proposal_id": a.get("proposal_id"),
                    "status": a.get("status"),
                })
                # Record any delegations OUT of this artifact.
                children = a.get("delegated_to") or []
                if children:
                    child = children[0]
                    child_action_id = child.get("community_id")
                    child_name = snapshot.action_names.get(child_action_id, "Unnamed")
                    snapshot.delegations_out[a["id"]] = {
                        "action_community_id": child_action_id,
                        "action_name": child_name,
                        "child_container_id": child.get("id"),
                        "child_status": child.get("status"),
                        "child_artifact_count": len(child.get("artifacts", [])),
                    }
            snapshot.container_artifacts[c["id"]] = artifacts_flat
    except Exception as e:
        logger.warning("observe_community: failed to fetch work_tree for %s: %s", community_id, e)

    # Community chat — fetch recent messages (diff since last read, or last 15).
    try:
        chat_url = f"/entities/community/{community_id}/comments?limit=15"
        if chat_after:
            chat_url += f"&after={chat_after}"
        resp = await client._client.get(chat_url)
        resp.raise_for_status()
        snapshot.chat_messages = resp.json()
    except Exception as e:
        logger.debug("observe_community: failed to fetch chat for %s: %s", community_id, e)

    return snapshot
