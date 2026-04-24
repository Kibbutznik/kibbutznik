#!/usr/bin/env python3
"""
Run a KBZ community simulation with AI agents.

Usage:
    python -m agents.run_simulation [--rounds 10] [--backend anthropic] [--model claude-haiku-4-5-20251001]

Requires:
    - KBZ API running at localhost:8000 (uvicorn kbz.main:app)
    - For anthropic: ANTHROPIC_API_KEY environment variable
    - For ollama: ollama running locally with the specified model
"""
import os
import sys

# Auto-activate the local virtual environment if kbz is not importable.
def _ensure_venv():
    try:
        import kbz  # noqa: F401
    except ModuleNotFoundError:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        venv_python = os.path.join(root, ".venv", "bin", "python")
        if os.path.isfile(venv_python):
            print(f"[kbz] Re-launching with venv python: {venv_python}", flush=True)
            os.execv(venv_python, [venv_python, "-m", "agents.run_simulation"] + sys.argv[1:])
        else:
            sys.exit(
                "ERROR: 'kbz' module not found and no .venv found.\n"
                "Run: cd /Users/uriee/claude/kbz && source .venv/bin/activate"
            )

_ensure_venv()

import argparse
import asyncio
import logging

from agents.orchestrator import Orchestrator
from agents.persona import load_all_personas


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet down httpx
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def main():
    parser = argparse.ArgumentParser(
        description="Run KBZ AI community simulation",
        epilog="""
Examples:
  # Default Anthropic
  python -m agents.run_simulation --rounds 10

  # Long local Ollama simulation
  python -m agents.run_simulation --backend ollama --model gemma4:26b --rounds 0 --delay 3
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--rounds", type=int, default=10,
                        help="Number of rounds (0 = continuous/infinite)")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Delay between rounds in seconds")
    parser.add_argument("--backend", default="anthropic", choices=["anthropic", "ollama"],
                        help="LLM backend")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001",
                        help="LLM model name (e.g. gemma4:26b for Ollama)")
    parser.add_argument("--api-url", default="http://localhost:8000", help="KBZ API URL")
    parser.add_argument("--community-name", default="AI Kibbutz", help="Community name")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    # Ollama-specific options
    ollama_group = parser.add_argument_group("Ollama options")
    ollama_group.add_argument("--ollama-ctx", type=int, default=8192,
                              help="Context window size (default: 8192)")
    ollama_group.add_argument("--ollama-temp", type=float, default=0.7,
                              help="Temperature (default: 0.7)")
    ollama_group.add_argument("--ollama-timeout", type=float, default=300.0,
                              help="Request timeout in seconds (default: 300)")
    ollama_group.add_argument("--ollama-max-tokens", type=int, default=2048,
                              help="Max output tokens (default: 2048)")
    ollama_group.add_argument("--retries", type=int, default=3,
                              help="Max retries per LLM call (default: 3)")
    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger("simulation")

    personas = load_all_personas()
    logger.info(f"Loaded {len(personas)} personas: {[p.name for p in personas]}")

    orch = Orchestrator(
        community_name=args.community_name,
        api_url=args.api_url,
        llm_backend=args.backend,
        llm_model=args.model,
        personas=personas,
        ollama_timeout=args.ollama_timeout,
        ollama_num_ctx=args.ollama_ctx,
        ollama_temperature=args.ollama_temp,
        ollama_num_predict=args.ollama_max_tokens,
        max_retries=args.retries,
    )

    try:
        await orch.setup()
        logger.info(f"\nCommunity '{orch.community_name}' ready with {len(orch.agents)} agents")
        logger.info(f"Community ID: {orch.community_id}")
        logger.info(f"Agents: {[a.persona.name for a in orch.agents]}")

        await orch.run(rounds=args.rounds, delay=args.delay)

        # Print final status
        status = await orch.get_status()
        logger.info(f"\n{'='*60}")
        logger.info("FINAL STATUS")
        logger.info(f"{'='*60}")
        logger.info(f"Community: {status['community']['name']}")
        logger.info(f"Members: {status['community']['member_count']}")
        logger.info(f"Rounds played: {status['round']}")
        logger.info(f"Total events: {status['total_events']}")
        for agent_info in status["agents"]:
            logger.info(f"  {agent_info['name']} ({agent_info['role']}): {agent_info['actions_taken']} actions")

    except KeyboardInterrupt:
        logger.info("\nSimulation interrupted by user")
    except Exception as e:
        logger.error(f"Simulation error: {e}", exc_info=True)
    finally:
        await orch.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
