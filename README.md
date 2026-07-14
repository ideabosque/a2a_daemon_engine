# A2A Daemon Engine

**Package Version:** 0.0.1
**A2A SDK:** v1.0.2 (`a2a-sdk[http-server]`)
**Status:** Phases 1–9 complete; Phase 10 (gateway-mediated `ai_agent_core_engine` integration) planned
**Last Updated:** 2026-07-14

An Agent-to-Agent (A2A) protocol daemon engine module for the
**SilvaEngine Gateway** (`silvaengine_gateway`). It is **not a standalone
service** — it is loaded as a registered gateway module and all client access
flows through the gateway's HTTP, WebSocket, and SSE routes. The gateway
handles authentication, routing, SSE client management, and dispatch; this
module provides the A2A protocol logic, agent execution, task persistence,
and LLM handler bridging.

Built on the official
[A2A SDK](https://github.com/a2aproject/a2a-samples) server pattern, with
dual-backend persistence (DynamoDB + PostgreSQL), multi-tenant isolation,
and pluggable LLM handler integration.

## How It Integrates with the Gateway

The daemon is registered as a **SilvaEngine Gateway module** via `deploy()` in
`main.py`, which declares three gateway-facing entry points:

| Entry Point | Gateway Route | Method | Purpose |
|-------------|---------------|--------|---------|
| `a2a_core_graphql` | `POST /{ep}/a2a_core_graphql` | POST | GraphQL CRUD for agents, tasks, messages, settings |
| `a2a` | `POST /{ep}/a2a` | POST | A2A JSON-RPC protocol (`message/send`, `message/stream`, `tasks/get`, `tasks/cancel`) |
| `sse_message` | `POST /{ep}/sse` | POST | A2A JSON-RPC + push activity to SSE clients (partition-scoped) |

The gateway additionally exposes:

| Gateway Route | Method | Purpose |
|---------------|--------|---------|
| `GET /{ep}/sse` | GET | SSE stream — gateway-managed `sse_manager` delivers live A2A task events to connected clients |
| `GET /{ep}/a2a_sse` | GET | A2A-specific SSE stream (alias) |

**The daemon does not listen on its own port or run its own HTTP server in
production.** All transport, auth, and SSE client lifecycle is owned by
`silvaengine_gateway`. The daemon's internal SDK Starlette app and FastAPI
operations app exist for local development and testing only.

### Architecture — Gateway Dispatch Flow

```
A2A Client
    │
    ▼ POST /{ep}/a2a  (message/send | message/stream)
silvaengine_gateway  (auth, routing, SSE client mgmt)
    │
    ▼ dispatch → A2ADaemonEngine.a2a(**params)
A2ADaemonExecutor
    │
    ├── Non-streaming → handler.ask_model(input_messages, context)
    │       └── GraphQL → ai_agent_core_engine → final_output
    │
    └── Streaming → handler.ask_model(..., stream_queue, stream_event)
            └── WebSocket → ai_agent_core_engine → chunk_delta frames
                    └── Dual-path: SDK EventQueue + sse_manager.broadcast_to_partition()

Client receives:
    • Non-streaming: single JSON-RPC response with A2A Message
    • Streaming: live SSE events via GET /{ep}/sse (gateway-managed)
```

### Local Development Standalone Mode

For local development and testing, the daemon *can* run as a standalone HTTP
server using the internal SDK Starlette app:

| Route | Method | Purpose |
|-------|--------|---------|
| `/.well-known/agent-card.json` | GET | Public Agent Card (protocol version `1.0.0`) |
| `/` | POST | JSON-RPC compatibility endpoint (slash-style: `message/send`, `tasks/get`, `tasks/cancel`) |
| `/v1` | POST | SDK native JSON-RPC dispatcher (`SendMessage`, `GetTask`, `CancelTask`) |
| `/tasks/{task_id}/stream` | GET | SSE task streaming with `Last-Event-ID` replay buffer |
| `/rest/health` | GET | Health check |
| `/rest/{endpoint_id}/a2a_core_graphql` | POST | GraphQL API for agents, tasks, messages, settings |

This mode is for development only — production deployments go through the
gateway.

## Key Features

- **A2A SDK v1.0** — JSON-RPC over HTTP (slash-style at `/`, native at `/v1`)
- **Gateway module** — registered via `deploy()`, dispatched by `silvaengine_gateway`
- **Public Agent Card** at `/.well-known/agent-card.json` with ETag / Last-Modified
- **SDK AgentExecutor** integration (`A2ADaemonExecutor`)
- **Dual-backend persistence** — DynamoDB (PynamoDB) or PostgreSQL (SQLAlchemy + Alembic)
- **Multi-tenant isolation** via composite partition keys (`{endpoint_id}#{part_id}`)
- **SSE streaming** — gateway-managed `sse_manager` delivers live tokens to connected clients
- **GraphQL operations API** for CRUD on agents, tasks, messages, settings
- **Dual authentication** — local JWT (HS256) and AWS Cognito (RS256 + JWKS)
- **Pluggable LLM handlers** — per-agent `module_name` / `class_name` selection
- **Hermes Agent bridge** — route A2A tasks to Hermes Agent API Server via HTTP + SSE
- **Gateway-mediated core engine bridge** (Phase 10, planned) — GraphQL for non-streaming, WebSocket for streaming
- **Production hardening** — rate limiting, health monitoring, circuit breakers, OpenTelemetry
- **Experimental gRPC transport** — JSON-over-gRPC with bidirectional streaming
- **AWS Lambda support** — serverless JSON-RPC dispatch via `A2ADaemonEngine.a2a(**event)`

## Project Structure

```
a2a_daemon_engine/
├── a2a_daemon_engine/
│   ├── main.py                          # A2ADaemonEngine — gateway module entry, deploy(), serverless dispatch
│   ├── schema.py                        # GraphQL schema (Query, Mutations, types)
│   ├── handlers/
│   │   ├── config.py                    # Config singleton — backend, auth, Phase 10 settings
│   │   ├── a2a_server.py                # Builds SDK Starlette app + DefaultRequestHandler
│   │   ├── a2a_executor.py              # A2ADaemonExecutor(AgentExecutor) — request routing
│   │   ├── a2a_handlers.py              # Business handlers (delivery, retry, routing)
│   │   ├── a2a_jsonrpc_bridge.py         # JSON-RPC → SDK request normalization
│   │   ├── a2a_taskstore.py              # DynamoDBA2ATaskStore (SDK TaskStore)
│   │   ├── a2a_sse.py                   # SSEEventQueue, StreamingTaskManager, stream routes
│   │   ├── sse_manager.py               # Gateway-compatible SSE client manager
│   │   ├── a2a_core.py                  # GraphQL handler for agents/tasks/messages/settings
│   │   ├── a2a_utility.py              # DynamoDB query/mutation helpers
│   │   ├── a2a_ai_agent_utility.py      # Phase 10 bridge utility (agent resolution, streaming)
│   │   ├── a2a_hermes_handler.py        # Hermes Agent bridge plugin (HTTP + SSE)
│   │   ├── a2a_extended_card.py          # Extended agent card with auth gating
│   │   ├── a2a_telemetry.py             # OpenTelemetry instrumentation (optional)
│   │   ├── a2a_health_monitor.py        # Health monitoring + circuit breakers
│   │   ├── a2a_rate_limiter.py         # Token-bucket rate limiting
│   │   ├── a2a_cancellation.py          # Task cancellation propagation
│   │   ├── a2a_pushconfig.py           # Push notification config + SSRF allowlist
│   │   ├── a2a_cost_extension.py       # Cost/quota tracking (scaffold)
│   │   ├── a2a_secure_passport.py      # Secure passport (scaffold)
│   │   ├── a2a_grpc.py                 # gRPC transport (experimental)
│   │   ├── a2a_graphql_subscriptions.py # WebSocket GraphQL subscriptions
│   │   ├── middleware.py               # FlexJWTMiddleware (local + Cognito)
│   │   ├── auth_router.py              # OAuth2 /auth/token endpoint
│   │   ├── jwt_local.py                # Local JWT (HS256)
│   │   ├── jwt_cognito.py             # Cognito JWT (RS256 + JWKS)
│   │   └── schema.py                  # GraphQL schema wiring
│   ├── models/
│   │   ├── dynamodb/                   # PynamoDB models (agent, task, message, setting)
│   │   ├── postgresql/                 # SQLAlchemy ORM models + base
│   │   └── repositories/
│   │       ├── base.py                 # EntityRepository abstract base
│   │       ├── dispatch.py             # get_repo() — backend dispatch
│   │       ├── dynamodb/               # DynamoDB repository implementations
│   │       └── postgresql/             # PostgreSQL repository implementations
│   ├── mutations/                      # GraphQL mutations (agent, task, message, setting)
│   ├── queries/                        # GraphQL queries (agent, task, message, setting)
│   ├── types/                          # GraphQL types (agent, task, message, setting)
│   ├── utils/                          # Exceptions, normalization helpers
│   └── tests/                          # Test suite (see Testing below)
├── migration/
│   ├── alembic.ini                     # Alembic config (PostgreSQL)
│   └── alembic/versions/               # 4 migrations (agents, tasks, messages, settings)
├── docs/                               # Architecture, protocol, development plan, integration
├── AGENTS.md                           # Contributor reference
├── pyproject.toml                      # Poetry config, deps, ruff, pytest
└── README.md                           # This file
```

## Installation

### Prerequisites

- Python 3.10–3.12
- Poetry (or pip)
- `silvaengine_gateway` (for production deployment)

### Setup

```bash
# Clone the repository
git clone https://github.com/ideabosque/a2a_daemon_engine.git
cd a2a_daemon_engine

# Install with Poetry
poetry install

# Or install with pip (including dev deps)
pip install -e .[dev]

# Optional extras
pip install -e .[grpc]          # gRPC transport
pip install -e .[telemetry]    # OpenTelemetry
pip install -e .[postgresql]   # PostgreSQL backend (SQLAlchemy + psycopg2 + Alembic)
pip install -e .[all]           # All extras
```

### PostgreSQL Backend Setup

```bash
# Set backend
export DB_BACKEND=postgresql

# Run migrations
alembic -c migration/alembic.ini upgrade head
```

## Configuration

Configuration is injected by the gateway at module initialization time
(`Config.initialize(logger, **setting)`). The following settings are recognized:

```bash
# Backend selection: "dynamodb" (default) or "postgresql"
export DB_BACKEND=dynamodb

# AWS (DynamoDB backend)
export REGION_NAME=us-east-1
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret

# PostgreSQL backend (when DB_BACKEND=postgresql)
# Configure connection in alembic.ini or via DATABASE_URL

# Authentication (used by local dev server; gateway handles auth in production)
export AUTH_PROVIDER=local
# Required for AUTH_PROVIDER=local. Must be ≥ 32 chars and not a default/weak
# value (e.g. CHANGEME, secret, password).
export JWT_SECRET_KEY=replace-me-with-a-strong-random-secret-of-32-or-more-chars

# Phase 10: ai_agent_core_engine bridge (planned — gateway-mediated)
export A2A_DEFAULT_AGENT_UUID=a2a-default-agent
export A2A_STREAM_TIMEOUT=120.0
export A2A_STREAMING_ENABLED=true

# Phase 10: Hermes Agent bridge
export HERMES_API_URL=http://localhost:8642
export HERMES_API_KEY=your-api-key
export HERMES_MODEL=hermes-agent
export HERMES_STREAM_TIMEOUT=300
```

## Deployment

### Production — As a Gateway Module

In production, the daemon is loaded by `silvaengine_gateway` as a registered
module. The gateway owns the HTTP listener, WebSocket connections, SSE client
lifecycle, authentication, and routing. The daemon's `deploy()` function
declares its entry points (`a2a`, `a2a_core_graphql`, `sse_message`) and the
gateway dispatches incoming requests to them.

To register the module with the gateway, add it to the gateway's module
configuration and ensure the package is importable in the gateway's Python
environment.

### AWS Lambda (Serverless)

`A2ADaemonEngine.a2a(**event)` accepts JSON-RPC 2.0 dictionaries only —
non-JSON-RPC payloads are rejected. The bridge in `a2a_jsonrpc_bridge.py`
normalizes the request into SDK protobuf types and dispatches to
`DefaultRequestHandler`.

```python
from a2a_daemon_engine.main import A2ADaemonEngine

daemon = A2ADaemonEngine(logger, transport="lambda", port=0, ...)

def lambda_handler(event, context):
    # event must be a JSON-RPC 2.0 dictionary
    return daemon.a2a(**event)
```

## Local Development

### Run the Standalone Dev Server

For local development, the daemon can run its own HTTP server with the internal
SDK Starlette app and FastAPI operations app:

```bash
# Via Poetry
poetry run python -m a2a_daemon_engine.main

# Or via the dev script
python a2a_daemon_engine/tests/start_daemon.py
```

The dev server listens on `http://0.0.0.0:${PORT:-8001}`.

### Verify (Local Dev Server)

```bash
# Health check
curl http://localhost:8001/rest/health

# Agent Card
curl http://localhost:8001/.well-known/agent-card.json

# Compatibility endpoint (slash-style methods)
curl -X POST http://localhost:8001/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "message/send", "params": {"message": {"role": "user", "parts": [{"text": "hello"}]}}, "id": 1}'

# SDK native dispatcher
curl -X POST http://localhost:8001/v1 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "SendMessage", "params": {"message": {"role": "user", "parts": [{"text": "hello"}]}}, "id": 1}'

# SSE task stream (replace TASK_ID with a real task identifier)
curl -N http://localhost:8001/tasks/TASK_ID/stream
```

### Verify (Via Gateway)

When deployed behind `silvaengine_gateway`, the same operations are available
through gateway routes (replace `<gateway-host>:<port>` and `<endpoint_id>`):

```bash
# A2A JSON-RPC
curl -X POST http://<gateway-host>:<port>/<endpoint_id>/a2a \
  -H "Content-Type: application/json" \
  -H "Part-ID: <tenant_id>" \
  -d '{"jsonrpc": "2.0", "method": "message/send", "params": {"message": {"role": "user", "parts": [{"text": "hello"}]}}, "id": 1}'

# GraphQL
curl -X POST http://<gateway-host>:<port>/<endpoint_id>/a2a_core_graphql \
  -H "Content-Type: application/json" \
  -H "Part-ID: <tenant_id>" \
  -d '{"query": "{ a2aAgentList { agent_id name } }"}'

# SSE stream
curl -N http://<gateway-host>:<port>/<endpoint_id>/sse
```

## Authentication

In **production**, the gateway handles authentication before dispatching to
the daemon. The daemon's own JWT middleware (`FlexJWTMiddleware`) is used only
in local development / standalone mode.

Two providers are supported, selected at startup with `AUTH_PROVIDER`:

| Provider | Algorithm | Required env vars |
|---|---|---|
| `local` | HS256 | `JWT_SECRET_KEY` (≥ 32 chars, must not be a default/weak value); optional `LOCAL_USER_FILE`, `ADMIN_STATIC_TOKEN` |
| `cognito` | RS256 + JWKS | `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`, `COGNITO_APP_SECRET`, optional `COGNITO_JWKS_URL` |

Local users are obtained via `POST /rest/auth/token` (OAuth2 password grant,
form-encoded). Public protocol routes (`/`, `/v1`, `/.well-known/...`,
`/tasks/{task_id}/stream`) are not gated by `FlexJWTMiddleware`.

## Multi-Tenancy

All persistence uses a composite partition key:

```
partition_key = "{endpoint_id}#{part_id}"
```

`endpoint_id` is taken from URL paths like `/{endpoint_id}/a2a_core_graphql`;
`part_id` is read from the `Part-ID` request header (or query parameter when
that path component is absent). The gateway passes `partition_key` into the
dispatch call, and all data access flows through this composite key, so
cross-tenant access is hard-isolated at the row level.

## Dual-Backend Persistence

The daemon supports two database backends, selected via `DB_BACKEND`:

| Backend | Config | Models | Migrations |
|---|---|---|---|
| **DynamoDB** (default) | `DB_BACKEND=dynamodb` | `models/dynamodb/` (PynamoDB) | Auto-create on startup |
| **PostgreSQL** | `DB_BACKEND=postgresql` | `models/postgresql/` (SQLAlchemy) | Alembic (`migration/alembic/`) |

Repository selection is dispatched at runtime via `models/repositories/dispatch.py`
→ `get_repo(entity_type)`, which lazily initializes the appropriate repository
based on `Config.DB_BACKEND`.

## Pluggable LLM Handlers

Agent execution is data-driven via per-agent `module_name` and `class_name` in
the agent registry. The executor loads the handler dynamically and calls
`ask_model()` with the same contract regardless of backend.

### Hermes Agent Handler

Routes A2A tasks to a running Hermes Agent API Server via HTTP + SSE:

```
module_name: "a2a_daemon_engine.handlers.a2a_hermes_handler"
class_name:  "HermesAgentHandler"
```

Per-agent metadata: `hermes_api_url`, `hermes_api_key`, `hermes_model`,
`hermes_timeout`. See [Hermes Integration Guide](docs/HERMES_INTEGRATION.md)
for full setup, A2A state mapping, and E2E test instructions.

### Core Engine Handler (Phase 10 — Planned)

Gateway-mediated integration with `ai_agent_core_engine`:

- **Non-streaming** (`SendMessage`) → GraphQL mutation to
  `POST /{ep}/ai_agent_core_graphql`
- **Streaming** (`SendStreamingMessage`) → WebSocket to
  `/{ep}/ai_agent_core_ws` with `chunk_delta` frames
- **Client-facing streaming** stays on SSE (`GET /{ep}/sse` — gateway-managed)

See [Development Plan — Phase 10](docs/A2A_DEVELOPMENT_PLAN.md#phase-10-gateway-mediated-ai_agent_core_engine-integration)
for the full design.

## Testing

### Run Tests

```bash
# All unit + skipped-live tests
python -m pytest a2a_daemon_engine/tests -q

# Live API tests (requires running dev daemon)
# On bash:
A2A_RUN_LIVE_API_TESTS=1 python -m pytest a2a_daemon_engine/tests/test_api.py -q
# On PowerShell:
$env:A2A_RUN_LIVE_API_TESTS='1'; python -m pytest a2a_daemon_engine/tests/test_api.py -q

# With coverage
python -m pytest --cov=a2a_daemon_engine --cov-report=html

# By marker
python -m pytest -m unit
python -m pytest -m "graphql or cache"
python -m pytest -m integration
```

### Test Files

| File | Coverage |
|------|---------|
| `test_phase6.py` | A2A SDK v1.0 upgrade (state migration, enums, pagination) |
| `test_phase8.py` | Production hardening, SSE infrastructure, security |
| `test_phase9.py` | Advanced extensions (gRPC, subscriptions, health, rate limit) |
| `test_phase10.py` | Phase 10 bridge (agent resolution, streaming, persistence) |
| `test_hermes_handler.py` | Hermes Agent handler (mocked HTTP via `httpx.MockTransport`) |
| `test_executor_unit.py` | Executor unit tests |
| `test_handlers_unit.py` | Business handler unit tests |
| `test_a2a_jsonrpc_bridge.py` | JSON-RPC bridge normalization |
| `test_jwt_validation.py` | JWT validation (local + Cognito) |
| `test_api.py` | Live API integration tests |
| `test_postgresql_backend.py` | PostgreSQL backend tests |
| `test_helpers.py` | Fixture support for live-API suites |

### Test Markers

`unit`, `integration`, `slow`, `a2a`, `agent`, `task`, `message`, `setting`,
`server`, `graphql`, `cache`, `performance`

### Runnable Scripts (not pytest)

- `a2a_tck_checker.py` — A2A TCK compliance checker
- `a2a_rpc_verifier.py` — JSON-RPC protocol verifier
- `validate_agent_card.py` — Agent Card validator
- `register_hermes_agent.py` — Sample Hermes agent registration

## Lint and Format

```bash
# Ruff (line length 88, target py310)
python -m ruff check a2a_daemon_engine/

# Black
python -m black a2a_daemon_engine/

# Mypy (ignore_missing_imports = true)
python -m mypy a2a_daemon_engine/
```

## Development Phases

| Phase | Theme | Status |
|-------|-------|--------|
| 1–3 | Core SDK alignment (AgentExecutor, TaskStore, async GraphQL) | ✅ Complete |
| 4 | Server restructuring (SDK app primary, FastAPI at `/rest`) | ✅ Complete |
| 5 | Event-driven message delivery (retry + status tracking) | ✅ Complete |
| 6 | A2A SDK v1.0 upgrade (state migration, enums, pagination) | ✅ Complete |
| 7 | Streaming and multi-turn (SSE, INPUT_REQUIRED, AUTH_REQUIRED) | ✅ Complete |
| 8 | Production hardening (extended cards, telemetry, TCK, security) | ✅ Complete |
| 9 | Advanced extensions (gRPC, subscriptions, health, rate limit) | ✅ Complete |
| 10 | Gateway-mediated `ai_agent_core_engine` integration (GraphQL + WebSocket) | 📋 Planned |

See the [Development Plan](docs/A2A_DEVELOPMENT_PLAN.md) for full details.

## Documentation

| Document | Purpose |
|----------|---------|
| [Architecture](docs/A2A_ARCHITECTURE.md) | HTTP surface table, runtime components, request-flow sequence diagram |
| [Protocol Call Flow](docs/A2A_PROTOCOL_CALL_FLOW.md) | Per-method call paths for `message/send`, `tasks/get`, `tasks/cancel` |
| [Development Plan](docs/A2A_DEVELOPMENT_PLAN.md) | Phases 1–10, implementation notes, release gates |
| [Test Plan](docs/A2A_TEST_PLAN.md) | Unit / live / release-gate coverage |
| [Integration Test Plan](docs/INTEGRATION_TEST_PLAN.md) | End-to-end / integration playbook |
| [Integration Scenarios SOP](docs/INTEGRATION_SCENARIOS_SOP.md) | Integration scenarios standard operating procedure |
| [Hermes Integration](docs/HERMES_INTEGRATION.md) | Hermes Agent bridge setup, state mapping, E2E tests |
| [Hermes Bridge Dev Plan](docs/HERMES_A2A_BRIDGE_DEVELOPMENT_PLAN.md) | Hermes bridge development plan |
| [Protocol Analysis](docs/a2a-protocol-analysis.md) | Protocol background and design suggestions |
| [Documentation Index](docs/DOCUMENTATION_INDEX.md) | Documentation navigation guide |
| [AGENTS.md](AGENTS.md) | Contributor reference (environment, key files, JSON-RPC rules) |
| [Tests README](a2a_daemon_engine/tests/README.md) | Test suite overview and coverage |

## License

MIT