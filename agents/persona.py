"""Persona loading and management."""
import os
import random
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Traits:
    openness: float = 0.5
    cooperation: float = 0.5
    initiative: float = 0.5
    patience: float = 0.5
    loyalty: float = 0.5
    social_energy: float = 0.5
    confrontation: float = 0.5


@dataclass
class Persona:
    name: str
    role: str
    traits: Traits
    background: str
    decision_style: str
    communication_style: str

    def trait_summary(self) -> str:
        """Human-readable summary of traits for LLM context."""
        t = self.traits
        lines = []
        if t.openness > 0.7:
            lines.append("very open to new ideas")
        elif t.openness < 0.4:
            lines.append("skeptical of new ideas")

        if t.cooperation > 0.7:
            lines.append("highly cooperative")
        elif t.cooperation < 0.4:
            lines.append("independent-minded")

        if t.initiative > 0.7:
            lines.append("proactive, often proposes things")
        elif t.initiative < 0.4:
            lines.append("reactive, rarely initiates")

        if t.patience > 0.7:
            lines.append("patient and deliberate")
        elif t.patience < 0.4:
            lines.append("impatient, wants action")

        if t.social_energy > 0.7:
            lines.append("socially active, comments frequently")
        elif t.social_energy < 0.4:
            lines.append("quiet, engages selectively")

        if t.confrontation > 0.7:
            lines.append("willing to challenge and debate")
        elif t.confrontation < 0.4:
            lines.append("avoids confrontation")

        return ", ".join(lines) if lines else "balanced personality"


def load_persona(filepath: str) -> Persona:
    with open(filepath) as f:
        data = yaml.safe_load(f)
    return Persona(
        name=data["name"],
        role=data.get("role", "Community Member"),
        traits=Traits(**data.get("traits", {})),
        background=data.get("background", ""),
        decision_style=data.get("decision_style", ""),
        communication_style=data.get("communication_style", ""),
    )


def load_all_personas(directory: str | None = None) -> list[Persona]:
    """Load the default cooperative-persona roster.

    Intentionally skips the `adversarial/` subdirectory so regular
    simulations aren't poisoned with trolls / sybils / free-riders by
    default. Use `load_adversarial_personas()` to opt in.
    """
    if directory is None:
        directory = str(Path(__file__).parent / "personas")
    personas = []
    for filename in sorted(os.listdir(directory)):
        if filename.endswith((".yaml", ".yml")):
            personas.append(load_persona(os.path.join(directory, filename)))
    return personas


def load_adversarial_personas() -> list[Persona]:
    """Load the opt-in adversarial persona roster.

    Used by the eval suite and the replay harness to stress-test
    governance under hostile actors: Troll (Yoni), FreeRider (Shira),
    and a sybil pair (Tal + Noa) that co-support each other silently.
    """
    directory = Path(__file__).parent / "personas" / "adversarial"
    if not directory.exists():
        return []
    out = []
    for filename in sorted(os.listdir(directory)):
        if filename.endswith((".yaml", ".yml")):
            out.append(load_persona(str(directory / filename)))
    return out


# Extra names used when the requested member count exceeds the number of YAML personas.
_EXTRA_NAMES = [
    "Alex", "Sam", "Jordan", "Morgan", "Casey", "Riley",
    "Avery", "Quinn", "Blake", "Drew", "Jamie", "Kai",
    "Skyler", "Reese", "Finley", "Rowan", "Emery", "Sage",
    "River", "Hayden", "Phoenix", "Dakota", "Remi", "Shiloh",
    "Lennon", "Lyric", "Nova", "Zion", "Cruz", "Indigo",
]

#: Hard ceiling: 6 YAML personas + 30 generated names.
MAX_MEMBERS = len(_EXTRA_NAMES) + 6  # 36


def build_persona_list(count: int, directory: str | None = None) -> list[Persona]:
    """Return exactly *count* personas for a simulation.

    * count ≤ available YAML files → shuffle and take the first *count*.
    * count > available YAML files → use all YAML personas and pad with
      freshly generated random personas (names drawn from ``_EXTRA_NAMES``).
    * count is clamped to [2, MAX_MEMBERS].
    """
    count = max(2, min(count, MAX_MEMBERS))
    yaml_personas = load_all_personas(directory)
    if count <= len(yaml_personas):
        shuffled = yaml_personas[:]
        random.shuffle(shuffled)
        return shuffled[:count]
    # Need more than the YAML files can provide — pad with generated personas.
    result = yaml_personas[:]  # all YAML personas first
    extra_needed = count - len(yaml_personas)
    available_names = [n for n in _EXTRA_NAMES if not any(p.name == n for p in result)]
    random.shuffle(available_names)
    for name in available_names[:extra_needed]:
        result.append(generate_persona(name))
    return result


# ── Dynamic persona generation for newcomers ──────────────

_BACKGROUNDS = [
    "Recently joined and eager to understand how governance works. Asks lots of questions and supports initiatives that seem well-reasoned.",
    "Brings experience from another cooperative community. Values transparency and structured decision-making processes.",
    "Passionate about democratic participation. Believes every member should have an equal voice in community decisions.",
    "Interested in how working groups can help the community tackle complex projects. Supportive of action proposals.",
    "Focused on sustainability and long-term community health. Cautious about rapid changes but open to well-argued proposals.",
    "A pragmatic newcomer who evaluates proposals on their practical merit. Tends to support concrete, actionable initiatives.",
    "Enthusiastic about collective governance and eager to join working groups. Wants to prove themselves as a valuable contributor.",
    "Values careful deliberation. Prefers to observe discussions before weighing in, but has strong opinions once formed.",
]

_DECISION_STYLES = [
    "Careful observer who supports proposals aligned with community values. Takes time to understand before acting.",
    "Enthusiastic supporter of new initiatives. Quick to propose joining action committees and working groups.",
    "Balanced decision-maker who weighs pros and cons. Comments to understand proposals better before supporting.",
    "Tends to support collaborative proposals. Skeptical of changes that reduce community oversight.",
    "Focuses on practical outcomes. Supports proposals that have clear benefits and realistic implementation plans.",
]

_COMMUNICATION_STYLES = [
    "Friendly and curious, asks good questions.",
    "Direct and clear, focuses on substance over style.",
    "Thoughtful and measured, considers multiple perspectives before commenting.",
    "Warm and encouraging, tries to find common ground in discussions.",
    "Concise and pragmatic, gets straight to the point.",
]


def generate_persona(name: str) -> Persona:
    """Generate a random persona for a newcomer agent."""
    return Persona(
        name=name,
        role="Community Member",
        traits=Traits(
            openness=round(random.uniform(0.3, 0.9), 2),
            cooperation=round(random.uniform(0.4, 0.9), 2),
            initiative=round(random.uniform(0.3, 0.7), 2),
            patience=round(random.uniform(0.3, 0.8), 2),
            loyalty=round(random.uniform(0.4, 0.8), 2),
            social_energy=round(random.uniform(0.3, 0.8), 2),
            confrontation=round(random.uniform(0.2, 0.6), 2),
        ),
        background=random.choice(_BACKGROUNDS),
        decision_style=random.choice(_DECISION_STYLES),
        communication_style=random.choice(_COMMUNICATION_STYLES),
    )
