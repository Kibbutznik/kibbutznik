# Stage 3: Big Brother Viewer

## Overview
A real-time web viewer for watching KBZ community governance unfold like a reality show. Viewers can observe agent activity, browse proposals, inspect agent personalities, and interview bots about their decisions.

## Architecture

```
Browser (viewer/)                    Server (FastAPI)
  index.html + app.js + style.css      |
       |                                |
       |-- fetch /simulation/status --->|-- Orchestrator.get_status()
       |-- fetch /simulation/agents --->|-- Agent details + traits
       |-- fetch /simulation/events --->|-- Event history
       |-- POST /simulation/interview ->|-- LLM generates in-character response
       |-- POST /simulation/run-round ->|-- Trigger manual round
       |                                |
       |<-- ws://host/ws/events --------|-- EventBus broadcasts:
       |                                |   - agent.action (every agent turn)
       |                                |   - round.start / round.end
       |                                |   - pulse.executed
       |                                |   - proposal.accepted/rejected
```

## Quick Start

### Prerequisites
1. KBZ API database running (PostgreSQL)
2. Anthropic API key: `export ANTHROPIC_API_KEY=sk-...`
   OR Ollama running locally

### Run
```bash
# Start simulation with viewer (default: 10 rounds, Claude Haiku)
python -m agents.run_with_viewer --rounds 20 --delay 2.0

# With Ollama
python -m agents.run_with_viewer --backend ollama --model llama3.2 --rounds 20

# Custom options
python -m agents.run_with_viewer \
  --rounds 30 \
  --delay 3.0 \
  --community-name "My Experiment" \
  --verbose
```

Then open **http://localhost:8000/viewer/** in your browser.

## Viewer Tabs

### Dashboard
- **Community Overview**: Member count, round number, pulses completed, total events
- **Pulse Progress Bar**: Visual indicator of support toward next pulse trigger
- **Activity Feed**: Real-time scrolling feed of all agent actions, color-coded by type
- **Proposal Board**: Three-column view (On The Air | Out There | Results)
- **Run Round Button**: Manually trigger a simulation round

### Agents
- **Agent Cards**: Grid of all 6 agents with name, role, background, action count
- **Expanded View**: Click a card to see:
  - **Traits Radar Chart**: 7-dimension personality visualization (Chart.js)
  - **Action History**: Scrollable list of recent actions with reasoning

### Interview (Big Brother Feature)
- Select an agent from the sidebar
- Type a question in natural language
- Agent responds **in character** using their persona + action history as context
- Conversation history maintained per agent within the session

### Timeline
- Vertical timeline of all pulses (Next / Active / Done)
- Each pulse node shows support progress and associated proposals
- Accepted/rejected proposals displayed with status icons

## Tech Stack
- **No build step** — React 18 + Chart.js + Babel loaded from CDN
- **Same-origin serving** — FastAPI serves viewer at `/viewer/` (no CORS issues)
- **Real-time updates** — WebSocket + 5-second polling fallback
- **3 files total**: `index.html`, `style.css`, `app.js`

## File Structure
```
viewer/
  index.html    # HTML shell with CDN imports
  style.css     # Dark surveillance theme
  app.js        # React SPA (all components)
```

## Key Files
- `viewer/app.js` — All React components and data fetching logic
- `viewer/style.css` — Dark theme with CSS Grid responsive layout
- `agents/run_with_viewer.py` — Entry point combining API + simulation + viewer
- `agents/simulation_api.py` — REST endpoints for viewer data
- `agents/orchestrator.py` — Simulation engine (emits events to WebSocket)

## Event Types on WebSocket

| Event | Source | When |
|-------|--------|------|
| `agent.action` | orchestrator | Every agent turn (with agent_name, action_type, details) |
| `round.start` | orchestrator | Beginning of each round |
| `round.end` | orchestrator | End of each round |
| `pulse.executed` | pulse_service | Pulse completes execution |
| `proposal.accepted` | pulse_service | Proposal passes threshold |
| `proposal.rejected` | pulse_service | Proposal fails threshold |

## CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--rounds` | 10 | Number of simulation rounds |
| `--delay` | 2.0 | Seconds between rounds |
| `--backend` | anthropic | LLM backend (anthropic/ollama) |
| `--model` | claude-haiku-4-5-20251001 | LLM model |
| `--community-name` | "AI Kibbutz" | Community name |
| `--host` | 0.0.0.0 | Server host |
| `--port` | 8000 | Server port |
| `--verbose` | false | Debug logging |
