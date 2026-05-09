# A2A Development Plan

**Target Protocol:** A2A SDK v1.0

## Current State

The daemon now uses the SDK Starlette application as the only HTTP A2A
protocol surface:

- `GET /.well-known/agent-card.json`
- `POST /`
- `GET /tasks/{task_id}/stream`

The FastAPI app mounted at `/rest` is limited to operations endpoints:

- `GET /rest/health`
- `GET /rest/me`
- `GET /rest/{endpoint_id}`
- `POST /rest/{endpoint_id}/a2a_core_graphql`

Removed protocol surfaces:

- `/rest/a2a-jsonrpc`
- `/rest/a2a/{endpoint_id}/...`
- `handlers/a2a_jsonrpc.py`
- direct `action=...` dispatch through `A2ADaemonEngine.a2a()`
- lowercase/pre-v1 task-state fallback helpers

## Implementation Notes

| Area | Status | Notes |
| --- | --- | --- |
| SDK app as primary HTTP app | Done | `main.py` mounts the SDK app at root and the operations app under `/rest`. |
| Agent Card | Done | `a2a_server.py` advertises protocol version `1.0.0`. |
| JSON-RPC protocol | Done | Native SDK JSON-RPC is served at `/`; serverless JSON-RPC dispatch remains available through `A2ADaemonEngine.a2a(**event)`. |
| Task state handling | Done | Internal helpers now resolve v1 uppercase state names only. |
| Task persistence | Done | `DynamoDBA2ATaskStore` implements SDK task-store methods and maps persisted states to v1 names. |
| Operations API | Done | `/rest` exposes health, identity, endpoint info, and GraphQL only. |
| gRPC adapter | Experimental | JSON-over-gRPC remains available for transport experimentation. |

## Release Gates

- Run unit tests with the local SilvaEngine dependency stack installed.
- Run live SDK/TCK or reference-client validation against a running daemon.
- Verify production configuration for auth, CORS, persistence, and streaming.
- Decide whether the experimental gRPC adapter should be promoted, rewritten with
  generated protobuf stubs, or kept out of production deployments.
