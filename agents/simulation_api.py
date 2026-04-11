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
    "or-mistral-small":     {"backend": "openrouter", "model": "mistralai/mistral-small-creative",         "think": False},
    "or-lunaris":           {"backend": "openrouter", "model": "sao10k/l3-lunaris-8b",               "think": False},
    "or-gemini-flash-lite": {"backend": "openrouter", "model": "google/gemini-2.5-flash-lite-preview","think": False},
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
