"""
FastAPI router for managing simulations and interviewing agents.
Mount this on the main app when running simulations.
"""
import asyncio
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Callable, Awaitable

from agents.orchestrator import Orchestrator

router = APIRouter(prefix="/simulation", tags=["simulation"])
log = logging.getLogger("simulation")

# ── Global state ────────────────────────────────────────
_orchestrator: Orchestrator | None = None

# Restart callback registered by run_with_viewer at startup.
# Signature: async () -> None
_restart_callback: Callable[[], Awaitable[None]] | None = None

# Track restarting state so the viewer can show a spinner
_restarting: bool = False


def set_orchestrator(orch: Orchestrator):
    global _orchestrator
    _orchestrator = orch


def set_restart_callback(cb: Callable[[], Awaitable[None]]):
    global _restart_callback
    _restart_callback = cb


def get_orchestrator() -> Orchestrator:
    if _orchestrator is None:
        raise HTTPException(status_code=503, detail="No simulation running")
    return _orchestrator


# ── Models ──────────────────────────────────────────────

class InterviewRequest(BaseModel):
    agent_name: str
    question: str


class InterviewResponse(BaseModel):
    agent_name: str
    answer: str


class ChatMessageRequest(BaseModel):
    message: str
    community_id: str | None = None  # target community; defaults to root


class LLMSwitchRequest(BaseModel):
    preset: str


# ── Status / agents ─────────────────────────────────────

@router.get("/status")
async def simulation_status():
    if _restarting:
        return {"restarting": True, "round": 0, "paused": False, "total_events": 0}
    orch = get_orchestrator()
    return await orch.get_status()


@router.get("/agents")
async def list_agents():
    orch = get_orchestrator()
    return [
        {
            "name": a.persona.name,
            "role": a.persona.role,
            "user_id": a.user_id,
            "background": a.persona.background,
            "traits": {
                "openness": a.persona.traits.openness,
                "cooperation": a.persona.traits.cooperation,
                "initiative": a.persona.traits.initiative,
                "patience": a.persona.traits.patience,
                "loyalty": a.persona.traits.loyalty,
                "social_energy": a.persona.traits.social_energy,
                "confrontation": a.persona.traits.confrontation,
            },
            "eagerness": a.eagerness,
            "eager_front": a.eager_front,
            "rounds_since_acted": a.rounds_since_acted,
            "actions_taken": len(a.action_history),
            "recent_actions": [
                {
                    "action": log.action_type,
                    "details": log.details,
                    "reason": log.reason,
                    "time": log.timestamp.isoformat(),
                    "eagerness": log.eagerness,
                    "eager_front": log.eager_front,
                }
                for log in a.action_history[-10:]
            ],
        }
        for a in orch.agents
    ]


# ── Control ──────────────────────────────────────────────

@router.post("/pause")
async def pause_simulation():
    orch = get_orchestrator()
    orch.pause()
    return {"paused": True}


@router.post("/resume")
async def resume_simulation():
    orch = get_orchestrator()
    orch.resume()
    return {"paused": False}


@router.post("/restart")
async def restart_simulation():
    """Stop the running simulation, wipe the DB, and start fresh — same config, no server restart."""
    global _restarting
    if _restarting:
        raise HTTPException(status_code=409, detail="Restart already in progress")
    if _restart_callback is None:
        raise HTTPException(status_code=503, detail="Restart not available (no callback registered)")
    _restarting = True
    log.info("Restart requested via API")
    # Run the restart asynchronously so the HTTP response can return immediately
    asyncio.create_task(_do_restart())
    return {"status": "restarting"}


async def _do_restart():
    global _restarting
    try:
        await _restart_callback()
    except Exception as e:
        log.error("Restart failed: %s", e, exc_info=True)
    finally:
        _restarting = False


# ── Interview ────────────────────────────────────────────

@router.post("/interview", response_model=InterviewResponse)
async def interview_agent(req: InterviewRequest):
    orch = get_orchestrator()
    answer = await orch.interview_agent(req.agent_name, req.question)
    return InterviewResponse(agent_name=req.agent_name, answer=answer)


# ── Chat ─────────────────────────────────────────────────

@router.post("/chat")
async def post_chat(req: ChatMessageRequest):
    """Post a message to the community chat as Big Brother (the viewer operator)."""
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    orch = get_orchestrator()
    result = await orch.post_chat(req.message.strip(), community_id=req.community_id)
    return result


# ── LLM switcher ─────────────────────────────────────────

LLM_PRESETS = {
    "claude-haiku":         {"backend": "anthropic",  "model": "claude-haiku-4-5-20251001",          "think": False},
    "ollama-gemma4":        {"backend": "ollama",     "model": "gemma4:26b",                         "think": False},
    "ollama-gemma4-e4b":    {"backend": "ollama",     "model": "gemma4:e4b",                         "think": False},
    "ollama-qwen3":         {"backend": "ollama",     "model": "qwen3:8b",                           "think": False},
    "ollama-qwen3-think":   {"backend": "ollama",     "model": "qwen3:8b",                           "think": True},
    # OpenRouter retired `mistralai/mistral-small-creative` (404s on
    # all calls as of 2026-04-30 — see commit history). Switched to
    # the current dated alias `mistral-small-2603` (March 2026 build),
    # which is the live successor on the OpenRouter catalog.
    "or-mistral-small":     {"backend": "openrouter", "model": "mistralai/mistral-small-2603",               "think": False},
    # Mistral Small 3.2 24B — same generation as the retired
    # `mistral-small-creative` (Mistral Small 3 family). First fallback
    # if M4 (`mistral-small-2603`) feels too flat / general-purpose.
    "or-mistral-small-3.2": {"backend": "openrouter", "model": "mistralai/mistral-small-3.2-24b-instruct",   "think": False},
    # TheDrummer's Cydonia 24B v4.1 — community creative-writing /
    # roleplay fine-tune of Mistral Small 3 24B. Spiritual successor
    # to the retired `mistral-small-creative` for persona-driven bots.
    "or-cydonia-24b":       {"backend": "openrouter", "model": "thedrummer/cydonia-24b-v4.1",                "think": False},
    # `or-lunaris` (sao10k/l3-lunaris-8b) was tested over 6 prompt-
    # tuning cycles in 2026-04-30. The model is a creative-writing
    # fine-tune that hallucinates ids ("12345678", "P-7f3a91c4")
    # rather than copying real ones from state — even when a literal
    # ready-to-paste JSON object sits adjacent in the prompt. After
    # 6 cycles the community produced 0 statements / 0 actions / 0
    # non-Membership Accepted. Capability ceiling, not a prompting
    # problem. Removed from the preset list to prevent accidental
    # reuse; if you want to re-test, re-add the line — the prefix
    # system + SUPPORT QUEUE branch on `lunaris-tuning` is preserved.
    "or-gemini-flash-lite": {"backend": "openrouter", "model": "google/gemini-2.5-flash-lite-preview","think": False},
    # MiniMax M2.5 — OpenRouter "free" tier, no credit usage (rate-limited
    # but adequate for sustained bot simulation). Smoke-tested on the
    # JSON-action prompt format and produces the exact shape we need.
    # Useful as a fallback when the OpenRouter account runs out of paid
    # credits.
    "or-minimax-free":      {"backend": "openrouter", "model": "minimax/minimax-m2.5:free",                  "think": False},
    # OpenAI gpt-oss-20b via OpenRouter's :nitro lane — same weights
    # as the local Ollama gpt-oss:20b but routed through providers
    # tuned for low latency. Useful for 100-bot simulations where
    # sequential per-turn latency dominates wall time.
    "or-gpt-oss-20b-nitro": {"backend": "openrouter", "model": "openai/gpt-oss-20b:nitro",          "think": False},
}


@router.get("/llm")
async def get_llm():
    orch = get_orchestrator()
    backend = orch.engine.backend
    model = orch.engine.model
    think = orch.engine.ollama_think
    preset = next(
        (k for k, v in LLM_PRESETS.items()
         if v["backend"] == backend and v["model"] == model and v.get("think", False) == think),
        "custom",
    )
    return {"preset": preset, "backend": backend, "model": model, "think": think, "presets": LLM_PRESETS}


@router.post("/llm")
async def set_llm(req: LLMSwitchRequest):
    orch = get_orchestrator()
    if req.preset not in LLM_PRESETS:
        raise HTTPException(status_code=400, detail=f"Unknown preset '{req.preset}'. Available: {list(LLM_PRESETS)}")
    cfg = LLM_PRESETS[req.preset]
    orch.set_llm(cfg["backend"], cfg["model"], ollama_think=cfg.get("think", False))
    return {"preset": req.preset, **cfg}


# ── Manual round ─────────────────────────────────────────

@router.post("/run-round")
async def run_one_round():
    orch = get_orchestrator()
    events = await orch.run_round()
    return {
        "round": orch._round,
        "events": [
            {
                "agent": e.agent_name,
                "action": e.action_type,
                "details": e.details,
                "reason": e.reason,
                "success": e.success,
                "time": e.timestamp.isoformat(),
            }
            for e in events
        ],
    }


@router.get("/agent-stats")
async def get_agent_stats():
    """Per-agent action breakdown for the metrics tab.

    Aggregates `orch.events` (the in-memory event log of the running
    simulation) by agent and action_type. Returns counts + success
    rate per agent so the UI can show "who did how much of what."
    """
    orch = get_orchestrator()
    stats: dict[str, dict] = {}
    for e in orch.events:
        s = stats.setdefault(e.agent_name, {
            "total": 0,
            "successes": 0,
            "failures": 0,
            "by_action": {},
        })
        s["total"] += 1
        s["by_action"][e.action_type] = s["by_action"].get(e.action_type, 0) + 1
        if e.success:
            s["successes"] += 1
        else:
            s["failures"] += 1
    # Stable order by total desc, then name.
    sorted_agents = sorted(
        stats.items(),
        key=lambda kv: (-kv[1]["total"], kv[0]),
    )
    return {
        "total_events": len(orch.events),
        "agents": [
            {
                "name": name,
                "total": s["total"],
                "successes": s["successes"],
                "failures": s["failures"],
                "success_rate": (s["successes"] / s["total"]) if s["total"] else 0.0,
                "by_action": s["by_action"],
            }
            for name, s in sorted_agents
        ],
    }


@router.get("/events")
async def get_events(limit: int = 50, offset: int = 0):
    orch = get_orchestrator()
    all_events = orch.events
    end = max(0, len(all_events) - offset)
    start = max(0, end - limit)
    events = list(reversed(all_events[start:end]))
    return {
        "total": len(all_events),
        "events": [
            {
                "agent": e.agent_name,
                "action": e.action_type,
                "details": e.details,
                "reason": e.reason,
                "success": e.success,
                "time": e.timestamp.isoformat(),
                "community_id": e.community_id,
                "ref_id": e.ref_id,
            }
            for e in events
        ],
    }
