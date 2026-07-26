# OpenCLAW ↔ A2A Daemon Engine Integration — Design & Implementation Plan

> Plan to add an OpenCLAW bridge plugin alongside the existing Hermes bridge,
> following the same Phase 10 handler pattern. Companion to
> [`HERMES_INTEGRATION.md`](HERMES_INTEGRATION.md), which documents the working
> Hermes bridge this design mirrors.

**Status:** Planned — not yet implemented
**Date:** 2026-07-16

> ⚠️ **Provenance note.** The Hermes doc was written against a Hermes server
> running on this machine. There is **no OpenCLAW server running locally**, so
> this plan is built from the **published OpenCLAW documentation** (see §11),
> not from live probing. Every item in §10 (Open Questions) must be confirmed
> against a real OpenCLAW gateway before the code is trusted.

---

## 1. Overview

`OpenClawAgentHandler` will route A2A tasks to a running **OpenCLAW Gateway**
over HTTP + SSE, exactly as `HermesAgentHandler` does for Hermes. It plugs into
the **same Phase 10 bridge, same executor, same A2A protocol surface**; routing
stays purely data-driven via per-agent `module_name` / `class_name` metadata in
the agent registry. No executor or gateway change is required — only a new
handler class and its registration.

### Architecture

```
                    Client (test runner / chatbot / another A2A agent)
                              │
                         POST /{endpoint_id}/a2a
                              │
                              ▼
                 ┌──────────────────────────────────────────────┐
                 │   silvaengine_gateway (port 8765)            │
                 │   dispatch_a2a → A2ADaemonEngine.a2a()       │
                 │              A2ADaemonExecutor.execute()     │
                 │                  │                            │
                 │           Phase 10 Bridge                    │
                 │           resolve_agent(uuid)  (PostgreSQL)  │
                 │           load_agent_handler()               │
                 │             ├── module=hermes_handler → Hermes│
                 │             ├── module=core_engine_handler → gateway WS│
                 │             └── module=openclaw_handler        │
                 │                  → OpenClawAgentHandler        │
                 │                    → POST /v1/chat/completions │
                 │                      (stream:true → SSE)      │
                 └──────────────────────────────────────────────┘
                              │
                              ▼
                 ┌──────────────────────────────────────────────┐
                 │   OpenCLAW Gateway (default port 18789)        │
                 │   POST /v1/chat/completions (OpenAI-compat)   │
                 │   POST /v1/responses        (OpenResponses)   │
                 │   GET  /v1/models                             │
                 │   (no cancel / stop / approval endpoints)     │
                 └──────────────────────────────────────────────┘
```

---

## 2. How OpenCLAW differs from Hermes

These differences drive the whole design — do not assume Hermes parity.

| Aspect | Hermes | OpenCLAW | Consequence for the bridge |
|---|---|---|---|
| **Per-request agent selection** | ❌ server-side active profile only | ✅ `model: "openclaw/<agentId>"` **or** `x-openclaw-agent-id` header | OpenCLAW can serve many agents from one gateway; a per-agent `openclaw_agent_id` metadata key selects which one. This is the capability the Hermes bridge lacked. |
| **Streaming API** | two-step: `POST /v1/runs` → `GET /v1/runs/{id}/events` | one-step: `POST /v1/chat/completions` with `stream:true` → SSE | Simpler. No `run_id` round-trip; read `choices[].delta.content` until `data: [DONE]`. |
| **SSE event shape** | `{"event":"message.delta","delta":"..."}` | `{"choices":[{"delta":{"content":"..."}}]}` (chat) or `response.output_text.delta` (responses) | New chunk parser; do not reuse the Hermes event names. |
| **Cancel** | `POST /v1/runs/{id}/stop` | ❌ none documented | `cancel_run` is best-effort local only (close stream, set `stream_event`); OpenCLAW keeps running server-side. Must be documented, not silently swallowed. |
| **Human approval** | `POST /v1/runs/{id}/approval` | ❌ none documented | No `INPUT_REQUIRED` passthrough. Out of scope. |
| **Default port** | 8642 | 18789 | Config default differs. |

**Net:** the OpenCLAW bridge is *simpler* on streaming and *more capable* on
agent selection, but *weaker* on lifecycle control (no cancel/approval).

---

## 3. OpenCLAW API reference (from published docs — verify per §10)

| Endpoint | Method | Purpose |
|---|---|---|
| `/v1/chat/completions` | POST | OpenAI Chat Completions (streaming + non-streaming) |
| `/v1/responses` | POST | OpenResponses format (richer: reasoning, tool items) |
| `/v1/models` | GET | List agents (each agent appears as a model id) |
| `/v1/models/{id}` | GET | Single model/agent |
| `/v1/embeddings` | POST | Embeddings (not used by the bridge) |

- **Base URL / port:** `http://localhost:18789` (default).
- **Auth:** `Authorization: Bearer <token>` on every endpoint.
- **Agent selection:** `model` = `"openclaw"`, `"openclaw/default"`, or
  `"openclaw/<agentId>"`; **or** the `x-openclaw-agent-id` header.
- **Streaming:** `stream: true` → `Content-Type: text/event-stream`, lines are
  `event: <type>` / `data: <json>`, terminated by `data: [DONE]`.
  - chat/completions chunk: `choices[0].delta.content`
  - responses events: `response.output_text.delta`, `response.completed`,
    `response.failed`, plus item/lifecycle events.
- **No** cancel, stop, or approval endpoints are documented.

**Endpoint choice for the bridge:** use **`/v1/chat/completions`** for both
non-streaming and streaming. It is the closest analog to the existing Hermes
non-streaming path and its streaming chunk shape (`delta.content`) is trivial to
drain. `/v1/responses` is richer (reasoning/tool items) but more complex;
defer it unless a concrete need appears (§10.5).

---

## 4. Handler contract

`OpenClawAgentHandler` implements the **same narrow bridge contract** as
`HermesAgentHandler`, so the executor, drain loop, dual-path SSE emission, and
persistence are reused unchanged:

```python
class OpenClawAgentHandler:
    def __init__(self, logger, agent_config, setting=None, context=None,
                 http_transport=None): ...     # http_transport for MockTransport in tests

    def ask_model(self, input_messages, context,
                  stream_queue=None, stream_event=None) -> dict:
        # stream_queue is None -> _ask_non_streaming (single dict)
        # else               -> _ask_streaming (feeds stream_queue, sets stream_event)

    # Optional lifecycle — best-effort only (no OpenCLAW server-side stop):
    def cancel_run(self, run_id) -> bool: ...
```

Return shape (matches the bridge's `normalize_final_output`):
`{"content": str, "role": "agent", "metadata": {...}, "error": str|None}`.

**Streaming chunk protocol** the drain loop already understands
(`execute_ai_agent_streaming`): put `{"name": "token", "value": delta}` per
`delta.content`; `{"name": "error", "value": msg}` on failure; **no** `run_id`
or `approval` chunks (OpenCLAW has neither). Set `stream_event` in `finally`.

---

## 5. Configuration

### 5.1 Per-agent metadata (highest priority — stored in DB `a2a_agents.metadata`)

| Key | Default | Description |
|---|---|---|
| `module_name` | *(required)* | `a2a_daemon_engine.handlers.a2a_openclaw_handler` |
| `class_name` | *(required)* | `OpenClawAgentHandler` |
| `openclaw_api_url` | `http://localhost:18789` | OpenCLAW gateway base URL |
| `openclaw_api_key` | *(empty)* | Bearer token |
| `openclaw_agent_id` | *(empty)* | Selects the specific OpenCLAW agent; becomes `model: "openclaw/<id>"` (or the `x-openclaw-agent-id` header). Empty → `"openclaw"` (default agent). |
| `openclaw_agent_selector` | `model` | `model` or `header` — how to pass the agent id (§10.1). |
| `openclaw_timeout` | `300` | SSE stream timeout (seconds) |

### 5.2 Global fallbacks (gateway `.env`, lowest priority)

`OPENCLAW_API_URL`, `OPENCLAW_API_KEY`, `OPENCLAW_AGENT_ID`,
`OPENCLAW_STREAM_TIMEOUT` — resolved exactly like the `HERMES_*` vars in
`Config` (agent metadata → setting dict → Config default).

---

## 6. A2A state mapping

| OpenCLAW SSE (chat/completions) | A2A Task State | Bridge action |
|---|---|---|
| first `choices[].delta.role` | `WORKING` | begin; synthesize a local `run_id` for A2A task tracking |
| `choices[].delta.content` | `WORKING` | `token` chunk → A2A text artifact (SDK + SSE) |
| `finish_reason` / `data: [DONE]` | `COMPLETED` | set `stream_event`; persist final message |
| HTTP non-200 or malformed chunk | `FAILED` | `error` chunk; set `FAILED` |
| A2A `tasks/cancel` | `CANCELED` | **local** unblock only (close stream, set `stream_event`); OpenCLAW keeps running — see §2 |

No `INPUT_REQUIRED` / approval row: OpenCLAW exposes no approval mechanism.

---

## 7. Implementation tasks

Ordered; each independently verifiable. Mirrors how the Hermes bridge shipped.

| # | Task |
|---|---|
| O.1 | `handlers/a2a_openclaw_handler.py` — `OpenClawAgentHandler` with `_ask_non_streaming` (POST chat/completions, `stream:false`) and `_ask_streaming` (POST `stream:true`, drain `delta.content` → `stream_queue` until `[DONE]`). Injectable `http_transport`. |
| O.2 | Agent selection: build `model` as `openclaw/<openclaw_agent_id>` (default `"openclaw"`), or send `x-openclaw-agent-id` header, per `openclaw_agent_selector`. |
| O.3 | `Config`: add `openclaw_*` fields + `OPENCLAW_*` env resolution, mirroring the `hermes_*` block. Extend `resolve_agent`'s metadata/env injection the same way. |
| O.4 | `cancel_run`: best-effort local (no server call); document that OpenCLAW continues server-side. |
| O.5 | Docs: promote this file to an "as-built" reference (like HERMES_INTEGRATION.md) once verified; add a `register_openclaw_agent.py` sample using direct SQL (same JSON-scalar workaround as Hermes, §8.1 of the Hermes doc). |
| O.6 | Tests: `test_openclaw_handler.py` (unit, `httpx.MockTransport`, no services) covering non-streaming, streaming deltas, `[DONE]` termination, error, agent-id → model mapping, config resolution. Then live `test_openclaw_gateway_live.py` / `test_openclaw_sse_live.py` / `test_openclaw_chatbot.py` mirroring the Hermes live suite. |

**Reuse, don't fork:** O.1's `_to_openai_messages`, header building, and the
streaming drain contract are nearly identical to `a2a_hermes_handler.py`. Copy
the structure; the only real deltas are the endpoint, the chunk parser, agent
selection, and the absent cancel/approval.

---

## 8. Testing plan

| Script | Purpose | Services |
|---|---|---|
| `test_openclaw_handler.py` | unit, mocked HTTP | none |
| `test_openclaw_gateway_live.py` | E2E through the gateway (health, register, non-streaming, streaming, unknown-agent, wrong-key) | Gateway + OpenCLAW + PostgreSQL |
| `test_openclaw_sse_live.py` | SSE real-time chunks | Gateway + OpenCLAW + PostgreSQL |
| `test_openclaw_chatbot.py` | interactive streaming chat (filter SSE by `task_id`, per the cross-talk fix already in the Hermes chatbot) | Gateway + OpenCLAW + PostgreSQL |

A distinctive OpenCLAW test the Hermes suite can't have: **register two A2A
agents pointing at two different `openclaw_agent_id`s on the same gateway and
assert each answers as its own agent** — proving per-request selection (§2).

---

## 9. Reused infrastructure (no changes needed)

- `A2ADaemonExecutor` — routes by `operation`; already handler-agnostic.
- `execute_ai_agent_streaming` / `_non_streaming` — the drain loop and dual-path
  SSE emission already consume the `{"name":"token"/"error"}` chunk protocol.
- `resolve_agent` / `load_agent_handler` — pure metadata-driven; adding a handler
  is data, not code, on the executor side.
- SSE delivery (`/{ep}/a2a_sse`, `broadcast_to_partition`) and `context_id`
  conversation memory — inherited unchanged.

---

## 10. Open questions — MUST verify against a live OpenCLAW gateway

1. **Agent-selection mechanism.** The OpenResponses doc lists both
   `model: "openclaw/<agentId>"` and the `x-openclaw-agent-id` header; the
   ClawTrust guide mentions neither. Confirm which the target build honors, and
   whether it differs between `/v1/chat/completions` and `/v1/responses`.
   `openclaw_agent_selector` exists so we can switch without a code change.
2. **`GET /v1/models` agent listing.** Confirm each agent surfaces as a model id
   (`openclaw/<id>`), so registration tooling can enumerate available agents.
3. **Cancel.** Confirm there is truly no stop endpoint. If a newer build adds one,
   wire `cancel_run` to it (Hermes parity) instead of best-effort local.
4. **Auth modes.** Docs mention shared-secret Bearer **and** trusted-proxy header
   auth. The bridge assumes Bearer; confirm that's available in the deployment.
5. **chat/completions vs responses.** Default to chat/completions (§3). Revisit
   only if reasoning/tool-item fidelity from `/v1/responses` is required.
6. **Tool calls in the stream.** If OpenCLAW emits tool-call deltas, decide
   whether to surface them as A2A `tool_call`/`tool_result` chunks (Hermes does)
   or ignore them (metadata-only).

---

## 11. References

- OpenCLAW OpenResponses HTTP API: <https://docs.openclaw.ai/gateway/openresponses-http-api>
- OpenCLAW docs home: <https://docs.openclaw.ai/>
- OpenCLAW configuration reference: <https://docs.openclaw.ai/gateway/configuration-reference>
- ClawTrust OpenClaw API guide (port 18789, chat/completions SSE): <https://clawtrust.ai/blog/openclaw-api-guide>
- Existing bridge this mirrors: [`HERMES_INTEGRATION.md`](HERMES_INTEGRATION.md)
