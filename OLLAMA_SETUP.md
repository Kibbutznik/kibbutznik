# Running Long KBZ Simulations with Ollama

This guide explains how to set up and run extended KBZ simulations using a local Ollama model (e.g., `gemma4:26b`) on your Mac.

## Prerequisites

1. **Ollama installed** — Download from https://ollama.com
2. **Model downloaded** — Run `ollama pull gemma4:26b` (17 GB, ~30 min)
3. **KBZ environment set up** — Python 3.12+, dependencies installed

## Quick Start

### 1. Activate the KBZ virtual environment

```bash
cd /Users/uriee/claude/kbz
source .venv/bin/activate
```

After activation, your prompt will show `(.venv)` prefix:
```bash
(.venv) $ python -m agents.run_with_viewer --backend ollama --model gemma4:26b
```

### 2. Verify Ollama is running and the model is available

```bash
# Check if Ollama is running
/usr/local/bin/ollama list

# Expected output:
# NAME                 ID              SIZE      MODIFIED
# gemma4:26b       5571076f3d70    17 GB     3 days ago
```

If the model isn't listed, pull it:
```bash
/usr/local/bin/ollama pull gemma4:26b
```

To ensure Ollama stays responsive, you can keep it warmed up:
```bash
# In a separate terminal, keep the model loaded
/usr/local/bin/ollama run gemma4:26b "Hello"
```

### 3. Start the KBZ API server

In one terminal:
```bash
cd /Users/uriee/claude/kbz
source .venv/bin/activate
uvicorn kbz.main:app --host 0.0.0.0 --port 8000
```

### 4. Start the simulation with Ollama

In another terminal:
```bash
cd /Users/uriee/claude/kbz
source .venv/bin/activate

# Basic: 100 rounds
python -m agents.run_with_viewer --backend ollama --model gemma4:26b --rounds 100

# Continuous (infinite) — stop with Ctrl+C
python -m agents.run_with_viewer --backend ollama --model gemma4:26b --rounds 0 --delay 3

# Custom settings for a 26B model
python -m agents.run_with_viewer \
  --backend ollama \
  --model gemma4:26b \
  --rounds 0 \
  --delay 3 \
  --ollama-ctx 16384 \
  --ollama-temp 0.6 \
  --ollama-timeout 300 \
  --retries 3
```

Open http://localhost:8000/viewer/ in your browser to watch in real-time.

## Command Reference

### Activate Environment

```bash
source /Users/uriee/claude/kbz/.venv/bin/activate
```

Deactivate:
```bash
deactivate
```

### Run Simulation (with viewer)

```bash
python -m agents.run_with_viewer [OPTIONS]
```

**Common Options:**
- `--backend ollama` — Use local Ollama
- `--model gemma4:26b` — Model name (must be downloaded first)
- `--rounds N` — Number of rounds (0 = continuous)
- `--delay N` — Seconds between rounds (default: 2.0)
- `--ollama-ctx N` — Context window size (default: 8192, max ~16384 for 26B on Mac)
- `--ollama-temp N` — Temperature 0.0-1.0 (default: 0.7)
- `--ollama-timeout N` — Request timeout seconds (default: 300)
- `--retries N` — Max retries per LLM call (default: 3)
- `--verbose` — Show debug logs

### Run Simulation (CLI only, no viewer)

```bash
python -m agents.run_simulation [OPTIONS]
```

Same options as `run_with_viewer`.

### Check Available Models

```bash
/usr/local/bin/ollama list
```

### Pull a New Model

```bash
/usr/local/bin/ollama pull gemma4:26b
# Or
/usr/local/bin/ollama pull gemma4:31b
/usr/local/bin/ollama pull qwen3:30b
```

### Ollama System Info

```bash
/usr/local/bin/ollama show gemma4:26b
```

## Optimization Tips for Long Simulations

### Memory Management
- Default keeps last 5000 events in memory
- Older events are auto-trimmed to prevent memory leaks
- LLM stats logged every 10 rounds

### Performance
- **Increase `--delay`** if Mac is slow (e.g., `--delay 5`)
- **Reduce `--ollama-ctx`** if out of memory (e.g., `--ollama-ctx 4096`)
- **Lower `--ollama-temp`** (0.5-0.6) for faster, more consistent responses
- **Increase `--retries`** (4-5) if timeouts happen frequently

### Monitor Resource Usage

In another terminal:
```bash
# Watch CPU/memory
top -l 1 | head -20

# Or Activity Monitor
open -a "Activity Monitor"
```

Watch for:
- `ollama` process using consistent GPU/memory
- Python process (simulation) using moderate CPU
- No unbounded memory growth

## Troubleshooting

### "Model not found"
```bash
# Pull the model first
/usr/local/bin/ollama pull gemma4:26b

# Verify
/usr/local/bin/ollama list | grep gemma4
```

### "Connection refused" / "Ollama not running"
```bash
# Check if Ollama is running
ps aux | grep ollama

# Start Ollama (if using Ollama.app)
open /Applications/Ollama.app

# Or start ollama binary
/usr/local/bin/ollama serve
```

### Timeouts (LLM calls taking >300s)
- Increase `--ollama-timeout` (e.g., 600)
- Reduce context window `--ollama-ctx` (fewer prompts)
- Reduce batch size with `--delay` increase
- Check Activity Monitor — Mac might be low on memory

### LLM errors after retries
```bash
# Check logs for details
tail -f simulation.log | grep -i "error\|lLM"

# Reduce temperature for more stable output
--ollama-temp 0.5
```

### Out of Memory
- Reduce `--ollama-ctx` to 4096 or 2048
- Increase `--delay` to space out requests
- Close other apps on Mac
- Check Ollama model size: `ollama show gemma4:26b`

## File Locations

- **KBZ root**: `/Users/uriee/claude/kbz/`
- **Virtual env**: `/Users/uriee/claude/kbz/.venv/`
- **Simulation logs**: `/Users/uriee/claude/kbz/simulation.log`
- **Decision engine**: `/Users/uriee/claude/kbz/agents/decision_engine.py`
- **Orchestrator**: `/Users/uriee/claude/kbz/agents/orchestrator.py`

## Example: Full Setup & Run

```bash
# Terminal 1: Start API server
cd /Users/uriee/claude/kbz
source .venv/bin/activate
uvicorn kbz.main:app

# Terminal 2: Start continuous simulation
cd /Users/uriee/claude/kbz
source .venv/bin/activate
python -m agents.run_with_viewer \
  --backend ollama \
  --model gemma4:26b \
  --rounds 0 \
  --delay 3 \
  --ollama-ctx 12288 \
  --verbose

# Terminal 3 (optional): Watch logs
tail -f simulation.log | grep -E "Round|error|LLM Stats"

# Browser: Open viewer
open http://localhost:8000/viewer/
```

## LLM Stats in Viewer

The viewer header shows:
- **Model**: Current LLM backend (e.g., "gemma4")
- **Latency**: Average response time in seconds
- **Hover tooltip**: Full stats (call count, errors, total latency)

These update every 10 rounds.

## Additional Notes

- **Retry backoff**: Failures trigger exponential backoff (2^attempt, capped at 30s)
- **System prompt**: Ollama receives a system prompt that encourages clean JSON output
- **Health check**: Simulation verifies model availability before starting
- **Async I/O**: All LLM calls are async — simulation remains responsive
