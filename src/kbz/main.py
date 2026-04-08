from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from kbz.routers import communities, users, members, proposals, pulses, statements, actions, comments, ws

app = FastAPI(
    title="KBZ - Kibutznik Governance Platform",
    description="Pulse-based direct democracy governance API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(communities.router, prefix="/communities", tags=["communities"])
app.include_router(members.router, tags=["members"])
app.include_router(proposals.router, tags=["proposals"])
app.include_router(pulses.router, tags=["pulses"])
app.include_router(statements.router, tags=["statements"])
app.include_router(actions.router, tags=["actions"])
app.include_router(comments.router, tags=["comments"])
app.include_router(ws.router, tags=["websocket"])


@app.get("/health")
async def health():
    return {"status": "ok"}
