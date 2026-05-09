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
- `POST /`
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

curl -X POST http://localhost:8001/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "message/send", "params": {"message": {"role": "user", "parts": [{"text": "hello"}]}}, "id": 1}'
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

## Serverless

`A2ADaemonEngine.a2a(**event)` accepts JSON-RPC 2.0 dictionaries only:

```python
def lambda_handler(event, context):
    return daemon.a2a(**event)
```

## Documentation

- [Architecture](docs/A2A_ARCHITECTURE.md)
- [Protocol Call Flow](docs/A2A_PROTOCOL_CALL_FLOW.md)
- [Development Plan](docs/A2A_DEVELOPMENT_PLAN.md)
- [Test Plan](docs/A2A_TEST_PLAN.md)
