# A2A Daemon Engine

**Package Version:** 0.0.1
**Status:** SDK v1.0 architecture in place; release validation pending
**Last Updated:** 2026-05-09

A dedicated Agent-to-Agent (A2A) protocol daemon engine for distributed agent
communication and multi-agent orchestration using the official A2A SDK server
pattern.

## Architecture

The SDK Starlette app is the primary HTTP application and owns the A2A protocol
surface:

- `GET /.well-known/agent-card.json`
- `POST /` — JSON-RPC compatibility endpoint accepting slash-style methods
  (`message/send`, `tasks/get`, `tasks/cancel`)
- `POST /v1` — SDK native JSON-RPC dispatcher (`SendMessage`, `GetTask`,
  `CancelTask`, with v0.3 compatibility enabled)
- `GET /tasks/{task_id}/stream`

The FastAPI app is mounted under `/rest` for operations only:

- `GET /rest/health`
- `GET /rest/me`
- `GET /rest/{endpoint_id}`
- `POST /rest/{endpoint_id}/a2a_core_graphql`

Removed legacy surfaces:

- `/rest/a2a-jsonrpc`
- `/rest/a2a/{endpoint_id}/...`
- direct `action=...` dispatch through `A2ADaemonEngine.a2a()`
- `handlers/a2a_jsonrpc.py`

## Key Features

- Native A2A SDK JSON-RPC at `/`
- Public Agent Card at `/.well-known/agent-card.json`
- SDK `AgentExecutor` integration
- DynamoDB-backed SDK `TaskStore`
- SSE task streaming and replay buffer support
- GraphQL operations API for daemon data
- JWT authentication for operations routes
- HTTP and experimental gRPC transports

## Installation

```bash
poetry install
```

## Configuration

```bash
export A2A_TRANSPORT=http
export PORT=8001
export REGION_NAME=us-east-1
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
export AUTH_PROVIDER=local
```

## Run

```bash
poetry run python -m a2a_daemon_engine.main
```

## Verify

```bash
curl http://localhost:8001/rest/health

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

## Python Client

```python
import requests

response = requests.post(
    "http://localhost:8001/",
    json={
        "jsonrpc": "2.0",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"text": "Hello"}],
            }
        },
        "id": 1,
    },
)
print(response.json())
```

## Authentication

The operations app under `/rest` is JWT-authenticated. Two providers are
supported, selected at startup with `AUTH_PROVIDER`:

| Provider | Algorithm | Required env vars |
|---|---|---|
| `local` | HS256 | `JWT_SECRET_KEY` (≥ 32 chars, must not be a default/weak value); optional `LOCAL_USER_FILE`, `ADMIN_STATIC_TOKEN` |
| `cognito` | RS256 + JWKS | `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`, `COGNITO_APP_SECRET`, optional `COGNITO_JWKS_URL` |

Local users are obtained via `POST /rest/auth/token` (OAuth2 password grant
form-encoded). Public protocol routes (`/`, `/v1`, `/.well-known/...`,
`/tasks/{task_id}/stream`) are not gated by `FlexJWTMiddleware`.

## Multi-Tenancy

DynamoDB partitioning uses a composite key:

```
partition_key = "{endpoint_id}#{part_id}"
```

`endpoint_id` is taken from URL paths like `/rest/{endpoint_id}/a2a_core_graphql`;
`part_id` is read from the `Part-ID` request header (or query parameter when
that path component is absent). All persistence and GraphQL queries flow
through this composite key, so cross-tenant access is hard-isolated at the row
level.

## Deployment

### Docker / Uvicorn

```bash
poetry run python -m a2a_daemon_engine.main
# Listens on http://0.0.0.0:${PORT:-8001}
```

### AWS Lambda (serverless)

`A2ADaemonEngine.a2a(**event)` accepts JSON-RPC 2.0 dictionaries only — non
JSON-RPC payloads are rejected.

```python
from a2a_daemon_engine.main import A2ADaemonEngine

daemon = A2ADaemonEngine(logger, transport="lambda", port=0, ...)

def lambda_handler(event, context):
    # event must be a JSON-RPC 2.0 dictionary
    return daemon.a2a(**event)
```

The bridge in `handlers/a2a_jsonrpc_bridge.py` converts the JSON-RPC request
into the SDK's protobuf request types and dispatches to the same
`DefaultRequestHandler` used by the HTTP path.

## Documentation

- [Architecture](docs/A2A_ARCHITECTURE.md)
- [Protocol Call Flow](docs/A2A_PROTOCOL_CALL_FLOW.md)
- [Development Plan](docs/A2A_DEVELOPMENT_PLAN.md)
- [Test Plan](docs/A2A_TEST_PLAN.md)
- [Integration Test Plan](docs/INTEGRATION_TEST_PLAN.md)
- [Documentation Index](docs/DOCUMENTATION_INDEX.md)
