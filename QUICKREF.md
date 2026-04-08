# KBZ Quick Reference

## Activation

```bash
# Method 1: Use activation script
cd /Users/uriee/claude/kbz && source activate.sh

# Method 2: Manual
cd /Users/uriee/claude/kbz && source .venv/bin/activate
```

## Start API Server

```bash
# Terminal 1
cd /Users/uriee/claude/kbz && source .venv/bin/activate
uvicorn kbz.main:app --host 0.0.0.0 --port 8000
```

## Run Simulation

```bash
# Terminal 2
cd /Users/uriee/claude/kbz && source .venv/bin/activate

# Continuous Ollama (gemma4:26b)
python -m agents.run_with_viewer --backend ollama --model gemma4:26b --rounds 0 --delay 3

# 100 rounds with custom settings
python -m agents.run_with_viewer --backend ollama --model gemma4:26b --rounds 100 \
  --ollama-ctx 16384 --ollama-temp 0.6

# Default (Anthropic Claude, 10 rounds)
python -m agents.run_with_viewer

# Verbose logging
python -m agents.run_with_viewer --backend ollama --model gemma4:26b --rounds 0 -v
```

## Viewer

- **URL**: http://localhost:8000/viewer/
- **Dashboard**: Pulse progress, proposals, activity feed
- **Agents Tab**: Browse all agents and their history
- **Actions Tab**: Action/sub-community tree
- **Statements Tab**: View statements by community
- **Click elements**: See details, linked proposals, interviews

## Ollama Management

```bash
# Check installed models
/usr/local/bin/ollama list

# Pull a model
/usr/local/bin/ollama pull gemma4:26b

# Show model details
/usr/local/bin/ollama show gemma4:26b

# Test model
/usr/local/bin/ollama run gemma4:26b "Hello"

# Verify Ollama is running
ps aux | grep "[o]llama"
```

## Logs & Monitoring

```bash
# Tail simulation logs
tail -f simulation.log

# Watch for LLM stats (every 10 rounds)
tail -f simulation.log | grep "LLM Stats"

# Watch for errors
tail -f simulation.log | grep -i "error"

# Full debug output
tail -f simulation.log | grep -v "INFO"
```

## Database

```bash
# Run migrations
alembic upgrade head

# Check DB status
psql kbz_db -c "SELECT * FROM communities LIMIT 1;"
```

## Testing

```bash
# Run all tests
.venv/bin/pytest tests/ -x -q

# Run specific test
.venv/bin/pytest tests/test_proposals.py -v

# With coverage
.venv/bin/pytest tests/ --cov
```

## Git

```bash
# See changes
git status

# Stage and commit
git add agents/decision_engine.py
git commit -m "Improve LLM logic"

# View log
git log --oneline -5
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Module not found" | `source .venv/bin/activate` |
| API connection refused | Start API: `uvicorn kbz.main:app` |
| Ollama model not found | `ollama pull gemma4:26b` |
| Timeout errors | Increase `--ollama-timeout 600` |
| Out of memory | Reduce `--ollama-ctx 4096` |
| No LLM stats | Wait 10 rounds (logged every 10) |
| Paused simulation | Click play button in viewer |

## Key Files

| Path | Purpose |
|------|---------|
| `agents/decision_engine.py` | LLM interface (Claude, Ollama) |
| `agents/orchestrator.py` | Simulation engine, round loop |
| `agents/persona.py` | Agent personality generation |
| `viewer/app.js` | Web UI (React) |
| `kbz/main.py` | FastAPI app entry |
| `OLLAMA_SETUP.md` | Detailed Ollama guide |
| `README.md` | Full documentation |
| `simulate.log` | Simulation logs |

## Common Customizations

### Change LLM timeout
```bash
--ollama-timeout 600  # 10 minutes
```

### Reduce delay for faster rounds
```bash
--delay 1  # 1 second between rounds
```

### Larger context window for better responses
```bash
--ollama-ctx 16384  # Double the default
```

### Lower temperature for consistent behavior
```bash
--ollama-temp 0.5  # More deterministic
```

### More retries for unreliable connections
```bash
--retries 5  # Default is 3
```

## Stats Interpretation

From header: `gemma4 (5.2s)` means:
- Model: gemma4:26b
- Avg response time: 5.2 seconds per LLM call

From logs: `Round 50: 47 calls, avg 4.8s, 0 errors, 4821 events` means:
- 47 LLM calls so far
- Average 4.8s per call
- No failed calls (0 errors)
- 4821 events kept in memory (auto-trims at 5000)

## Full Example

```bash
# Terminal 1: Start API
$ cd /Users/uriee/claude/kbz
$ source activate.sh
$ uvicorn kbz.main:app

# Terminal 2: Run simulation
$ cd /Users/uriee/claude/kbz
$ source .venv/bin/activate
$ python -m agents.run_with_viewer \
    --backend ollama \
    --model gemma4:26b \
    --rounds 0 \
    --delay 3 \
    --ollama-ctx 12288 \
    --ollama-temp 0.6 \
    --verbose

# Terminal 3: Watch logs
$ tail -f simulation.log | grep "Round\|LLM Stats\|error"

# Browser: Open viewer
$ open http://localhost:8000/viewer/
```

Stop simulation: Press **Ctrl+C** in simulation terminal
