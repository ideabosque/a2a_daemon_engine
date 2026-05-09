# A2A Test Plan

## Scope

Validate the v1-only A2A daemon structure:

- SDK Agent Card discovery at `GET /.well-known/agent-card.json`
- Native SDK JSON-RPC at `POST /`
- Task streaming at `GET /tasks/{task_id}/stream`
- Operations endpoints under `/rest`
- Serverless JSON-RPC dispatch through `A2ADaemonEngine.a2a(**event)`

## Unit Tests

| Area | Expected coverage |
| --- | --- |
| Executor | v1 task-state resolution, cancellation, event emission helpers |
| Task store | v1 state mapping, task conversion, cursor task listing |
| JSON-RPC bridge | JSON-RPC dictionary to SDK request conversion and SDK response wrapping |
| Handlers | GraphQL/core persistence calls and error handling |
| Auth | JWT secret validation and auth middleware behavior |

## Live/API Tests

Run live tests only against a running daemon:

```bash
set A2A_RUN_LIVE_API_TESTS=1
pytest a2a_daemon_engine/tests/test_api.py -v
```

Required live checks:

- `GET /.well-known/agent-card.json` returns a valid v1 Agent Card.
- `POST /` accepts `message/send`.
- `POST /` accepts `tasks/get` for existing tasks.
- `POST /` accepts `tasks/cancel` for cancellable tasks.
- `GET /rest/health` returns healthy status.
- `POST /rest/{endpoint_id}/a2a_core_graphql` executes GraphQL requests.

## Release Gates

- Unit tests pass locally.
- Live API tests pass against a running daemon.
- A2A reference client or TCK validation passes against `POST /`.
- Removed legacy paths return 404 or are otherwise unavailable:
  `/rest/a2a-jsonrpc` and `/rest/a2a/{endpoint_id}/...`.
