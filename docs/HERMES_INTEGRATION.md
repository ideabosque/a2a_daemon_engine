# Hermes Agent ↔ A2A Daemon Engine Integration

> Setup guide, architecture, A2A state mapping, configuration reference, and
> end-to-end test instructions for the Hermes bridge plugin.
>
> **Updated:** 2026-07-14 — reflects actual implementation including A2A SDK v2
> constraints, PostgreSQL backend, gateway integration, and SSE streaming fixes.

---

## 1. Overview

The `HermesAgentHandler` (`a2a_daemon_engine/handlers/a2a_hermes_handler.py`)
is a Phase 10 bridge plugin that routes A2A tasks to a running **Hermes Agent
API Server** instance via HTTP + SSE, instead of calling an in-process LLM
handler.

Both the in-process `ai_agent_core_engine` LLM handler and the Hermes handler
are reached through the **same Phase 10 bridge, same executor, same A2A
protocol surface**. Routing is purely data-driven via per-agent `module_name`
and `class_name` metadata in the agent registry.

### Architecture

```
                    Client (ai_agent_core_engine / test runner / chatbot)
                              │
                         POST /{endpoint_id}/a2a
                              │
                              ▼
                 ┌──────────────────────────────────────────────┐
                 │   silvaengine_gateway (port 8765)            │
                 │   dispatch_a2a → A2ADaemonEngine.a2a()       │
                 │                         │                    │
                 │              A2ADaemonExecutor               │
                 │                .execute()                    │
                 │                  │                            │
                 │                  ▼                            │
                 │           Phase 10 Bridge                    │
                 │           resolve_agent(uuid)                │
                 │             ↓ DB lookup (PostgreSQL)        │
                 │           load_agent_handler()              │
                 │             │                                │
                 │             ├── module=llm_handler          │
                 │             │     → ai_agent_core_engine      │
                 │             │       (in-process, unchanged)  │
                 │             │                               │
                 │             └── module=hermes_handler        │
                 │                  → HermesAgentHandler        │
                 │                    → POST /v1/runs           │
                 │                    → GET /v1/runs/{id}/events│
                 │                    → SSE token streaming     │
                 └──────────────────────────────────────────────┘
                              │
                              ▼
                 ┌──────────────────────────────────────────────┐
                 │   Hermes Agent API Server (port 8642)         │
                 │   /v1/chat/completions (non-streaming)        │
                 │   /v1/runs + /v1/runs/{id}/events (SSE)      │
                 │   /v1/runs/{id}/stop (cancel)                │
                 │   /v1/runs/{id}/approval (human-in-loop)     │
                 └──────────────────────────────────────────────┘
```

**Key change from original plan:** The A2A daemon runs in-process inside the
SilvaEngine Gateway, not as a standalone process. All requests go through
the gateway's `dispatch_a2a` handler at `POST /{endpoint_id}/a2a`.

---

## 2. Prerequisites

### 2.1 Hermes Agent API Server

Enable the API server on the Hermes Agent side:

```bash
# In ~/.hermes/.env (or ~/AppData/Local/hermes/.env on Windows)
API_SERVER_ENABLED=true
API_SERVER_KEY=hermes-local-key

# Optional: allow CORS if needed
# API_SERVER_CORS_ORIGINS=http://localhost:8765

# Start Hermes (the API server starts automatically)
# Verify:
curl http://127.0.0.1:8642/health -H "Authorization: Bearer *** → {"status":"ok"}
curl http://127.0.0.1:8642/v1/models -H "Authorization: Bearer *** → model list
```

### 2.2 SilvaEngine Gateway Configuration

The gateway's `.env` file (`silvaengine_gateway/tests/.env`) must include:

```ini
# Database backend — PostgreSQL required for agent metadata
db_backend=postgresql
PG_HOST=localhost
PG_PORT=5432
PG_USER=silvaengine
PG_PASSWORD=silvaengine
PG_DB=silvaengine

# Hermes Agent A2A bridge — global fallbacks (agent config should be in DB)
HERMES_API_URL=http://127.0.0.1:8642
HERMES_API_KEY=hermes-local-key
HERMES_MODEL=hermes-agent
HERMES_STREAM_TIMEOUT=300
```

**Important:** The `A2A_AI_AGENT_MODULE` and `A2A_AI_AGENT_CLASS` env vars are
NOT set in `.env`. These should be stored in the agent's `metadata` JSON in
the database, not as env vars. The `HERMES_*` vars are global fallbacks only.

### 2.3 Start the Gateway

```bash
cd silvaengine_gateway
python -m silvaengine_gateway.tests.run_daemon
# Verify:
curl http://127.0.0.1:8765/health  # → {"status":"ok","service":"silvaengine-gateway"}
```

### 2.4 PostgreSQL Container

```bash
docker start silvaengine-postgres
# Verify:
docker exec silvaengine-postgres pg_isready -U silvaengine
# → accepting connections
```

---

## 3. Agent Registration

### 3.1 Register via Direct SQL (Recommended)

The `silvaengine_utility` JSON scalar has a serialization bug that prevents
passing `metadata` as a GraphQL dict variable. Use the direct SQL helper
instead:

```bash
cd a2a_daemon_engine
python a2a_daemon_engine/tests/register_hermes_agent.py
```

This script:
1. Reads the gateway `.env` for PostgreSQL connection details and Hermes settings
2. Inserts/updates the `hermes-agent` record in `a2a_agents` with full metadata:

```json
{
  "module_name": "a2a_daemon_engine.handlers.a2a_hermes_handler",
  "class_name": "HermesAgentHandler",
  "hermes_api_url": "http://127.0.0.1:8642",
  "hermes_api_key": "hermes-local-key",
  "hermes_model": "hermes-agent",
  "hermes_timeout": 300.0
}
```

### 3.2 Per-Agent Metadata Keys

| Key | Default | Description |
|-----|---------|-------------|
| `module_name` | *(required)* | `a2a_daemon_engine.handlers.a2a_hermes_handler` |
| `class_name` | *(required)* | `HermesAgentHandler` |
| `hermes_api_url` | `http://localhost:8642` | Hermes API Server base URL |
| `hermes_api_key` | *(empty)* | Bearer token for API Server auth |
| `hermes_model` | `hermes-agent` | Model name passed to Hermes |
| `hermes_timeout` | `300` | SSE stream timeout in seconds |

**Config resolution priority:** agent metadata (DB) → setting dict → `Config` defaults (env vars).

---

## 4. A2A State Mapping

### 4.1 Real Hermes SSE Event Format

The real Hermes API Server uses `{"event": "..."}` format (not `{"type": "..."}`):

| Hermes SSE Event | A2A Task State | Bridge Action |
|------------------|---------------|---------------|
| `{"event": "run.created"}` (run_id returned) | `WORKING` | Register `run_id` via `on_run_id` callback |
| `{"event": "message.delta", "delta": "..."}` | `WORKING` | Accumulate token; emit to SSE per-chunk |
| `{"event": "reasoning.available", "text": "..."}` | `WORKING` | Reasoning metadata (no token emission) |
| `{"event": "tool.call", ...}` | `WORKING` | Tool execution started (metadata only) |
| `{"event": "tool.result", ...}` | `WORKING` | Tool execution completed (metadata only) |
| `{"event": "approval.required", ...}` | `INPUT_REQUIRED` | Emit `approval` chunk; store `pending_approval` |
| `{"event": "run.completed", "output": "..."}` | `COMPLETED` | Set `stream_event`; accumulate final text |
| `{"event": "run.failed", "error": "..."}` | `FAILED` | Emit error chunk; set `FAILED` state |
| `POST /v1/runs/{id}/stop` | `CANCELED` | External cancel via `tasks/cancel` |
| `POST /v1/runs/{id}/approval` | *(continues run)* | Resolved via `operation="approval_response"` |

### 4.2 A2A SDK v2 Constraints

The A2A SDK v2 (`a2a-sdk==1.0.2`) imposes two critical constraints on the
`on_message_send` path:

1. **Single Message only** — emitting multiple `Message` objects raises
   `InvalidAgentResponseError: Multiple Message objects received.`
2. **No TaskStatusUpdateEvent** — emitting status events raises
   `InvalidAgentResponseError: Received TaskStatusUpdateEvent in message mode.`

**Bridge fix:** The streaming drain loop emits token chunks to **SSE only**
(per-chunk, for real-time streaming). A **single accumulated Message** is
emitted to the SDK EventQueue after the stream completes. Status events
(WORKING/COMPLETED) go to SSE only, not the SDK EventQueue.

This means `message/send` returns a single JSON-RPC response with the full
text, while SSE clients receive real-time token streaming.

---

## 5. End-to-End Flows

### 5.1 Non-Streaming (`message/send` with `operation: "message_response"`)

```
1. Client sends:
   POST /gpt/a2a  (through gateway)
   { "jsonrpc": "2.0", "method": "message/send",
     "params": { "message": { "role": "ROLE_USER", "parts": [{"text": "Hello"}] },
                 "metadata": { "operation": "message_response", "agent_uuid": "hermes-agent" } } }

2. Gateway → dispatch_a2a → A2ADaemonExecutor.execute()
   → resolve_agent("hermes-agent") → PostgreSQL DB returns metadata
     → module_name = a2a_hermes_handler, class_name = HermesAgentHandler
     → hermes_api_url, hermes_api_key, hermes_model from metadata
   → execute_ai_agent_non_streaming()
     → handler.ask_model(input_messages, context)
       → POST http://127.0.0.1:8642/v1/chat/completions
       ← {"choices": [{"message": {"content": "Hello!"}}]}
   → emit single agent text Message to SDK EventQueue
   (no status events — SDK v2 rejects TaskStatusUpdateEvent)

3. Client receives:
   {"jsonrpc": "2.0", "result": {"role": "ROLE_AGENT", "parts": [{"text": "Hello!"}]}}
```

### 5.2 Streaming (`message/send` with `operation: "task_execution"` + `stream: true`)

```
1. Client sends:
   POST /gpt/a2a  (through gateway)
   { "method": "message/send",
     "params": { "message": {...}, "metadata": {
       "operation": "task_execution", "agent_uuid": "hermes-agent",
       "stream": true, "task_data": {"task_id": "..."} } } }

2. Executor → _handle_task_execution()
   → execute_ai_agent_streaming()
     [background thread]:
       → POST /v1/runs → { "run_id": "run_abc" }
       → GET /v1/runs/run_abc/events (SSE)
         {"event": "message.delta", "delta": "Hello"}  → SSE only
         {"event": "message.delta", "delta": "!"}       → SSE only
         {"event": "run.completed"}                    → break
     [drain loop]:
       → token chunks → _emit_to_sse() only (real-time streaming)
       → stream_event set → loop exits
       → _emit_to_sdk(event_queue, "Hello!")  → single accumulated Message
       → _emit_status_to_sse("COMPLETED")     → SSE only (not SDK)

3. Client receives:
   - SSE stream: task_artifact events with per-token chunks in real-time
   - HTTP response: single JSON-RPC result with full text
```

### 5.3 Cancel (`tasks/cancel`)

```
1. Client sends: POST /gpt/a2a { "method": "tasks/cancel", "params": {"id": "task-123"} }
2. A2ADaemonExecutor._cancel_external_run("task-123")
   → handler.cancel_run("run_abc") → POST /v1/runs/run_abc/stop
   → stream_event.set() [unblocks drain loop]
3. Client receives: task-canceled or "Task not found" (if already completed)
```

**Note:** `message/send` does not register tasks in the SDK's
`ActiveTaskRegistry`, so `tasks/cancel` typically returns "Task not found"
for `message/send` tasks. This is an A2A SDK v2 limitation, not a bug.

---

## 6. Testing

### 6.1 Test Scripts Location

All Hermes integration test scripts are in:
`silvaengine_gateway/silvaengine_gateway/tests/`

| Script | Purpose | Services Required |
|--------|---------|-------------------|
| `test_hermes_handler.py` | 24 unit tests (mocked HTTP, no services) | None |
| `test_hermes_gateway_live.py` | 7 E2E tests through the gateway | Gateway + Hermes + PostgreSQL |
| `test_hermes_sse_live.py` | SSE streaming test with real-time chunks | Gateway + Hermes + PostgreSQL |
| `test_hermes_chatbot.py` | Interactive streaming chatbot | Gateway + Hermes + PostgreSQL |
| `register_hermes_agent.py` | Register hermes-agent in PostgreSQL | PostgreSQL |

### 6.2 Unit Tests (No Services Needed)

```bash
cd a2a_daemon_engine
python -m pytest a2a_daemon_engine/tests/test_hermes_handler.py -v
# → 24 passed
```

Coverage: non-streaming, streaming, token deltas, tool call/result, approval,
error, cancel, resolve_approval, config resolution, message conversion, headers.

### 6.3 E2E Gateway Tests (7 tests)

```bash
python silvaengine_gateway/tests/test_hermes_gateway_live.py
```

Test sequence:
1. Hermes API Server health & models
2. Gateway health & A2A GraphQL ping
3. Register/verify hermes-agent fixture
4. Non-streaming message/send → real Hermes response
5. Compatibility message/send (different prompt) → real Hermes response
6. CancelTask via gateway
7. Failure case: unknown agent + wrong Hermes key + correct key sanity

### 6.4 SSE Streaming Test

```bash
python silvaengine_gateway/tests/test_hermes_sse_live.py
# Or with custom prompt:
python silvaengine_gateway/tests/test_hermes_sse_live.py --prompt "Write a haiku about AI"
```

Opens an SSE connection, sends a streaming message, and prints real-time
chunks from Hermes. Also verifies the Hermes `/v1/runs` + `/events` path
directly for comparison.

### 6.5 Interactive Chatbot

```bash
python silvaengine_gateway/tests/test_hermes_chatbot.py
# With system prompt:
python silvaengine_gateway/tests/test_hermes_chatbot.py --system "You are a pirate"
```

Interactive chat with real-time streaming. Each message streams token chunks
via SSE as they arrive from Hermes. Type `quit` to exit, `clear` to reset
conversation history.

### 6.6 Agent Registration

```bash
cd a2a_daemon_engine
python a2a_daemon_engine/tests/register_hermes_agent.py
```

Reads the gateway `.env` for PostgreSQL and Hermes settings, then
inserts/updates the `hermes-agent` record with full metadata via direct SQL.

---

## 7. Configuration Reference

### 7.1 Per-Agent Metadata (highest priority — stored in DB)

| Key | Default | Description |
|-----|---------|-------------|
| `module_name` | *(required)* | `a2a_daemon_engine.handlers.a2a_hermes_handler` |
| `class_name` | *(required)* | `HermesAgentHandler` |
| `hermes_api_url` | `http://localhost:8642` | Hermes API Server base URL |
| `hermes_api_key` | *(empty)* | Bearer token for API Server auth |
| `hermes_model` | `hermes-agent` | Model name passed to Hermes |
| `hermes_timeout` | `300` | SSE stream timeout in seconds |

### 7.2 Global Fallbacks (env vars in gateway `.env`, lowest priority)

| Variable | Default | Description |
|----------|---------|-------------|
| `HERMES_API_URL` | `http://localhost:8642` | Hermes API Server base URL |
| `HERMES_API_KEY` | *(empty)* | Bearer token |
| `HERMES_MODEL` | `hermes-agent` | Model name |
| `HERMES_STREAM_TIMEOUT` | `300.0` | SSE stream timeout (seconds) |

### 7.3 Hermes Agent Side

| Variable | Description |
|----------|-------------|
| `API_SERVER_ENABLED` | Set to `true` to enable the API server |
| `API_SERVER_KEY` | Bearer token for API auth |
| `API_SERVER_CORS_ORIGINS` | Comma-separated allowed origins (optional) |

---

## 8. Known Issues & Design Decisions

### 8.1 GraphQL JSON Scalar Serialization Bug

The `silvaengine_utility` JSON scalar type has a serialization bug:
`JSON.serialize() missing 1 required positional argument: 'value'`. This
prevents fetching `metadata` via GraphQL queries. The bridge works around
this by:

1. Querying the agent via GraphQL **without** the `metadata` field
2. Falling back to **direct SQL** via SQLAlchemy to fetch the `metadata` column
3. Parsing the metadata JSON (which contains `module_name`, `class_name`, `hermes_*`)

This is implemented in `resolve_agent()` in `a2a_ai_agent_utility.py`.

### 8.2 A2A SDK v2 Streaming Constraints

The A2A SDK v2 `on_message_send` (used by `message/send`) rejects:
- Multiple `Message` objects → `InvalidAgentResponseError`
- `TaskStatusUpdateEvent` → "Received TaskStatusUpdateEvent in message mode"

**Bridge fix:**
- Token chunks → SSE only (per-chunk real-time streaming)
- Single accumulated `Message` → SDK EventQueue (after stream completes)
- Status events (WORKING/COMPLETED) → SSE only (not SDK EventQueue)
- Executor's `_handle_task_execution` → no status event emissions for `message/send`

### 8.3 Agent Registration via GraphQL

GraphQL mutations can't pass `metadata` as a JSON dict variable due to the
scalar bug (§8.1). The `register_hermes_agent.py` helper uses direct SQL
via `psycopg2` to insert/update the agent record with full metadata.

### 8.4 `resolve_agent()` Response Envelope

The `a2a_core_graphql()` method returns a wrapped response:
`{"statusCode": 200, "body": "..."}` with camelCase keys (`agentId`,
`agentName`). The `resolve_agent()` function unwraps the envelope and
normalizes camelCase to snake_case before checking for null fields.

### 8.5 Cancel via `message/send`

`message/send` does not register tasks in the SDK's `ActiveTaskRegistry`,
so `tasks/cancel` returns "Task not found". This is an A2A SDK v2 limitation.
The cancel endpoint still works — it just reports the task isn't in the
registry because `message/send` doesn't create an A2A task.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Agent not found: hermes-agent" | Agent not registered or `resolve_agent()` can't find it | Run `register_hermes_agent.py`; verify PostgreSQL is running |
| "AI agent error: Agent not found" | `resolve_agent()` returns None — metadata empty or DB down | Check PostgreSQL container; verify agent metadata has `module_name`/`class_name` |
| 401 from Hermes | Wrong `hermes_api_key` | Check agent metadata or `HERMES_API_KEY` in gateway `.env` |
| "Multiple Message objects received" | Bridge emits per-token Messages to SDK | Fixed — single accumulated Message emitted after stream completes |
| "TaskStatusUpdateEvent in message mode" | Executor emits status events for `message/send` | Fixed — status events go to SSE only, not SDK EventQueue |
| Streaming hangs | SSE not emitting `run.completed` | Check Hermes logs; verify run completes; check `HERMES_STREAM_TIMEOUT` |
| Cancel returns "Task not found" | `message/send` doesn't register in `ActiveTaskRegistry` | Known A2A SDK v2 limitation; not a bug |
| GraphQL `metadata` query fails | `silvaengine_utility` JSON scalar serialization bug | Bridge uses direct SQL fallback for metadata; GraphQL query omits `metadata` field |
| SSE connection returns 401 | Gateway auth rejects token | Set `GATEWAY_AUTH_PROVIDER=local` in gateway `.env` for local testing |

---

## 10. File Reference

### A2A Daemon Engine

| File | Role |
|------|------|
| `handlers/a2a_hermes_handler.py` | HermesAgentHandler — HTTP + SSE bridge to Hermes API Server |
| `handlers/a2a_ai_agent_utility.py` | Phase 10 bridge — resolve agent, load handler, execute, persist; response envelope unwrapping; direct SQL metadata fallback; streaming drain loop (single Message, SSE-only status) |
| `handlers/a2a_executor.py` | A2ADaemonExecutor — routes A2A tasks; no status events for message/send (SDK v2 constraint) |
| `handlers/a2a_core.py` | GraphQL handler — injects `partition_key` into GraphQL context for PG repos |
| `handlers/config.py` | Config singleton — `hermes_*` fields, `a2a_ai_agent_*` fields |
| `handlers/schema.py` | GraphQL schema — `a2aAgent` query uses camelCase `agentId` |

### SilvaEngine Gateway

| File | Role |
|------|------|
| `app.py` | `build_setting_from_env()` — includes `HERMES_*` and `A2A_AI_AGENT_*` env vars |
| `router_builder.py` | `_make_sync_handler` — extracts `partition_key` from Part-Id header |
| `routes.yaml` | A2A daemon engine route config — `/{endpoint_id}/a2a` for REST dispatch |

### Test Scripts (in `silvaengine_gateway/tests/`)

| File | Purpose |
|------|---------|
| `test_hermes_handler.py` | 24 unit tests with mocked HTTP |
| `test_hermes_gateway_live.py` | 7 E2E tests through the gateway |
| `test_hermes_sse_live.py` | SSE streaming test with real-time chunks |
| `test_hermes_chatbot.py` | Interactive streaming chatbot |
| `register_hermes_agent.py` | Agent registration via direct SQL |

### Hermes Agent API Server Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/chat/completions` | POST | Non-streaming chat (OpenAI-compatible) |
| `/v1/runs` | POST | Create a run, get `run_id` |
| `/v1/runs/{id}` | GET | Poll run state |
| `/v1/runs/{id}/events` | GET (SSE) | Stream token deltas, tool progress, lifecycle |
| `/v1/runs/{id}/stop` | POST | Cancel a running agent turn |
| `/v1/runs/{id}/approval` | POST | Resolve pending human approval |
| `/v1/models` | GET | List available models |
| `/health` | GET | Health check |

Auth: `Authorization: Bearer *** on all endpoints.