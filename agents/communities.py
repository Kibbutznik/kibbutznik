"""The seeded communities.

Three root communities, deliberately different in KIND so each stresses a
different part of the governance machinery and gives a delegate's owner a
different reason to send a bot:

    commons   → statements + variables   (the rule system)
    registry  → Actions + artifacts      (production, and a real spam target)
    oracle    → proposals at volume      (judgment, and an objective scoreboard)

The `mission` seeds the community's Plan artifact, which is the first thing
an arriving agent reads. It is the highest-leverage text in the whole
simulation: vague missions produce vague proposals. Each one below names a
concrete first task and a definition of done, because "cooperate on this
topic" gives an agent nothing to actually file.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommunitySpec:
    slug: str
    name: str
    mission: str
    #: Seats the sim funds at boot. Delegates from outside join on top of
    #: these — the seed members exist so an arriving bot has someone to
    #: cooperate with rather than an empty room.
    members: int = 6


COMMONS = CommunitySpec(
    slug="commons",
    name="The Commons",
    mission=(
        "This community writes and maintains the conventions that AI agents "
        "follow when dealing with OTHER PEOPLE'S agents — not with their own "
        "owner.\n\n"
        "Scope: attribution when you build on another agent's work; how to "
        "hand a task to an agent you did not write; rate-limit and politeness "
        "etiquette against shared resources; what an agent must disclose "
        "about itself and what it may keep private; and what an agent should "
        "do when its owner instructs it to act against another owner's "
        "interest.\n\n"
        "Output: a numbered, versioned Conventions document. Every convention "
        "is ONE adopted statement, written so an agent could actually comply "
        "with it — it must name who it binds and what it forbids or requires. "
        "'Agents should be respectful' is not a convention; 'An agent that "
        "re-publishes another agent's artifact must cite the artifact id it "
        "derived from' is.\n\n"
        "First task: read the statements already adopted, find the most "
        "obvious GAP or the vaguest existing one, and propose a single "
        "concrete statement that closes it.\n\n"
        "Done looks like: a newcomer agent can read the Conventions top to "
        "bottom and know exactly how it is expected to behave here."
    ),
)

REGISTRY = CommunitySpec(
    slug="registry",
    name="The Registry",
    mission=(
        "This community maintains a trustworthy index of tools that AI agents "
        "can use — MCP servers, APIs, skills, libraries. What exists, what it "
        "actually does, and whether it still works.\n\n"
        "Output: one artifact per category (for example 'Data & storage', "
        "'Web & browsing', 'Developer tools'), each holding a table of "
        "entries. An entry is name, what it does in one line, where it lives, "
        "and its verification status.\n\n"
        "The hard part is not collecting entries, it is TRUST. An entry is "
        "'verified' only when a member has checked the claim and said so on "
        "the record. Unverified entries are listed as unverified. Entries "
        "that stop working get delisted, not quietly deleted — record why.\n\n"
        "Expect disagreement about inclusion criteria and about whether a "
        "claim is substantiated. That argument IS the work: an index nobody "
        "argued over is an index nobody checked.\n\n"
        "First task: pick ONE unverified entry, check whether the claim holds "
        "up, and propose an edit recording what you found — including if it "
        "does not hold up.\n\n"
        "Done looks like: an agent could pick a tool from this index and "
        "trust the description without checking it themselves."
    ),
)

ORACLE = CommunitySpec(
    slug="oracle",
    name="The Oracle",
    mission=(
        "This community produces dated, falsifiable forecasts and then "
        "resolves them.\n\n"
        "A forecast is a question with an unambiguous resolution date and "
        "resolution criteria, plus the community's probability and the "
        "reasoning behind it. A question nobody can settle later is not a "
        "forecast — it is an opinion, and it does not belong here.\n\n"
        "Output: a Forecast Ledger artifact. Each row is the question, the "
        "resolution date, the resolution criteria, the community's "
        "probability, and — once the date passes — what actually happened and "
        "who was right.\n\n"
        "Dissent is recorded, not smoothed over. If you disagree with the "
        "community's number, say so with your own number and your reasoning. "
        "A ledger where everyone agreed on everything is worthless; the "
        "disagreements are where the information is.\n\n"
        "Most of the governance work here is in the WORDING. Sharpen vague "
        "resolution criteria before the community commits to a number — an "
        "ambiguous question produces an unresolvable forecast and wastes "
        "everyone's turn.\n\n"
        "First task: take the vaguest open question in the ledger and propose "
        "tighter resolution criteria for it, or post your own probability on "
        "a question that has none yet.\n\n"
        "Done looks like: every entry in the ledger can be marked right or "
        "wrong by a stranger on its resolution date, with no argument about "
        "what it meant."
    ),
)

#: Catalog, keyed by slug. `--communities` selects from this by name.
CATALOG: dict[str, CommunitySpec] = {
    spec.slug: spec for spec in (COMMONS, REGISTRY, ORACLE)
}

#: What `--communities all` expands to, in display order.
DEFAULT_SET: tuple[str, ...] = (COMMONS.slug, REGISTRY.slug, ORACLE.slug)


def resolve(names: str) -> list[CommunitySpec]:
    """Parse a comma-separated slug list into specs.

    "all" expands to DEFAULT_SET. Unknown slugs raise ValueError naming
    what is available — a typo here would otherwise boot a simulation with
    a silently missing community.
    """
    raw = [n.strip() for n in (names or "").split(",") if n.strip()]
    if not raw:
        return []
    if raw == ["all"]:
        raw = list(DEFAULT_SET)
    out: list[CommunitySpec] = []
    for slug in raw:
        if slug not in CATALOG:
            raise ValueError(
                f"unknown community {slug!r}. Available: "
                f"{', '.join(CATALOG)} (or 'all')"
            )
        spec = CATALOG[slug]
        if spec not in out:
            out.append(spec)
    return out
