#!/usr/bin/env python3
"""
Run a KBZ simulation with the Big Brother viewer.

Combines the KBZ API, simulation engine, and web viewer into a single server.

Usage:
    python -m agents.run_with_viewer [--rounds 10] [--backend anthropic] [--model claude-haiku-4-5-20251001]

Then open http://localhost:8000/viewer/ in your browser.
"""
import os
import sys

# Auto-activate the local virtual environment if kbz is not importable.
# This lets you run `python -m agents.run_with_viewer` without sourcing activate.sh first.
def _ensure_venv():
    try:
        import kbz  # noqa: F401
    except ModuleNotFoundError:
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        venv_python = os.path.join(root, ".venv", "bin", "python")
        if os.path.isfile(venv_python):
            print(f"[kbz] Re-launching with venv python: {venv_python}", flush=True)
            os.execv(venv_python, [venv_python, "-m", "agents.run_with_viewer"] + sys.argv[1:])
        else:
            sys.exit(
                "ERROR: 'kbz' module not found and no .venv found.\n"
                "Run: cd /Users/uriee/claude/kbz && source .venv/bin/activate"
            )

_ensure_venv()

import argparse
import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agents.orchestrator import Orchestrator
from agents.persona import load_all_personas, build_persona_list, MAX_MEMBERS
from agents.simulation_api import (
    router as sim_router,
    set_orchestrator,
    set_restart_callback,
)


DEFAULT_MISSION = (
    "This community is writing a Kibbutznik Handbook: a concrete, practical "
    "document a newcomer can read to understand how this community actually "
    "works — what its values look like in practice, how decisions get made, "
    "and what daily life looks like. Each artifact is ONE SECTION of that "
    "handbook, e.g. \"How we resolve disagreements\", \"The morning stand-up "
    "ritual\", \"What happens when someone wants to leave\", \"How we onboard "
    "a new member\". Sections must be specific, procedural, and written for a "
    "real reader to follow. Do NOT write mission statements, slogans, or "
    "abstract principles — those belong in Community Rules (AddStatement)."
)


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    datefmt = "%H:%M:%S"

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter(fmt, datefmt))

    # File handler — always write full logs
    log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "simulation.log")
    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(fmt, datefmt))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    logging.getLogger("simulation").info(f"Logging to: {log_path}")


async def run_simulation(orch: Orchestrator, rounds: int, delay: float):
    """Run the simulation as a background task (first start — calls setup)."""
    logger = logging.getLogger("simulation")
    try:
        await orch.setup()
        logger.info(f"Community '{orch.community_name}' ready with {len(orch.agents)} agents")
        logger.info(f"Community ID: {orch.community_id}")
        logger.info(f"Viewer: http://localhost:8000/viewer/")
        await orch.run(rounds=rounds, delay=delay)

        status = await orch.get_status()
        logger.info(f"\nSimulation complete. {status['total_events']} total events over {status['round']} rounds.")
    except asyncio.CancelledError:
        logger.info("Simulation cancelled")
    except Exception as e:
        logger.error(f"Simulation error: {e}", exc_info=True)


async def _resume_simulation(orch: Orchestrator, rounds: int, delay: float):
    """Restart the simulation loop without calling setup() — community and agents already exist."""
    logger = logging.getLogger("simulation")
    try:
        logger.info(f"Resuming simulation from round {orch._round} — community '{orch.community_name}'")
        await orch.run(rounds=rounds, delay=delay)

        status = await orch.get_status()
        logger.info(f"\nSimulation complete. {status['total_events']} total events over {status['round']} rounds.")
    except asyncio.CancelledError:
        logger.info("Simulation loop cancelled")
    except Exception as e:
        logger.error(f"Simulation error after restart: {e}", exc_info=True)


def main():
    parser = argparse.ArgumentParser(
        description="Run KBZ simulation with Big Brother viewer",
        epilog="""
Examples:
  # Run 10 rounds with Anthropic (default)
  python -m agents.run_with_viewer

  # Long continuous simulation with local Ollama model
  python -m agents.run_with_viewer --backend ollama --model gemma4:26b --rounds 0 --delay 3

  # Ollama with custom context window and temperature
  python -m agents.run_with_viewer --backend ollama --model gemma4:26b --rounds 100 \\
      --ollama-ctx 16384 --ollama-temp 0.6
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--rounds", type=int, default=10,
                        help="Number of rounds (0 = continuous/infinite)")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="Delay between rounds in seconds (default: 2.0)")
    parser.add_argument("--backend", default="anthropic", choices=["anthropic", "ollama", "openrouter"],
                        help="LLM backend (default: anthropic)")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001",
                        help="LLM model name (e.g. gemma4:26b for Ollama)")
    parser.add_argument("--community-name", default="AI Kibbutz", help="Community name")
    parser.add_argument(
        "--members", type=int, default=6,
        metavar="N",
        help=f"Number of agents to start with (2–{MAX_MEMBERS}, default: 6). "
             "Uses the built-in YAML personas first; extra slots are filled with "
             "randomly generated agents.",
    )
    parser.add_argument(
        "--mission",
        default=None,
        help=(
            "Concrete briefing written onto the root ArtifactContainer so "
            "agents know what kind of content they should be producing. "
            "Defaults to the Kibbutznik Handbook briefing (see DEFAULT_MISSION)."
        ),
    )
    parser.add_argument("--host", default="0.0.0.0", help="Server host")
    parser.add_argument("--port", type=int, default=8000, help="Server port")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging (same as --log-level debug)")
    parser.add_argument("--log-level", default=None,
                        choices=["debug", "info", "warning", "error"],
                        help="Override console log level (default: info)")

    # Ollama-specific options
    ollama_group = parser.add_argument_group("Ollama options (only used with --backend ollama)")
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
    ollama_group.add_argument("--ollama-think", action="store_true", default=False,
                              help="Enable thinking mode for Ollama models that support it (e.g. qwen3)")
    parser.add_argument(
        "--reset-db",
        action="store_true",
        help="Drop and recreate all database tables before starting (fresh slate).",
    )
    args = parser.parse_args()

    verbose = args.verbose or (args.log_level == "debug")
    setup_logging(verbose)
    log = logging.getLogger("simulation")

    if getattr(args, "reset_db", False):
        import asyncio as _asyncio
        from kbz.database import engine as _engine
        from kbz.models import Base as _Base  # noqa: F401 — ensures all models are registered

        async def _reset():
            async with _engine.begin() as conn:
                await conn.run_sync(_Base.metadata.drop_all)
                await conn.run_sync(_Base.metadata.create_all)

        _asyncio.run(_reset())
        log.info("Database reset: all tables dropped and recreated.")

    if args.rounds == 0:
        log.info("Running in CONTINUOUS mode (rounds=0). Press Ctrl+C to stop.")

    personas = build_persona_list(args.members)
    mission = args.mission if args.mission is not None else DEFAULT_MISSION

    def _make_orchestrator(n_members: int) -> Orchestrator:
        """Build an Orchestrator with the current args but a fresh persona list."""
        return Orchestrator(
            community_name=args.community_name,
            mission=mission,
            api_url=f"http://localhost:{args.port}",
            llm_backend=args.backend,
            llm_model=args.model,
            personas=build_persona_list(n_members),
            ollama_timeout=args.ollama_timeout,
            ollama_num_ctx=args.ollama_ctx,
            ollama_temperature=args.ollama_temp,
            ollama_num_predict=args.ollama_max_tokens,
            max_retries=args.retries,
            ollama_think=args.ollama_think,
        )

    orch = _make_orchestrator(args.members)
    set_orchestrator(orch)

    viewer_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "viewer")

    # Import the artifact cascade machinery so accepted/rejected parent
    # EditArtifact proposals flip their originating sub-action container
    # back to COMMITTED/OPEN the same way main.py does.
    from kbz.database import async_session
    from kbz.services.artifact_service import ArtifactService
    from kbz.services.event_bus import event_bus

    async def _artifact_cascade_loop() -> None:
        import uuid as _uuid
        queue = event_bus.subscribe()
        try:
            while True:
                event = await queue.get()
                if event.event_type not in ("proposal.accepted", "proposal.rejected"):
                    continue
                proposal_id_str = event.data.get("proposal_id")
                if not proposal_id_str:
                    continue
                try:
                    proposal_id = _uuid.UUID(str(proposal_id_str))
                except (ValueError, TypeError):
                    continue
                try:
                    async with async_session() as session:
                        svc = ArtifactService(session)
                        if event.event_type == "proposal.accepted":
                            await svc.on_parent_proposal_accepted(proposal_id)
                        else:
                            await svc.on_parent_proposal_rejected(proposal_id)
                        await session.commit()
                except Exception as e:
                    logging.getLogger("simulation").exception(
                        "Artifact cascade handler failed: %s", e
                    )
        except asyncio.CancelledError:
            event_bus.unsubscribe(queue)
            raise

    # Mutable container so the restart closure can update the task reference
    _sim_state: dict = {"task": None}  # tracks the running simulation task

    async def _restart():
        """Cancel the running simulation loop and relaunch it with the same
        orchestrator and existing data.  Nothing is wiped — agents, community,
        proposals and history are all preserved."""
        log = logging.getLogger("simulation")
        log.info("=== RESTARTING SIMULATION LOOP (data preserved) ===")

        # 1. Cancel the running sim task
        task = _sim_state["task"]
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        log.info("Old simulation loop stopped.")

        # 2. Resume from where we left off — same orchestrator, same community.
        #    Reset the paused flag so the loop doesn't stall immediately.
        orch.resume()

        # 3. Relaunch the loop
        new_task = asyncio.create_task(
            _resume_simulation(orch, rounds=args.rounds, delay=args.delay)
        )
        _sim_state["task"] = new_task
        log.info("Simulation loop restarted (round %d).", orch._round)

    # TKG ingestor — owns a subscription to the event_bus and writes
    # nodes/edges/embeddings as governance events happen. Started inside the
    # lifespan so it dies cleanly with the app.
    from kbz.database import async_session
    from kbz.services.tkg_ingestor import TKGIngestor
    from agents.bot_runner import BotRunner

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        cascade_task = asyncio.create_task(_artifact_cascade_loop())
        tkg_ingestor = TKGIngestor(async_session)
        await tkg_ingestor.start()
        # Bots delegated by humans run alongside the sim, with the same
        # LLM engine. They poll the DB for active BotProfiles and take
        # turns on behalf of their owning users.
        bot_runner = BotRunner(
            session_factory=async_session,
            engine=orch.engine,
            api_base_url="http://localhost:8000",
        )
        await bot_runner.start()
        sim_task = asyncio.create_task(run_simulation(orch, rounds=args.rounds, delay=args.delay))
        _sim_state["task"] = sim_task
        set_restart_callback(_restart)
        try:
            yield
        finally:
            cascade_task.cancel()
            try:
                await cascade_task
            except asyncio.CancelledError:
                pass
            try:
                await bot_runner.stop()
            except Exception:
                log.exception("BotRunner shutdown failed")
            try:
                await tkg_ingestor.stop()
            except Exception:
                log.exception("TKGIngestor shutdown failed")

    # Build the combined app with lifespan
    from kbz.routers import (
        actions,
        artifacts,
        auth,
        closeness,
        comments,
        communities,
        invites,
        me,
        members,
        memory,
        metrics,
        proposals,
        pulses,
        statements,
        tkg,
        users,
        wallet_webhook,
        wallets,
        ws,
    )
    combined_app = FastAPI(
        title="KBZ Big Brother",
        description="KBZ Governance + AI Agents + Big Brother Viewer",
        lifespan=lifespan,
    )
    combined_app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    combined_app.include_router(users.router, prefix="/users", tags=["users"])
    combined_app.include_router(communities.router, prefix="/communities", tags=["communities"])
    combined_app.include_router(members.router, tags=["members"])
    combined_app.include_router(proposals.router, tags=["proposals"])
    combined_app.include_router(pulses.router, tags=["pulses"])
    combined_app.include_router(statements.router, tags=["statements"])
    combined_app.include_router(actions.router, tags=["actions"])
    combined_app.include_router(comments.router, tags=["comments"])
    combined_app.include_router(closeness.router, tags=["closeness"])
    combined_app.include_router(artifacts.router)
    combined_app.include_router(memory.router, tags=["memory"])
    combined_app.include_router(tkg.router)
    combined_app.include_router(metrics.router)
    combined_app.include_router(auth.router)
    combined_app.include_router(invites.router)
    combined_app.include_router(me.router)
    combined_app.include_router(wallets.router)
    combined_app.include_router(wallet_webhook.router)
    combined_app.include_router(ws.router, tags=["websocket"])
    combined_app.include_router(sim_router)
    combined_app.mount("/viewer", StaticFiles(directory=viewer_dir, html=True), name="viewer")

    @combined_app.get("/health")
    async def health():
        return {"status": "ok"}

    uvicorn.run(combined_app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
