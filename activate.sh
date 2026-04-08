#!/bin/bash
# Quick activation script for KBZ simulations
# Usage: source /Users/uriee/claude/kbz/activate.sh

KBZ_ROOT="/Users/uriee/claude/kbz"

# Activate virtual environment
if [ ! -d "$KBZ_ROOT/.venv" ]; then
    echo "❌ Virtual environment not found at $KBZ_ROOT/.venv"
    echo "Run: python3 -m venv $KBZ_ROOT/.venv && pip install -e '.[agents]'"
    return 1
fi

source "$KBZ_ROOT/.venv/bin/activate"

# Change to project root so relative paths work
cd "$KBZ_ROOT"

echo "✓ KBZ environment activated  (python: $(which python))"
echo ""
echo "Quick commands:"
echo "  1. Start API server:"
echo "     uvicorn kbz.main:app --host 0.0.0.0 --port 8000"
echo ""
echo "  2. Run Ollama simulation (continuous):"
echo "     python -m agents.run_with_viewer --backend ollama --model gemma4:26b --rounds 0 --delay 5"
echo ""
echo "  3. Run with custom Ollama settings (recommended for 26B):"
echo "     python -m agents.run_with_viewer --backend ollama --model gemma4:26b \\"
echo "       --rounds 100 --ollama-ctx 16384 --ollama-temp 0.6 --delay 5"
echo ""
echo "  4. Check available Ollama models:"
echo "     /usr/local/bin/ollama list"
echo ""
echo "  5. View logs:"
echo "     tail -f simulation.log"
echo ""
echo "Docs: $KBZ_ROOT/QUICKREF.md"
