# A2A Daemon Engine — Development Plan

**Document Version:** 0.5.1
**Engine Version:** 0.0.1 package; Phase 6 targets A2A SDK v1.0
**Target Protocol:** A2A v1.0.0 (Google, Q1 2026)
**Last Updated:** 2026-05-02
**Status:** Phases 1–5 complete · Phase 6 compatibility cleanup in progress with initial code fixes applied · Phase 7 planned

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current Architecture](#2-current-architecture)
3. [Implementation Status](#3-implementation-status)
4. [Protocol Gap Analysis (v0.3 → v1.0)](#4-protocol-gap-analysis-v03--v10)
5. [Cross-Cutting Concerns](#5-cross-cutting-concerns)
6. [Development Roadmap](#6-development-roadmap)
7. [Ecosystem Integration](#7-ecosystem-integration)
8. [Testing & Compliance Strategy](#8-testing--compliance-strategy)
9. [Risks & Mitigations](#9-risks--mitigations)
10. [Quick Reference](#10-quick-reference)
11. [Success Criteria](#11-success-criteria)

---

## 1. Executive Summary

The **A2A Daemon Engine** is a production-grade service implementing the [A2A Protocol](https://a2a-protocol.org/) for distributed agent-to-agent communication and orchestration. Built on canonical A2A SDK patterns, it provides a multi-tenant, persistent, JWT-secured foundation for multi-agent systems within the SilvaEngine ecosystem.

The engine has been partially moved toward **A2A SDK v1.0** (`pyproject.toml` now declares `a2a-sdk[http-server] ^1.0.0`), but the runtime code still contains mixed v0.3/v1.0 assumptions. Phase 6 should therefore be treated as an active compatibility and migration phase, not a completed milestone. The strategic milestone remains full **v1.0 protocol compliance**: stronger type safety, `SCREAMING_SNAKE_CASE` task states, enterprise security readiness, the expanded 11-operation RPC surface, and a richer task-state machine.

### 1.1 Completed Phases (v0.2.0)

| Phase | Description | Status |
|-------|-------------|--------|
| 1–3 | Core SDK alignment — canonical [`AgentExecutor`](../a2a_daemon_engine/handlers/a2a_executor.py), [`DynamoDBA2ATaskStore`](../a2a_daemon_engine/handlers/a2a_taskstore.py), and async GraphQL wrappers | ✅ Complete |
| 4 | Server restructuring — A2A SDK Starlette app as primary; FastAPI mounted at `/rest` | ✅ Complete |
| 5 | Event-driven message delivery with exponential-backoff retry and DynamoDB status tracking | ✅ Complete |

### 1.2 Capabilities Delivered

- **Agent registry & capability-based discovery** (REST + GraphQL)
- **Task lifecycle management** with cancellation support
- **Asynchronous message routing** with HTTP POST delivery, 3-attempt exponential backoff (1s → 2s → 4s)
- **Multi-tenant data isolation** via composite partition keys (`{endpoint_id}#{part_id}`)
- **Dual authentication**: local JWT (HS256) and AWS Cognito (RS256 + JWKS)
- **Dual deployment**: HTTP (Uvicorn) and AWS Lambda (serverless)
- **Native A2A endpoints**: `/.well-known/agent-card.json` (auto-exposed) and JSON-RPC at root `/`

### 1.3 Strategic Direction (Phases 6–9)

| Milestone | Theme | Target |
|-----------|-------|--------|
| Phase 6 | Complete A2A SDK v1.0 compatibility, enum/state migration, and runtime verification | Immediate / Q3 2026 |
| Phase 7 | Streaming (SSE), multi-turn (`INPUT_REQUIRED` / `AUTH_REQUIRED`), standardized push notifications | Q3 2026 |
| Phase 8 | Production hardening — security, observability, TCK compliance | Q4 2026 |
| Phase 9 | Optional transports (gRPC), advanced extensions, ecosystem integrations | 2027+ |

---

## 2. Current Architecture

### 2.1 High-Level View

The daemon follows the canonical A2A SDK pattern: the SDK's Starlette application is the primary HTTP entrypoint, and the legacy FastAPI REST layer is mounted at `/rest` as a secondary management API.

```
┌──────────────────────────────────────────────────────────┐
│  A2AStarletteApplication (PRIMARY — port 8001)           │
│  ├── /.well-known/agent-card.json   (auto-exposed)       │
│  ├── /                              (native JSON-RPC)    │
│  ├── DefaultRequestHandler                               │
│  │   ├── A2ADaemonExecutor   (canonical AgentExecutor)   │
│  │   └── DynamoDBA2ATaskStore (persistent TaskStore)     │
│  └── EventQueue (async events)                           │
├──────────────────────────────────────────────────────────┤
│  FastAPI (mounted at /rest)                              │
│  ├── /rest/health                                        │
│  ├── /rest/{endpoint_id}/a2a_core_graphql                │
│  ├── /rest/a2a/{endpoint_id}/agents/*                    │
│  ├── /rest/a2a/{endpoint_id}/tasks/*                     │
│  └── /rest/a2a-jsonrpc          (consolidated JSON-RPC)  │
├──────────────────────────────────────────────────────────┤
│  Business Logic Layer                                    │
│  └── handlers/a2a_handlers.py (handshake, routing,       │
│      task assignment, message delivery)                  │
├──────────────────────────────────────────────────────────┤
│  Data Layer — DynamoDB (PynamoDB models)                 │
│  └── A2AAgent · A2ATask · A2AMessage · A2ASetting        │
└──────────────────────────────────────────────────────────┘
```

### 2.2 Design Principles

1. **Canonical SDK pattern** — no hand-rolled protocol routing where the SDK provides it ([`a2a_executor.py`](../a2a_daemon_engine/handlers/a2a_executor.py), [`a2a_taskstore.py`](../a2a_daemon_engine/handlers/a2a_taskstore.py))
2. **Multi-tenancy** — composite partition keys enforce hard data isolation between endpoints/tenants
3. **Protocol first** — native A2A JSON-RPC at `/` is primary; `/rest` is auxiliary for REST clients and admin tooling
4. **Event-driven** — async operations flow through the SDK `EventQueue` with persistent state in DynamoDB
5. **Pluggable auth** — `AUTH_PROVIDER` env var selects local JWT or Cognito at startup

### 2.3 Module Layout

```
a2a_daemon_engine/
├── main.py                      # Engine entry point, daemon lifecycle
├── handlers/
│   ├── a2a_executor.py          # Canonical AgentExecutor implementation
│   ├── a2a_taskstore.py         # DynamoDB-backed TaskStore
│   ├── a2a_server.py            # A2A protocol server wrapper
│   ├── a2a_handlers.py          # Business logic (handshake, routing, delivery)
│   ├── a2a_jsonrpc.py           # Consolidated JSON-RPC handler
│   ├── a2a_app.py               # FastAPI REST application
│   ├── a2a_core.py              # GraphQL schema entry
│   ├── a2a_utility.py           # Async DB wrappers
│   ├── auth_router.py           # /auth/* endpoints
│   ├── middleware.py            # FlexJWTMiddleware
│   ├── jwt_local.py             # Local HS256 JWT
│   ├── jwt_cognito.py           # Cognito RS256 JWT
│   └── config.py                # Config singleton
├── models/                      # PynamoDB models (Agent, Task, Message, Setting)
├── queries/  · mutations/  · types/   # GraphQL layer
└── tests/                       # Pytest suite (asyncio mode = auto)
```

---

## 3. Implementation Status

### 3.1 Components — Verified

| Component | File | Status | Notes |
|-----------|------|--------|-------|
| Engine entry & CLI | [`main.py`](../a2a_daemon_engine/main.py) | ⚠️ Partial | HTTP daemon path correctly awaits `server.serve()`; sync wrappers still use `asyncio.run()` and need host-context review |
| Canonical AgentExecutor | [`a2a_executor.py`](../a2a_daemon_engine/handlers/a2a_executor.py) | ✅ | Routes `task_execution`, `message_routing`, `agent_registration` |
| DynamoDB TaskStore | [`a2a_taskstore.py`](../a2a_daemon_engine/handlers/a2a_taskstore.py) | ✅ | In-memory event cache; production should externalize (Redis/DDB Streams) |
| A2A protocol server | [`a2a_server.py`](../a2a_daemon_engine/handlers/a2a_server.py) | ✅ | `AgentCard` declares `streaming=False`, `pushNotifications=False` (line 293) |
| Business handlers | [`a2a_handlers.py`](../a2a_daemon_engine/handlers/a2a_handlers.py) | ✅ | HTTP delivery + 3 retries, exponential backoff |
| FastAPI REST | [`a2a_app.py`](../a2a_daemon_engine/handlers/a2a_app.py) | ✅ | CORS allows `*` (line 98) — must be hardened pre-prod |
| Custom JSON-RPC | [`a2a_jsonrpc.py`](../a2a_daemon_engine/handlers/a2a_jsonrpc.py) | ⚠️ Legacy / Deprecated | Module now warns on import but still exists and is still called from the serverless `a2a()` path |
| GraphQL schema | [`a2a_core.py`](../a2a_daemon_engine/handlers/a2a_core.py), [`schema.py`](../a2a_daemon_engine/handlers/schema.py) | ✅ | Full CRUD on Agent / Task / Message / Setting |
| PynamoDB models | [`models/`](../a2a_daemon_engine/models/) | ✅ | Composite PK `endpoint_id#part_id` |
| Local JWT | [`jwt_local.py`](../a2a_daemon_engine/handlers/jwt_local.py) | ✅ | HS256 |
| Cognito JWT | [`jwt_cognito.py`](../a2a_daemon_engine/handlers/jwt_cognito.py) | ✅ | RS256 + JWKS rotation |

### 3.2 Components — Partial / Pending

| Component | Gap | Phase |
|-----------|-----|-------|
| `AgentCard` v1.0 fields | Missing `securitySchemes`, `extensions`, `supportedInterfaces`, `iconUrl` | 6 |
| `TaskState` enum | Uses lowercase v0.3 names; v1.0 is `SCREAMING_SNAKE_CASE` and adds `AUTH_REQUIRED` / `REJECTED` | 6 |
| `contextId` propagation | Not threaded through executor or TaskStore | 6 |
| Streaming (`SendStreamingMessage`, `SubscribeToTask`) | Not implemented; SSE not wired | 7 |
| Push notification config CRUD | Replaced by ad-hoc HTTP POST in `deliver_message_to_agent` | 7 |
| Multi-turn (`INPUT_REQUIRED` / `AUTH_REQUIRED`) | No state-machine transitions emitted | 7 |
| `ListTasks` (cursor pagination) | Helper exists in TaskStore but not exposed via SDK | 6 |
| Extended Agent Card | `supportsAuthenticatedExtendedCard=False` (server.py:316) | 8 |
| Test coverage | Minimal pytest fixtures; needs A2A TCK + integration suite | 8 |
| OpenTelemetry instrumentation | Optional dep declared but unused | 8 |
| `from __future__ import print_function` | Removed package-wide | 6 |

### 3.3 Code-Level Issues Discovered (2026-05-02 Audit)

These issues were found by inspecting the actual codebase and represent concrete bugs or gaps not fully captured in the plan above:

| ID | Severity | Issue | Location | Phase |
|----|----------|-------|----------|-------|
| CLI-1 | **Critical** | `asyncio.run()` used inside daemon method that already runs async — will fail under Uvicorn | [`main.py:399`](../a2a_daemon_engine/main.py#L399) — `asyncio.run(server.serve())` should be `await server.serve()` | 6 |
| CLI-2 | **High** | `a2a_executor.py:252` imports `handle_agent_registration` but only `handle_agent_handshake` exists — **import will fail at runtime** | [`a2a_executor.py:252`](../a2a_daemon_engine/handlers/a2a_executor.py#L252) vs [`a2a_handlers.py`](../a2a_daemon_engine/handlers/a2a_handlers.py) | 6 |
| CLI-3 | **Medium** | `a2a_executor.cancel()` now uses a compatibility resolver for `TaskState.CANCELED` / `TaskState.canceled`; still needs reference-client coverage against SDK v1.0 | [`a2a_executor.py`](../a2a_daemon_engine/handlers/a2a_executor.py) | 6 |
| CLI-4 | **High** | `cancel()` doesn't validate task is in a cancellable state — allows cancelling terminal/already-cancelled tasks | [`a2a_executor.py:277-315`](../a2a_daemon_engine/handlers/a2a_executor.py#L277) | 6 |
| CLI-5 | **High** | `a2a_jsonrpc.py` implements only 3 methods (`agent.getCard`, `agent.listSkills`, `ping`) — doesn't route through SDK `DefaultRequestHandler` at all | [`a2a_jsonrpc.py:94-180`](../a2a_daemon_engine/handlers/a2a_jsonrpc.py#L94) | 6 |
| CLI-6 | **High** | `a2a_jsonrpc.py:213` uses `asyncio.run()` in `process_a2a_jsonrpc_message_sync` — breaks inside async hosts | [`a2a_jsonrpc.py:213`](../a2a_daemon_engine/handlers/a2a_jsonrpc.py#L213) | 6 |
| CLI-7 | **Medium** | `Config.jwt_secret_key` defaults to `"CHANGEME"` with no startup validation — security risk in production | [`config.py:189`](../a2a_daemon_engine/handlers/config.py#L189) | **COMPLETED** - Now validates and rejects weak secrets at startup |
| CLI-8 | **Medium** | `TaskState` enum mapping in `_map_status_to_taskstate` uses v0.3 lowercase names (`input_required`) — must support v1.0 `SCREAMING_SNAKE_CASE` | [`a2a_taskstore.py:212-231`](../a2a_daemon_engine/handlers/a2a_taskstore.py#L212) | 6 |
| CLI-9 | **Medium** | `_map_status_to_taskstate` missing `AUTH_REQUIRED` and `REJECTED` states from v1.0 spec | [`a2a_taskstore.py:212-231`](../a2a_daemon_engine/handlers/a2a_taskstore.py#L212) | 6 |
| CLI-10 | **Medium** | `AgentCapabilities(streaming=False, pushNotifications=False)` — streaming/push notifications not yet functional, correctly declared as disabled | [`a2a_server.py:293-296`](../a2a_daemon_engine/handlers/a2a_server.py#L293) | 7 |
| CLI-11 | **Medium** | `list_tasks()` returns plain list with no cursor pagination support — not A2A v1.0 compliant | [`a2a_taskstore.py:352-386`](../a2a_daemon_engine/handlers/a2a_taskstore.py#L352) | 6 |
| CLI-12 | **Medium** | `_event_cache` is a plain dict with no size bound — can grow unbounded in long-running processes | [`a2a_taskstore.py:64`](../a2a_daemon_engine/handlers/a2a_taskstore.py#L64) | 7 |
| CLI-13 | **Medium** | Task model missing `contextId` field needed for v1.0 protocol grouping of related tasks | [`models/a2a_task.py`](../a2a_daemon_engine/models/a2a_task.py) | 6 |
| CLI-14 | **Low** | `_task_to_dict` uses `datetime.utcnow().isoformat()` — should use `datetime.now(timezone.utc)` for Python 3.11+ | [`a2a_taskstore.py:321,332`](../a2a_daemon_engine/handlers/a2a_taskstore.py#L321) | 6 |
| CLI-15 | **Low** | `handle_state_sync` in `a2a_handlers.py` calls `Config.a2a_core.a2a_core_graphql` synchronously — should be async | [`a2a_handlers.py:298`](../a2a_daemon_engine/handlers/a2a_handlers.py#L298) | 8 |
| CLI-16 | **Low** | `find_best_agent` does an `import json` inside the loop body — should be top-level | [`a2a_handlers.py:340`](../a2a_daemon_engine/handlers/a2a_handlers.py#L340) | 8 |
| CLI-17 | **Low** | Test suite is integration-only (requires DynamoDB) — no unit tests for handler logic, executor, or TaskStore | [`tests/`](../a2a_daemon_engine/tests/) | 8 |
| CLI-18 | **Medium** | Initial enum compatibility helper added for uppercase/lowercase SDK members; still needs verification against a real installed `a2a-sdk ^1.0.0` environment | [`a2a_executor.py`](../a2a_daemon_engine/handlers/a2a_executor.py), [`a2a_taskstore.py`](../a2a_daemon_engine/handlers/a2a_taskstore.py) | 6 |

---

## 4. Protocol Gap Analysis (v0.3 → v1.0)

A complete protocol-level review was performed in [`a2a-protocol-analysis.md`](a2a-protocol-analysis.md) (2026-05-02). The findings below summarize what applies to this engine specifically.

### 4.1 Critical (SDK Upgrade Required)

| Gap | Current | v1.0 Requirement | Impact |
|-----|---------|------------------|--------|
| SDK version | `a2a-sdk[http-server] ^0.3.0` ([pyproject.toml:64](../pyproject.toml#L64)) | `^1.0.0` | Breaking changes across types & enums |
| RPC operations | 4 effective methods | 11 normative methods | Missing 7 core RPCs |
| Enum casing | kebab-case (`input-required`) | `SCREAMING_SNAKE_CASE` (`INPUT_REQUIRED`) | All persisted state strings must migrate |
| Task states | 5 in use (`submitted`, `working`, `input_required`, `completed`, `canceled`, `failed`, `unknown`) | 7 canonical (`WORKING`, `INPUT_REQUIRED`, `AUTH_REQUIRED`, `COMPLETED`, `FAILED`, `CANCELED`, `REJECTED`) | Add `AUTH_REQUIRED`, `REJECTED` |
| Type system source of truth | Pydantic | Protobuf (normative); Pydantic generated from it | Stricter validation surface |

**Canonical v1.0 Task State Machine:**
```
                      ┌─→ COMPLETED
                      │
WORKING ─→ INPUT_REQUIRED ─→ COMPLETED / FAILED
        │
        ├─→ AUTH_REQUIRED  ─→ COMPLETED / FAILED
        │
        ├─→ FAILED   (terminal)
        ├─→ CANCELED (terminal)
        └─→ REJECTED (terminal)
```

### 4.2 Major (Feature Implementation)

| Feature | Status | Priority | Notes |
|---------|--------|----------|-------|
| Agent Card v1.0 fields | Minimal | P0 | Add `capabilities`, `securitySchemes`, `skills` (typed), `extensions`, `supportedInterfaces` |
| Streaming (SSE) | Not implemented | P0 | `SendStreamingMessage` + `SubscribeToTask` |
| Push notification config | Custom HTTP POST | P1 | Migrate to `Create/Get/List/DeleteTaskPushNotificationConfig` |
| `contextId` | Not implemented | P1 | Group related tasks/messages |
| Extended Agent Card | Not implemented | P2 | Authenticated discovery (`GetExtendedAgentCard`) |
| Extension declaration | Not implemented | P2 | Register Traceability + Secure Passport |
| `ListTasks` cursor pagination | Helper only | P1 | Plumb to RPC surface |
| JWS Agent Card signing | Not implemented | P2 | Required for zero-trust deployments |

### 4.3 Required v1.0 Operation Coverage

| Operation | Current | Action |
|-----------|---------|--------|
| `SendMessage` | Custom path | Implement via SDK `DefaultRequestHandler` |
| `SendStreamingMessage` | ❌ | Implement SSE in executor |
| `GetTask` | Partial | Complete via TaskStore |
| `ListTasks` | ❌ (helper exists) | Add cursor pagination |
| `CancelTask` | Partial | Full state-machine compliance |
| `SubscribeToTask` | ❌ | Implement SSE re-subscribe with `Last-Event-ID` |
| `CreateTaskPushNotificationConfig` | ❌ | Replace `deliver_message_to_agent` |
| `GetTaskPushNotificationConfig` | ❌ | New |
| `ListTaskPushNotificationConfigs` | ❌ | New |
| `DeleteTaskPushNotificationConfig` | ❌ | New |
| `GetExtendedAgentCard` | ❌ | Authenticated endpoint |

### 4.4 Protocol-Level Gaps Inherited from A2A v1.0

These are gaps in the **specification itself** (per [`a2a-protocol-analysis.md`](a2a-protocol-analysis.md) §4) that we should design around defensively:

| Gap | Our Mitigation |
|-----|----------------|
| No standard registry API | Continue using our GraphQL discovery; revisit if A2A v1.1 standardizes one |
| No protocol-level rate limiting / backpressure | Implement HTTP `429 Retry-After` convention; advertise per-skill limits in metadata |
| No cancellation propagation downstream | When daemon delegates, propagate `CancelTask` via `referenceTaskIds` |
| Streaming reconnection fragility | Maintain a 100-event replay buffer per task; honor `Last-Event-ID` |
| No standard health/liveness | Keep `/rest/health`; document as engine-specific |
| No cost/quota visibility | Define a private extension under `https://silvaengine.com/extensions/` |
| Agent Card cache staleness | Emit `ETag`/`If-None-Match` and bump `version` on every change |

### 4.5 Architectural Decisions

**Keep:**
- AgentExecutor pattern (canonical)
- `DynamoDBA2ATaskStore` (persistent task state)
- Multi-tenant composite partition keys
- JWT model (local + Cognito)
- EventQueue integration

**Change:**
- Remove hand-rolled JSON-RPC routing → use SDK `DefaultRequestHandler` (deprecate [`a2a_jsonrpc.py`](../a2a_daemon_engine/handlers/a2a_jsonrpc.py))
- Demote `/rest` to admin-only API (clear Auth + scoping)
- Migrate task-state strings to `SCREAMING_SNAKE_CASE` (rewrite [`a2a_taskstore.py:_map_status_to_taskstate`](../a2a_daemon_engine/handlers/a2a_taskstore.py))
- Replace bespoke push notifications with A2A-standard `PushNotificationConfig`
- Remove `from __future__ import print_function` (Python 2 cruft) package-wide
- Replace `asyncio.run()` calls in [`main.py`](../a2a_daemon_engine/main.py) (lines 276, 284, 298) with `await` — they break inside async hosts

**Add:**
- `contextId` plumbing through executor and store
- SSE endpoints (`SendStreamingMessage`, `SubscribeToTask`) with replay buffer
- `ListTasks` with opaque cursor pagination
- `INPUT_REQUIRED` / `AUTH_REQUIRED` state transitions
- Traceability extension registration in Agent Card
- `ETag` / `Last-Modified` on Agent Card responses

---

## 5. Cross-Cutting Concerns

### 5.1 Security

- **Default credentials**: `JWT_SECRET_KEY` must reject `"CHANGEME"` / empty / weak values at startup
- **CORS**: `allow_origins=["*"]` in [`a2a_app.py:98`](../a2a_daemon_engine/handlers/a2a_app.py#L98) — make configurable; deny `*` if `Authorization` header expected
- **Push notification SSRF**: validate webhook URLs against an allowlist (or denylist private CIDRs) when implementing `PushNotificationConfig`
- **JWS Agent Card signing**: defer to Phase 8; required for cross-org deployments

### 5.2 Observability

- **Structured logging**: standardize on JSON logs with `partition_key`, `task_id`, `context_id`, `message_id` fields
- **OpenTelemetry**: add `traceparent` / `tracestate` propagation; instrument FastAPI + outbound `httpx` calls; export OTLP
- **Metrics**: emit task-state-transition counters, delivery-attempt histograms, retry counts

### 5.3 Multi-Tenancy

- All DynamoDB access flows through composite PK `endpoint_id#part_id` — verified across handlers
- Cross-tenant leakage tests must be part of the integration suite (Phase 8)

### 5.4 Async Hygiene

- Replace top-level `asyncio.run()` in [`main.py`](../a2a_daemon_engine/main.py) with proper async context to allow hosting under Lambda Powertools or Mangum
- Audit `a2a_core.a2a_core_graphql` calls inside async functions — currently mixed sync/async

---

## 6. Development Roadmap

Each phase below lists scope, key file touch-points, and concrete acceptance criteria.

### Phase 6 — A2A SDK v1.0 Upgrade & Compatibility Audit

**Goal:** Achieve A2A v1.0 protocol compliance at the type-system and core-RPC level.

**Status:** Partially implemented. Initial compatibility fixes are in place, but the codebase still needs runtime verification against an environment that can install `a2a-sdk ^1.0.0` and the private SilvaEngine dependencies.

| Task | Effort | Files / Locations | Status |
|------|--------|-------------------|--------|
| Fix broken `handle_agent_registration` import | 0.25d | [`a2a_executor.py`](../a2a_daemon_engine/handlers/a2a_executor.py) | **Done** - `_handle_agent_registration()` imports and calls `handle_agent_handshake` |
| Fix HTTP daemon `asyncio.run()` misuse | 0.5d | [`main.py`](../a2a_daemon_engine/main.py) | **Mostly done** - HTTP daemon awaits `server.serve()`; sync compatibility wrappers still need review |
| Bump `a2a-sdk` to `^1.0.0` | 0.5d | [`pyproject.toml:64`](../pyproject.toml#L64) | **Done** - dependency declaration updated |
| Verify SDK v1.0 imports and enum names at runtime | 0.5d | [`a2a_executor.py`](../a2a_daemon_engine/handlers/a2a_executor.py), [`a2a_taskstore.py`](../a2a_daemon_engine/handlers/a2a_taskstore.py) | **Partial** - compatibility helper now resolves uppercase and lowercase members; v1.0 install still blocked by private dependency availability |
| Migrate persisted `TaskState` strings to `SCREAMING_SNAKE_CASE` | 0.5d | [`a2a_taskstore.py`](../a2a_daemon_engine/handlers/a2a_taskstore.py); DynamoDB rows | **Partial** - model defaults and async wrappers now write uppercase; existing DynamoDB rows still need migration/backfill |
| Add `AUTH_REQUIRED`, `REJECTED` to status map | 0.5d | [`a2a_taskstore.py`](../a2a_daemon_engine/handlers/a2a_taskstore.py) | **Done in mapping** - still needs integration tests |
| Fix `cancel()` state-machine behavior | 0.25d | [`a2a_executor.py:277`](../a2a_daemon_engine/handlers/a2a_executor.py#L277) | **Mostly done** - terminal-state check and normalized enum resolution are in place; needs SDK reference-client coverage |
| Thread `contextId` through executor/store/model | 1d | [`a2a_executor.py`](../a2a_daemon_engine/handlers/a2a_executor.py), [`a2a_taskstore.py`](../a2a_daemon_engine/handlers/a2a_taskstore.py), [`models/a2a_task.py`](../a2a_daemon_engine/models/a2a_task.py) | **Partial** - model/store support exists; executor and handler propagation need end-to-end verification |
| Remove hand-rolled JSON-RPC from protocol path | 1d | [`a2a_jsonrpc.py`](../a2a_daemon_engine/handlers/a2a_jsonrpc.py), [`main.py`](../a2a_daemon_engine/main.py), [`a2a_app.py`](../a2a_daemon_engine/handlers/a2a_app.py) | **Partial** - SDK app is primary for HTTP, but deprecated JSON-RPC remains in REST/serverless paths |
| Implement `SendMessage` via SDK | 1d | [`a2a_executor.py`](../a2a_daemon_engine/handlers/a2a_executor.py), [`a2a_server.py`](../a2a_daemon_engine/handlers/a2a_server.py) | **Needs verification** - `DefaultRequestHandler` is wired, but reference-client coverage is not documented |
| Implement `GetTask` + `ListTasks` with cursor support | 1d | [`a2a_taskstore.py:list_tasks`](../a2a_daemon_engine/handlers/a2a_taskstore.py) | **Partial** - helper now matches the current GraphQL wrapper and returns `(tasks, next_token)`; SDK RPC exposure and tests are still required |
| Strip Python 2 `from __future__ import print_function` | 0.25d | Package-wide | **Done** - removed from package files |
| Add `createdAt` / `lastModified` to Task model | 0.5d | [`models/a2a_task.py`](../a2a_daemon_engine/models/a2a_task.py) | **Done in model** - migration/backfill strategy still needed for existing rows |

**Current Verified State:**
1. `pyproject.toml` now declares `a2a-sdk[http-server] ^1.0.0`.
2. `DynamoDBA2ATaskStore` contains uppercase v1.0 status mapping for `AUTH_REQUIRED` and `REJECTED`.
3. The HTTP daemon path builds the SDK Starlette app as primary and mounts FastAPI under `/rest`.
4. Deprecated custom JSON-RPC is still present and reachable through compatibility paths.
5. Package compilation succeeds in the local virtualenv.
6. Full dependency installation is blocked by unavailable private package distribution (`SilvaEngine-DynamoDB-Base`), so runtime compatibility with a fresh SDK v1.0 install is not yet proven.

**Phase 6 Acceptance Criteria:**
- All SDK enum references use the casing and symbols provided by installed `a2a-sdk ^1.0.0`.
- All new persisted task states are `SCREAMING_SNAKE_CASE`; legacy lowercase rows are normalized or migrated safely.
- `SendMessage`, `GetTask`, `ListTasks`, `CancelTask`, and `agent.getCard` pass against the SDK reference client.
- `ListTasks` cursor behavior has unit coverage and integration coverage against DynamoDB/local DynamoDB.
- A2A Inspector validates the Agent Card with no schema errors.
- Deprecated JSON-RPC compatibility paths are either removed from protocol traffic or explicitly documented as non-compliant legacy interfaces.
- Weak/default `JWT_SECRET_KEY` rejection is covered by tests.
- Pytest suite runs from the repository configuration without path mismatch or missing-fixture failures.

### Phase 7 — Streaming & Multi-Turn (≈1 week)

**Goal:** Real-time updates and interactive workflows.

| Task | Effort | Files / Locations |
|------|--------|-------------------|
| Implement `SendStreamingMessage` (SSE) | 2d | [`a2a_executor.py`](../a2a_daemon_engine/handlers/a2a_executor.py); EventQueue → SSE adapter |
| Implement `SubscribeToTask` with `Last-Event-ID` | 1d | New SSE replay buffer in TaskStore (last 100 events) |
| Emit `INPUT_REQUIRED` transitions | 1d | [`a2a_executor.py`](../a2a_daemon_engine/handlers/a2a_executor.py); add resume API |
| Emit `AUTH_REQUIRED` transitions | 1d | Wire to JWT failure → `AUTH_REQUIRED` event |
| Migrate to `PushNotificationConfig` CRUD | 2d | New `a2a_pushconfig.py`; deprecate ad-hoc HTTP POST in [`a2a_handlers.py:deliver_message_to_agent`](../a2a_daemon_engine/handlers/a2a_handlers.py) |
| Complete `CancelTask` (full state-machine) | 0.5d | [`a2a_executor.py:cancel`](../a2a_daemon_engine/handlers/a2a_executor.py#L277) |
| Webhook URL allowlist (anti-SSRF) | 0.5d | Validate against config-driven allowlist |
| Set `AgentCapabilities(streaming=True, pushNotifications=True)` | 0.25d | [`a2a_server.py:293`](../a2a_daemon_engine/handlers/a2a_server.py#L293) |

**Acceptance criteria:**
- SSE stream survives reconnect with no event loss (verified via integration test)
- `INPUT_REQUIRED` round-trip works end-to-end with reference client
- Push-notification webhook rejects loopback / private CIDR URLs by default
- All 11 v1.0 RPCs implemented

### Phase 8 — Production Hardening (≈1 week)

**Goal:** Security, observability, and TCK compliance.

| Task | Effort | Files / Locations |
|------|--------|-------------------|
| Implement `GetExtendedAgentCard` | 1d | [`a2a_server.py:316`](../a2a_daemon_engine/handlers/a2a_server.py#L316); auth-gated route |
| Configurable CORS (no wildcard with auth) | 0.5d | [`a2a_app.py:98`](../a2a_daemon_engine/handlers/a2a_app.py#L98); env var `A2A_CORS_ORIGINS` |
| Reject weak `JWT_SECRET_KEY` at startup | 0.25d | [`config.py`](../a2a_daemon_engine/handlers/config.py) |
| Register Traceability extension in Agent Card | 0.5d | [`a2a_server.py`](../a2a_daemon_engine/handlers/a2a_server.py) |
| OpenTelemetry instrumentation (HTTP + outbound `httpx`) | 1d | New middleware; activate `[telemetry]` extras |
| `ETag` / `Last-Modified` on Agent Card | 0.5d | SDK-side hook or middleware |
| Comprehensive pytest suite (unit + integration) | 2d | [`tests/`](../a2a_daemon_engine/tests/) |
| A2A TCK compliance run | 1d | External harness; document any waivers |
| A2A Inspector validation | 0.25d | One-shot pre-release |
| Cross-tenant isolation tests | 0.5d | Integration tests with two `partition_key`s |

**Acceptance criteria:**
- A2A TCK passes with no protocol violations
- All endpoints emit OTLP traces with `traceparent` propagation
- Cross-tenant data access tests fail closed (HTTP 403/404)
- Coverage ≥ 70% on `handlers/`

### Phase 9 — Future Enhancements

- gRPC transport ([`main.py:401`](../a2a_daemon_engine/main.py#L401) currently `NotImplementedError`)
- GraphQL subscriptions for live agent/task updates
- Agent health monitoring & circuit breakers
- Rate limiting extension (per-skill quotas in Agent Card)
- Cancellation propagation down delegated chains
- Secure Passport extension if PII / cross-trust-boundary use cases emerge
- Cost/quota visibility extension

---

## 7. Ecosystem Integration

The daemon sits within the SilvaEngine ecosystem and complements other engines.

### 7.1 Position vs. MCP

| Layer | Protocol | Use Case |
|-------|----------|----------|
| Agent ↔ Agent | **A2A** (this engine) | Delegation, multi-agent orchestration |
| Agent ↔ Tool | **MCP** (e.g., MCP KG Inquirer) | Tool / resource integration |

A2A and MCP are **complementary**, not competing. The daemon should expose itself as an A2A server that other agents discover; tool calls inside agent execution should remain MCP.

### 7.2 Knowledge Graph Engine

- The Knowledge Graph Engine (separate repo at `../knowledge_graph_engine/`) can register itself as an A2A agent here for cross-engine task delegation.
- A future extension could declare "knowledge query" as an A2A skill backed by KG.

### 7.3 Multi-Agent Coordination

- Use `contextId` to group related tasks across agents in a workflow
- Use `referenceTaskIds` to link refinement / follow-up tasks
- Implement Traceability extension to maintain end-to-end trace IDs across agent hops

---

## 8. Testing & Compliance Strategy

### 8.1 Test Pyramid

| Layer | Tooling | Coverage Target |
|-------|---------|-----------------|
| Unit | `pytest` + `pytest-asyncio` | ≥ 70% on `handlers/` |
| Integration | `pytest` + local DynamoDB (Docker) | All RPC happy-paths + auth + multi-tenant isolation |
| Protocol compliance | A2A TCK | 100% pass on supported operations |
| Schema validation | A2A Inspector | Agent Card valid pre-release |
| Load | `locust` or similar | 100+ concurrent agents, 1000 tasks/min |

### 8.2 CI Gates (Phase 8)

- `ruff` + `black` (style)
- `mypy` (type check)
- `pytest --cov` (coverage threshold)
- A2A Inspector validation on every PR touching Agent Card
- A2A TCK on release branches

---

## 9. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| SDK v1.0 introduces unexpected breaking changes | High | High | Pin to specific minor version; track upstream changelog; gate upgrade behind integration suite |
| DynamoDB row migration (state casing) leaves stragglers | Medium | Medium | Idempotent migration script with dry-run mode; status-string normalization at read time during transition |
| SSE reconnection logic loses events without replay buffer | Medium | High | Mandate `Last-Event-ID` + 100-event ring buffer per task |
| Push-notification webhook used for SSRF | High | High | Strict allowlist + private-CIDR denylist; URL validation on `CreateTaskPushNotificationConfig` |
| Cross-tenant leakage via `partition_key` typo | Low | Critical | Cross-tenant integration tests; PK assembly via single helper (`_get_partition_key`) |
| Agent Card cached stale by clients | Medium | Medium | `ETag` + `version` field bumped on every change |
| `asyncio.run()` calls fail under Lambda | High | Medium | Replace with `await` (Phase 6 task) |
| Test coverage too low to detect regressions | High | High | Phase 8 dedicates 2 days to pytest suite; CI coverage gate |

---

## 10. Quick Reference

### 10.1 Environment Variables

```bash
# Server
A2A_TRANSPORT=http              # http | lambda | grpc (Phase 9)
PORT=8001
A2A_CONFIG_FILE=                # Optional JSON config

# Authentication
AUTH_PROVIDER=local             # local | cognito
JWT_SECRET_KEY=                 # Required; must NOT be "CHANGEME"
LOCAL_USER_FILE=                # Local provider user store
ADMIN_STATIC_TOKEN=             # Optional admin bypass token
COGNITO_USER_POOL_ID=
COGNITO_APP_CLIENT_ID=
COGNITO_APP_SECRET=
COGNITO_JWKS_URL=

# AWS / DynamoDB
REGION_NAME=us-east-1
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=

# Phase 8 additions (planned)
A2A_CORS_ORIGINS=               # Comma-separated; replaces "*"
A2A_PUSH_WEBHOOK_ALLOWLIST=     # CIDRs / hostnames allowed for push
OTEL_EXPORTER_OTLP_ENDPOINT=    # OpenTelemetry collector
```

### 10.2 Health & Discovery

```bash
# Engine health
curl http://localhost:8001/rest/health

# A2A Agent Card (auto-exposed by SDK)
curl http://localhost:8001/.well-known/agent-card.json

# Native A2A JSON-RPC at root
curl -X POST http://localhost:8001/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"agent.getCard","params":{},"id":1}'
```

### 10.3 Deployment Modes

| Mode | Entry | Notes |
|------|-------|-------|
| HTTP | `poetry run a2a-daemon` → Uvicorn on `:8001` | Default; A2A SDK app primary, FastAPI mounted at `/rest` |
| Lambda | `A2ADaemonEngine.a2a(...)` invoked from Lambda handler | Action-based or JSON-RPC dispatch |
| gRPC | Phase 9 | Currently raises `NotImplementedError` |

### 10.4 Key Codebase Metrics (verified 2026-05-01)

- **Codebase**: ~8,500 LoC across 43 Python files
- **Test coverage**: Limited (improvement targeted in Phase 8)
- **Storage**: DynamoDB with composite-key partitioning
- **Auth**: HS256 (local) + RS256 (Cognito)

---

## 11. Success Criteria

### 11.1 Functional

- Agents register and are discoverable by capability filter
- Tasks created, tracked, cancelled, and queryable
- Messages route reliably with retry + status tracking
- All 11 v1.0 RPCs implemented (post-Phase 7)
- Multi-turn flows (`INPUT_REQUIRED`, `AUTH_REQUIRED`) round-trip correctly

### 11.2 Performance Targets

- 100+ concurrent agents
- 1,000+ tasks/minute throughput
- < 100ms message routing latency (p50)
- < 500ms (p99) for `SendMessage` end-to-end

### 11.3 Reliability Targets

- 99.9% uptime
- Automatic retry with exponential backoff on transient failures
- Graceful degradation under load (no event loss in SSE streams)

### 11.4 Security Requirements

- Refuse to start with default / weak `JWT_SECRET_KEY`
- CORS restricted to configured origins
- JWT enforced on all non-public endpoints
- Multi-tenant data isolation verified by integration tests
- Push-notification webhooks validated against allowlist

### 11.5 Compliance

- A2A Inspector: Agent Card valid
- A2A TCK: 100% pass on implemented operations
- Coverage: ≥ 70% on `handlers/`

---

## Appendix A — Related Documents

- [`a2a-protocol-analysis.md`](a2a-protocol-analysis.md) — Deep protocol-level analysis (2026-05-02)
- [`DOCUMENTATION_INDEX.md`](DOCUMENTATION_INDEX.md) — Documentation map
- [Project README](../README.md) — Engine overview & quick start

## Appendix B — External References

- [A2A Protocol v1.0 Specification](https://a2a-protocol.org/v1.0.0/specification)
- [A2A Python SDK](https://github.com/a2aproject/a2a-python)
- [A2A Samples](https://github.com/a2aproject/a2a-samples)
- [A2A Inspector](https://github.com/a2aproject/a2a-inspector)
- [A2A TCK](https://github.com/a2aproject/a2a-tck)
- [A2A + MCP Comparison](https://a2a-protocol.org/v1.0.0/topics/a2a-and-mcp)
- [A2A Enterprise Guide](https://a2a-protocol.org/v1.0.0/topics/enterprise-ready)
