# A2A Daemon Engine

**Package Version**: 0.0.1
**Status**: Phases 1-5 complete; Phase 6-8 implementation work has landed and release validation is in progress
**Last Updated**: 2026-05-07

A dedicated Agent-to-Agent (A2A) protocol daemon engine for distributed agent communication and multi-agent orchestration following canonical A2A SDK patterns.

## Recent Updates

**Post-reformation architecture is in place**, and the project is now validating the A2A SDK v1.0 compatibility and hardening work:
- **Phase 1-3**: Core SDK alignment (AgentExecutor, TaskStore, async wrappers)
- **Phase 4**: Architecture restructured (A2A app as primary)
- **Phase 5**: Event-driven message delivery with retry logic
- **Phase 6**: A2A SDK v1.0 dependency, task-state compatibility helpers, SDK-backed JSON-RPC routing, cursor-style task listing, and JWT secret validation
- **Phase 7**: SSE streaming components, task-event replay buffers, `INPUT_REQUIRED` / `AUTH_REQUIRED` emitters, and push-notification configuration helpers
- **Phase 8**: Extended agent-card manager, OpenTelemetry helper module, TCK/checker utilities, cross-tenant test scaffolding, configurable CORS, Pydantic v2 `model_dump()`, and import hygiene
- **Current focus**: run the full suite with the sibling SilvaEngine packages installed or path-loaded, wire remaining optional helpers into production paths, and confirm A2A Inspector/TCK results against a live daemon

See [A2A_DEVELOPMENT_PLAN.md](docs/A2A_DEVELOPMENT_PLAN.md) for current status, gaps, and roadmap.

## Overview

The AI A2A Daemon Engine is a production-ready service for agent-to-agent communication in distributed systems. It enables agents to register, discover each other, exchange messages, and coordinate tasks using the official [A2A Protocol](https://a2a-protocol.org/).

### Architecture (Post-Reformation)

The engine follows the **canonical A2A SDK pattern** (Option A):

```
┌─────────────────────────────────────┐
│    A2A SDK App (PRIMARY)            │
│  ┌─────────────────────────────┐   │
│  │ /.well-known/agent-card.json│   │ ← Auto-exposed
│  │ / (Native JSON-RPC)          │   │ ← A2A Protocol
│  └─────────────────────────────┘   │
│                                     │
│  ┌─────────────────────────────┐   │
│  │  FastAPI (mounted at /rest) │   │
│  │  └─ /rest/health            │   │
│  │  └─ /rest/a2a/{endpoint}/*  │   │
│  │  └─ /rest/a2a-jsonrpc       │   │
│  └─────────────────────────────┘   │
└─────────────────────────────────────┘
```

## Key Features

### Core Capabilities
- **A2A Protocol Compliance**: Follows [official A2A SDK patterns](https://github.com/a2aproject/a2a-samples)
- **Agent Registry & Discovery**: Dynamic agent registration with capability advertisement
- **Task Management**: Task creation, assignment, tracking, and cancellation
- **Message Delivery**: HTTP POST delivery with exponential backoff retry logic (3 attempts)
- **Event-Driven Architecture**: Uses EventQueue for async operations
- **Persistent Storage**: DynamoDB-backed TaskStore for durable task state

### Infrastructure
- **Multi-Tenant Architecture**: Composite partition key (`endpoint_id#part_id`) for data isolation
- **GraphQL API**: Comprehensive schema for queries and mutations
- **REST API**: RESTful endpoints at `/rest/*` (v0.2.0+)
- **Native A2A SDK**: Auto-exposed agent card and JSON-RPC at root
- **JWT Authentication**: Support for local JWT (HS256) and AWS Cognito (RS256 + JWKS)
- **Dual Deployment**: HTTP (FastAPI) and Serverless (AWS Lambda)

## Quick Start

### Installation

```bash
# Clone repository
git clone https://github.com/silvaengine/a2a-daemon-engine.git
cd a2a-daemon-engine

# Install dependencies
poetry install
```

### Configuration

```bash
# Environment variables
export A2A_TRANSPORT=http
export PORT=8001
export REGION_NAME=us-east-1
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
export AUTH_PROVIDER=local  # or 'cognito'
```

### Run Server

```bash
# Start daemon
poetry run python -m a2a_daemon_engine.main

# Server will start on http://0.0.0.0:8001
```

### Verify Installation

```bash
# Health check
curl http://localhost:8001/rest/health

# Agent card (new in v0.2.0)
curl http://localhost:8001/.well-known/agent-card.json

# Native A2A JSON-RPC (new in v0.2.0)
curl -X POST http://localhost:8001/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "agent.getCard", "params": {}, "id": 1}'
```

## API Endpoints

### v0.2.0 Endpoint Structure

⚠️ **Breaking Change**: All REST endpoints moved to `/rest` prefix

#### New A2A Protocol Endpoints
- `GET /.well-known/agent-card.json` - Agent capabilities card (auto-exposed)
- `POST /` - Native A2A JSON-RPC endpoint
- `GET /tasks/{task_id}/stream` - SSE task stream with `Last-Event-ID` replay support
- `GET /.well-known/agent-card-extended.json` - Authenticated extended agent card (implementation helper available; production route wiring should be verified)

#### REST API (with /rest prefix)
- `GET /rest/health` - Health check
- `POST /rest/{endpoint_id}/a2a_core_graphql` - GraphQL endpoint
- `POST /rest/a2a/{endpoint_id}/agents/register` - Register agent
- `POST /rest/a2a/{endpoint_id}/agents/{agent_id}/handshake` - Agent handshake
- `POST /rest/a2a/{endpoint_id}/tasks/create` - Create task
- `POST /rest/a2a/{endpoint_id}/agents/{agent_id}/message` - Send message
- `GET /rest/a2a/{endpoint_id}/agents` - List agents
- `POST /rest/a2a-jsonrpc` - Consolidated JSON-RPC handler

See [docs/A2A_DEVELOPMENT_PLAN.md](docs/A2A_DEVELOPMENT_PLAN.md) for current migration status and roadmap.

## Usage Examples

### Python Client

```python
import requests

base_url = "http://localhost:8001"
endpoint_id = "my-endpoint"
token = "your_jwt_token"

# Register an agent
response = requests.post(
    f"{base_url}/rest/a2a/{endpoint_id}/agents/register",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "agent_id": "agent-1",
        "agent_name": "My Agent",
        "capabilities": ["task-execution", "data-processing"],
        "endpoint_url": "http://agent:9000"
    }
)

# Create a task
response = requests.post(
    f"{base_url}/rest/a2a/{endpoint_id}/tasks/create",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "task_type": "data-processing",
        "priority": "high",
        "input_data": {"query": "process data"}
    }
)

# Send a message
response = requests.post(
    f"{base_url}/rest/a2a/{endpoint_id}/agents/agent-2/message",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "from_agent_id": "agent-1",
        "to_agent_id": "agent-2",
        "message_type": "text",
        "payload": {"text": "Hello"}
    }
)
# Message delivery happens asynchronously with retries
```

### JavaScript/TypeScript

```typescript
const baseUrl = "http://localhost:8001";
const endpointId = "my-endpoint";
const token = "your_jwt_token";

// Register agent
const response = await fetch(
  `${baseUrl}/rest/a2a/${endpointId}/agents/register`,
  {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      agent_id: "agent-1",
      agent_name: "My Agent",
      capabilities: ["task-execution"],
      endpoint_url: "http://agent:9000"
    })
  }
);
```

### cURL

```bash
# Register agent
curl -X POST http://localhost:8001/rest/a2a/my-endpoint/agents/register \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "agent-1",
    "agent_name": "My Agent",
    "capabilities": ["task-execution"],
    "endpoint_url": "http://agent:9000"
  }'

# Create task
curl -X POST http://localhost:8001/rest/a2a/my-endpoint/tasks/create \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "data-processing",
    "priority": "high",
    "input_data": {"query": "process data"}
  }'
```

## Architecture

### Canonical A2A SDK Pattern

The daemon follows the official A2A SDK pattern where:
1. **A2A SDK Starlette app is primary** - Handles protocol at root
2. **FastAPI app mounted at /rest** - Legacy REST API
3. **Agent card auto-exposed** - Standard `.well-known/agent-card.json`
4. **EventQueue-driven** - Async operations via event queue
5. **Persistent TaskStore** - DynamoDB-backed task state

### Layer Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    A2A SDK Layer (Primary)                   │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  A2AStarletteApplication                               │ │
│  │  ├─ AgentCard (auto-exposed)                           │ │
│  │  ├─ DefaultRequestHandler                              │ │
│  │  ├─ A2ADaemonExecutor (canonical pattern)              │ │
│  │  └─ DynamoDBA2ATaskStore (persistent)                  │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│               REST API Layer (Mounted at /rest)              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  FastAPI Application                                   │ │
│  │  ├─ FlexJWTMiddleware (authentication)                 │ │
│  │  ├─ Auth Router (/auth/*)                              │ │
│  │  └─ A2A REST Routes (/a2a/*)                           │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                    Business Logic Layer                      │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  A2A Handlers (a2a_handlers.py)                        │ │
│  │  ├─ handle_agent_handshake()                           │ │
│  │  ├─ handle_task_assignment()                           │ │
│  │  ├─ handle_message_routing() + delivery               │ │
│  │  └─ deliver_message_to_agent() (HTTP POST + retries)  │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                    Data Layer (GraphQL)                      │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  A2A Core GraphQL (a2a_core.py)                        │ │
│  │  ├─ Schema (Query, Mutations)                          │ │
│  │  └─ Async Wrappers (a2a_utility.py)                    │ │
│  └────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  PynamoDB Models                                       │ │
│  │  ├─ A2AAgentModel                                      │ │
│  │  ├─ A2ATaskModel                                       │ │
│  │  ├─ A2AMessageModel                                    │ │
│  │  └─ A2ASettingModel                                    │ │
│  └────────────────────────────────────────────────────────┘ │
│                        DynamoDB                              │
└─────────────────────────────────────────────────────────────┘
```

## Deployment

### Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . /app

RUN pip install poetry && poetry install

EXPOSE 8001

CMD ["poetry", "run", "python", "-m", "a2a_daemon_engine.main"]
```

```bash
# Build and run
docker build -t a2a-daemon .
docker run -p 8001:8001 \
  -e AUTH_PROVIDER=local \
  -e REGION_NAME=us-east-1 \
  a2a-daemon
```

### Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: a2a-daemon
spec:
  replicas: 3
  selector:
    matchLabels:
      app: a2a-daemon
  template:
    metadata:
      labels:
        app: a2a-daemon
    spec:
      containers:
      - name: a2a-daemon
        image: a2a-daemon:0.2.0
        ports:
        - containerPort: 8001
        env:
        - name: AUTH_PROVIDER
          value: "local"
        - name: REGION_NAME
          value: "us-east-1"
---
apiVersion: v1
kind: Service
metadata:
  name: a2a-daemon
spec:
  selector:
    app: a2a-daemon
  ports:
  - protocol: TCP
    port: 80
    targetPort: 8001
  type: LoadBalancer
```

### AWS Lambda (Serverless)

```python
# lambda_handler.py
from a2a_daemon_engine.main import A2ADaemonEngine

daemon = A2ADaemonEngine(transport="lambda", ...)

def lambda_handler(event, context):
    return daemon.a2a(
        action=event['action'],
        **event['params']
    )
```

## Multi-Tenancy

The daemon supports multi-tenancy via composite partition keys:

```
partition_key = "{endpoint_id}#{part_id}"

Examples:
- "customer-a#department-1"
- "customer-b#department-2"
- "prod#us-east"
```

Pass `Part-ID` header or use URL parameter to specify partition:

```bash
curl -X POST http://localhost:8001/rest/a2a/my-endpoint/agents/register \
  -H "Part-ID: department-1" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{...}'
```

## Authentication

### Local JWT (HS256)

```python
# Generate token
import jwt

token = jwt.encode(
    {"username": "user1", "exp": ...},
    secret_key="your-secret",
    algorithm="HS256"
)
```

### AWS Cognito (RS256)

Configure Cognito User Pool and set environment variables:

```bash
export AUTH_PROVIDER=cognito
export COGNITO_REGION=us-east-1
export COGNITO_POOL_ID=us-east-1_XXXXXX
export COGNITO_CLIENT_ID=your_client_id
```

## Testing

```bash
# Run all tests
poetry run pytest

# Run specific test
poetry run pytest a2a_daemon_engine/tests/test_a2a_handlers.py

# With coverage
poetry run pytest --cov=a2a_daemon_engine
```

## Documentation

- [Development Plan](docs/A2A_DEVELOPMENT_PLAN.md) - Development roadmap
- [Protocol Analysis](docs/a2a-protocol-analysis.md) - A2A v1.0 analysis and recommendations
- [Documentation Index](docs/DOCUMENTATION_INDEX.md) - Documentation map

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

MIT License - see LICENSE file for details

## Support

- GitHub Issues: https://github.com/silvaengine/a2a-daemon-engine/issues
- Documentation: See `docs/` directory
- A2A Protocol: https://a2a-protocol.org/

## Changelog

### v0.2.0 (2025-12-31)
- ✅ **BREAKING**: All REST endpoints moved to `/rest` prefix
- ✅ **NEW**: Agent card auto-exposed at `/.well-known/agent-card.json`
- ✅ **NEW**: Native A2A JSON-RPC at root `/`
- ✅ **NEW**: Event-driven message delivery with retry logic
- ✅ Refactored to canonical A2A SDK pattern (AgentExecutor, TaskStore)
- ✅ DynamoDB-backed persistent TaskStore
- ✅ Architecture restructured (A2A app as primary)

### v0.1.0 (2025-12-01)
- Initial release
- REST API for agents, tasks, messages
- GraphQL data layer
- JWT authentication
- Multi-tenant support

---

**Built with ❤️ following official [A2A SDK patterns](https://github.com/a2aproject/a2a-samples)**
