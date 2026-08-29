# Agent Instructions - A2A Daemon Engine

## Environment

- Prefer the project venv: `c:\Python312\env\Scripts\activate.bat`
  - If SDK behavior looks wrong, first compare `python -c "import sys; print(sys.executable)"` and `python -c "import importlib.metadata as m; print(m.version('a2a-sdk'))"`.
  - The current expected SDK line is `a2a-sdk==1.0.2`, installed from `a2a-sdk[http-server]`.
- Install deps with `poetry install`, or `pip install -e .[dev]` if Poetry is unavailable.
- On PowerShell, prefer module invocation from the repo root: `python -m pytest ...`, `python -m ruff check ...`.

## Architecture

The HTTP daemon is a two-app stack:

1. SDK Starlette app is primary and owns the A2A protocol surface:
   - `GET /.well-known/agent-card.json`
   - `POST /` for compatibility JSON-RPC methods: `message/send`, `tasks/get`, `tasks/cancel`
   - `POST /v1` for the SDK dispatcher and native v1 method names such as `SendMessage`, `GetTask`, `CancelTask`
   - `GET /tasks/{task_id}/stream` for SSE streaming

2. FastAPI operations app is mounted at `/rest` for non-protocol routes:
   - `GET /rest/health`
   - `GET /rest/me`
   - `GET /rest/{endpoint_id}`
   - `POST /rest/{endpoint_id}/a2a_core_graphql`
   - `POST /rest/auth/token`

Removed legacy surfaces - do not re-add or document as active:

- `/rest/a2a-jsonrpc`
- `/rest/a2a/{endpoint_id}/...`
- direct `action=...` dispatch through `A2ADaemonEngine.a2a()`
- `handlers/a2a_jsonrpc.py`
- `handlers/a2a_sdk_compat.py`

## Entrypoints

| Command | Purpose |
|---------|---------|
| `poetry run python -m a2a_daemon_engine.main` | Production CLI |
| `python a2a_daemon_engine/tests/start_daemon.py` | Local dev server |
| `python -m pytest a2a_daemon_engine/tests` | Unit and skipped-live test suite |
| `$env:A2A_RUN_LIVE_API_TESTS='1'; python -m pytest a2a_daemon_engine/tests/test_api.py` | Live API tests against a running daemon |

## Key Files

### Core server and app

- `a2a_daemon_engine/main.py` - `A2ADaemonEngine`; serverless JSON-RPC entrypoint and HTTP daemon lifecycle. Mounts the REST app at `/rest`.
- `a2a_daemon_engine/handlers/a2a_server.py` - Builds the SDK Starlette app. Wires `DefaultRequestHandler`, task store, compatibility JSON-RPC endpoint, and context metadata extraction.
- `a2a_daemon_engine/handlers/a2a_app.py` - FastAPI operations app mounted at `/rest`.
- `a2a_daemon_engine/handlers/config.py` - `Config` singleton for logger, settings, `a2a_server`, `a2a_core`, auth provider, and runtime state.

### Request handling and execution

- `a2a_daemon_engine/handlers/a2a_executor.py` - `A2ADaemonExecutor(AgentExecutor)` implementation. Routes `message_response`, `task_execution`, `message_routing`, and `agent_registration`.
- `a2a_daemon_engine/handlers/a2a_handlers.py` - Business handlers called by the executor.
- `a2a_daemon_engine/handlers/a2a_jsonrpc_bridge.py` - JSON-RPC dictionary bridge used by serverless and the `/` compatibility endpoint. It normalizes legacy slash-style payloads into SDK requests, including missing `messageId`, role casing, and `parts[].type` removal.
- `a2a_daemon_engine/handlers/a2a_taskstore.py` - `DynamoDBA2ATaskStore(TaskStore)` plus event-cache helpers.
- `a2a_daemon_engine/handlers/a2a_sse.py` - SSE queue, streaming manager, and `/tasks/{task_id}/stream`.

### Auth and middleware

- `a2a_daemon_engine/handlers/middleware.py` - `FlexJWTMiddleware` for local JWT and Cognito.
- `a2a_daemon_engine/handlers/auth_router.py` - `/auth/token` OAuth2 token endpoint mounted under `/rest`.
- `a2a_daemon_engine/handlers/jwt_local.py` - Local JWT creation and verification.
- `a2a_daemon_engine/handlers/jwt_cognito.py` - Cognito JWT verification via JWKS.

### Infrastructure and extensions

- `a2a_daemon_engine/handlers/a2a_core.py` - GraphQL handler for agents, tasks, messages, and settings.
- `a2a_daemon_engine/handlers/a2a_utility.py` - DynamoDB query and mutation helpers.
- `a2a_daemon_engine/handlers/a2a_extended_card.py` - Extended agent card support.
- `a2a_daemon_engine/handlers/a2a_cancellation.py` - Task cancellation logic.
- `a2a_daemon_engine/handlers/a2a_secure_passport.py` - Secure passport / identity verification.
- `a2a_daemon_engine/handlers/a2a_health_monitor.py` - Health monitoring.
- `a2a_daemon_engine/handlers/a2a_rate_limiter.py` - Rate limiting.
- `a2a_daemon_engine/handlers/a2a_pushconfig.py` - Push notification configuration.
- `a2a_daemon_engine/handlers/a2a_cost_extension.py` - Cost extension for agent cards.
- `a2a_daemon_engine/handlers/a2a_grpc.py` - gRPC transport.
- `a2a_daemon_engine/handlers/a2a_graphql_subscriptions.py` - Phase 9 GraphQL subscription manager (live task / agent / message updates).
- `a2a_daemon_engine/handlers/a2a_telemetry.py` - OpenTelemetry instrumentation. Optional: imports `opentelemetry.*` inside `try/except ImportError` and degrades to a no-op when the `[telemetry]` extra is not installed (`OPENTELEMETRY_AVAILABLE = False`). Activation requires `pip install -e .[telemetry]` plus `OTEL_EXPORTER_OTLP_ENDPOINT`.
- `a2a_daemon_engine/handlers/a2a_ai_agent_utility.py` - Phase 10 bridge utility. Resolves `ai_agent_core_engine` agent config, loads the LLM handler, and invokes `ask_model` with streaming-thread bridging into A2A `EventQueue` and `SSEEventQueue`.
- `a2a_daemon_engine/handlers/a2a_hermes_handler.py` - Hermes Agent handler. Phase 10 bridge plugin that routes A2A tasks to a Hermes Agent API Server instance via HTTP + SSE instead of in-process LLM calls. Implements `ask_model`, `cancel_run`, and `resolve_approval`.
- `a2a_daemon_engine/handlers/a2a_core_engine_handler.py` - Core Engine Agent handler. Phase 10 bridge plugin that routes A2A tasks to `ai_agent_core_engine` via `silvaengine_gateway` using GraphQL (non-streaming) and WebSocket (streaming) transports. Implements `ask_model`, `cancel_run`, and `resolve_approval`.
- `a2a_daemon_engine/handlers/schema.py` - GraphQL schema definitions.

### Phase 10 Configuration

Environment variables that control the `ai_agent_core_engine` bridge:

| Variable | Default | Description |
|----------|---------|-------------|
| `A2A_AI_AGENT_TYPE` | `None` | Handler shorthand: `hermes`, `core_engine`, `openclaw`, or `llm` (maps to module/class via `AGENT_TYPE_MAP`) |
| `A2A_DEFAULT_AGENT_UUID` | `a2a-default-agent` | Fallback agent UUID when request metadata does not specify one |
| `A2A_STREAM_TIMEOUT` | `120.0` | Maximum seconds to wait for a streaming LLM call |
| `A2A_STREAMING_ENABLED` | `true` | Whether streaming (`SendStreamingMessage`) is allowed |

Handler resolution priority (first wins):
1. Agent metadata `module_name` / `class_name` (explicit, per-agent — escape hatch for custom handlers)
2. Agent metadata `agent_type` (shorthand → `AGENT_TYPE_MAP`)
3. `Config.a2a_ai_agent_type` (env-var shorthand fallback)

`AGENT_TYPE_MAP` in `a2a_ai_agent_utility.py` maps:

| `agent_type` | module | class |
|---|---|---|
| `hermes` | `a2a_daemon_engine.handlers.a2a_hermes_handler` | `HermesAgentHandler` |
| `core_engine` | `a2a_daemon_engine.handlers.a2a_core_engine_handler` | `CoreEngineAgentHandler` |
| `openclaw` | `a2a_daemon_engine.handlers.a2a_openclaw_handler` | `OpenClawAgentHandler` |
| `llm` | `ai_agent_core_engine.handlers.llm_handler` | `LLMHandler` |

Startup flag `Config.phase10_available` is `True` only when both `ai_agent_core_engine` is importable and `Config.a2a_core` is initialized.

Phase 10 request metadata accepts both snake_case and camelCase aliases:
`agent_uuid` / `agentId`, `thread_uuid` / `threadId`, `run_uuid` / `runId`,
and `stream` / `streaming`.

### Hermes Agent Handler

When `module_name` = `a2a_daemon_engine.handlers.a2a_hermes_handler` and
`class_name` = `HermesAgentHandler`, the Phase 10 bridge routes A2A tasks to
a running Hermes Agent API Server instance via HTTP + SSE.

Per-agent metadata keys (stored in the agent record's `metadata` JSON):
- `hermes_api_url` — Hermes API Server base URL (default: `http://localhost:8642`)
- `hermes_api_key` — Bearer token for API Server auth
- `hermes_model` — Model name to pass (default: `hermes-agent`)
- `hermes_timeout` — SSE stream timeout in seconds (default: `300`)

Global fallbacks (read from settings/env vars at startup):
- `HERMES_API_URL`, `HERMES_API_KEY`, `HERMES_MODEL`, `HERMES_STREAM_TIMEOUT`

Config resolution priority: agent metadata (highest) → setting dict → `Config` defaults.

The executor maintains a per-task external-run registry
(`A2ADaemonExecutor._active_external_runs`) that maps A2A `task_id` to the
active Hermes `run_id` + handler. This is populated via the bridge's
`on_run_id` callback when a `run_id` chunk is drained, and is used for:
- **Cancel passthrough** — `tasks/cancel` calls `handler.cancel_run(run_id)`
  (`POST /v1/runs/{id}/stop`) before the task-store cancellation.
- **Approval passthrough** — `operation="approval_response"` metadata with
  `task_id`, `approved`, and `reason` calls
  `handler.resolve_approval(run_id, approved, reason)`
  (`POST /v1/runs/{id}/approval`).

The bridge drain loop handles these Hermes chunk types:
- `token` — emitted as A2A text artifacts (SDK + SSE)
- `run_id` — registered in the per-task external-run registry
- `approval` — emits `INPUT_REQUIRED` task state; stores `pending_approval` in metadata
- `tool_call` / `tool_result` — metadata only (progress indicators, not text)
- `error` — sets `FAILED` task state

See `docs/HERMES_INTEGRATION.md` for the full setup guide, A2A state mapping, and E2E test instructions.

### Core Engine Agent Handler

When `module_name` = `a2a_daemon_engine.handlers.a2a_core_engine_handler` and
`class_name` = `CoreEngineAgentHandler`, the Phase 10 bridge routes A2A tasks
to `ai_agent_core_engine` through `silvaengine_gateway` using its public
transport contracts — **GraphQL** for non-streaming and **WebSocket** for
streaming — instead of importing core-engine internals.

Per-agent metadata keys (stored in the agent record's `metadata` JSON):
- `core_engine_graphql_url` — Gateway GraphQL base URL (default: `http://localhost:8765`)
- `core_engine_ws_url` — Gateway WebSocket base URL (default: `ws://localhost:8765`)
- `core_engine_token` — JWT token for gateway auth
- `core_engine_agent_uuid` — Default agent UUID for the core engine
- `core_engine_updated_by` — Updated-by user ID (default: `a2a-daemon`)
- `core_engine_stream_timeout` — WebSocket stream timeout in seconds (default: `120`)

Global fallbacks (read from settings/env vars at startup):
- `CORE_ENGINE_GRAPHQL_URL`, `CORE_ENGINE_WS_URL`, `CORE_ENGINE_TOKEN`,
  `CORE_ENGINE_AGENT_UUID`, `CORE_ENGINE_UPDATED_BY`, `CORE_ENGINE_STREAM_TIMEOUT`

Config resolution priority: agent metadata (highest) → setting dict → `Config` defaults.

**Non-streaming path** (`SendMessage`): POSTs a GraphQL mutation to the
gateway's `/{endpoint_id}/ai_agent_core_graphql` route. Three steps:
1. `ask_model` mutation — starts the async task, returns `async_task_uuid` + `current_run_uuid`
2. `execute_ask_model` mutation — runs synchronously, persists the assistant message
3. `message_list` query — retrieves the final assistant response content

**Streaming path** (`SendStreamingMessage`): Connects to the gateway's
`/{endpoint_id}/ai_agent_core_ws` WebSocket, sends an `ask_model` action with
`stream: true`, and drains `chunk_delta` frames into the bridge `stream_queue`
as `token` chunks until `is_message_end` or error. The executor and
`a2a_ai_agent_utility.py` drain loop, dual-path emission, and persistence
machinery are reused unchanged (same contract as `HermesAgentHandler`).

**Cancel passthrough** — `cancel_run()` closes the WebSocket, which unblocks
the streaming drain loop.

**Approval passthrough** — `resolve_approval()` sends a non-streaming
`ask_model` with the approval response as the user query.

Injectable transports for testing: `ws_connect` factory + `graphql_client`
(httpx client with `MockTransport`).

### Tests layout

- Phase 6 / 8 / 9 tests live in `tests/test_phase6.py`, `tests/test_phase8.py`, `tests/test_phase9.py`. There is no `test_phase7.py`; SSE / streaming coverage is consolidated into `test_phase8.py`.
- Phase 10 tests live in `tests/test_phase10.py`.
- `tests/test_hermes_handler.py` covers the Hermes Agent handler (mocked HTTP via `httpx.MockTransport`).
- `tests/test_core_engine_handler.py` covers the Core Engine Agent handler (mocked GraphQL via `httpx.MockTransport` + mocked WebSocket via injectable `ws_connect`).
- `tests/a2a_rpc_verifier.py`, `tests/a2a_tck_checker.py`, and `tests/validate_agent_card.py` are runnable scripts, not pytest test files.
- `tests/test_helpers.py` and `tests/test_data.json` are fixture support for the live-API and integration suites.
- The serverless JSON-RPC bridge has its own dedicated suite at `tests/test_a2a_jsonrpc_bridge.py`.

## SDK Type System

- The installed SDK is protobuf-based in this workspace.
- `a2a.types.SendMessageRequest` is a protobuf message, not a Pydantic model.
- Do not call `.model_dump()` or `.model_validate()` on SDK protobuf types.
- Use `google.protobuf.json_format.ParseDict` and `MessageToDict` for protobuf paths.
- Keep the `hasattr(request_type, "model_validate")` branch in `a2a_jsonrpc_bridge.py`; it preserves forward compatibility if a Pydantic SDK variant is installed later.

## JSON-RPC Method Rules

- `POST /` accepts legacy slash-style methods used by `test_api.py`: `message/send`, `tasks/get`, `tasks/cancel`.
- `POST /v1` is the SDK dispatcher path and expects native v1 method names such as `SendMessage`.
- Unknown methods should remain JSON-RPC errors, not HTTP 404s.
- The compatibility endpoint must keep metadata available to the executor through `ServerCallContext.state`.

## Task Execution Dry Run

Dry-run task execution is driven by JSON-RPC metadata. Keep these shapes working:

```json
{
  "jsonrpc": "2.0",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [{"type": "text", "text": "Execute a dry-run test task"}]
    },
    "metadata": {
      "operation": "task_execution",
      "task_data": {
        "task_id": "test-task-exec-001",
        "task_type": "test",
        "priority": "low",
        "dry_run": true
      }
    }
  },
  "id": 2
}
```

Also preserve compatibility with:

- `params.task_data` and `params.taskData`
- `message.metadata`
- `dry_run`, `dryRun`, and `dry-run`
- boolean-like strings such as `"true"`
- `task_id`, `taskId`, and `id`

Expected dry-run response text includes both the task id and `dry-run mode`.

## Local Testing

1. Start the daemon: `python a2a_daemon_engine/tests/start_daemon.py`
2. Run unit tests: `python -m pytest a2a_daemon_engine/tests -q`
3. Run live API tests in PowerShell:

```powershell
$env:A2A_RUN_LIVE_API_TESTS = '1'
python -m pytest a2a_daemon_engine/tests/test_api.py -q
```

### Quick Checks

```powershell
curl http://localhost:8001/.well-known/agent-card.json

curl -Method POST http://localhost:8001/ `
  -ContentType "application/json" `
  -Body '{"jsonrpc":"2.0","method":"message/send","params":{"message":{"role":"user","parts":[{"text":"hello"}]}},"id":1}'
```

REST routes may require auth depending on middleware settings. Use `/rest/auth/token` or the test helper token when checking `/rest/health` or GraphQL in an authenticated run.

## Lint and Format

- `black` line length is 88.
- `ruff` is configured in `pyproject.toml`.
- `mypy` uses `ignore_missing_imports = true`.
- Useful focused checks:
  - `python -m ruff check a2a_daemon_engine/handlers/a2a_server.py a2a_daemon_engine/handlers/a2a_executor.py`
  - `python -m pytest a2a_daemon_engine/tests/test_phase6.py -q`

## Test Markers

Available pytest markers: `unit`, `integration`, `slow`, `a2a`, `agent`, `task`, `message`, `setting`, `server`, `graphql`, `cache`, `performance`.

## Serverless

`A2ADaemonEngine.a2a(**event)` accepts JSON-RPC 2.0 dictionaries only. It uses `a2a_jsonrpc_bridge.py` to construct SDK requests before calling `DefaultRequestHandler`.

```python
def lambda_handler(event, context):
    return daemon.a2a(**event)
```
