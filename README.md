# KBZ — Pulse-Based AI Governance Simulation

A FastAPI-based platform for simulating pulse-driven direct democracy with AI agents. Includes a real-time Big Brother viewer for observing agent behavior and governance evolution.

## Quick Start (Ollama on Mac)

### 1. Activate the environment

```bash
cd /Users/uriee/claude/kbz
source activate.sh
```

Or manually:
```bash
cd /Users/uriee/claude/kbz
source .venv/bin/activate
```

### 2. Start the API server (Terminal 1)

```bash
uvicorn kbz.main:app --host 0.0.0.0 --port 8000
```

### 3. Run a long simulation (Terminal 2)

```bash
# Continuous Ollama simulation with gemma4:26b
python -m agents.run_with_viewer \
  --backend ollama \
  --model gemma4:26b \
  --rounds 0 \
  --delay 3 \
  --verbose

# Or with the default Anthropic Claude:
python -m agents.run_with_viewer --rounds 50
```

### 4. Open the viewer

Open http://localhost:8000/viewer/ in your browser.

## Detailed Documentation

- **[OLLAMA_SETUP.md](./OLLAMA_SETUP.md)** — Complete guide for local Ollama simulations, troubleshooting, and optimization
- **[agents/decision_engine.py](./agents/decision_engine.py)** — LLM integration (Claude API, Ollama)
- **[agents/orchestrator.py](./agents/orchestrator.py)** — Simulation engine
- **[agents/persona.py](./agents/persona.py)** — Agent personality generation

## Architecture

### Core Components

- **FastAPI backend** (`kbz/main.py`) — REST API for governance operations
- **SQLAlchemy + PostgreSQL** — Community, members, proposals, pulses, statements, actions
- **Agent orchestrator** (`agents/orchestrator.py`) — Runs agents through governance cycles
- **Decision engine** (`agents/decision_engine.py`) — LLM interface (Anthropic Claude or Ollama)
- **React SPA viewer** (`viewer/app.js`) — Real-time web UI with WebSocket updates

### Key Concepts

#### Pulses
The heartbeat of governance. Members support pulses to advance proposals through stages:
- **Draft** → **OutThere** → **OnTheAir** → **Accepted/Rejected**

#### Proposals
Governance transactions:
- **AddStatement** — Constitution rules
- **ChangeVariable** — Adjust thresholds
- **AddAction** — Create sub-communities (working groups)
- **JoinAction** — Join a working group
- **Membership** — Welcome new members
- **ThrowOut** / **RemoveStatement** / **ReplaceStatement** — Other actions

#### Actions = Sub-Communities
Each action has its own members, variables, proposals, and pulses. Perfect for committees, working groups, or project teams.

#### Membership
New users submit membership proposals. When accepted, they become full AI agents with:
- Personality (background, decision style, communication style, traits)
- Action history
- Proposal preferences
- Pulse strategy

## Installation

### Requirements
- Python 3.12+
- PostgreSQL 13+
- Ollama (optional, for local simulations)

### Setup

```bash
# Clone/navigate to project
cd /Users/uriee/claude/kbz

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e ".[agents]"

# Set up database
alembic upgrade head

# Download Ollama model (if using Ollama)
/usr/local/bin/ollama pull gemma4:26b
```

## Running Simulations

### With Anthropic Claude (requires ANTHROPIC_API_KEY)

```bash
python -m agents.run_with_viewer --rounds 20
```

### With local Ollama

```bash
python -m agents.run_with_viewer \
  --backend ollama \
  --model gemma4:26b \
  --rounds 0 \
  --delay 3
```

### With the standalone runner (no viewer)

```bash
python -m agents.run_simulation --backend ollama --model gemma4:26b --rounds 100
```

## Configuration

### CLI Arguments

#### Common
- `--rounds N` — Number of rounds (0 = continuous)
- `--delay N` — Seconds between rounds (default: 2.0)
- `--backend {anthropic|ollama}` — LLM backend
- `--model NAME` — Model name (e.g., claude-haiku-4-5-20251001 or gemma4:26b)
- `--community-name NAME` — Custom community name
- `--verbose` — Debug logging

#### Ollama-specific
- `--ollama-ctx N` — Context window (default: 8192)
- `--ollama-temp N` — Temperature 0.0-1.0 (default: 0.7)
- `--ollama-timeout N` — Request timeout seconds (default: 300)
- `--ollama-max-tokens N` — Max output tokens (default: 2048)
- `--retries N` — Max retries per LLM call (default: 3)

## Viewer Features

### Dashboard
- Community overview (member count, pulse progress)
- Activity feed (real-time events)
- Proposal board (draft, out-there, on-the-air columns)
- Pulse progress bar

### Navigation
- **Agent sidebar** — Explore all agents
- **Action sidebar** — Quick navigation between actions/sub-communities
- **Action tree** — Detailed hierarchy of actions with member counts

### Detail Panels
- Click any agent, proposal, statement, or action for details
- View membership proposals, related proposals for statements
- Interview agents (ask questions, see their reasoning)

### Real-Time Updates
- WebSocket events stream agent actions
- LLM stats (model, latency, call count) in header
- Memory-bounded event log (auto-trims old events)

## Testing

```bash
# Run all tests
.venv/bin/pytest tests/ -x -q

# Specific test file
.venv/bin/pytest tests/test_proposals.py -v

# With coverage
.venv/bin/pytest tests/ --cov=agents --cov=src/kbz
```

## Development

### Project Structure

```
/Users/uriee/claude/kbz/
├── agents/                    # AI agent orchestration
│   ├── orchestrator.py       # Simulation engine
│   ├── decision_engine.py    # LLM interface
│   ├── agent.py              # Individual agent
│   ├── persona.py            # Personality generation
│   ├── run_with_viewer.py    # CLI with viewer
│   └── run_simulation.py      # CLI without viewer
├── src/kbz/                   # FastAPI backend
│   ├── main.py               # App entry
│   ├── routers/              # API endpoints
│   ├── services/             # Business logic
│   ├── models/               # SQLAlchemy ORM
│   └── schemas/              # Pydantic schemas
├── viewer/                    # React SPA
│   ├── app.js                # Main component
│   ├── index.html            # Entry point
│   └── style.css             # Styling
├── tests/                     # Pytest tests
├── alembic/                   # Database migrations
├── OLLAMA_SETUP.md           # Ollama guide
└── activate.sh               # Quick activation
```

### Git Workflow

```bash
# Check status
git status

# Stage changes
git add agents/decision_engine.py

# Create commit (auto-signed via Claude)
git commit -m "Improve LLM retry logic"

# View recent commits
git log --oneline -5
```

## Troubleshooting

### "ModuleNotFoundError: No module named 'agents'"
- Activate the virtual environment: `source .venv/bin/activate`
- Install in development mode: `pip install -e ".[agents]"`

### "Connection refused" (API)
- Make sure the API server is running: `uvicorn kbz.main:app`
- Check it's listening on port 8000: `lsof -i :8000`

### Ollama timeouts
- See **[OLLAMA_SETUP.md](./OLLAMA_SETUP.md)** — Troubleshooting section

### Database errors
- Check PostgreSQL is running: `psql -U postgres -c "SELECT 1"`
- Run migrations: `alembic upgrade head`

## Resources

- **Pulse-Based Governance**: See `agents/decision_engine.py` for the KBZ governance rules
- **Agent Persona System**: `agents/persona.py` — personality traits, backgrounds, decision styles
- **API Documentation**: http://localhost:8000/docs (when server is running)
- **Simulation Logs**: `simulation.log`

## Key Features Implemented

✅ Pulse-based direct democracy (proposals → pulses → decisions)
✅ AI agents with dynamic personas
✅ Action sub-communities (working groups)
✅ Real-time Big Brother viewer with WebSocket updates
✅ Local Ollama support for long simulations
✅ Retry logic with exponential backoff
✅ Memory-bounded event tracking
✅ LLM stats and monitoring
✅ Newcomer → Full Agent promotion pipeline
✅ Proposal edit with support reset
✅ Statement-to-proposal linking

## Future Ideas

- [ ] Persistent storage of simulation runs
- [ ] Comparative analysis across multiple simulations
- [ ] Human member joining during simulation
- [ ] Custom governance rule sets
- [ ] Agent team dynamics and alliance formation
- [ ] Performance profiling dashboard
