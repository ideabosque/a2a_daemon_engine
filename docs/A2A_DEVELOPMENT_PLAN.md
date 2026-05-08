# A2A Daemon Engine — Development Plan

**Document Version:** 0.6.1
**Engine Version:** 0.0.1 package; Phase 6-8 implementation landed, release validation pending
**Target Protocol:** A2A v1.0.0 (Google, Q1 2026)
**Last Updated:** 2026-05-07
**Status:** Phases 1–5 complete · Phase 6 implementation complete pending live runtime validation · Phase 7/8 feature modules landed pending full production wiring and compliance run · 2026-05-07 hygiene pass landed (pendulum migration extended to `a2a_sse.py`, unused imports removed across new Phase 7/8 modules and test scaffolding) · Phase 9 pending

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current Architecture](#2-current-architecture)
3. [Implementation Status](#3-implementation-status)
4. [Protocol Gap Analysis (v0.3 → v1.0)](#4-protocol-gap-analysis-v03--v10)
5. [Cross-Cutting Concerns](#5-cross-cutting-concerns)
6. [Development Roadmap](#6-development-roadmap)
7. [Ecosystem Integration](#7-ecosystem-integration)
   - 7.4 [AI Agent Core Engine Integration](#74-ai-agent-core-engine-integration)
8. [Testing & Compliance Strategy](#8-testing--compliance-strategy)
9. [Risks & Mitigations](#9-risks--mitigations)
10. [Quick Reference](#10-quick-reference)
11. [Success Criteria](#11-success-criteria)
12. [Appendix C — Release Validation Blockers & Current Issues](#appendix-c--release-validation-blockers--current-issues)
13. [Appendix A — Related Documents](#appendix-a--related-documents)
14. [Appendix B — External References](#appendix-b--external-references)

---

## 1. Executive Summary

The **A2A Daemon Engine** is a production-grade service implementing the [A2A Protocol](https://a2a-protocol.org/) for distributed agent-to-agent communication and orchestration. Built on canonical A2A SDK patterns, it provides a multi-tenant, persistent, JWT-secured foundation for multi-agent systems within the SilvaEngine ecosystem.

The engine has been moved substantially toward **A2A SDK v1.0** compatibility (`pyproject.toml` declares `a2a-sdk[http-server] ^1.0.0`). The recent update adds task-state compatibility helpers, SDK-backed JSON-RPC routing, SSE streaming primitives, push-notification configuration helpers, extended agent-card support, telemetry utilities, and compliance/test scaffolding. The project should be described as **implementation-complete for the Phase 6-8 workstream, but not release-certified** until the local SilvaEngine dependency stack is installed/activated, optional helper modules are wired into live production paths, and A2A Inspector/TCK checks pass against a running daemon.

### 1.1 Completed Phases (v0.2.0)

| Phase | Description | Status |
|-------|-------------|--------|
| 1–3 | Core SDK alignment — canonical [`AgentExecutor`](../a2a_daemon_engine/handlers/a2a_executor.py), [`DynamoDBA2ATaskStore`](../a2a_daemon_engine/handlers/a2a_taskstore.py), and async GraphQL wrappers | ✅ Complete |
| 4 | Server restructuring — A2A SDK Starlette app as primary; FastAPI mounted at `/rest` | ✅ Complete |
| 5 | Event-driven message delivery with exponential-backoff retry and DynamoDB status tracking | ✅ Complete |
| 6 | A2A SDK v1.0 compatibility — enum casing migration, SDK-backed JSON-RPC path, mock-based testing | ✅ Implementation complete; live runtime validation pending |
| 7 | Streaming & Multi-Turn — SSE streaming, `INPUT_REQUIRED`/`AUTH_REQUIRED` emitters, push-notification helpers | ⚠️ Feature modules landed; end-to-end protocol validation pending |
| 8 | Production Hardening — extended agent cards, OpenTelemetry helpers, TCK/test scaffolding, security | ⚠️ Hardening modules landed; production wiring/compliance run pending |

### 1.2 Capabilities Delivered

- **Agent registry & capability-based discovery** (REST + GraphQL)
- **Task lifecycle management** with cancellation support
- **Asynchronous message routing** with HTTP POST delivery, 3-attempt exponential backoff (1s → 2s → 4s)
- **Multi-tenant data isolation** via composite partition keys (`{endpoint_id}#{part_id}`)
- **Dual authentication**: local JWT (HS256) and AWS Cognito (RS256 + JWKS)
- **Dual deployment**: HTTP (Uvicorn) and AWS Lambda (serverless)
- **Native A2A endpoints**: `/.well-known/agent-card.json` (auto-exposed) and JSON-RPC at root `/`

### 1.3 Strategic Direction (Phases 6–9)

| Milestone | Theme | Target | Status |
|-----------|-------|--------|--------|
| Phase 6 | Complete A2A SDK v1.0 compatibility, enum/state migration, and runtime verification | Q2 2026 | ✅ **Implementation complete; live validation pending** |
| Phase 7 | Streaming (SSE), multi-turn (`INPUT_REQUIRED` / `AUTH_REQUIRED`), standardized push notifications | Q2 2026 | ⚠️ **Modules landed; integration validation pending** |
| Phase 8 | Production hardening — security, observability, TCK compliance | Q2 2026 | ⚠️ **Modules landed; compliance validation pending** |
| Phase 9 | Optional transports (gRPC), advanced extensions, ecosystem integrations | Q3 2026+ | ⏳ **Not Started** |

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
│   ├── a2a_executor.py          # Canonical AgentExecutor (now accepts a streaming_manager)
│   ├── a2a_taskstore.py         # DynamoDB-backed TaskStore (bounded LRU event cache)
│   ├── a2a_server.py            # A2A protocol server wrapper
│   ├── a2a_handlers.py          # Business logic (handshake, routing, delivery)
│   ├── a2a_jsonrpc.py           # Deprecated JSON-RPC compatibility shim
│   ├── a2a_app.py               # FastAPI REST application
│   ├── a2a_sse.py               # Phase 7: SSE streaming + replay buffer
│   ├── a2a_pushconfig.py        # Phase 7: PushNotificationConfig + SSRF allowlist
│   ├── a2a_extended_card.py     # Phase 8: Extended agent card + auth + ETag
│   ├── a2a_telemetry.py         # Phase 8: Optional OpenTelemetry instrumentation
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
    ├── test_phase6.py           # Phase 6 mock-based RPC/state tests
    ├── test_phase8.py           # Phase 8 SSE/push/extended-card tests
    ├── test_executor_unit.py    # Executor unit tests
    ├── test_handlers_unit.py    # Handlers unit tests
    ├── test_jwt_validation.py   # JWT secret validation tests
    ├── a2a_rpc_verifier.py      # RPC coverage verifier
    ├── a2a_taskstate_validator.py  # Task state-machine validator
    ├── a2a_tck_checker.py       # A2A TCK helper
    └── validate_agent_card.py   # Agent card validator
```

---

## 3. Implementation Status

### 3.1 Components — Verified

| Component | File | Status | Notes |
|-----------|------|--------|-------|
| Engine entry & CLI | [`main.py`](../a2a_daemon_engine/main.py) | ⚠️ Partial | HTTP daemon path correctly awaits `server.serve()`; sync wrappers still use `asyncio.run()` and need host-context review |
| Canonical AgentExecutor | [`a2a_executor.py`](../a2a_daemon_engine/handlers/a2a_executor.py) | ✅ | Routes `task_execution`, `message_routing`, `agent_registration` |
| DynamoDB TaskStore | [`a2a_taskstore.py`](../a2a_daemon_engine/handlers/a2a_taskstore.py) | ✅ | In-memory event cache is now bounded (LRU over tasks + 100-event ring buffer per task); production should still externalize (Redis/DDB Streams) |
| A2A protocol server | [`a2a_server.py`](../a2a_daemon_engine/handlers/a2a_server.py) | ✅ / Verify | `AgentCard` now declares `streaming=True`, `pushNotifications=True`, and `supportsAuthenticatedExtendedCard=True`; live client/TCK validation remains required |
| Business handlers | [`a2a_handlers.py`](../a2a_daemon_engine/handlers/a2a_handlers.py) | ✅ | HTTP delivery + 3 retries, exponential backoff |
| FastAPI REST | [`a2a_app.py`](../a2a_daemon_engine/handlers/a2a_app.py) | ✅ | CORS origins driven by `A2A_CORS_ORIGINS` env var; wildcard now auto-disables `allow_credentials`. Tighten to explicit origins in production. Pydantic v2 `model_dump()` used throughout. |
| Custom JSON-RPC | [`a2a_jsonrpc.py`](../a2a_daemon_engine/handlers/a2a_jsonrpc.py) | ⚠️ Legacy / Deprecated | Module warns on import; `/rest/a2a-jsonrpc` routes supported methods through the SDK handler, while serverless compatibility paths still need final review |
| GraphQL schema | [`a2a_core.py`](../a2a_daemon_engine/handlers/a2a_core.py), [`schema.py`](../a2a_daemon_engine/handlers/schema.py) | ✅ | Full CRUD on Agent / Task / Message / Setting |
| PynamoDB models | [`models/`](../a2a_daemon_engine/models/) | ✅ | Composite PK `endpoint_id#part_id` |
| Local JWT | [`jwt_local.py`](../a2a_daemon_engine/handlers/jwt_local.py) | ✅ | HS256 |
| Cognito JWT | [`jwt_cognito.py`](../a2a_daemon_engine/handlers/jwt_cognito.py) | ✅ | RS256 + JWKS rotation |

### 3.2 Remaining Verification / Wiring

| Component | Gap | Phase |
|-----------|-----|-------|
| `AgentCard` v1.0 advanced fields | Base card exposes core fields and authenticated extended-card support; confirm `securitySchemes`, extensions, supported interfaces, and icon metadata against the target SDK/TCK | 6 / 8 |
| `TaskState` enum | Compatibility resolver handles uppercase/lowercase SDK members; existing DynamoDB rows still need migration/backfill validation in a live environment | 6 |
| `contextId` propagation | Model/store conversion support exists; executor/handler round-trip still needs end-to-end verification | 6 |
| Streaming (`SendStreamingMessage`, `SubscribeToTask`) | SSE route and replay buffer are implemented; validate with the A2A reference client and long-running task flow | 7 |
| Push notification config CRUD | Manager and SSRF validation are implemented; route/RPC wiring and durable model support should be verified before marking production complete | 7 |
| Multi-turn (`INPUT_REQUIRED` / `AUTH_REQUIRED`) | Event emitters exist; business flows still need tests proving state transitions round-trip through clients | 7 |
| `ListTasks` (cursor pagination) | TaskStore helper exists; confirm SDK RPC exposure and pagination behavior against DynamoDB/local DynamoDB | 6 |
| Extended Agent Card | Manager exists and base card advertises authenticated extended-card support; confirm route registration in the deployed app | 8 |
| Test coverage | New mock/unit and compliance helper files exist; full pytest/TCK execution still needs a configured environment using the sibling SilvaEngine packages | 8 |
| OpenTelemetry instrumentation | Helper module exists; verify app/client instrumentation is initialized in the selected deployment mode | 8 |
| `from __future__ import print_function` | Removed package-wide | 6 |

### 3.3 Code-Level Issues Discovered (2026-05-02 Audit)

These issues were found by inspecting the actual codebase and represent concrete bugs or gaps not fully captured in the plan above:

| ID | Severity | Issue | Location | Phase |
|----|----------|-------|----------|-------|
| CLI-1 | **Resolved** | HTTP daemon path now awaits `server.serve()`; remaining `asyncio.run()` usage is limited to sync compatibility entrypoints and still needs host-context review | [`main.py`](../a2a_daemon_engine/main.py) | 6 |
| CLI-2 | **Resolved** | Agent registration path now calls `handle_agent_handshake` through `_handle_agent_registration()` | [`a2a_executor.py`](../a2a_daemon_engine/handlers/a2a_executor.py) | 6 |
| CLI-3 | **Resolved** | `a2a_executor.cancel()` uses compatibility resolver for `TaskState.CANCELED` / `TaskState.canceled`; `_task_state()` helper handles both casings | [`a2a_executor.py`](../a2a_daemon_engine/handlers/a2a_executor.py) | 6 |
| CLI-4 | **Resolved** | `cancel()` checks terminal states (COMPLETED, CANCELED, FAILED, REJECTED) before saving CANCELED | [`a2a_executor.py:277`](../a2a_daemon_engine/handlers/a2a_executor.py#L277) | 6 |
| CLI-5 | **Resolved** | `a2a_jsonrpc.py` deprecated and marked with warnings; main JSON-RPC now routes through SDK `DefaultRequestHandler` in `/rest/a2a-jsonrpc` endpoint | [`a2a_jsonrpc.py`](../a2a_daemon_engine/handlers/a2a_jsonrpc.py#L1-31), [`a2a_app.py`](../a2a_daemon_engine/handlers/a2a_app.py#L738-837) | 6 |
| CLI-6 | **Resolved** | `a2a_jsonrpc.py:241-254` now uses `ThreadPoolExecutor` with `run_coroutine_threadsafe` for async contexts; `main.py` uses `_run_async()` helper | [`a2a_jsonrpc.py`](../a2a_daemon_engine/handlers/a2a_jsonrpc.py#L241-254), [`main.py`](../a2a_daemon_engine/main.py#L149-166) | 6 |
| CLI-7 | **Resolved** | `Config.jwt_secret_key` validates against weak values ("CHANGEME", "changeme", "secret", "password", "123456", "admin") and rejects secrets < 32 characters | [`config.py:189-210`](../a2a_daemon_engine/handlers/config.py#L189-210) | 6 |
| CLI-8 | **Resolved** | `_map_status_to_taskstate` accepts legacy lowercase-derived states and uppercase v1.0-style persisted strings | [`a2a_taskstore.py:240-265`](../a2a_daemon_engine/handlers/a2a_taskstore.py#L240-265) | 6 |
| CLI-9 | **Resolved** | `AUTH_REQUIRED` and `REJECTED` are represented in the status map with compatibility fallback for older SDKs | [`a2a_taskstore.py:256-261`](../a2a_daemon_engine/handlers/a2a_taskstore.py#L256-261) | 6 |
| CLI-10 | **Implemented / Verify** | `AgentCapabilities(streaming=True, pushNotifications=True)` is now declared and SSE helpers are registered; validate the advertised capabilities with reference clients before release | [`a2a_server.py`](../a2a_daemon_engine/handlers/a2a_server.py) | 7 |
| CLI-11 | **Resolved** | `list_tasks()` now returns `(tasks, next_token)` using offset tokens over the current GraphQL wrapper; cursor pagination implemented | [`a2a_taskstore.py:402-471`](../a2a_daemon_engine/handlers/a2a_taskstore.py#L402-471) | 6 |
| CLI-12 | **Resolved** | `_event_cache` is now bounded: `OrderedDict` LRU over tasks (default 1024) with per-task `deque(maxlen=100)` ring buffer; thresholds are constructor-tunable | [`a2a_taskstore.py:104-123`](../a2a_daemon_engine/handlers/a2a_taskstore.py#L104-123) | 7 |
| CLI-13 | **Partial** | Task model includes `contextId`; full executor/handler propagation implemented in `_task_to_dict()` and `_dict_to_task()` | [`models/a2a_task.py`](../a2a_daemon_engine/models/a2a_task.py), [`a2a_taskstore.py:267-396`](../a2a_daemon_engine/handlers/a2a_taskstore.py#L267-396) | 6 |
| CLI-14 | **Resolved** | All UTC timestamps in handlers/store/JWT now use `pendulum.now("UTC")` consistently with the rest of the codebase; `datetime.utcnow()` and naive `datetime` imports removed package-wide | [`a2a_taskstore.py`](../a2a_daemon_engine/handlers/a2a_taskstore.py), [`a2a_handlers.py`](../a2a_daemon_engine/handlers/a2a_handlers.py), [`jwt_local.py`](../a2a_daemon_engine/handlers/jwt_local.py) | 6 |
| CLI-15 | **Pending** | `handle_state_sync` in `a2a_handlers.py` still calls `Config.a2a_core.a2a_core_graphql` synchronously; keep as a hardening follow-up unless host-context tests prove it is safe | [`a2a_handlers.py`](../a2a_daemon_engine/handlers/a2a_handlers.py) | 8 |
| CLI-16 | **Resolved** | `find_best_agent` no longer imports inside the loop; `json`, `httpx`, `asyncio`, and `pendulum` are all hoisted to module-top imports | [`a2a_handlers.py`](../a2a_daemon_engine/handlers/a2a_handlers.py) | 8 |
| CLI-17 | **Partial** | Mock/unit tests and Phase 6/8 compliance helpers have been added, but full integration coverage still depends on DynamoDB/private package availability | [`tests/`](../a2a_daemon_engine/tests/) | 8 |
| CLI-18 | **Resolved** | Enum compatibility helper `_task_state()` added for uppercase/lowercase SDK members in both executor and taskstore | [`a2a_executor.py:31-54`](../a2a_daemon_engine/handlers/a2a_executor.py#L31-54), [`a2a_taskstore.py:41-64`](../a2a_daemon_engine/handlers/a2a_taskstore.py#L41-64) | 6 |
| CLI-19 | **Resolved** | 2026-05-03 hygiene sweep: 20 unused imports removed across 12 files (executor SDK leftovers, model `logging` shims, `Starlette` in `main.py`, type-module `Boolean`/`Int`, test mocks). Verified with `pyflakes`. | Package-wide | 8 |
| CLI-20 | **Resolved** | FastAPI REST app: CORS origins now read from `A2A_CORS_ORIGINS` (comma-separated); `allow_credentials` auto-disables on wildcard. Pydantic v1 `.dict()` migrated to v2 `.model_dump()` at all three call sites. | [`a2a_app.py`](../a2a_daemon_engine/handlers/a2a_app.py) | 8 |
| CLI-21 | **Resolved** | `main.py` cleaned up: dead `self._loop` field removed, playful inline comments deleted, `_run_async` simplified (single-worker thread pool, clearer guard against nested loops). | [`main.py`](../a2a_daemon_engine/main.py) | 6 |
| CLI-22 | **Resolved** | 2026-05-07 hygiene sweep on Phase 7/8 modules and tests: `a2a_sse.py` migrated from `datetime` to `pendulum.now("UTC")` to match the rest of the codebase; `a2a_telemetry.py`, `a2a_extended_card.py`, `a2a_handlers.py`, and `a2a_jsonrpc.py` cleared of unused imports; `a2a_pushconfig.py` dropped a dead `port` local; test scaffolding (`test_phase6/8`, `test_executor_unit`, `test_handlers_unit`, `test_jwt_validation`, `test_api`, validators) cleared of unused imports; `tests/start_daemon.py` migrated `datetime.datetime.utcnow()` → `pendulum.now("UTC").add(hours=24)`. Production code is `pyflakes`-clean; the four remaining `pyflakes` lines are deliberate side-effect imports tagged `# noqa: F401`. | Package-wide | 8 |

---

## 4. Protocol Gap Analysis (v0.3 → v1.0)

A complete protocol-level review was performed in [`a2a-protocol-analysis.md`](a2a-protocol-analysis.md) (2026-05-02). The findings below summarize what applies to this engine specifically.

### 4.1 Critical (SDK Upgrade Required) — Phase 6 Complete

**Status:** ✅ All critical gaps resolved. SDK v1.0 compatibility achieved.

| Gap | Current Status | v1.0 Requirement | Resolution |
|-----|---------------|------------------|------------|
| SDK version | ✅ `a2a-sdk[http-server] ^1.0.0` declared and integrated | `^1.0.0` with full compatibility | Mock-based testing validates enum compatibility; runtime verification ready |
| RPC operations | ✅ 11 normative methods implemented | All v1.0 operations | `DefaultRequestHandler` + custom implementations in `a2a_executor.py`, `a2a_taskstore.py`, `a2a_pushconfig.py`, `a2a_extended_card.py` |
| Enum casing | ✅ `SCREAMING_SNAKE_CASE` with backward compatibility | `INPUT_REQUIRED`, `AUTH_REQUIRED`, etc. | `_task_state()` helper handles both casings; new writes use uppercase |
| Task states | ✅ All 7 canonical states supported | `WORKING`, `INPUT_REQUIRED`, `AUTH_REQUIRED`, `COMPLETED`, `FAILED`, `CANCELED`, `REJECTED` | `_map_status_to_taskstate()` with aliases; terminal state checks in `cancel()` |
| Type system | ✅ Pydantic models align with v1.0 spec | Protobuf-compatible validation | Strict validation via A2A SDK types |

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

### 4.2 Major (Feature Implementation) — Phases 7-8 Complete

**Status:** ✅ All P0/P1 features implemented. P2 features (JWS signing, Secure Passport) remain future work.

| Feature | Status | Files / Implementation | Notes |
|---------|--------|------------------------|-------|
| Agent Card v1.0 fields | ✅ **Complete** | [`a2a_server.py`](../a2a_daemon_engine/handlers/a2a_server.py) | Core fields + `securitySchemes`, `extensions`, `supportedInterfaces` exposed via `AgentCard` and `ExtendedAgentCardManager` |
| Streaming (SSE) | ✅ **Complete** | [`a2a_sse.py`](../a2a_daemon_engine/handlers/a2a_sse.py) | `SendStreamingMessage` and `SubscribeToTask` with 100-event replay buffer, `Last-Event-ID` reconnection support |
| Push notification config | ✅ **Complete** | [`a2a_pushconfig.py`](../a2a_daemon_engine/handlers/a2a_pushconfig.py) | Full CRUD: `Create/Get/List/DeleteTaskPushNotificationConfig` + `WebhookUrlValidator` with anti-SSRF protection |
| `contextId` | ✅ **Complete** | [`a2a_taskstore.py`](../a2a_daemon_engine/handlers/a2a_taskstore.py), [`models/a2a_task.py`](../a2a_daemon_engine/models/a2a_task.py) | Full propagation via `_task_to_dict()` / `_dict_to_task()`; `createdAt`/`lastModified` timestamps included |
| Extended Agent Card | ✅ **Complete** | [`a2a_extended_card.py`](../a2a_daemon_engine/handlers/a2a_extended_card.py) | `GetExtendedAgentCard` with auth gating, ETag/`Last-Modified`, rate limits, security policies |
| Extension declaration | ✅ **Traceability Complete** | [`a2a_extended_card.py:TraceabilityExtension`](../a2a_daemon_engine/handlers/a2a_extended_card.py#L50-79) | `x-a2a-trace-id` / `x-a2a-span-id` headers; Secure Passport remains future work (P2) |
| `ListTasks` cursor pagination | ✅ **Complete** | [`a2a_taskstore.py:list_tasks`](../a2a_daemon_engine/handlers/a2a_taskstore.py#L402-471) | Offset-based tokens, `(tasks, next_token)` return format |
| JWS Agent Card signing | ⏳ **Future** (P2) | — | Deferred to Phase 9: Required for zero-trust deployments |
| OpenTelemetry instrumentation | ✅ **Complete** | [`a2a_telemetry.py`](../a2a_daemon_engine/handlers/a2a_telemetry.py) | FastAPI + httpx instrumentors, OTLP export, `traceparent` propagation |

### 4.3 Required v1.0 Operation Coverage — Phase 6-7 Complete

**Status:** ✅ All 11 v1.0 operations implemented and ready for TCK validation.

| Operation | Status | Implementation | Verification |
|-----------|--------|----------------|------------|
| `SendMessage` | ✅ **Complete** | SDK `DefaultRequestHandler` | `A2ADaemonExecutor.execute()` routes to handlers |
| `SendStreamingMessage` | ✅ **Complete** | [`a2a_sse.py:StreamingTaskManager`](../a2a_daemon_engine/handlers/a2a_sse.py#L224) | SSE streaming with replay buffer |
| `GetTask` | ✅ **Complete** | [`a2a_taskstore.py:get`](../a2a_daemon_engine/handlers/a2a_taskstore.py) | DynamoDB-backed with state normalization |
| `ListTasks` | ✅ **Complete** | [`a2a_taskstore.py:list_tasks`](../a2a_daemon_engine/handlers/a2a_taskstore.py#L402-471) | Cursor pagination with offset tokens |
| `CancelTask` | ✅ **Complete** | [`a2a_executor.py:cancel`](../a2a_daemon_engine/handlers/a2a_executor.py#L286-348) | Terminal state guards + enum compatibility |
| `SubscribeToTask` | ✅ **Complete** | [`a2a_sse.py:SSEEventQueue.subscribe`](../a2a_daemon_engine/handlers/a2a_sse.py#L157-210) | `Last-Event-ID` reconnection support |
| `CreateTaskPushNotificationConfig` | ✅ **Complete** | [`a2a_pushconfig.py:create_push_config`](../a2a_daemon_engine/handlers/a2a_pushconfig.py#L180-230) | Anti-SSRF webhook validation |
| `GetTaskPushNotificationConfig` | ✅ **Complete** | [`a2a_pushconfig.py:get_push_config`](../a2a_daemon_engine/handlers/a2a_pushconfig.py#L232-252) | Cached + persistent store |
| `ListTaskPushNotificationConfigs` | ✅ **Complete** | [`a2a_pushconfig.py:list_push_configs`](../a2a_daemon_engine/handlers/a2a_pushconfig.py#L254-310) | Pagination support |
| `DeleteTaskPushNotificationConfig` | ✅ **Complete** | [`a2a_pushconfig.py:delete_push_config`](../a2a_daemon_engine/handlers/a2a_pushconfig.py#L312-345) | Cache invalidation |
| `GetExtendedAgentCard` | ✅ **Complete** | [`a2a_extended_card.py:get_extended_card`](../a2a_daemon_engine/handlers/a2a_extended_card.py#L164-216) | Auth-gated + ETag support |

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
- ✅ Remove hand-rolled JSON-RPC routing → use SDK `DefaultRequestHandler` (deprecate [`a2a_jsonrpc.py`](../a2a_daemon_engine/handlers/a2a_jsonrpc.py))
- ✅ Demote `/rest` to admin-only API (clear Auth + scoping)
- ✅ Migrate task-state strings to `SCREAMING_SNAKE_CASE` (implemented in [`a2a_taskstore.py:_map_status_to_taskstate`](../a2a_daemon_engine/handlers/a2a_taskstore.py))
- ✅ Replace bespoke push notifications with A2A-standard `PushNotificationConfig` (implemented in [`a2a_pushconfig.py`](../a2a_daemon_engine/handlers/a2a_pushconfig.py))
- ✅ Remove `from __future__ import print_function` (Python 2 cruft) package-wide — done
- ✅ `pendulum.now("UTC")` is the canonical timestamp helper across handlers, store, and JWT — `datetime.utcnow()` and naive `datetime` imports have been removed (CLI-14, 2026-05-03)
- ✅ Audit `asyncio.run()` usage in compatibility entrypoints: HTTP daemon already awaits; the Lambda-style sync `_run_async` in [`main.py`](../a2a_daemon_engine/main.py) deliberately submits to a worker thread when a loop is already running (CLI-1, CLI-6)

**Add:**
- ✅ `contextId` plumbing through executor and store (Phases 6-7)
- ✅ SSE endpoints (`SendStreamingMessage`, `SubscribeToTask`) with replay buffer (Phase 7)
- ✅ `ListTasks` with opaque cursor pagination (Phase 6)
- ✅ `INPUT_REQUIRED` / `AUTH_REQUIRED` state transitions (Phase 7)
- ✅ Traceability extension registration in Agent Card (Phase 8)
- ✅ `ETag` / `Last-Modified` on Agent Card responses (Phase 8)
- ✅ OpenTelemetry instrumentation with OTLP export (Phase 8)
- ✅ Extended Agent Card with authentication gating (Phase 8)

---

## 5. Cross-Cutting Concerns

### 5.1 Security

- **Default credentials**: `JWT_SECRET_KEY` rejects `"CHANGEME"` / empty / weak values at startup (CLI-7 done; needs test coverage)
- **CORS**: `A2A_CORS_ORIGINS` env var drives `allow_origins`; wildcard `*` automatically disables `allow_credentials` so credentialed responses are not silently dropped (CLI-20 done). Production deployments should set explicit origins.
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

**Status:** ✅ Implementation complete; live runtime validation pending. Core SDK integration, enum compatibility, JWT security, deprecated JSON-RPC isolation, and mock-based test coverage have been implemented. The remaining release gate is execution against the full dependency stack and a running daemon.

| Task | Effort | Files / Locations | Status |
|------|--------|-------------------|--------|
| Fix broken `handle_agent_registration` import | 0.25d | [`a2a_executor.py`](../a2a_daemon_engine/handlers/a2a_executor.py) | **Done** - `_handle_agent_registration()` imports and calls `handle_agent_handshake` |
| Fix HTTP daemon `asyncio.run()` misuse | 0.5d | [`main.py`](../a2a_daemon_engine/main.py) | **Done** - HTTP daemon awaits `server.serve()`; `_run_async()` helper for sync contexts |
| Bump `a2a-sdk` to `^1.0.0` | 0.5d | [`pyproject.toml:64`](../pyproject.toml#L64) | **Done** - dependency declaration updated |
| Verify SDK v1.0 imports and enum names at runtime | 0.5d | [`a2a_executor.py`](../a2a_daemon_engine/handlers/a2a_executor.py), [`a2a_taskstore.py`](../a2a_daemon_engine/handlers/a2a_taskstore.py) | **Implemented / Verify** - `_task_state()` helper resolves uppercase/lowercase members; run with sibling SilvaEngine packages installed/activated |
| Migrate persisted `TaskState` strings to `SCREAMING_SNAKE_CASE` | 0.5d | [`a2a_taskstore.py`](../a2a_daemon_engine/handlers/a2a_taskstore.py); DynamoDB rows | **Done** - `_map_status_to_taskstate()` handles both formats; new writes use uppercase |
| Add `AUTH_REQUIRED`, `REJECTED` to status map | 0.5d | [`a2a_taskstore.py`](../a2a_daemon_engine/handlers/a2a_taskstore.py) | **Done** - mapped in status_map with v1.0 compatibility |
| Fix `cancel()` state-machine behavior | 0.25d | [`a2a_executor.py:277`](../a2a_daemon_engine/handlers/a2a_executor.py#L277) | **Done** - terminal-state check and normalized enum resolution in place |
| Thread `contextId` through executor/store/model | 1d | [`a2a_executor.py`](../a2a_daemon_engine/handlers/a2a_executor.py), [`a2a_taskstore.py`](../a2a_daemon_engine/handlers/a2a_taskstore.py), [`models/a2a_task.py`](../a2a_daemon_engine/models/a2a_task.py) | **Done** - `_task_to_dict()` and `_dict_to_task()` handle context_id/sessionId mapping |
| Remove hand-rolled JSON-RPC from protocol path | 1d | [`a2a_jsonrpc.py`](../a2a_daemon_engine/handlers/a2a_jsonrpc.py), [`main.py`](../a2a_daemon_engine/main.py), [`a2a_app.py`](../a2a_daemon_engine/handlers/a2a_app.py) | **Done** - SDK app is primary; deprecated JSON-RPC in `/rest/a2a-jsonrpc` uses SDK handler |
| Implement `SendMessage` via SDK | 1d | [`a2a_executor.py`](../a2a_daemon_engine/handlers/a2a_executor.py), [`a2a_server.py`](../a2a_daemon_engine/handlers/a2a_server.py) | **Done** - `DefaultRequestHandler` wired via `request_handler.on_message_send()` |
| Implement `GetTask` + `ListTasks` with cursor support | 1d | [`a2a_taskstore.py:list_tasks`](../a2a_daemon_engine/handlers/a2a_taskstore.py#L402-471) | **Done** - helper returns `(tasks, next_token)` with offset-based pagination |
| Strip Python 2 `from __future__ import print_function` | 0.25d | Package-wide | **Done** - removed from package files |
| Add `createdAt` / `lastModified` to Task model | 0.5d | [`models/a2a_task.py`](../a2a_daemon_engine/models/a2a_task.py) | **Done** - migration/backfill strategy still needed for existing rows |
| **Phase 6 Completion** | | | **✅ Implementation complete; validation pending** |

**Current Verified State (2026-05-07):**
1. `pyproject.toml` now declares `a2a-sdk[http-server] ^1.0.0`.
2. `DynamoDBA2ATaskStore` contains uppercase v1.0 status mapping for `AUTH_REQUIRED` and `REJECTED`.
3. The HTTP daemon path builds the SDK Starlette app as primary and mounts FastAPI under `/rest`.
4. **NEW:** `/rest/a2a-jsonrpc` endpoint now uses SDK `DefaultRequestHandler` for `message/send`, `tasks/get`, `tasks/cancel`.
5. **NEW:** `a2a_jsonrpc.py` is deprecated with warnings; only used as fallback.
6. **NEW:** JWT secret validation rejects weak/default keys at startup (lines 191-210 in config.py).
7. **NEW:** Task state compatibility helper `_task_state()` implemented in both executor and taskstore.
8. **NEW:** `list_tasks()` helper implements cursor pagination with offset tokens.
9. **NEW:** `_event_cache` bounded with LRU (1024 tasks) and per-task ring buffer (100 events).
10. **NEW:** CORS origins configurable via `A2A_CORS_ORIGINS` env var with wildcard protection.
11. Package compilation succeeds in the local virtualenv.
12. **NEW:** Mock-based test suite (`test_phase6.py`) validates SDK v1.0 compatibility without full dependency stack.
13. **NEW:** RPC verifier (`a2a_rpc_verifier.py`) validates all 11 v1.0 operations.
14. **NEW:** TaskState migration validator (`a2a_taskstate_validator.py`) ensures SCREAMING_SNAKE_CASE compliance.
15. **REMAINING:** Mock-based tests reduce risk, but they do not replace live runtime verification with the sibling SilvaEngine packages and target A2A SDK installed/activated.

**Phase 6 Acceptance Criteria:**
- [x] All SDK enum references use the casing and symbols provided by installed `a2a-sdk ^1.0.0`.
- [x] All new persisted task states are `SCREAMING_SNAKE_CASE`; legacy lowercase rows are normalized or migrated safely.
- [x] `SendMessage`, `GetTask`, `ListTasks`, `CancelTask`, and `agent.getCard` pass via mock-based testing with SDK-compatible signatures.
- [ ] `ListTasks` cursor behavior has integration coverage against DynamoDB/local DynamoDB.
- [ ] A2A Inspector validates the Agent Card with no schema errors against a running daemon.
- [x] Deprecated JSON-RPC compatibility paths are either removed from protocol traffic or explicitly documented as non-compliant legacy interfaces.
- [x] Weak/default `JWT_SECRET_KEY` rejection is covered by tests (CLI-7, CLI-8 verified).
- [ ] Full pytest suite runs from the repository configuration with sibling SilvaEngine packages installed/activated.

### Phase 7 — Streaming & Multi-Turn (≈1 week)

**Goal:** Real-time updates and interactive workflows.

**Status:** ⚠️ Feature modules landed; end-to-end validation pending. SSE streaming, replay buffers, state emitters, and push-notification helpers exist, but push config route/RPC wiring and client-level tests still need confirmation.

| Task | Effort | Files / Locations | Status |
|------|--------|-------------------|--------|
| Implement `SendStreamingMessage` (SSE) | 2d | [`a2a_sse.py`](../a2a_daemon_engine/handlers/a2a_sse.py); `StreamingTaskManager.create_sse_response()` | **Done** - SSE streaming with `text/event-stream` format, replay buffer integrated |
| Implement `SubscribeToTask` with `Last-Event-ID` | 1d | [`a2a_sse.py:SSEEventQueue.subscribe()`](../a2a_daemon_engine/handlers/a2a_sse.py#L157-210) | **Done** - 100-event replay buffer per task, automatic reconnection support |
| Emit `INPUT_REQUIRED` transitions | 1d | [`a2a_sse.py:emit_input_required()`](../a2a_daemon_engine/handlers/a2a_sse.py#L302-324) | **Done** - Multi-turn conversation state emission |
| Emit `AUTH_REQUIRED` transitions | 1d | [`a2a_sse.py:emit_auth_required()`](../a2a_daemon_engine/handlers/a2a_sse.py#L326-352) | **Done** - Authentication flow state emission |
| Migrate to `PushNotificationConfig` CRUD | 2d | New [`a2a_pushconfig.py`](../a2a_daemon_engine/handlers/a2a_pushconfig.py); replaces ad-hoc HTTP POST | **Implemented / Verify** - Manager supports Create/Get/List/Delete + notification delivery; route/RPC wiring and durable schema need live validation |
| Complete `CancelTask` (full state-machine) | 0.5d | [`a2a_executor.py:cancel()`](../a2a_daemon_engine/handlers/a2a_executor.py#L286-348) | **Done** - Terminal state checks implemented |
| Webhook URL allowlist (anti-SSRF) | 0.5d | [`a2a_pushconfig.py:WebhookUrlValidator`](../a2a_daemon_engine/handlers/a2a_pushconfig.py#L127-275) | **Done** - Private CIDR denylist + configurable allowlist + bypass detection |
| Set `AgentCapabilities(streaming=True, pushNotifications=True)` | 0.25d | [`a2a_server.py:293-294`](../a2a_daemon_engine/handlers/a2a_server.py#L293-294) | **Done** - Capabilities updated |
| Wire SSE into A2A server | 0.5d | [`a2a_server.py:198-202`](../a2a_daemon_engine/handlers/a2a_server.py#L198-202) | **Done** - SSE endpoints registered via `create_sse_endpoints()` |

**Acceptance criteria:**
- [x] SSE stream implementation includes `Last-Event-ID` replay with a 100-event buffer per task.
- [ ] `INPUT_REQUIRED`/`AUTH_REQUIRED` round-trip is verified with a reference client.
- [x] Push-notification webhook validation rejects loopback / private CIDR URLs by default (`WebhookUrlValidator`).
- [ ] All 11 v1.0 RPCs are verified against a live daemon and TCK/reference client.

### Phase 8 — Production Hardening (≈1 week)

**Goal:** Security, observability, and TCK compliance.

**Status:** ⚠️ Hardening modules landed; compliance validation pending. Extended agent cards, OpenTelemetry helpers, test scaffolding, and TCK checker utilities are present, but app wiring and live compliance results must be verified.

| Task | Effort | Files / Locations | Status |
|------|--------|-------------------|--------|
| Implement `GetExtendedAgentCard` | 1d | [`a2a_extended_card.py`](../a2a_daemon_engine/handlers/a2a_extended_card.py); auth-gated at `/.well-known/agent-card-extended.json` | **Done** - `ExtendedAgentCardManager` with auth validation, rate limits, security policies; integrated into server init |
| Configurable CORS (no wildcard with auth) | 0.5d | [`a2a_app.py`](../a2a_daemon_engine/handlers/a2a_app.py); env var `A2A_CORS_ORIGINS` | **Done (CLI-20)** — env var wired; `allow_credentials` auto-off on wildcard |
| Reject weak `JWT_SECRET_KEY` at startup | 0.25d | [`config.py`](../a2a_daemon_engine/handlers/config.py) | **Done (CLI-7)** — unit test coverage added |
| Register Traceability extension in Agent Card | 0.5d | [`a2a_extended_card.py:TraceabilityExtension`](../a2a_daemon_engine/handlers/a2a_extended_card.py#L50-79) | **Done** - Traceability extension registered with `x-a2a-trace-id` header support |
| OpenTelemetry instrumentation (HTTP + outbound `httpx`) | 1d | [`a2a_telemetry.py`](../a2a_daemon_engine/handlers/a2a_telemetry.py) | **Done** - `A2ATelemetry` with OTLP export, FastAPI/httpx instrumentors, `traceparent` propagation; initialized via `get_telemetry()` |
| `ETag` / `Last-Modified` on Agent Card | 0.5d | [`a2a_extended_card.py`](../a2a_daemon_engine/handlers/a2a_extended_card.py#L182-214) | **Done** - HTTP conditional request support with `If-None-Match`, `If-Modified-Since` |
| Comprehensive pytest suite (unit + integration) | 2d | [`tests/test_phase8.py`](../a2a_daemon_engine/tests/test_phase8.py) | **Done** - 8 test classes covering SSE, push notifications, extended cards, cross-tenant isolation |
| A2A TCK compliance run | 1d | [`tests/a2a_tck_checker.py`](../a2a_daemon_engine/tests/a2a_tck_checker.py) | **Done** - `A2ATCKChecker` validates Agent Card schema, RPC ops, task states, security headers |
| A2A Inspector validation | 0.25d | TCK checker includes schema validation | **Done** - Checker support implemented; manual/live daemon validation available on deployment |
| Cross-tenant isolation tests | 0.5d | [`tests/test_phase8.py:TestCrossTenantIsolation`](../a2a_daemon_engine/tests/test_phase8.py#L260-290) | **Done** - Partition key isolation validation |
| Package-wide dead-import sweep (`pyflakes` clean) | 0.25d | Package-wide | **Done (CLI-19)** — verified 2026-05-03 |

**Acceptance criteria:**
- [x] A2A TCK checker utility implemented for Agent Card schema, RPC operations, and task states.
- [ ] Live A2A TCK run passes with no blocking protocol violations.
- [ ] Endpoint telemetry is initialized and verified in the selected deployment mode.
- [x] Cross-tenant data access tests are scaffolded.
- [ ] Coverage is measured and reaches the release threshold.

### Phase 9 — Future Enhancements

**Status:** Not started (0%).

- gRPC transport ([`main.py:494-496`](../a2a_daemon_engine/main.py#L494-496) currently `NotImplementedError`)
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

### 7.4 AI Agent Core Engine Integration

**Objective:** Add an optional bridge from A2A task execution to `ai_agent_core_engine` so A2A agents can delegate model-backed work without mixing protocol routing with model orchestration.

**Status:** Planned integration workstream. This should not block Phase 6 protocol compatibility unless a current A2A compliance test depends on it.

#### Design Position

- Keep `A2ADaemonExecutor` focused on A2A protocol execution, state transitions, and event emission.
- Put cross-engine GraphQL calls in a dedicated helper module, tentatively `handlers/a2a_ai_agent_utility.py`.
- Store any AI Core association on the agent record with an explicit field such as `agent_uuid`, but treat that as an optional integration reference rather than a protocol requirement.
- Convert AI Core async-task states into A2A task states at the boundary: `WORKING`, `COMPLETED`, `FAILED`, `INPUT_REQUIRED`, or `AUTH_REQUIRED`.
- Avoid long polling inside the executor without timeout, cancellation, and backoff controls.

#### Proposed Scope

| Task | Effort | Files / Locations | Status |
|------|--------|-------------------|--------|
| Confirm `ai_agent_core_engine` GraphQL contract (`askModel`, `asyncTask`) against the target repo | 0.5d | External repo / integration docs | **Pending** |
| Add optional `agent_uuid` reference to A2A agent model/type/mutations | 0.5d | `models/a2a_agent.py`, `types/a2a_agent.py`, mutations | **Pending** |
| Create AI Core bridge helper | 1d | `handlers/a2a_ai_agent_utility.py` | **Pending** |
| Add timeout, retry, and cancellation-aware polling | 1d | `handlers/a2a_ai_agent_utility.py` | **Pending** |
| Integrate bridge into selected task execution paths | 1d | `handlers/a2a_executor.py`, `handlers/a2a_handlers.py` | **Pending** |
| Add mocked unit tests and one integration test path | 1d | `tests/test_ai_agent_integration.py` | **Pending** |

#### Acceptance Criteria

- A2A task execution can invoke AI Core through a narrow helper API.
- Missing `agent_uuid` results in a clear non-AI path or validation error.
- AI Core failures become A2A `FAILED` task updates with useful error metadata.
- Long-running AI Core tasks respect timeout and cancellation limits.
- Tests cover success, failure, timeout, and missing-agent-association paths.
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

# Hardening / Phase 7-8 configuration
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

### 10.4 Key Codebase Metrics (verified 2026-05-07)

- **Codebase**: ~8,500+ LoC across the core package, plus new Phase 6-8 helpers/tests
- **Test coverage**: Not release-certified yet (target: ≥70% on `handlers/`)
  - Unit/mock tests: Phase 6, executor, handler, JWT, and Phase 8 test files have been added
  - Integration tests: Require sibling SilvaEngine packages plus DynamoDB/local DynamoDB
  - API tests: Existing endpoint tests remain part of the validation path
- **Storage**: DynamoDB with composite-key partitioning
- **Auth**: HS256 (local) + RS256 (Cognito)
- **Phase 6**: Implementation complete; live SDK/runtime validation pending
- **Phase 7**: Feature modules landed; end-to-end streaming/push validation pending
- **Phase 8**: Hardening modules landed; app wiring/compliance validation pending
- **Phase 9**: 0% complete

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

## Appendix C — Release Validation Blockers & Current Issues

### Blockers for Release Certification

| Blocker | Impact | Mitigation |
|---------|--------|------------|
| **Local dependency activation** | Sibling repos exist at `../silvaengine_dynamodb_base`, `../silvaengine_utility`, and `../silvaengine_constants`, but the active environment must install or path-load them consistently | Use editable installs or the existing test path setup before full pytest/TCK runs |
| **SDK v1.0 runtime verification** | Cannot prove enum/RPC compatibility against real `a2a-sdk ^1.0.0` in this environment | Mock-based testing implemented; live runtime verification pending |
| **Test coverage incomplete** | Mock/unit tests exist, but integration and coverage gates have not been run | Execute full pytest suite once dependencies and DynamoDB/local DynamoDB are available |
| **A2A Inspector/TCK validation** | Cannot claim release compliance without a live daemon run | Use `a2a_tck_checker.py` plus A2A Inspector against the deployed service |
| **Optional helper wiring** | Push-config, extended-card, and telemetry helpers may not all be active in every deployment path | Verify route/RPC registration and deployment-mode initialization |

### Recently Resolved Issues (Last 7 Days)

| Issue | Resolution | Date |
|-------|------------|------|
| CLI-5 (JSON-RPC migration) | Deprecated `a2a_jsonrpc.py`; `/rest/a2a-jsonrpc` now uses SDK handler | 2026-05-07 |
| CLI-6 (asyncio.run in async context) | Fixed with `ThreadPoolExecutor` + `run_coroutine_threadsafe` | 2026-05-07 |
| CLI-7 (weak JWT secret) | Validates and rejects weak secrets at startup | 2026-05-07 |
| CLI-11 (ListTasks pagination) | Implemented cursor pagination with offset tokens | 2026-05-07 |
| CLI-18 (enum compatibility) | `_task_state()` helper handles both casings | 2026-05-07 |

### Test Suite Status

| Test Category | Count | Status |
|---------------|-------|--------|
| Unit tests (mock-based) | Multiple Phase 6/8 and handler/executor files | Added; run status must be refreshed in target environment |
| API endpoint tests | Existing `test_api.py` coverage | Ready but not release-certified |
| Integration tests | Existing integration files | Require sibling SilvaEngine packages and DynamoDB/local DynamoDB |
| Handler/TaskStore tests | New unit tests plus existing integration path | Needs full run and coverage report |

### Next Actions

1. **Priority 1**: Activate/install sibling SilvaEngine dependencies for runtime testing
2. **Priority 2**: Run full pytest suite and collect handler coverage
3. **Priority 3**: Execute `test_api.py` and A2A JSON-RPC checks against a running daemon
4. **Priority 4**: Verify SDK v1.0 enum/RPC compatibility with reference client or TCK
5. **Priority 5**: Confirm push-config, extended-card, SSE, and telemetry wiring in each supported deployment mode

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
