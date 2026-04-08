# Stage 2: AI Agent Emulation

## Overview
AI-powered community members that observe, think, discuss, and act within KBZ communities. Each agent has a unique personality, social skills, full knowledge of governance rules, and awareness of what's happening in their community.

## Architecture

```
┌─────────────────────────────────────────────┐
│                 Orchestrator                 │
│  Setup → Run rounds → Track events          │
├──────────┬──────────┬──────────┬────────────┤
│  Agent   │  Agent   │  Agent   │  Agent ... │
│  Rivka   │  Moshe   │  Dana    │  Yoav      │
├──────────┴──────────┴──────────┴────────────┤
│            Observe → Think → Act             │
├──────────────────────────────────────────────┤
│  Community State    │   Decision Engine      │
│  Observer           │   (LLM: Haiku/Ollama)  │
├─────────────────────┴───────────────────────┤
│              KBZ API Client                  │
├──────────────────────────────────────────────┤
│              KBZ REST API                    │
└──────────────────────────────────────────────┘
```

## Agent Loop: Observe → Think → Act

### 1. OBSERVE (Browse the Community)
Before each decision, agents fetch the full community state:
- All active members and their seniority
- Community statements (the "constitution")
- Active proposals (OutThere and OnTheAir) with support counts
- Comments on proposals (what others are saying)
- Recent accepted/rejected proposals (what happened)
- Pulse status (how close to triggering)
- Governance variables (current thresholds)

This is equivalent to a human member browsing the community dashboard.

### 2. THINK (LLM Decision)
The decision engine sends to the LLM:
- Agent's persona (personality, background, decision style, communication style)
- Full KBZ governance rules (so they know what they can do and how it works)
- Current community state summary
- Agent's recent action history (for self-awareness)

The LLM returns a structured JSON action.

### 3. ACT (Execute via API)
Available actions:
- `support_pulse` — vote to advance to next governance cycle
- `support_proposal` — support an existing proposal
- `create_proposal` — create + submit + self-support a new proposal
- `comment` — comment on a proposal (explain reasoning, discuss)
- `reply_comment` — reply to another agent's comment
- `vote_comment` — upvote/downvote a comment
- `do_nothing` — observe without acting

## Personas

Six distinct personalities defined in `agents/personas/*.yaml`:

| Name | Role | Key Traits |
|------|------|------------|
| **Rivka** | Community Visionary | High openness (0.9), impatient (0.3), proposes boldly |
| **Moshe** | Community Guardian | Low openness (0.3), very patient (0.9), values stability |
| **Dana** | Community Mediator | High cooperation (0.9), builds bridges, seeks compromise |
| **Yoav** | Community Challenger | High confrontation (0.85), devil's advocate, Socratic questioning |
| **Tamar** | Community Connector | Very social (0.95), champions new members, inclusive |
| **Avi** | Community Executor | High initiative (0.9), very impatient (0.2), action-focused |

Each persona has:
- **Traits** (7 dimensions): openness, cooperation, initiative, patience, loyalty, social_energy, confrontation
- **Background**: who they are and what they care about
- **Decision style**: how they evaluate proposals and make choices
- **Communication style**: how they comment and discuss

## Running a Simulation

### Prerequisites
1. KBZ API running: `uvicorn kbz.main:app --reload`
2. Anthropic API key: `export ANTHROPIC_API_KEY=sk-...`
   OR Ollama running with a model

### Quick Start
```bash
# With Claude Haiku (default)
python -m agents.run_simulation --rounds 10

# With Ollama
python -m agents.run_simulation --backend ollama --model llama3.2 --rounds 10

# Custom options
python -m agents.run_simulation \
  --rounds 20 \
  --delay 1.0 \
  --community-name "My Experiment" \
  --verbose
```

### Simulation API
When a simulation is running, these endpoints are available:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/simulation/status` | Full simulation status |
| GET | `/simulation/agents` | All agents with traits and action history |
| POST | `/simulation/interview` | Ask an agent a question (Big Brother!) |
| POST | `/simulation/run-round` | Trigger one round manually |
| GET | `/simulation/events` | Event log with pagination |

### Interview Example
```bash
curl -X POST http://localhost:8000/simulation/interview \
  -H "Content-Type: application/json" \
  -d '{"agent_name": "Rivka", "question": "Why did you create that proposal?"}'
```

## Key Files
- `agents/agent.py` — Core agent class (observe → think → act loop)
- `agents/decision_engine.py` — LLM integration (Anthropic + Ollama)
- `agents/community_state.py` — Community observer (the agent's "eyes")
- `agents/persona.py` — Persona loading and trait system
- `agents/orchestrator.py` — Multi-agent simulation runner
- `agents/api_client.py` — Typed KBZ API wrapper
- `agents/simulation_api.py` — REST endpoints for viewer
- `agents/personas/*.yaml` — Persona definitions

## Test Coverage
- 19 tests covering persona loading, decision parsing, community observation, agent actions, and full agent cycles
- Mock LLM engine for deterministic testing
- Integration tests with real KBZ API (test DB)

## Social Dynamics
Agents don't just vote — they engage socially:
- **Comment before supporting**: Agents explain their reasoning on proposals
- **Reply to discussions**: Agents respond to other agents' comments
- **Vote on comments**: HackerNews-style scoring of discussion quality
- **Read the room**: Agents see what others have said before deciding
- **Remember their history**: Each agent tracks what they've done for self-awareness
