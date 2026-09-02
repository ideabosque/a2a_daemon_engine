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
| Version | 0.4.0 (draft — adds Phase 13 protocol-conformance scenarios: multimodal Parts, durable push notifications + delivery, extended-card wiring, configurable I/O modes, gateway streaming deviation; metadata-only handler resolution) |
| Owner / contact | SilvaEngine Team — `<confirm contact>` `assumed` |
| Last updated | 2026-09-02 |
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
  - **Phase 13 — protocol conformance (C1–C8):**
    - **Multimodal Parts (C1/C2)** — agent `output_files` emitted as A2A file
      Parts (`url` / `raw`), structured output as data Parts; inbound
      client file/data Parts captured onto the task `input_data`
      (`input_files` / `input_data_parts`) instead of being dropped
      (`_agent_parts_message` / `_extract_input_parts` in `a2a_executor.py`)
    - **Push notification delivery + durable config (C3/C4)** — an SDK
      `push_sender` POSTs registered webhooks on task-state change; the
      `DurablePushNotificationConfigStore` (`a2a_pushconfig_store.py`)
      persists configs into `a2a_settings` (DynamoDB **and** PostgreSQL) so
      they survive process/Lambda recycle; anti-SSRF `WebhookUrlValidator`
      gates every write
    - **Authenticated extended Agent Card (C5)** — `agent/getAuthenticatedExtendedCard`
      returns a card enriched over the public one (traceability extension +
      documentation URL), not a verbatim copy
    - **Interrupt states (C7)** — streaming backends map `approval` →
      `INPUT_REQUIRED` and `auth_required` → `AUTH_REQUIRED` (backend-agnostic)
    - **Configurable I/O modes (C8)** — `default_input_modes` /
      `default_output_modes` settings-driven (default `text`)
    - **Gateway streaming deviation (C6)** — through `silvaengine_gateway`,
      `message/stream` / `tasks/resubscribe` return one aggregated JSON-RPC
      response; live events flow out-of-band on `/{ep}/a2a_sse` (documented
      deviation, see `A2A_ARCHITECTURE.md`)
  - **Metadata-only handler resolution** — the per-agent handler is resolved
    from the agent's DB `metadata.module_name` / `class_name` (or the
    `A2A_AI_AGENT_TYPE` shorthand); the legacy `A2A_AI_AGENT_MODULE` /
    `A2A_AI_AGENT_CLASS` env-var fallback has been **removed**
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
| Required env vars (names only) | `region_name`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `endpoint_id`, `part_id`, `transport`, `port`, `jwt_secret_key`, `AUTH_PROVIDER`, `A2A_RUN_LIVE_API_TESTS`, `A2A_TEST_INITIALIZE_TABLES`, `A2A_AI_AGENT_TYPE`, `A2A_DEFAULT_AGENT_UUID`, `A2A_STREAMING_ENABLED`, `A2A_STREAM_TIMEOUT`, `A2A_PUSH_WEBHOOK_ALLOWLIST`, `A2A_PUSH_REQUIRE_HTTPS`, `A2A_DEFAULT_INPUT_MODES`, `A2A_DEFAULT_OUTPUT_MODES`, `A2A_DOCUMENTATION_URL`, `db_backend` (`dynamodb` \| `postgresql`), `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_PASSWORD`, `PG_DB` (or `DATABASE_URL`), `HERMES_API_URL`, `HERMES_API_KEY`, `HERMES_MODEL`, `HERMES_STREAM_TIMEOUT`, `CORE_ENGINE_GRAPHQL_URL`, `CORE_ENGINE_WS_URL`, `CORE_ENGINE_TOKEN`, `CORE_ENGINE_AGENT_UUID`, `CORE_ENGINE_UPDATED_BY`, `CORE_ENGINE_STREAM_TIMEOUT` |
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
   -> Phase 13 Protocol Conformance
        - INT-017: multimodal Parts round-trip (output files / inbound file+data)
        - INT-018: push notification durability + delivery (both backends)
        - INT-019: authenticated extended card differs from public
        - INT-020: configurable I/O modes + gateway streaming deviation
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
| **Preconditions** | `Config.phase10_available is True`; agent record resolves a handler via `metadata.module_name`/`class_name` (or `A2A_AI_AGENT_TYPE` shorthand); `a2a_core` initialized |
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

### INT-017 — Multimodal Parts round-trip (Phase 13 C1/C2)

| Field | Value |
|---|---|
| **ID** | INT-017 |
| **Name** | Agent output files emitted as file Parts; inbound file/data Parts captured |
| **Priority** | P2 |
| **Type** | API + workflow |
| **CI trigger** | nightly |
| **Preconditions** | INT-002 passed; a handler whose `ask_model` returns `output_files` (mock or real) |
| **Dependencies** | a2a_executor (`_agent_parts_message`, `_file_part`, `_data_part`, `_extract_input_parts`) |
| **Test data** | outbound: bridge result with `output_files=[{url, filename, media_type}]` and a structured data payload. inbound: `message/send` carrying a `FilePart` (url) + a `DataPart` alongside text |
| **Steps** | 1. Drive a message/task whose backend returns `content` + `output_files`; inspect the emitted A2A `Message.parts`. 2. Send a message with mixed text/file/data parts; inspect the persisted task `input_data`. |
| **Expected behavior** | Emitted message contains a text Part plus one file Part per `output_files` entry (`url` or `raw`, with `filename`/`media_type`) and a data Part for structured output; streaming path emits files-only at completion (text already streamed). Inbound non-text parts appear under `input_data.input_files` / `input_data.input_data_parts`; text-only messages add neither key. |
| **Validation points** | output_file_parts_emitted, data_part_emitted, streaming_files_at_completion, inbound_parts_captured, text_only_unchanged |
| **Cross-system checks** | Persisted task `input_data` matches the parts the client sent |

### INT-018 — Push notification config durability + delivery (Phase 13 C3/C4)

| Field | Value |
|---|---|
| **ID** | INT-018 |
| **Name** | Push config survives restart (both backends) and a webhook is POSTed on state change |
| **Priority** | P2 |
| **Type** | end-to-end + database |
| **CI trigger** | pre-release |
| **Preconditions** | INT-002 passed; a reachable test webhook receiver; `A2A_PUSH_WEBHOOK_ALLOWLIST` includes its host; run once per `db_backend` (`dynamodb`, `postgresql`) |
| **Dependencies** | a2a_pushconfig_store (`DurablePushNotificationConfigStore`), a2a_server push_sender, a2a_settings repo (both backends), WebhookUrlValidator |
| **Test data** | 1 task; 1 allowlisted HTTPS webhook URL; 1 disallowed/private URL |
| **Steps** | 1. `tasks/pushNotificationConfig/set` an allowlisted webhook → assert an `a2a_settings` row `push_config#{task_id}` persisted. 2. Simulate cold process (new store instance / fresh invocation) and read back via `tasks/pushNotificationConfig/get` and the dispatch path. 3. Drive the task to a terminal state → assert the webhook receiver got an HTTP POST. 4. `set` a disallowed/private-CIDR URL → expect rejection, nothing persisted. 5. `tasks/pushNotificationConfig/delete` → assert the row is removed. |
| **Expected behavior** | Config is durable across recycle on both backends; cold reads reload from `a2a_settings`; webhook POST fires on state change; SSRF/allowlist violations are rejected on write and never persisted; delete removes the row. |
| **Validation points** | config_persisted_a2a_settings, cold_read_reloads, webhook_delivered_on_state_change, ssrf_rejected_not_persisted, delete_removes_row, both_backends_pass |
| **Cross-system checks** | `a2a_settings` row content == config returned by `get`; delivery observed at the receiver matches task terminal state |

### INT-019 — Authenticated extended Agent Card (Phase 13 C5)

| Field | Value |
|---|---|
| **ID** | INT-019 |
| **Name** | `agent/getAuthenticatedExtendedCard` returns more than the public card |
| **Priority** | P2 |
| **Type** | API |
| **CI trigger** | nightly |
| **Preconditions** | INT-001 passed; `capabilities.extended_agent_card=true` on the card |
| **Dependencies** | a2a_server (`_build_extended_agent_card`), DefaultRequestHandler extended_agent_card |
| **Test data** | valid JWT (for the authenticated call) |
| **Steps** | 1. `GET /.well-known/agent-card.json` (public). 2. `agent/getAuthenticatedExtendedCard` (authenticated). 3. Diff the two cards. |
| **Expected behavior** | Extended card declares the traceability extension (`https://a2a-protocol.org/extensions/traceability/v1`) in `capabilities.extensions` — absent from the public card — and carries `documentation_url` when `A2A_DOCUMENTATION_URL` is set. It is not a verbatim copy. |
| **Validation points** | extended_has_traceability_extension, public_lacks_extension, extended_not_verbatim_copy, documentation_url_present_when_set |
| **Cross-system checks** | none |

### INT-020 — Configurable I/O modes + gateway streaming deviation (Phase 13 C6/C8)

| Field | Value |
|---|---|
| **ID** | INT-020 |
| **Name** | I/O modes reflect settings; gateway `message/stream` is buffered with SSE out-of-band |
| **Priority** | P2 |
| **Type** | API + event |
| **CI trigger** | nightly |
| **Preconditions** | INT-001 passed; for the streaming leg, the daemon reachable through `silvaengine_gateway` |
| **Dependencies** | a2a_server (`_setting_list`, `_create_agent_card`), main.py stream collectors, sse_manager |
| **Test data** | settings `A2A_DEFAULT_OUTPUT_MODES="text, file, application/json"` (CSV); 1 streaming request |
| **Steps** | 1. Set output modes via CSV env setting; `GET /.well-known/agent-card.json` → assert `defaultOutputModes`. 2. Default (unset) → assert `["text"]`. 3. Through the gateway, `message/stream` → assert one aggregated JSON-RPC response (`status: streaming_complete`, `events: [...]`). 4. Subscribe to `/{ep}/a2a_sse` during the same run → assert live token events arrive there, filtered by `task_id`. |
| **Expected behavior** | Card advertises exactly the configured modes (CSV or YAML list parsed); default is text-only. Gateway `message/stream` returns an aggregated response, not an open SSE body; live events are delivered on `/{ep}/a2a_sse`. |
| **Validation points** | modes_from_csv_setting, modes_default_text, gateway_stream_aggregated, sse_live_events_out_of_band |
| **Cross-system checks** | Aggregated `events` count == live events observed on `/{ep}/a2a_sse` for the run |

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
| `push_disallowed_url` | `tasks/pushNotificationConfig/set` with a disallowed / private-CIDR / non-HTTPS webhook | Rejected with a validation error on write; **nothing persisted** to `a2a_settings`; no delivery attempted |
| `push_webhook_unreachable` | Allowlisted webhook receiver down when the sender POSTs on state change | Delivery failure is contained (logged); task state unaffected; no daemon crash; config remains for retry/next event |
| `push_config_cold_process` | Register a push config, then serve the state-change from a fresh process (Lambda recycle) | Durable store reloads the config from `a2a_settings` (both backends); webhook still delivered — config is **not** lost with the process |

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
| Push config durability | Push config persisted in `a2a_settings` == config returned by `pushNotificationConfig/get` after a cold read (both backends) | 0 mismatches |
| Multimodal fidelity | Inbound file/data parts persisted to `input_data` == parts the client sent; emitted `output_files` == file Parts on the returned message | 0 dropped |

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
- ≥ 90% of P2 scenarios pass, including the Phase 13 conformance set
  (INT-017 multimodal Parts, INT-018 push durability+delivery on both backends,
  INT-019 extended card, INT-020 I/O modes + gateway streaming deviation).
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
| Nightly | All P1 + P2 (INT-004, INT-006, INT-007, INT-009, INT-013, **INT-017, INT-019, INT-020**) + resilience subset + **live report export** | report only |
| Pre-release | Full suite + failure/resilience (Section 8) + reconciliation (Section 9) + Cognito (if configured) + Phase 10 (if available) + **Hermes bridge (INT-014, if `HERMES_API_URL` set)** + **Core Engine bridge (INT-015, if `CORE_ENGINE_GRAPHQL_URL` set)** + **Phase 13 conformance (INT-017..020; INT-018 needs a test webhook receiver)** + **both backends (dynamodb + postgresql)** + **dated live report with per-call input/output** | yes — blocks release |

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
   the release depends on it. Requires `ai_agent_core_engine` importable and an
   agent record that resolves a handler via `metadata.module_name`/`class_name`
   (or the `A2A_AI_AGENT_TYPE` shorthand). The legacy
   `A2A_AI_AGENT_MODULE`/`A2A_AI_AGENT_CLASS` env-var fallback is **removed**.
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
13. **Phase 13 protocol conformance** — `verified` in unit tests
    (`tests/test_phase13.py`): multimodal Parts (C1/C2), push delivery +
    durable store on both backends (C3/C4), extended card (C5), interrupt
    states (C7), configurable modes (C8), and the documented gateway streaming
    deviation (C6). **Live verification pending** (INT-018 needs a real webhook
    receiver; INT-017/019/020 confirm shape over the wire). Confirm whether
    push notifications are in scope for the release gate — if so, INT-018 is
    P1. New settings: `A2A_PUSH_WEBHOOK_ALLOWLIST` (recommended when push is
    used), `A2A_PUSH_REQUIRE_HTTPS`, `A2A_DEFAULT_INPUT_MODES`,
    `A2A_DEFAULT_OUTPUT_MODES`, `A2A_DOCUMENTATION_URL`.

Once you confirm or correct these items, the SOP moves from `draft` to
`approved` and full (non-subset) Phase 8+ test execution may proceed.