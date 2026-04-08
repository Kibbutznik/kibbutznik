# Stage 1: KBZ API Service

## Overview
The KBZ API implements the pulse-based direct democracy governance system defined in `KBZ_LOGIC.md`. It provides a RESTful API for managing communities, members, proposals, pulses, statements, actions, and comments.

## Tech Stack
- **Python 3.12** + **FastAPI** (async)
- **PostgreSQL 14** + **SQLAlchemy 2.0** (async ORM)
- **Alembic** for database migrations
- **pytest** + **httpx** for testing

## Quick Start

```bash
# Setup
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Database (requires PostgreSQL running)
createdb kbz
createdb kbz_test
alembic upgrade head

# Run server
uvicorn kbz.main:app --reload

# Run tests
pytest tests/ -v

# API docs (when server is running)
open http://localhost:8000/docs
```

## Architecture

```
Routers (HTTP endpoints) → Services (business logic) → Models (SQLAlchemy ORM)
                                    ↓
                              PostgreSQL DB
                                    ↓
                          Event Bus → WebSocket
```

All business logic lives in the **service layer** (`src/kbz/services/`). Routers are thin wrappers. The event bus emits governance events for real-time consumption.

## API Endpoints

### Users
| Method | Path | Description |
|--------|------|-------------|
| POST | `/users` | Create user |
| GET | `/users/{id}` | Get user profile |

### Communities
| Method | Path | Description |
|--------|------|-------------|
| POST | `/communities` | Create community (with founder + initial pulse) |
| GET | `/communities/{id}` | Get community details |
| GET | `/communities/{id}/variables` | Get governance variables |
| GET | `/communities/{id}/children` | Get child communities |
| GET | `/communities/{id}/members` | List active members |
| GET | `/communities/{id}/statements` | List active statements |
| GET | `/communities/{id}/actions` | List active actions |

### Proposals
| Method | Path | Description |
|--------|------|-------------|
| POST | `/communities/{id}/proposals` | Create proposal |
| GET | `/communities/{id}/proposals` | List proposals (optional `?status=` filter) |
| GET | `/proposals/{id}` | Get proposal details |
| PATCH | `/proposals/{id}/submit` | Move Draft → OutThere |
| POST | `/proposals/{id}/support` | Add support |
| DELETE | `/proposals/{id}/support/{user_id}` | Remove support |

### Pulses
| Method | Path | Description |
|--------|------|-------------|
| GET | `/communities/{id}/pulses` | List pulses |
| GET | `/pulses/{id}` | Get pulse details |
| POST | `/communities/{id}/pulses/support` | Add pulse support (auto-triggers when threshold met) |
| DELETE | `/communities/{id}/pulses/support/{user_id}` | Remove pulse support |

### Comments
| Method | Path | Description |
|--------|------|-------------|
| POST | `/entities/{type}/{entity_id}/comments` | Add comment |
| GET | `/entities/{type}/{entity_id}/comments` | Get comments (sorted by score) |
| POST | `/comments/{id}/score` | Update score (+1/-1) |

### WebSocket
| Path | Description |
|------|-------------|
| `ws://host/ws/events` | Real-time governance event stream |

## Governance Flow

```
1. Create Community → founder becomes member, initial Next pulse created
2. Submit Proposal → Draft → OutThere (gathering support)
3. Members Support → support count increments
4. Pulse Triggered → when enough members support the pulse:
   a. Active pulse proposals: accept (≥threshold) or reject
   b. Execute accepted proposals (add member, change variable, etc.)
   c. Move qualified OutThere → OnTheAir on new Active pulse
   d. Age remaining OutThere proposals, cancel if > MaxAge
   e. Increment all member seniority
   f. Create new Next pulse
```

## Proposal Types
All 14 types from KBZ_LOGIC.md are implemented:
`Membership`, `ThrowOut`, `AddStatement`, `RemoveStatement`, `ReplaceStatement`, `ChangeVariable`, `AddAction`, `EndAction`, `JoinAction`, `Funding`, `Payment`, `payBack`, `Dividend`, `SetMembershipHandler`

## Test Coverage
- **39 tests** covering all endpoints, services, and governance workflows
- Integration test: full community lifecycle (create → add members → statements → actions → variable changes → throw out)
- Edge cases: aging proposals, threshold calculations, duplicate prevention

## Key Files
- `src/kbz/services/pulse_service.py` — Pulse execution (heart of the system)
- `src/kbz/services/execution_service.py` — Proposal type dispatch
- `src/kbz/enums.py` — All enums, thresholds, default variables
- `src/kbz/services/event_bus.py` — Event emission for real-time viewer
