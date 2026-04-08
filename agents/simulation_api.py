"""
FastAPI router for managing simulations and interviewing agents.
Mount this on the main app when running simulations.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents.orchestrator import Orchestrator

router = APIRouter(prefix="/simulation", tags=["simulation"])

# Global simulation instance (set by the runner)
_orchestrator: Orchestrator | None = None


def set_orchestrator(orch: Orchestrator):
    global _orchestrator
    _orchestrator = orch


def get_orchestrator() -> Orchestrator:
    if _orchestrator is None:
        raise HTTPException(status_code=503, detail="No simulation running")
    return _orchestrator


class InterviewRequest(BaseModel):
    agent_name: str
    question: str


class InterviewResponse(BaseModel):
    agent_name: str
    answer: str


@router.get("/status")
async def simulation_status():
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


@router.post("/interview", response_model=InterviewResponse)
async def interview_agent(req: InterviewRequest):
    orch = get_orchestrator()
    answer = await orch.interview_agent(req.agent_name, req.question)
    return InterviewResponse(agent_name=req.agent_name, answer=answer)


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
    # Return newest events first so the viewer always gets fresh data
    # even when total events exceed the requested limit.
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
