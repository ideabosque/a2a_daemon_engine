# Continuous Integration Scenarios SOP — A2A Daemon Engine

> **Status: DRAFT — awaiting user confirmation.** Items marked `assumed`
> were pre-filled from project discovery (Phases 1–2 read-only analysis) and
> fall back to `config/skill-config.yaml` defaults. They must be confirmed or
> corrected before any test execution (Phase 8+) begins.

---

## 1. Document Control

| Field | Value |
|---|---|
| SOP title | A2A Daemon Engine CI Integration SOP |
| Version | 0.3.0 (draft — adds Hermes + Core Engine bridge scenarios + agent card skill rename) |
| Owner / contact | SilvaEngine Team — `<confirm contact>` `assumed` |
| Last updated | 2026-07-18 |
| Business domain | `generic` (A2A protocol daemon / multi-agent platform — not ecommerce/logistics/finance) |
| Target environment | `dev` (local daemon at `http://localhost:8001`; local PostgreSQL at `localhost:5432`); `staging` optional `assumed` |
| Approval status | `draft` |

## 2. Purpose and Scope

This SOP certifies that the A2A Daemon Engine — the SDK Starlette protocol
surface plus the FastAPI `/rest` operations app plus the serverless JSON-RPC
bridge — is integration-ready for the SDK v1.0 release validation. It replaces
the older `INTEGRATION_TEST_PLAN.md` test-case catalog with an
execution-governed, scenario-driven SOP aligned to the 13-phase certification
workflow.

- **In scope:**
  - Agent Card discovery: `GET /.well-known/agent-card.json`
  - JSON-RPC compatibility endpoint: `POST /` (`message/send`, `tasks/get`, `tasks/cancel`)
  - SDK native dispatcher: `POST /v1` (`SendMessage`, `GetTask`, `CancelTask`)
  - SSE task streaming: `GET /tasks/{task_id}/stream` (with `Last-Event-ID` replay)
  - Operations app under `/rest`: `health`, `me`, `{endpoint_id}`, `{endpoint_id}/a2a_core_graphql`, `auth/token`
  - Serverless dispatch: `A2ADaemonEngine.a2a(**event)` via `a2a_jsonrpc_bridge.py`
  - Multi-tenancy via composite PK `{endpoint_id}#{part_id}`
  - JWT auth (local HS256 + optional Cognito RS256) via `FlexJWTMiddleware`
  - **Dual-backend persistence** selected by `Config.DB_BACKEND`:
    - `dynamodb` (default): DynamoDB-backed SDK `TaskStore` (`DynamoDBA2ATaskStore`); `pynamodb` models under `models/dynamodb`; GraphQL CRUD via DynamoDB repos
    - `postgresql`: SQLAlchemy table models under `models/postgresql` (tables `a2a_agents`, `a2a_tasks`, `a2a_messages`, `a2a_settings`); PG repos under `models/repositories/postgresql`; Alembic migrations under `migration/` (private `version_table=a2a_alembic_version`); `Config._initialize_db_session` scoped_session from `PG_HOST`/`PG_PORT`/`PG_USER`/`PG_PASSWORD`/`PG_DB` (or `DATABASE_URL`)
  - GraphQL CRUD for agents, tasks, messages, settings (both backends)
  - Removed-legacy-surface regression (`/rest/a2a-jsonrpc`, `/rest/a2a/{endpoint_id}/...` return 404/unavailable)
  - Dry-run task execution metadata shapes
  - Phase 10 `ai_agent_core_engine` bridge (when `Config.phase10_available`)
  - **Phase 10 `HermesAgentHandler` bridge** — routes A2A tasks to a Hermes
    Agent API Server instance via HTTP + SSE (`a2a_hermes_handler.py`);
    per-agent metadata selects the handler
  - **Phase 10 `CoreEngineAgentHandler` bridge** — routes A2A tasks to
    `ai_agent_core_engine` via `silvaengine_gateway` using GraphQL
    (non-streaming) and WebSocket (streaming) transports
    (`a2a_core_engine_handler.py`)
  - **Agent Card skill rename** — the public `/.well-known/agent-card.json`
    now advertises four capability-style skills
    (`multi_agent_orchestration`, `agent_registry`, `conversational_ai`,
    `human_in_the_loop`) instead of internal operation names
  - **Per-task external-run registry** in `A2ADaemonExecutor` for cancel and
    approval passthrough to Hermes / Core Engine backends
  - **Live test report export** with per-call input arguments and output JSON (see Section 12)
- **Out of scope:**
  - Unit tests in isolation (covered separately by `test_phase6/8/9/10.py` and `test_executor_unit.py`)
  - A2A TCK compliance harness (`a2a_tck_checker.py` is a runnable script, not part of this SOP's pytest suite)
  - Load / performance / soak testing
  - gRPC transport (optional extra; included only if `[grpc]` installed and `A2A_TRANSPORT=grpc` confirmed)
  - OpenTelemetry activation (optional `[telemetry]` extra; no-op when absent)
- **System(s) under test:** `a2a_daemon_engine` package — the single HTTP
  daemon process serving both the SDK Starlette app and the mounted `/rest`
  FastAPI app, plus the serverless bridge sharing the same
  `DefaultRequestHandler`.

## 3. Environment and Access

| Item | Value / source |
|---|---|
| Environment target | `dev` — local daemon started via `python a2a_daemon_engine/tests/start_daemon.py` |
| Base URLs / endpoints | `http://localhost:8001` (SDK surface + `/rest`); serverless path tested in-process via `A2ADaemonEngine.a2a(**event)` |
| Credential source | Project venv `c:\Python312\env\Scripts\activate.bat`; test secrets from `a2a_daemon_engine/tests/.env` (copied from `.env.example`) — **never inline secrets in SOP/scripts/reports** |
| Required env vars (names only) | `region_name`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `endpoint_id`, `part_id`, `transport`, `port`, `jwt_secret_key`, `AUTH_PROVIDER`, `A2A_RUN_LIVE_API_TESTS`, `A2A_TEST_INITIALIZE_TABLES`, `A2A_AI_AGENT_MODULE`, `A2A_AI_AGENT_CLASS`, `A2A_DEFAULT_AGENT_UUID`, `A2A_STREAMING_ENABLED`, `A2A_STREAM_TIMEOUT`, `db_backend` (`dynamodb` \| `postgresql`), `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_PASSWORD`, `PG_DB` (or `DATABASE_URL`), `HERMES_API_URL`, `HERMES_API_KEY`, `HERMES_MODEL`, `HERMES_STREAM_TIMEOUT`, `CORE_ENGINE_GRAPHQL_URL`, `CORE_ENGINE_WS_URL`, `CORE_ENGINE_TOKEN`, `CORE_ENGINE_AGENT_UUID`, `CORE_ENGINE_UPDATED_BY`, `CORE_ENGINE_STREAM_TIMEOUT` |
| Data stores | **DynamoDB** (local Docker `amazon/dynamodb-local` on `:8000`, or AWS test tables; `pynamodb` models under `models/dynamodb`) **OR** **PostgreSQL** (local `localhost:5432` or remote; SQLAlchemy models under `models/postgresql`; Alembic migrations under `migration/`). Backend selected by `db_backend` in `tests/.env`. |
| Messaging / events | In-process `EventQueue` / `SSEEventQueue` (no external message broker); SSE over HTTP |
| Access constraints | None for dev (localhost); `FlexJWTMiddleware` gates `/rest/*` except `auth/token` and public protocol routes (`/`, `/v1`, `/.well-known/...`, `/tasks/{id}/stream`) |
| Provisioning policy | `auto-provision when safe` for local DynamoDB tables and test fixtures; `manual approval required` for any AWS-side table creation or Cognito user-pool provisioning |

> Names and sources only — no secrets inline. Test `jwt_secret_key` must be
> ≥ 32 chars and not a default/weak value (enforced by `jwt_local.py`).

## 4. Dependency Readiness Requirements

Each dependency must reach `available -> configured -> initialized -> operational`
before testing begins (`dependency.block_until_certified = true`).

| Dependency | Type | Health check | Required readiness | Owner |
|---|---|---|---|---|
| Python venv (3.12, `c:\Python312\env`) | infra | `python -c "import sys; print(sys.executable)"` | operational | SilvaEngine |
| `a2a-sdk==1.0.2` (`[http-server]`) | infra | `python -c "import importlib.metadata as m; print(m.version('a2a-sdk'))"` | operational | SilvaEngine |
| `silvaengine_utility`, `silvaengine_constants`, `SilvaEngine-DynamoDB-Base` | internal | import smoke test (`JSON` scalar imported from `silvaengine_utility.graphql`, not top-level) | configured | SilvaEngine |
| DynamoDB (local Docker or AWS test table) — `db_backend=dynamodb` | infra | `DescribeTable` on each A2A table; `GET /rest/health` | initialized | SilvaEngine |
| PostgreSQL (`db_backend=postgresql`) | infra | `SELECT version()`; `a2a_alembic_version` at head; `SQLAlchemy>=1.4` + `psycopg2-binary>=2.9` + `alembic>=1.10` installed (`[postgresql]` extra) | initialized | SilvaEngine |
| `a2a_daemon_engine` package (editable install) | internal | `python -c "import a2a_daemon_engine"` | operational | SilvaEngine |
| HTTP daemon (`start_daemon.py`) | internal | `curl http://localhost:8001/rest/health` + `GET /.well-known/agent-card.json` | operational (HTTP scenarios only) | SilvaEngine |
| Local JWT provider (`AUTH_PROVIDER=local`) | internal | `POST /rest/auth/token` returns HS256 token | operational | SilvaEngine |
| Cognito provider (`AUTH_PROVIDER=cognito`) | external | JWKS fetch + RS256 verify on a test token | operational `assumed` | Cognito owner |
| `ai_agent_core_engine` bridge (Phase 10) | external | `Config.phase10_available is True` | operational (only if scenarios require it) | `assumed` — confirm |
| Hermes Agent API Server (Phase 10 Hermes bridge) | external | `GET /health` on `HERMES_API_URL` returns 200 with `Authorization: Bearer $HERMES_API_KEY` | operational (only if Hermes scenarios in scope) | `assumed` — confirm |
| `silvaengine_gateway` (Phase 10 Core Engine bridge) | external | `GET /{ep}/ai_agent_core_graphql` reachable + `/{ep}/ai_agent_core_ws` WebSocket handshake succeeds with `CORE_ENGINE_TOKEN` | operational (only if Core Engine scenarios in scope) | `assumed` — confirm |
| gRPC transport (`[grpc]` extra) | infra | `grpcio` importable + `A2A_TRANSPORT=grpc` | operational (only if gRPC scenarios in scope) | `assumed` — out of scope unless confirmed |
| OpenTelemetry (`[telemetry]` extra) | infra | `OPENTELEMETRY_AVAILABLE` flag | configured (no-op acceptable) | SilvaEngine |

> **Backend note:** When `db_backend=postgresql`, the PostgreSQL dependency
> must reach `initialized` (Alembic migrated to head `0004`, all 4 `a2a_*`
> tables present with correct PKs + indexes) before any persistence scenario
> runs. The Alembic `version_table` is `a2a_alembic_version` (project-private)
> because the `silvaengine` DB is shared across SilvaEngine projects.

> The known historical blocker — `JSON.parse_value()` signature mismatch in
> `silvaengine_utility/graphql.py` (see `INTEGRATION_TEST_PLAN.md` Appendix C)
> — must be re-verified as resolved before Phase 8. If it recurs, mark the
> GraphQL lifecycle scenarios `blocked`.

## 5. Test Data Requirements

| Asset type | Count | Notes / constraints |
|---|---|---|
| A2A Agents | 2 | From `tests/test_data.json` — `agent_001` (Task Processor) + `agent_002` (Coordinator); realistic capabilities |
| A2A Tasks | 2 | Parameterized; cover `data-processing` and `coordination` types; include a dry-run variant |
| A2A Messages | 2 | User-role parts with text payloads; one with `messageId`, one without (bridge normalization) |
| A2A Settings | 2 | Daemon settings incl. `discovery_enabled`, `max_concurrent_tasks` |
| Tenants / partitions | 2 | `endpoint_a#part_a` and `endpoint_b#part_b` for cross-tenant isolation scenarios |
| Users / roles | 2 | `admin` (roles: admin,user) and `testuser` (roles: user) per `config/users.json.example` |
| JWT tokens | 4 | valid HS256, expired, wrong-audience, admin-static-bypass |

- **Load order:** foundation (env + tables) → master/tenant data → agents → tasks → messages → settings → relationships (agent↔task↔message).
- **Data source:** `generate realistic` from `tests/test_data.json` fixtures; `restore fixture set` for DynamoDB rows.

## 6. Execution Order

Derived from the A2A daemon dependency graph (foundation before protocol
surface before workflow before reconciliation). Deviation from the skill's
ecommerce default is intentional — this is a protocol platform, not a
business domain pipeline.

```text
Foundation (env + venv + SDK + data store)
  -> [if db_backend=dynamodb] DynamoDB table provisioning
  -> [if db_backend=postgresql] Alembic migration to head + table validation
  -> Server Startup (daemon health + agent card) [HTTP scenarios only]
  -> Auth (JWT issuance + middleware gating) [HTTP scenarios only]
  -> Protocol Surface (POST / compat + POST /v1 native + Agent Card) [HTTP]
  -> Persistence CRUD (agents/tasks/messages/settings via get_repo dispatch)
       - DynamoDB arm: DynamoDBA2ATaskStore + DynamoDB repos
       - PostgreSQL arm: SQLAlchemy repos + Config.db_session
  -> Task State Machine (SUBMITTED→WORKING→COMPLETED/FAILED/CANCELED + completed_at)
  -> Message Delivery (sent→delivered/acknowledged + delivered_at)
  -> Multi-Tenancy Isolation (composite PK + cross-tenant rejection)
  -> SSE Streaming (SendStreamingMessage + Last-Event-ID replay) [HTTP]
  -> Serverless Bridge (A2ADaemonEngine.a2a(**event) in-process)
   -> Phase 10 LLM Bridge (only if phase10_available)
        - INT-012: ai_agent_core_engine in-process bridge
        - INT-014: Hermes Agent HTTP+SSE bridge
        - INT-015: Core Engine gateway GraphQL/WebSocket bridge
   -> Agent Card Skill Validation (renamed capability-style skills)
   -> Failure & Resilience
  -> Data Reconciliation (persisted==returned, referential isolation, PK format)
  -> Live Report Export (per-call input/output — see Section 12)
  -> Certification
```

**Reason for deviation:** Default ecommerce sequence (Customer→Product→…→Billing)
does not apply. The daemon's dependencies are protocol-layer: SDK app →
TaskStore → executor → SSE → bridge. Order above is topological.

## 7. Integration Scenarios

Priority: **P1** = must pass to certify. **P2** = should pass. **P3** = nice-to-have.

### INT-001 — Server startup and public discovery

| Field | Value |
|---|---|
| **ID** | INT-001 |
| **Name** | Daemon boots; Agent Card + health discoverable |
| **Priority** | P1 |
| **Type** | API |
| **CI trigger** | on pull request |
| **Preconditions** | venv active; DynamoDB reachable; `start_daemon.py` not yet running |
| **Dependencies** | python_venv, a2a_sdk, dynamodb, a2a_daemon_engine |
| **Test data** | none |
| **Steps** | 1. Start `python a2a_daemon_engine/tests/start_daemon.py`. 2. `GET /rest/health`. 3. `GET /.well-known/agent-card.json`. |
| **Expected behavior** | Daemon listens on `:8001`; health returns 200; agent card is valid v1 JSON with `protocolVersion`, `name`, `version`, `skills` containing the four capability-style skills: `multi-agent-orchestration`, `agent-registry`, `conversational-ai`, `human-in-the-loop`. |
| **Validation points** | daemon_listening, health_ok, agent_card_valid_v1, agent_card_has_capability_skills |
| **Cross-system checks** | Agent card `protocolVersion` matches installed `a2a-sdk` major version; skills advertise client-facing capabilities, not internal operation names |

### INT-002 — JSON-RPC compatibility endpoint (POST /)

| Field | Value |
|---|---|
| **ID** | INT-002 |
| **Name** | Slash-style `message/send`, `tasks/get`, `tasks/cancel` work at `POST /` |
| **Priority** | P1 |
| **Type** | API |
| **CI trigger** | on pull request |
| **Preconditions** | INT-001 passed; valid JWT for any auth-gated path |
| **Dependencies** | a2a_sdk, a2a_jsonrpc_bridge, a2a_executor, a2a_taskstore |
| **Test data** | 1 user message with text part; 1 task id from INT-004 |
| **Steps** | 1. `POST /` `message/send` → expect task created. 2. `POST /` `tasks/get` with that id. 3. `POST /` `tasks/cancel`. |
| **Expected behavior** | All return JSON-RPC 2.0 envelopes; no HTTP 404; unknown method returns JSON-RPC error `-32601`, not HTTP 404. |
| **Validation points** | message_send_accepted, task_get_returns_state, task_cancel transitions to CANCELED |
| **Cross-system checks** | Task id present in `DynamoDBA2ATaskStore` after send |

### INT-003 — SDK native dispatcher (POST /v1)

| Field | Value |
|---|---|
| **ID** | INT-003 |
| **Name** | Native `SendMessage`, `GetTask`, `CancelTask` work at `POST /v1` |
| **Priority** | P1 |
| **Type** | API |
| **CI trigger** | on pull request |
| **Preconditions** | INT-001 passed |
| **Dependencies** | a2a_sdk DefaultRequestHandler, a2a_executor |
| **Test data** | 1 user message |
| **Steps** | 1. `POST /v1` `SendMessage`. 2. `GetTask`. 3. `CancelTask`. |
| **Expected behavior** | SDK dispatcher accepts native v1 method names; returns SDK-shaped responses. |
| **Validation points** | send_message_v1, get_task_v1, cancel_task_v1 |
| **Cross-system checks** | Same task id retrievable via compatibility `tasks/get` at `POST /` |

### INT-004 — Task state machine + DynamoDB TaskStore

| Field | Value |
|---|---|
| **ID** | INT-004 |
| **Name** | Task transitions SUBMITTED→WORKING→COMPLETED / FAILED / CANCELED |
| **Priority** | P1 |
| **Type** | workflow + database |
| **CI trigger** | nightly |
| **Preconditions** | INT-002/003 passed; DynamoDB table initialized |
| **Dependencies** | a2a_taskstore, a2a_executor, dynamodb |
| **Test data** | tasks from `test_data.json` + a dry-run task (`dry_run: true`) |
| **Steps** | 1. Create task. 2. Drive to WORKING. 3a. Complete. 3b. Fail. 3c. Cancel a terminal task (expect error). |
| **Expected behavior** | Each transition persisted; cancel of terminal returns error; dry-run response text contains task id + `dry-run mode`. |
| **Validation points** | state_transitions_valid, dry_run_text_correct, terminal_cancel_rejected |
| **Cross-system checks** | TaskStore row state == protocol-reported state |

### INT-005 — Dry-run task execution metadata shapes

| Field | Value |
|---|---|
| **ID** | INT-005 |
| **Name** | Dry-run honors all alias shapes from AGENTS.md |
| **Priority** | P1 |
| **Type** | API |
| **CI trigger** | on pull request |
| **Preconditions** | INT-002 passed |
| **Dependencies** | a2a_jsonrpc_bridge, a2a_executor |
| **Test data** | parametrized: `task_data`/`taskData`; `dry_run`/`dryRun`/`dry-run`; `"true"` string; `task_id`/`taskId`/`id`; `message.metadata` vs `params.metadata` |
| **Steps** | For each variant, `POST /` `message/send` with `operation: task_execution` + dry-run payload. |
| **Expected behavior** | Response text includes task id + `dry-run mode` for every variant. |
| **Validation points** | all_alias_shapes_accepted, dry_run_text_present |
| **Cross-system checks** | none |

### INT-006 — GraphQL operations CRUD under /rest

| Field | Value |
|---|---|
| **ID** | INT-006 |
| **Name** | Agents/Tasks/Messages/Settings Insert→Get→List→Delete via GraphQL |
| **Priority** | P1 |
| **Type** | API + database |
| **CI trigger** | nightly |
| **Preconditions** | INT-001 passed; JWT issued; DynamoDB initialized |
| **Dependencies** | a2a_core, schema.py, silvaengine_utility GraphQL, dynamodb |
| **Test data** | 2 agents, 2 tasks, 2 messages, 2 settings from `test_data.json` |
| **Steps** | For each entity: `POST /rest/{endpoint_id}/a2a_core_graphql` InsertUpdate → Get → List (paginated) → Delete. |
| **Expected behavior** | All mutations succeed; queries return inserted rows; deletes remove rows. **Blocker check:** `JSON.parse_value()` scalar bug from historical report must not recur. |
| **Validation points** | insert_ok, get_ok, list_paginated, delete_ok, no_json_scalar_error |
| **Cross-system checks** | Inserted agent rows reference correct `partition_key = {endpoint_id}#{part_id}` |

### INT-007 — Multi-tenancy isolation

| Field | Value |
|---|---|
| **ID** | INT-007 |
| **Name** | Composite PK enforces tenant isolation |
| **Priority** | P1 |
| **Type** | database + API |
| **CI trigger** | pre-release |
| **Preconditions** | INT-006 passed |
| **Dependencies** | a2a_core, dynamodb, FlexJWTMiddleware |
| **Test data** | 2 tenants: `endpoint_a#part_a`, `endpoint_b#part_b` |
| **Steps** | 1. Insert agent in tenant A. 2. Query tenant B for that agent. 3. Cross-tenant GraphQL query. 4. Tenant-A JWT against tenant-B resource. |
| **Expected behavior** | Tenant B query returns empty/404; cross-tenant GraphQL rejected (403); token scope mismatch rejected (401). |
| **Validation points** | row_isolation, graphql_cross_tenant_rejected, jwt_scope_rejected |
| **Cross-system checks** | Composite PK format `{endpoint_id}#{part_id}` enforced on every persisted row |

### INT-008 — Authentication flows

| Field | Value |
|---|---|
| **ID** | INT-008 |
| **Name** | Local JWT issuance + middleware gating + Cognito (if configured) |
| **Priority** | P1 |
| **Type** | API |
| **CI trigger** | on pull request (local); pre-release (cognito) |
| **Preconditions** | INT-001 passed |
| **Dependencies** | auth_router, jwt_local, jwt_cognito, FlexJWTMiddleware |
| **Test data** | valid HS256, expired, wrong-audience, admin-static tokens |
| **Steps** | 1. `POST /rest/auth/token` (password grant) → token. 2. `GET /rest/me` with token. 3. Expired token → 401. 4. Public route `/` with no token → not gated. 5. (Cognito) RS256 token via JWKS. |
| **Expected behavior** | Token issuance 200; `/rest/me` returns claims; expired → 401; public protocol routes not gated; Cognito RS256 verifies via JWKS. |
| **Validation points** | token_issued, me_claims, expired_rejected, public_routes_open, cognito_rs256_ok |
| **Cross-system checks** | Weak `JWT_SECRET_KEY` (e.g. `CHANGEME`) rejected at startup |

### INT-009 — SSE streaming and replay

| Field | Value |
|---|---|
| **ID** | INT-009 |
| **Name** | `SendStreamingMessage` + `Last-Event-ID` replay buffer |
| **Priority** | P2 |
| **Type** | event |
| **CI trigger** | nightly |
| **Preconditions** | INT-002 passed; `A2A_STREAMING_ENABLED=true` |
| **Dependencies** | a2a_sse, SSEEventQueue, EventQueue |
| **Test data** | 1 streaming task |
| **Steps** | 1. `SendStreamingMessage` → SSE stream. 2. Disconnect mid-stream. 3. Reconnect with `Last-Event-ID`. |
| **Expected behavior** | Stream established; no event loss within buffer window on reconnect. |
| **Validation points** | sse_stream_open, replay_from_last_event_id, no_event_loss |
| **Cross-system checks** | Event count on replay == event count from fresh stream within buffer |

### INT-010 — Serverless JSON-RPC bridge

| Field | Value |
|---|---|
| **ID** | INT-010 |
| **Name** | `A2ADaemonEngine.a2a(**event)` in-process dispatch |
| **Priority** | P1 |
| **Type** | API |
| **CI trigger** | on pull request |
| **Preconditions** | venv active; bridge importable |
| **Dependencies** | a2a_jsonrpc_bridge, DefaultRequestHandler |
| **Test data** | JSON-RPC 2.0 dicts (message/send, tasks/get, tasks/cancel); non-JSON-RPC payload (rejected) |
| **Steps** | 1. Call `daemon.a2a(**event)` with valid dict. 2. Call with missing `messageId` (bridge fills). 3. Call with role casing variants. 4. Call with non-JSON-RPC payload (expect rejection). 5. Verify `parts[].type` removal normalization. |
| **Expected behavior** | Bridge normalizes legacy payloads; constructs protobuf SDK requests; dispatches to same handler as HTTP path; rejects non-JSON-RPC. |
| **Validation points** | bridge_normalizes, protobuf_request_built, non_jsonrpc_rejected, type_field_stripped |
| **Cross-system checks** | Serverless result == HTTP `POST /` result for same logical request |

### INT-011 — Removed-legacy-surface regression

| Field | Value |
|---|---|
| **ID** | INT-011 |
| **Name** | Legacy routes unavailable |
| **Priority** | P2 |
| **Type** | API |
| **CI trigger** | on pull request |
| **Preconditions** | INT-001 passed |
| **Dependencies** | a2a_server, a2a_app |
| **Test data** | none |
| **Steps** | 1. `GET /rest/a2a-jsonrpc` → expect 404. 2. `/rest/a2a/{endpoint_id}/...` → 404. 3. Direct `action=...` dispatch via `a2a()` → rejected. |
| **Expected behavior** | All legacy surfaces return 404 or reject; no active handler. |
| **Validation points** | legacy_routes_404, action_dispatch_rejected |
| **Cross-system checks** | none |

### INT-012 — Phase 10 LLM bridge (conditional)

| Field | Value |
|---|---|
| **ID** | INT-012 |
| **Name** | `ai_agent_core_engine` bridge streams into A2A EventQueue |
| **Priority** | P2 (P1 if release depends on it) `assumed` |
| **Type** | end-to-end |
| **CI trigger** | pre-release |
| **Preconditions** | `Config.phase10_available is True`; `A2A_AI_AGENT_MODULE`/`A2A_AI_AGENT_CLASS` set; `a2a_core` initialized |
| **Dependencies** | a2a_ai_agent_utility, ai_agent_core_engine, LLM handler |
| **Test data** | 1 agent config `ai_agent_core_engine`; metadata aliases `agent_uuid`/`agentId`, `thread_uuid`/`threadId`, `stream`/`streaming` |
| **Steps** | 1. Resolve agent config. 2. Load LLM handler. 3. `ask_model` with streaming bridging into `EventQueue`/`SSEEventQueue`. 4. Honor `A2A_STREAM_TIMEOUT`. |
| **Expected behavior** | Streaming response bridged into A2A events; timeout enforced; both snake_case and camelCase metadata accepted. |
| **Validation points** | agent_resolved, llm_handler_loaded, stream_bridged, timeout_enforced, metadata_aliases_accepted |
| **Cross-system checks** | none (LLM output is non-deterministic; validate shape not content) |

### INT-013 — PostgreSQL backend persistence (`db_backend=postgresql`)

| Field | Value |
|---|---|
| **ID** | INT-013 |
| **Name** | PG repository CRUD + state semantics + multi-tenancy for all 4 entities |
| **Priority** | P1 (when `db_backend=postgresql`) |
| **Type** | database |
| **CI trigger** | on pull request (when PG env configured); pre-release |
| **Preconditions** | `db_backend=postgresql` in `tests/.env`; `PG_*` (or `DATABASE_URL`) set; `[postgresql]` extra installed; Alembic migrated to head `0004`; `Config._initialize_db_session` succeeds |
| **Dependencies** | SQLAlchemy, psycopg2, alembic, `models/postgresql/*`, `models/repositories/postgresql/*`, `Config.db_session`, `get_repo` dispatch |
| **Test data** | 2 agents, 2 tasks, 2 messages, 2 settings; 2 tenants (`test-endpoint#test-part`, `test-endpoint#other-part`) |
| **Steps** | For each entity via `get_repo(entity_type)`: 1. `insert_update` (create). 2. `get`. 3. `count`. 4. `insert_update` (update). 5. `list` (filtered). 6. `delete`. Then: task SUBMITTED→WORKING→COMPLETED (`completed_at` set on terminal only); message sent→delivered (`delivered_at` set on delivery only); cross-tenant `get`/`count`/`list` return null/0/empty. |
| **Expected behavior** | All calls pass; `normalize_row` returns column values (not SQLAlchemy `Base.metadata` for the `metadata` column); auto-generated uuid4 ids; task/message timestamp semantics match DynamoDB; composite PK `{endpoint_id}#{part_id}` enforced. |
| **Validation points** | pg_crud_all_entities, task_terminal_completed_at, message_delivered_at, normalize_row_metadata_correct, cross_tenant_null, auto_id_uuid4, pk_composite_format |
| **Cross-system checks** | PG `normalize_row` output keys == GraphQL type fields; task/message semantics == DynamoDB backend; `a2a_alembic_version` at `0004` |

### INT-014 — Hermes Agent bridge (conditional)

| Field | Value |
|---|---|
| **ID** | INT-014 |
| **Name** | `HermesAgentHandler` routes A2A tasks to Hermes API Server via HTTP + SSE |
| **Priority** | P2 (P1 if release depends on it) `assumed` |
| **Type** | end-to-end |
| **CI trigger** | pre-release |
| **Preconditions** | Hermes API Server running at `HERMES_API_URL` with `HERMES_API_KEY`; `hermes-agent` registered in A2A agent registry with `metadata.module_name = a2a_daemon_engine.handlers.a2a_hermes_handler` and `metadata.class_name = HermesAgentHandler`; daemon started with `HERMES_*` env vars |
| **Dependencies** | a2a_hermes_handler, a2a_ai_agent_utility, a2a_executor, Hermes API Server |
| **Test data** | 1 registered Hermes agent; non-streaming `SendMessage` payload; streaming `SendStreamingMessage` payload with `stream: true`; cancel and approval payloads |
| **Steps** | 1. `POST /v1` `SendMessage` with `metadata.agent_uuid=hermes-agent` → verify Hermes `/v1/chat/completions` called and response content returned. 2. `POST /v1` `SendStreamingMessage` → verify `run_id` chunk drained, token chunks emitted to SDK + SSE, `COMPLETED` state. 3. `POST /v1` `CancelTask` mid-stream → verify `POST /v1/runs/{id}/stop` called on Hermes, task → `CANCELED`. 4. Approval: Hermes emits `hermes.approval_required` → A2A `INPUT_REQUIRED`; client sends `operation=approval_response` → verify `POST /v1/runs/{id}/approval` called. |
| **Expected behavior** | Non-streaming returns Hermes-generated text; streaming emits token chunks with correct `run_id` registry; cancel passthrough hits Hermes stop endpoint; approval passthrough hits Hermes approval endpoint; per-task registry cleared on terminal state. |
| **Validation points** | hermes_non_streaming_ok, hermes_streaming_tokens, hermes_run_id_registered, hermes_cancel_passthrough, hermes_approval_passthrough, hermes_registry_cleared |
| **Cross-system checks** | Hermes API Server request logs show daemon-originated calls; A2A task state transitions match Hermes run lifecycle |

### INT-015 — Core Engine gateway bridge (conditional)

| Field | Value |
|---|---|
| **ID** | INT-015 |
| **Name** | `CoreEngineAgentHandler` routes A2A tasks to `ai_agent_core_engine` via `silvaengine_gateway` (GraphQL + WebSocket) |
| **Priority** | P2 (P1 if release depends on it) `assumed` |
| **Type** | end-to-end |
| **CI trigger** | pre-release |
| **Preconditions** | `silvaengine_gateway` running at `CORE_ENGINE_GRAPHQL_URL` / `CORE_ENGINE_WS_URL` with valid `CORE_ENGINE_TOKEN`; `core-engine-agent` registered in A2A agent registry with `metadata.module_name = a2a_daemon_engine.handlers.a2a_core_engine_handler` and `metadata.class_name = CoreEngineAgentHandler`; `ai_agent_core_engine` reachable through the gateway; daemon started with `CORE_ENGINE_*` env vars |
| **Dependencies** | a2a_core_engine_handler, a2a_ai_agent_utility, a2a_executor, silvaengine_gateway, ai_agent_core_engine |
| **Test data** | 1 registered Core Engine agent; non-streaming `SendMessage` payload; streaming `SendStreamingMessage` payload with `stream: true`; cancel payload |
| **Steps** | 1. `POST /v1` `SendMessage` with `metadata.agent_uuid=core-engine-agent` → verify gateway GraphQL `ask_model` + `execute_ask_model` + `message_list` 3-step flow returns assistant content. 2. `POST /v1` `SendStreamingMessage` → verify WebSocket `chunk_delta` frames drained as `token` chunks to SDK + SSE, `is_message_end` → `COMPLETED`. 3. `POST /v1` `CancelTask` mid-stream → verify WebSocket closed, task → `CANCELED`. |
| **Expected behavior** | Non-streaming returns assistant message from Core Engine via gateway GraphQL; streaming emits token chunks from `chunk_delta` frames; cancel closes WebSocket and unblocks drain loop; per-task registry cleared on terminal state. |
| **Validation points** | core_engine_gql_3step_ok, core_engine_ws_streaming_tokens, core_engine_ws_is_message_end, core_engine_cancel_closes_ws, core_engine_registry_cleared |
| **Cross-system checks** | Gateway request logs show GraphQL mutations + WebSocket `ask_model` actions; `ai_agent_core_engine` message store has persisted assistant message |

### INT-016 — Agent Card capability skills validation

| Field | Value |
|---|---|
| **ID** | INT-016 |
| **Name** | Agent card advertises capability-style skills, not internal operation names |
| **Priority** | P1 |
| **Type** | API |
| **CI trigger** | on pull request |
| **Preconditions** | INT-001 passed |
| **Dependencies** | a2a_server |
| **Test data** | none |
| **Steps** | 1. `GET /.well-known/agent-card.json`. 2. Parse `skills` array. 3. Verify exactly 4 skills with ids: `multi-agent-orchestration`, `agent-registry`, `conversational-ai`, `human-in-the-loop`. 4. Verify each skill has `name`, `description`, `tags`, `examples`. 5. Verify no skill id matches an internal operation name (`task_execution`, `message_routing`, `message_response`, `agent_discovery`). |
| **Expected behavior** | Card advertises 4 capability-style skills describing client-facing capabilities; internal operation names are not leaked as skill ids. |
| **Validation points** | skills_count_is_4, skill_ids_are_capabilities, no_internal_operation_names, skills_have_required_fields |
| **Cross-system checks** | Skills describe what the daemon does for clients, not how it routes internally |

## 8. Failure and Resilience Scenarios

| Scenario | Injected fault | Expected behavior |
|---|---|---|
| `missing_data` | `tasks/get` for unknown task id | JSON-RPC error (not HTTP 500); task-store returns not-found |
| `invalid_data` | `message/send` with empty parts / negative priority | JSON-RPC `-32602` invalid params with context |
| `api_failures` | Downstream handler raises during executor | Task → FAILED; error recorded; no daemon crash |
| `database_failures` | DynamoDB unreachable during TaskStore write | Graceful degradation / retry per `dependency.auto_retry`; task not silently dropped |
| `queue_failures` | SSEEventQueue consumer dies | Dead-letter / buffer replay on reconnect; no event loss within buffer |
| `authentication_failures` | Expired / malformed / wrong-audience JWT | 401 with challenge; public routes unaffected |
| `service_outages` | Daemon restart mid-stream | `Last-Event-ID` replay recovers; clients reconnect |
| `third_party_outages` | Cognito JWKS endpoint unreachable (when `AUTH_PROVIDER=cognito`) | Degrade gracefully; clear error; local provider unaffected |
| `bridge_rejection` | Non-JSON-RPC payload to `a2a(**event)` | Rejected with JSON-RPC error; no silent dispatch |
| `hermes_server_down` | Hermes API Server unreachable during `HermesAgentHandler.ask_model` | Task → `FAILED` with connection error; no hanging stream; registry cleared |
| `hermes_auth_failure` | Wrong `HERMES_API_KEY` (401 from Hermes) | Task → `FAILED` with auth error; no hanging stream |
| `hermes_stream_timeout` | Hermes SSE stream never sends `response.completed` | Drain loop timeout (`HERMES_STREAM_TIMEOUT`); task → `FAILED`; `stream_event` set; registry cleared |
| `core_engine_gw_down` | `silvaengine_gateway` unreachable during `CoreEngineAgentHandler.ask_model` | Task → `FAILED` with connection error; no hanging stream; registry cleared |
| `core_engine_ws_error_frame` | Gateway WebSocket sends `{"type":"error"}` mid-stream | Error chunk emitted; task → `FAILED`; WebSocket closed; registry cleared |
| `core_engine_no_assistant_msg` | GraphQL `message_list` returns no assistant message after `execute_ask_model` | Task → `FAILED` with "No assistant message found" error |
| `cancel_after_terminal` | `CancelTask` sent after task already `COMPLETED` | No Hermes stop call / no WebSocket close; response reports task already terminal |

## 9. Data Reconciliation Checks

| Check | Rule | Tolerance |
|---|---|---|
| Referential integrity | Every task references an existing agent; every message references an existing task | 0 orphans |
| Cross-system consistency | TaskStore row state == protocol-reported task state | 0 mismatches |
| Cross-path consistency | `POST /` result == `POST /v1` result == `a2a(**event)` result for same logical request | 0 mismatches |
| Count consistency | Tasks created == tasks persisted in DynamoDB | 0 |
| Tenant isolation | No row with tenant A's PK visible to tenant B query | 0 leaks |
| Timestamp drift | Task `createdAt` vs DynamoDB row timestamp | 5 seconds |
| Audit completeness | Every task state transition emitted as an SSE event | 0 missing within buffer |

## 10. Entry and Exit Criteria

**Entry criteria (testing may begin when):**
- Python venv active; `a2a-sdk==1.0.2` confirmed; all P1 infra dependencies operational.
- Data store initialized for the active backend:
  - `db_backend=dynamodb`: DynamoDB (local or AWS test) initialized; `GET /rest/health` returns healthy.
  - `db_backend=postgresql`: `[postgresql]` extra installed; Alembic migrated to head; all `a2a_*` tables present; `Config._initialize_db_session` succeeds.
- `jwt_secret_key` set to a non-weak ≥ 32-char value; `AUTH_PROVIDER` chosen.
- Test data loaded from `test_data.json` in dependency order.
- `silvaengine_utility.JSON` import resolved (import from `silvaengine_utility.graphql`, not top-level).

**Exit criteria (certification may be issued when):**
- All P1 scenarios (INT-001..008, INT-010, INT-016) pass; INT-012 passes if in scope;
  INT-014 passes if Hermes bridge in scope; INT-015 passes if Core Engine bridge in scope;
  **INT-013 passes when `db_backend=postgresql`**.
- ≥ 90% of P2 scenarios pass.
- Coverage ≥ 80% (`testing_plan.minimum_coverage_threshold`).
- No blocking defects; reconciliation checks clean (within tolerance).
- Removed-legacy-surface regression (INT-011) confirms no active legacy handlers.
- **Live test report exported** to `docs/test_results/live_integration_results_<YYYYMMDD>.md`
  with per-call input arguments and output JSON for every call executed
  (Section 12.2). Machine-readable transcript exported alongside it.

## 11. CI Trigger and Cadence

| Trigger | Scope run | Required to pass |
|---|---|---|
| On pull request | INT-001, INT-002, INT-003, INT-005, INT-008 (local), INT-010, INT-011, INT-016; **INT-013 (PG) when `db_backend=postgresql`** | yes — blocks merge |
| Nightly | All P1 + P2 (INT-004, INT-006, INT-007, INT-009, INT-013) + resilience subset + **live report export** | report only |
| Pre-release | Full suite + failure/resilience (Section 8) + reconciliation (Section 9) + Cognito (if configured) + Phase 10 (if available) + **Hermes bridge (INT-014, if `HERMES_API_URL` set)** + **Core Engine bridge (INT-015, if `CORE_ENGINE_GRAPHQL_URL` set)** + **both backends (dynamodb + postgresql)** + **dated live report with per-call input/output** | yes — blocks release |

## 12. Reporting and Certification Expectations

### 12.1 Report format and location

- **Report format:** `markdown` (per `reporting.default_format`).
- **Report location:** target project's `docs/test_results/` directory. The
  directory is created if missing. Two report types are produced per run:
  1. **Certification report** — `docs/test_results/integration_certification_report.md`
     (summary, dependency readiness, defects, coverage, certification decision).
  2. **Live integration report** — `docs/test_results/live_integration_results_<YYYYMMDD>.md`
     (dated; one **Function Results** block per call with exact input arguments
     and output JSON).
- A **machine-readable transcript** of every call's input arguments and output
  must also be exported to `a2a_daemon_engine/tests/pg_live_transcript.json`
  (or a backend-appropriate name) for programmatic consumption and audit.

### 12.2 Live test report — mandatory per-call input/output (Function Results)

Every test run must export a live report that records, **for each call
executed during the run**, the following fields in a numbered block:

| Field | Requirement |
|---|---|
| Method | `<group>.<method_name>` (e.g. `A2ATaskPGRepository.insert_update`) |
| Status | `pass` \| `fail` \| `error` \| `skipped` \| `blocked` |
| Elapsed | duration in milliseconds, measured around the actual call |
| Scenario ID | SOP scenario reference (e.g. `INT-004`, `SOP-§8-missing_data`) |
| Arguments | the **exact input arguments** passed to the call, as a JSON code block |
| Output | the **exact returned output** as a JSON code block. Truncate payloads > 2000 chars with a clear `... (truncated)` marker and keep the structurally relevant portion |
| Expected (on failure) | expected shape/value, only when Status is `fail`/`error` |
| Error / diff (on failure) | error message, status code, or expected-vs-actual diff, only when Status is `fail`/`error` |

The live report must follow `references/final-report-template.md` and include:
- Header metadata (timestamp, environment, endpoint, partition, SOP ref,
  pass/fail/error/skipped/blocked/total counts, final certification status).
- A **Function Results** section with one numbered block per call, in
  execution order.
- **End-to-End Workflow Validation**, **Failure and Resilience Results**,
  **Data Reconciliation**, **Coverage Analysis**, **Defect Analysis**,
  **Open Risks**, and **Certification Decision** sections.

> The live report is the primary evidence artifact for certification. A run
> that does not export per-call input arguments and output JSON is considered
> incomplete and cannot certify, regardless of pass counts.

### 12.3 Certification decision

- **Required certification decision:** one of `Integration Certified`,
  `Ready for UAT`, `Ready for Production`, `Ready with Conditions`,
  `Not Ready`.
- **Distribution:** SilvaEngine Team — `<confirm distribution>` `assumed`

## 13. Sign-off

| Role | Name | Date | Decision |
|---|---|---|---|
| Test owner | `<confirm>` | `<pending>` | `<pending>` |
| Release manager | `<confirm>` | `<pending>` | `<pending>` |

---

## Assumptions Requiring User Confirmation

The following were pre-filled from read-only discovery and must be confirmed
before full (non-PG-subset) certification proceeds. Items already verified
in the PG initiation run are marked `verified`.

1. **Target environment = `dev`** (local daemon + local PostgreSQL).
   Is `staging` also in scope? `assumed`
2. **Cognito provider in scope?** Marked `assumed` — confirm if RS256/JWKS
   scenarios must run in pre-release cadence.
3. **Phase 10 LLM bridge in scope?** INT-012 priority P2 — promote to P1 if
   the release depends on it. Requires `ai_agent_core_engine` importable +
   `A2A_AI_AGENT_MODULE`/`A2A_AI_AGENT_CLASS` env vars.
4. **Hermes Agent bridge in scope?** INT-014 priority P2 — promote to P1 if
   the release depends on it. Requires Hermes API Server running at
   `HERMES_API_URL` with `HERMES_API_KEY` set, and a `hermes-agent` record
   registered in the A2A agent registry with
   `metadata.module_name = a2a_daemon_engine.handlers.a2a_hermes_handler`.
5. **Core Engine gateway bridge in scope?** INT-015 priority P2 — promote to
   P1 if the release depends on it. Requires `silvaengine_gateway` running
   with `CORE_ENGINE_GRAPHQL_URL` / `CORE_ENGINE_WS_URL` / `CORE_ENGINE_TOKEN`
   set, `ai_agent_core_engine` reachable through the gateway, and a
   `core-engine-agent` record registered in the A2A agent registry with
   `metadata.module_name = a2a_daemon_engine.handlers.a2a_core_engine_handler`.
6. **gRPC transport out of scope** unless `[grpc]` extra installed and
   `A2A_TRANSPORT=grpc` confirmed.
7. **DynamoDB source** — local Docker (`amazon/dynamodb-local:8000`) vs AWS
   test tables? Determines whether table provisioning is auto or manual-approval.
8. **`silvaengine_utility.JSON` import** — `verified` resolved in-module
   (import from `silvaengine_utility.graphql`). The sibling checkout is not
   modified.
9. **PostgreSQL backend** — `verified` initiated: Alembic migrated to `0004`,
   4 tables provisioned, `Config._initialize_db_session` works, 13/13 PG
   integration tests pass, `normalize_row` metadata fix applied.
10. **Owner / contact / distribution** fields — confirm names and emails.
11. **Dual-backend pre-release gate** — confirm that pre-release must run both
    `db_backend=dynamodb` and `db_backend=postgresql` (currently `assumed` yes).
12. **Agent card skill rename** — `verified`: the card now advertises four
    capability-style skills (`multi_agent_orchestration`, `agent_registry`,
    `conversational_ai`, `human_in_the_loop`) instead of internal operation
    names. INT-016 validates this on every PR.

Once you confirm or correct these items, the SOP moves from `draft` to
`approved` and full (non-subset) Phase 8+ test execution may proceed.