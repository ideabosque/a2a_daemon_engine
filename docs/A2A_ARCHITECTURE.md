# A2A Daemon Engine Architecture

**Status:** Working architecture reference for the current implementation.
**Scope:** A2A HTTP JSON-RPC, SDK execution flow, SSE streaming, task store, push configuration helpers, extended agent cards, and experimental gRPC.

This document describes how A2A protocol traffic moves through this repository. It is intentionally implementation-facing: diagrams name the modules that currently receive, route, persist, stream, or adapt each call.

---

## 1. Runtime Shape

The daemon runs the A2A SDK Starlette app as the primary HTTP application. REST routes are mounted under `/rest` for compatibility and operational clients.

```mermaid
flowchart TB
    Client["A2A client / peer agent"]
    SDKApp["A2A SDK Starlette app\n/ and /.well-known/agent-card.json"]
    RestApp["FastAPI REST app\n/rest/*"]
    JsonRpc["/rest/a2a-jsonrpc\ncompat JSON-RPC route"]
    Server["A2AProtocolServer"]
    Handler["DefaultRequestHandler"]
    Executor["A2ADaemonExecutor"]
    TaskStore["DynamoDBA2ATaskStore\nor SDK InMemoryTaskStore fallback"]
    GraphQL["A2A Core GraphQL layer"]
    DynamoDB["DynamoDB models"]
    SSE["SSEEventQueue + StreamingTaskManager"]

    Client --> SDKApp
    Client --> RestApp
    RestApp --> JsonRpc
    SDKApp --> Handler
    JsonRpc --> Handler
    Handler --> Executor
    Handler --> TaskStore
    Executor --> GraphQL
    TaskStore --> GraphQL
    GraphQL --> DynamoDB
    Executor --> SSE
```

Primary modules:

| Concern | Module | Detailed Description |
|---|---|---|
| Process/runtime entry | `a2a_daemon_engine/main.py` | Builds daemon settings, initializes `Config`, selects the active transport (`http`, `lambda`, or experimental `grpc`), mounts the SDK app as the primary HTTP surface, and preserves the serverless-style `a2a()` action/JSON-RPC invocation path. |
| SDK server and Agent Card | `handlers/a2a_server.py` | Creates the A2A `AgentCard`, declares skills/capabilities, builds the SDK `DefaultRequestHandler`, wires the canonical executor and task store, registers SSE routes, and exposes compatibility methods for agent registration, task assignment, message routing, and discovery. |
| A2A execution | `handlers/a2a_executor.py` | Implements the SDK `AgentExecutor` contract. It adapts SDK `RequestContext` objects into internal operations, emits task status/message events, routes task/message/agent operations to business handlers, and handles task cancellation with SDK-version compatibility helpers. |
| Persistent task store adapter | `handlers/a2a_taskstore.py` | Bridges the A2A SDK task-store interface to the daemon's DynamoDB/GraphQL task model, including task get/save/list behavior, task-state normalization, cursor pagination, and replay-buffer alignment for streaming. |
| SSE streaming and replay | `handlers/a2a_sse.py` | Provides `SSEEvent`, per-task replay buffers, subscriber queues, `Last-Event-ID` reconnect support, task status/artifact emitters, and the `/tasks/{task_id}/stream` `text/event-stream` endpoint registration. |
| REST and compatibility JSON-RPC routes | `handlers/a2a_app.py`, `handlers/a2a_jsonrpc.py` | Hosts auxiliary FastAPI routes under `/rest`, authentication-aware REST endpoints, and the compatibility JSON-RPC route. The current `/rest/a2a-jsonrpc` path delegates selected protocol methods to the SDK handler; `a2a_jsonrpc.py` remains a deprecated compatibility shim. |
| Business compatibility handlers | `handlers/a2a_handlers.py` | Contains domain-level operations for agent handshake, task assignment, message routing, state sync, agent discovery, and message delivery retries. These handlers bridge REST/serverless requests and executor operations into GraphQL persistence. |
| Push config helpers | `handlers/a2a_pushconfig.py` | Implements A2A-style task push-notification configuration management, including create/get/list/delete behavior, webhook URL validation, private-network/SSRF protections, allowlist checks, and notification delivery helpers. |
| Extended card helpers | `handlers/a2a_extended_card.py` | Builds authenticated extended Agent Card responses with richer operational metadata such as rate limits, security policy, traceability extension data, cache headers, ETag handling, and conditional request support. |
| Experimental gRPC | `handlers/a2a_grpc.py` | Provides the Phase 9 JSON-over-gRPC adapter with unary and streaming handlers for send/get/list/cancel/subscribe operations. It adapts dict payloads into executor-compatible request contexts and should be replaced with generated protobuf stubs before production use. |

---

## 2. Endpoint Map

| Endpoint / Transport | Purpose | Current Status |
|---|---|---|
| `GET /.well-known/agent-card.json` | Public Agent Card discovery | SDK app exposes this |
| `POST /` | Native A2A SDK JSON-RPC endpoint | Primary protocol path |
| `POST /rest/a2a-jsonrpc` | Compatibility JSON-RPC route | Routes selected methods to SDK handler |
| `GET /tasks/{task_id}/stream` | SSE task update stream | Registered on the A2A SDK app |
| `/rest/a2a/{endpoint_id}/...` | REST compatibility/admin routes | Auxiliary |
| `grpc://host:port/a2a.A2AService/*` | JSON-over-gRPC experimental transport | Implemented as Phase 9 experimental |

---

## 3. Cross-Cutting Request Flow

Most protocol methods follow this pattern:

```mermaid
sequenceDiagram
    autonumber
    participant C as A2A Client
    participant App as SDK Starlette App
    participant RH as DefaultRequestHandler
    participant EX as A2ADaemonExecutor
    participant TS as TaskStore
    participant GQL as A2A Core GraphQL
    participant DB as DynamoDB

    C->>App: JSON-RPC request
    App->>RH: Parse and dispatch method
    RH->>TS: Load or create task state when needed
    RH->>EX: execute(RequestContext, EventQueue)
    EX->>GQL: Invoke business handler / persistence
    GQL->>DB: Query or mutate tenant-scoped records
    DB-->>GQL: Result
    GQL-->>EX: Domain result
    EX-->>RH: Status/message/artifact events
    RH-->>App: JSON-RPC response
    App-->>C: Response body
```

Tenant isolation is carried by `partition_key`, usually assembled from `endpoint_id#part_id` or passed through headers such as `Part-ID` / `X-Partition-Key` on compatibility routes.

---

## 4. Agent Card Discovery

### 4.1 `GET /.well-known/agent-card.json`

```mermaid
sequenceDiagram
    autonumber
    participant C as A2A Client
    participant App as SDK Starlette App
    participant Card as AgentCard
    participant Server as A2AProtocolServer

    C->>App: GET /.well-known/agent-card.json
    App->>Server: Read configured AgentCard
    Server->>Card: name, url, skills, capabilities
    Card-->>App: Public Agent Card
    App-->>C: 200 application/json
```

The card is created in `A2AProtocolServer._create_agent_card()`. It advertises streaming and push-notification capability.

### 4.2 Authenticated Extended Agent Card

```mermaid
sequenceDiagram
    autonumber
    participant C as Authenticated Client
    participant Route as Extended Card Route
    participant Auth as Auth Middleware
    participant Manager as ExtendedAgentCardManager
    participant Card as Base AgentCard

    C->>Route: GET extended-card endpoint with Authorization
    Route->>Manager: get_extended_card(request, require_auth=true)
    Manager->>Auth: Validate Authorization header
    Auth-->>Manager: Auth context
    Manager->>Card: Read public card fields
    Manager-->>Route: Extended card metadata, rate limits, security policy
    Route-->>C: 200 JSON with ETag/cache headers
```

Extended card support exists as a Phase 8 helper. Its route wiring should be verified in the selected deployment mode before release certification.

---

## 5. Message Send

### 5.1 JSON-RPC `message/send`

```mermaid
sequenceDiagram
    autonumber
    participant C as A2A Client
    participant App as SDK App or /rest/a2a-jsonrpc
    participant RH as DefaultRequestHandler
    participant EX as A2ADaemonExecutor
    participant H as handle_task_assignment / handle_message_routing
    participant GQL as GraphQL Layer
    participant DB as DynamoDB

    C->>App: POST JSON-RPC method=message/send
    App->>RH: on_message_send(SendMessageRequest, context)
    RH->>EX: execute(request_context, event_queue)
    EX->>EX: Extract user input, partition_key, operation
    alt operation == task_execution
        EX->>H: handle_task_assignment(partition_key, task_data)
    else operation == message_routing
        EX->>H: handle_message_routing(partition_key, message_data)
    else operation == agent_registration
        EX->>H: handle_agent_handshake(partition_key, agent_data)
    end
    H->>GQL: Persist or query domain object
    GQL->>DB: Tenant-scoped mutation/query
    DB-->>GQL: Result
    GQL-->>H: Result
    H-->>EX: status/data
    EX-->>RH: WORKING, text message, terminal status event
    RH-->>App: SendMessage response
    App-->>C: JSON-RPC result
```

The executor accepts multiple internal operations because this daemon bridges A2A SDK messages to existing SilvaEngine handlers.

---

## 6. Streaming Message / SSE

### 6.1 Send Streaming Message

Streaming is represented by SDK streaming plus the repo's SSE helper. The current concrete SSE endpoint is `/tasks/{task_id}/stream`; `StreamingTaskManager` emits task status and artifact events into `SSEEventQueue`.

```mermaid
sequenceDiagram
    autonumber
    participant C as A2A Streaming Client
    participant App as SDK App
    participant RH as DefaultRequestHandler
    participant EX as A2ADaemonExecutor
    participant EQ as EventQueue
    participant SSE as StreamingTaskManager
    participant SQ as SSEEventQueue

    C->>App: JSON-RPC streaming send
    App->>RH: Dispatch streaming request
    RH->>EX: execute(request_context, event_queue)
    EX->>EQ: put(WORKING)
    EX->>SSE: emit_task_status(task_id, "working")
    SSE->>SQ: put(task_status event)
    EX->>EQ: put(message/artifact events)
    EX->>SSE: emit_task_artifact(...) when produced
    SSE->>SQ: put(task_artifact event)
    EX->>EQ: put(COMPLETED/FAILED/INPUT_REQUIRED/AUTH_REQUIRED)
    EX->>SSE: emit_task_status(final state)
    RH-->>App: Streaming response events
    App-->>C: Streamed protocol events
```

### 6.2 Subscribe To Task via SSE

```mermaid
sequenceDiagram
    autonumber
    participant C as SSE Client
    participant Route as GET /tasks/{task_id}/stream
    participant STM as StreamingTaskManager
    participant Q as SSEEventQueue
    participant Buffer as Replay Buffer

    C->>Route: GET /tasks/{task_id}/stream\nLast-Event-ID: optional
    Route->>STM: create_sse_response(task_id, last_event_id)
    STM->>Q: subscribe(task_id, last_event_id)
    alt Last-Event-ID present
        Q->>Buffer: Find buffered events after id
        Buffer-->>Q: Missed events
        Q-->>C: Replay events as text/event-stream
    end
    loop Until disconnect
        Q-->>C: id/event/data SSE frame
    end
    C-->>Route: Disconnect
    Q->>Q: Remove subscriber queue
```

SSE frames use:

```text
id: evt-...
event: task_status
data: {"task_id":"...","state":"working"}
```

---

## 7. Task Query

### 7.1 JSON-RPC `tasks/get`

```mermaid
sequenceDiagram
    autonumber
    participant C as A2A Client
    participant App as SDK App or /rest/a2a-jsonrpc
    participant RH as DefaultRequestHandler
    participant TS as DynamoDBA2ATaskStore
    participant GQL as GraphQL Layer
    participant DB as DynamoDB

    C->>App: POST JSON-RPC method=tasks/get
    App->>RH: on_get_task(GetTaskRequest, context)
    RH->>TS: get(task_id)
    TS->>GQL: get_a2a_task(partition_key, task_id)
    GQL->>DB: Query task record
    DB-->>GQL: Stored task row
    GQL-->>TS: Task dict/model
    TS-->>RH: SDK Task object / compatible task
    RH-->>App: JSON-RPC result
    App-->>C: Task payload
```

### 7.2 Task List

The SDK request handler can use the task store listing contract when list support is exposed by the selected SDK route. The project task store implements cursor pagination.

```mermaid
sequenceDiagram
    autonumber
    participant C as A2A Client
    participant RH as Request Handler
    participant TS as DynamoDBA2ATaskStore
    participant GQL as GraphQL Layer
    participant DB as DynamoDB

    C->>RH: tasks/list with limit and cursor
    RH->>TS: list_tasks(partition_key, limit, cursor)
    TS->>GQL: Query task list
    GQL->>DB: Paginated query
    DB-->>GQL: items + next cursor
    GQL-->>TS: task list
    TS-->>RH: tasks, next_cursor
    RH-->>C: Task list result
```

---

## 8. Task Cancellation

### 8.1 JSON-RPC `tasks/cancel`

```mermaid
sequenceDiagram
    autonumber
    participant C as A2A Client
    participant App as SDK App or /rest/a2a-jsonrpc
    participant RH as DefaultRequestHandler
    participant EX as A2ADaemonExecutor
    participant TS as TaskStore
    participant GQL as GraphQL Layer
    participant DB as DynamoDB

    C->>App: POST JSON-RPC method=tasks/cancel
    App->>RH: on_cancel_task(CancelTaskRequest, context)
    RH->>EX: cancel(task_id)
    EX->>TS: get(task_id)
    TS->>GQL: get task
    GQL->>DB: Query task
    DB-->>GQL: Current state
    GQL-->>TS: Task
    alt Task is terminal
        EX-->>RH: No-op / already terminal
    else Task is cancellable
        EX->>TS: save task with CANCELED state
        TS->>GQL: update task status
        GQL->>DB: Persist CANCELED
        EX-->>RH: cancellation accepted
    end
    RH-->>App: JSON-RPC result
    App-->>C: Cancellation response
```

### 8.2 Delegated Cancellation Propagation

Phase 9 adds `CancellationPropagator` for parent/child cancellation chains.

```mermaid
sequenceDiagram
    autonumber
    participant C as Caller
    participant P as CancellationPropagator
    participant EX as AgentExecutor
    participant TS as TaskStore
    participant DA as Downstream Agent

    C->>P: cancel_task_chain(parent_task_id)
    P->>EX: cancel(parent_task_id)
    P->>TS: find referenceTaskIds / child refs
    TS-->>P: child task references
    loop Each child task
        P->>EX: cancel(child_task_id)
        alt child delegated to agent
            P->>DA: notify cancellation (future CancelTask RPC)
        end
    end
    P-->>C: CancellationResult
```

---

## 9. Multi-Turn States

`INPUT_REQUIRED` and `AUTH_REQUIRED` are emitted through streaming helpers when a task needs user input or authentication.

```mermaid
sequenceDiagram
    autonumber
    participant EX as A2ADaemonExecutor
    participant STM as StreamingTaskManager
    participant SQ as SSEEventQueue
    participant C as SSE Client

    alt More input needed
        EX->>STM: emit_input_required(task_id, prompt, options)
        STM->>SQ: task_status state=input_required
        SQ-->>C: SSE event task_status
    else Authentication needed
        EX->>STM: emit_auth_required(task_id, auth_url, scopes)
        STM->>SQ: task_status state=auth_required
        SQ-->>C: SSE event task_status
    end
```

---

## 10. Push Notification Config

The push configuration manager supports create/get/list/delete and anti-SSRF validation. Route/RPC wiring should be verified for the selected deployment mode.

### 10.1 Create Task Push Notification Config

```mermaid
sequenceDiagram
    autonumber
    participant C as A2A Client
    participant API as Push Config RPC/Route
    participant M as PushNotificationManager
    participant V as WebhookUrlValidator
    participant Store as Task Store / Persistent Store

    C->>API: create config(task_id, webhook_url, token, events)
    API->>M: create_push_config(...)
    M->>V: validate(webhook_url)
    alt URL rejected
        V-->>M: invalid reason
        M-->>API: validation error
        API-->>C: error
    else URL accepted
        M->>Store: persist config
        Store-->>M: saved
        M-->>API: PushNotificationConfig
        API-->>C: success
    end
```

### 10.2 Get/List/Delete Task Push Notification Config

```mermaid
sequenceDiagram
    autonumber
    participant C as A2A Client
    participant API as Push Config RPC/Route
    participant M as PushNotificationManager
    participant Store as Task Store / Persistent Store

    alt Get config
        C->>API: get(task_id, config_id)
        API->>M: get_push_config(...)
        M->>Store: load config
        Store-->>M: config or none
        M-->>API: result
    else List configs
        C->>API: list(task_id, cursor, limit)
        API->>M: list_push_configs(...)
        M->>Store: query configs
        Store-->>M: configs + next cursor
        M-->>API: result
    else Delete config
        C->>API: delete(task_id, config_id)
        API->>M: delete_push_config(...)
        M->>Store: delete config
        Store-->>M: deleted
        M-->>API: success
    end
    API-->>C: JSON-RPC/HTTP response
```

### 10.3 Push Notification Delivery

```mermaid
sequenceDiagram
    autonumber
    participant Worker as Task/Event Worker
    participant M as PushNotificationManager
    participant V as WebhookUrlValidator
    participant H as Webhook Receiver

    Worker->>M: notify task event
    M->>M: load matching push configs
    loop Each config
        M->>V: validate callback URL
        V-->>M: valid
        M->>H: HTTP POST event payload
        H-->>M: 2xx/4xx/5xx
    end
    M-->>Worker: delivery summary
```

---

## 11. Legacy REST Compatibility Methods

These are not the primary A2A protocol methods, but they remain useful for SilvaEngine clients and tests.

### 11.1 Register Agent

```mermaid
sequenceDiagram
    autonumber
    participant C as REST / Lambda Client
    participant Main as A2ADaemonEngine.a2a()
    participant H as handle_agent_handshake
    participant Server as A2AProtocolServer
    participant GQL as GraphQL Layer
    participant DB as DynamoDB

    C->>Main: action=register_agent
    Main->>H: handle_agent_handshake(partition_key, agent_info)
    H->>Server: handle_handshake(...)
    Server->>GQL: insertUpdateA2aAgent
    GQL->>DB: upsert agent
    DB-->>GQL: saved agent
    GQL-->>Server: result
    Server-->>H: registered
    H-->>Main: status/data
    Main-->>C: JSON result
```

### 11.2 Assign Task

```mermaid
sequenceDiagram
    autonumber
    participant C as REST / Lambda Client
    participant Main as A2ADaemonEngine.a2a()
    participant H as handle_task_assignment
    participant Server as A2AProtocolServer
    participant GQL as GraphQL Layer
    participant DB as DynamoDB

    C->>Main: action=assign_task
    Main->>H: handle_task_assignment(partition_key, task)
    alt A2A core initialized
        H->>GQL: insertUpdateA2aTask
        GQL->>DB: upsert task
        DB-->>GQL: saved task
        GQL-->>H: result
    else mocked / server compatibility path
        H->>Server: assign_task(partition_key, task)
        Server-->>H: result
    end
    H-->>Main: status/data
    Main-->>C: JSON result
```

### 11.3 Route Message

```mermaid
sequenceDiagram
    autonumber
    participant C as REST / Lambda Client
    participant Main as A2ADaemonEngine.a2a()
    participant H as handle_message_routing
    participant Server as A2AProtocolServer
    participant GQL as GraphQL Layer
    participant DB as DynamoDB

    C->>Main: action=route_message
    Main->>H: handle_message_routing(partition_key, message)
    alt A2A core initialized
        H->>GQL: insertUpdateA2aMessage
        GQL->>DB: upsert message
        DB-->>GQL: saved message
        GQL-->>H: result
    else mocked / server compatibility path
        H->>Server: route_message(partition_key, message)
        Server-->>H: result
    end
    H-->>Main: status/data
    Main-->>C: JSON result
```

---

## 12. Experimental gRPC Transport

Phase 9 adds a JSON-over-gRPC adapter. It is not generated from protobuf stubs yet, so treat it as experimental.

### 12.1 gRPC `SendMessage`

```mermaid
sequenceDiagram
    autonumber
    participant C as gRPC Client
    participant S as A2AGRPCServer
    participant G as A2AGRPCServicer
    participant EX as AgentExecutor
    participant Q as asyncio.Queue

    C->>S: /a2a.A2AService/SendMessage JSON payload
    S->>G: SendMessage(request, context)
    G->>Q: create event queue
    G->>EX: execute(_RequestContextAdapter, queue)
    EX-->>Q: status/message events
    G->>Q: drain events
    G-->>S: JSON response with events
    S-->>C: unary response
```

### 12.2 gRPC Streaming / Subscribe

```mermaid
sequenceDiagram
    autonumber
    participant C as gRPC Client
    participant G as A2AGRPCServicer
    participant EX as AgentExecutor
    participant Q as Stream Queue

    C->>G: SendMessageStream or SubscribeToTask
    G->>Q: register stream queue
    alt SendMessageStream
        G->>EX: execute(request_context, queue)
    end
    loop Active stream
        Q-->>G: task event
        G-->>C: TaskUpdate JSON payload
    end
    C-->>G: cancel/disconnect
    G->>G: unregister stream queue
```

---

## 13. Method Coverage Summary

| A2A Method / Capability | Primary Implementation | Notes |
|---|---|---|
| Agent Card discovery | SDK Starlette app + `A2AProtocolServer.agent_card` | Public card is primary; extended card is helper-managed |
| `message/send` | SDK `DefaultRequestHandler` + `A2ADaemonExecutor` | Implemented |
| Streaming message | SDK stream path + `a2a_sse.py` helpers | Requires live client validation |
| `tasks/get` | SDK handler + `DynamoDBA2ATaskStore.get` | Implemented |
| Task list | `DynamoDBA2ATaskStore.list_tasks` | Implemented at store layer |
| `tasks/cancel` | SDK handler + `A2ADaemonExecutor.cancel` | Implemented |
| Subscribe to task | `GET /tasks/{task_id}/stream` | SSE replay via `Last-Event-ID` |
| Push config create/get/list/delete | `a2a_pushconfig.py` | Helper complete; route/RPC wiring should be verified |
| Authenticated extended card | `a2a_extended_card.py` | Helper complete; route wiring should be verified |
| gRPC Send/Get/List/Cancel/Subscribe | `a2a_grpc.py` | Experimental JSON-over-gRPC adapter |
| REST register/assign/route/execute | `A2ADaemonEngine.a2a()` + `a2a_handlers.py` | Compatibility surface, not the primary A2A protocol path |

---

## 14. Open Architecture Questions

1. **Generated protobuf gRPC:** The gRPC adapter should be replaced or supplemented with generated protobuf service stubs before production use.
2. **Push config exposure:** `a2a_pushconfig.py` has the manager logic; the exact public JSON-RPC/REST exposure should be verified in the selected runtime.
3. **Extended card endpoint:** The helper supports authenticated extended cards and cache headers; route registration should be confirmed against the deployed SDK app.
4. **Live TCK validation:** The default test suite avoids AWS and live daemon dependencies. Release certification still needs TCK/Inspector runs against a running daemon with the intended persistence backend.
5. **Task event source of truth:** SSE currently has an in-memory replay buffer. If replay must survive process restart, events should be persisted alongside task history.
