# A2A Protocol Call Flow

**Purpose:** Step-by-step code traversal from `main.py` into each module/function used by A2A protocol methods.

This document complements `A2A_ARCHITECTURE.md`. The architecture document explains components and sequence diagrams. This document is a code-path runbook: where the request enters, which function is called next, and where state is read, written, streamed, or adapted.

---

## 1. Startup Path From `main.py`

### 1.1 CLI Startup

1. `a2a_daemon_engine/main.py::main()`
   - Configures process logging.
   - Reads environment variables:
     - `A2A_TRANSPORT`, default `http`
     - `PORT`, default `8001`
     - `A2A_CONFIG_FILE`
     - authentication and AWS/Cognito settings
   - Instantiates `A2ADaemonEngine(logger, **settings)`.
   - Calls `asyncio.run(a2a_daemon_engine.daemon())`.

2. `A2ADaemonEngine.__init__(logger, **setting)`
   - Calls `Config.initialize(logger, **setting)`.
   - Stores:
     - `self.transport`
     - `self.port`
     - `self.logger`
     - `self.setting`

3. `handlers/config.py::Config.initialize(logger, **setting)`
   - Calls `_set_parameters(setting)`.
   - Calls `_initialize_a2a_core(logger, setting)`.
   - Calls `_initialize_a2a_server(logger, setting)`.
   - Calls `_initialize_aws_services(logger, setting)`.
   - Optionally calls `_initialize_tables(logger)` when `initialize_tables` is enabled.

4. `Config._initialize_a2a_core(...)`
   - Builds the GraphQL core service.
   - Wires model/query/mutation modules through the SilvaEngine GraphQL layer.
   - Stores the initialized object as `Config.a2a_core`.

5. `Config._initialize_a2a_server(...)`
   - Instantiates `handlers/a2a_server.py::A2AProtocolServer`.
   - Stores it as `Config.a2a_server`.

6. `A2AProtocolServer.__init__(logger, **settings)`
   - Calls `_initialize_a2a_server()`.

7. `A2AProtocolServer._initialize_a2a_server()`
   - Creates skills with `_create_agent_skills(capability_list)`.
   - Creates public Agent Card with `_create_agent_card(...)`.
   - Creates `ExtendedAgentCardManager`.
   - Creates `DynamoDBA2ATaskStore`, falling back to SDK `InMemoryTaskStore` if needed.
   - Creates `SSEEventQueue`.
   - Creates `StreamingTaskManager`.
   - Creates `A2ADaemonExecutor`.
   - Creates SDK `DefaultRequestHandler`.
   - Creates SDK `A2AStarletteApplication`.
   - Calls `create_sse_endpoints(self.app, self.streaming_manager)`.

### 1.2 HTTP Runtime Startup

1. `A2ADaemonEngine.daemon()`
   - Branches on `self.transport`.

2. If `self.transport == "http"`:
   - Imports `uvicorn`, `FastAPI`, REST app, auth router, and JWT middleware.
   - Calls `Config.a2a_server.app.build()` to get the SDK Starlette app.
   - Creates a separate FastAPI REST app.
   - Adds `FlexJWTMiddleware` to REST app.
   - Mounts auth router.
   - Copies routes from `handlers/a2a_app.py::app` into the REST app.
   - Mounts REST app under `/rest` on the SDK app.
   - Runs `uvicorn.Server(config).serve()`.

3. Primary HTTP surfaces after startup:
   - `GET /.well-known/agent-card.json`
   - `POST /`
   - `GET /tasks/{task_id}/stream`
   - `POST /rest/a2a-jsonrpc`
   - REST compatibility routes under `/rest`

### 1.3 gRPC Runtime Startup

1. `A2ADaemonEngine.daemon()`
2. If `self.transport == "grpc"`:
   - Imports `A2AGRPCServer` and `GRPCConfig`.
   - Creates `GRPCConfig(host="0.0.0.0", port=self.port, max_workers=10)`.
   - Creates `A2AGRPCServer(...)`.
   - Calls `await server.start()`.

**Current note:** `handlers/a2a_grpc.py` is an experimental JSON-over-gRPC adapter, not a generated protobuf-stub implementation.

### 1.4 Serverless / Direct Invocation Startup

1. A host runtime instantiates `A2ADaemonEngine`.
2. Calls one of:
   - `A2ADaemonEngine.a2a_core_graphql(**params)`
   - `A2ADaemonEngine.a2a(**params)`

Direct invocation does not start Uvicorn. It uses the same initialized `Config` and synchronous `_run_async()` bridge.

---

## 2. Shared Helpers Used By Many Methods

### 2.1 Partition Key Assembly

Function: `main.py::A2ADaemonEngine._apply_partition_defaults(params)`

Steps:

1. Reads `endpoint_id` from request params or engine settings.
2. Reads `part_id` from request params or engine settings.
3. Writes `params["partition_key"]`.
4. Format:
   - `endpoint_id#part_id` when `part_id` exists
   - `endpoint_id` otherwise

This is the main multi-tenant routing key that downstream GraphQL and DynamoDB paths use.

### 2.2 Sync-To-Async Bridge

Function: `main.py::A2ADaemonEngine._run_async(coro)`

Steps:

1. Checks whether an event loop is already running.
2. If no loop is running, calls `asyncio.run(coro)`.
3. If a loop is already running, starts a one-worker `ThreadPoolExecutor`.
4. Runs the coroutine in the worker thread with `asyncio.run`.
5. Returns the coroutine result to synchronous callers such as Lambda-style action handlers.

---

## 3. Agent Card Discovery

### 3.1 `GET /.well-known/agent-card.json`

Entry point:

1. `main.py::A2ADaemonEngine.daemon()`
2. `Config.a2a_server.app.build()`
3. SDK `A2AStarletteApplication`

Call path:

1. Client sends `GET /.well-known/agent-card.json`.
2. SDK Starlette route handles the request.
3. SDK app reads the `AgentCard` passed during construction.
4. The card was created by `A2AProtocolServer._create_agent_card(...)`.
5. Response returns:
   - `name`
   - `description`
   - `url`
   - `version`
   - `defaultInputModes`
   - `defaultOutputModes`
   - `capabilities`
   - `skills`
   - `provider`

Important functions:

1. `handlers/a2a_server.py::A2AProtocolServer._create_agent_skills(capability_list)`
2. `handlers/a2a_server.py::A2AProtocolServer._create_agent_card(...)`

### 3.2 Extended Agent Card

Primary helper module:

1. `handlers/a2a_extended_card.py::ExtendedAgentCardManager`

Call path when wired into a route:

1. Client requests authenticated extended-card route.
2. Route calls `ExtendedAgentCardManager.get_extended_card(request, require_auth=True)`.
3. Manager authenticates request headers.
4. Manager reads base `AgentCard`.
5. Manager enriches response with:
   - rate-limit metadata
   - security policy
   - traceability metadata
   - cache headers / ETag support
6. Route returns extended card JSON.

Status:

- Helper is implemented.
- Deployment-specific route wiring should be verified.

---

## 4. JSON-RPC `message/send`

There are two implemented entry paths:

1. Native SDK app at `POST /`
2. Compatibility route at `POST /rest/a2a-jsonrpc`
3. Direct/serverless invocation through `A2ADaemonEngine.a2a(jsonrpc="2.0", method="message/send", ...)`

### 4.1 Native SDK App Path

Entry point:

1. `main.py::A2ADaemonEngine.daemon()`
2. `Config.a2a_server.app.build()`
3. SDK `A2AStarletteApplication`

Step-by-step:

1. Client sends JSON-RPC request to `POST /`:
   - `method = "message/send"`
   - `params = SendMessageRequest fields`

2. SDK Starlette app parses the request.

3. SDK app calls `DefaultRequestHandler.on_message_send(...)`.
   - Object was created in `A2AProtocolServer._initialize_a2a_server()`.

4. SDK handler interacts with:
   - `DynamoDBA2ATaskStore`
   - `A2ADaemonExecutor`

5. SDK handler calls:
   - `A2ADaemonExecutor.execute(request_context, event_queue)`

6. `A2ADaemonExecutor.execute(...)`:
   - Calls `request_context.get_user_input()`.
   - Reads `partition_key` from context.
   - Reads internal `operation` from context, defaulting to `task_execution`.
   - Routes to one of:
     - `_handle_task_execution(...)`
     - `_handle_message_routing(...)`
     - `_handle_agent_registration(...)`

7. For default task execution:
   - `_handle_task_execution(partition_key, request_context, event_queue)`
   - Imports `handle_task_assignment`.
   - Reads `task_data`.
   - Adds user input as description when needed.
   - Emits `WORKING` through `_status_update_event(...)`.
   - Calls `handle_task_assignment(partition_key, task_data)`.

8. `handlers/a2a_handlers.py::handle_task_assignment(...)`:
   - Generates `task_id` if absent.
   - If no `Config.a2a_core` and a compatible `Config.a2a_server.assign_task` exists, delegates to the compatibility method.
   - Otherwise resolves `assigned_agent_id`.
   - May call `find_best_agent(...)`.
   - Persists with `Config.a2a_core.insert_update_a2a_task(...)`.
   - Returns status/data.

9. Executor emits:
   - agent text message through `_agent_text_message(...)`
   - terminal status through `_status_update_event(COMPLETED or FAILED)`

10. SDK handler formats the response.

11. SDK app returns JSON-RPC response to client.

### 4.2 Compatibility JSON-RPC Route Path

Entry point:

1. `main.py::A2ADaemonEngine.daemon()`
2. REST app mounted under `/rest`
3. `handlers/a2a_app.py::a2a_jsonrpc_endpoint(request)`

Step-by-step:

1. Client sends `POST /rest/a2a-jsonrpc`.
2. `a2a_jsonrpc_endpoint(request)` calls `await request.json()`.
3. It checks `Config.a2a_server` and SDK type availability.
4. It reads partition key from:
   - `Part-ID`
   - `X-Partition-Key`
5. It builds `ServerCallContext(agent_card=..., partition_key=...)`.
6. It reads `request_handler = Config.a2a_server.request_handler`.
7. If `method == "message/send"`:
   - Builds `SendMessageRequest(**message["params"])`.
   - Calls `await request_handler.on_message_send(send_request, context)`.
   - Returns `JSONResponse(content=response.model_dump())`.

### 4.3 Serverless / Direct JSON-RPC Path

Entry point:

1. `main.py::A2ADaemonEngine.a2a(**params)`

Step-by-step:

1. Direct caller passes:
   - `jsonrpc="2.0"`
   - `method="message/send"`
   - `params={...}`
2. `a2a()` calls `_apply_partition_defaults(params)`.
3. It checks for JSON-RPC format.
4. It reads `Config.a2a_server.request_handler`.
5. It builds `ServerCallContext`.
6. It builds `SendMessageRequest`.
7. It calls:
   - `_run_async(request_handler.on_message_send(send_request, context))`
8. It serializes `response.model_dump()` through `Serializer.json_dumps(...)`.

---

## 5. JSON-RPC `tasks/get`

### 5.1 Compatibility Route Path

Entry point:

1. `handlers/a2a_app.py::a2a_jsonrpc_endpoint(request)`

Step-by-step:

1. Client sends `POST /rest/a2a-jsonrpc` with `method = "tasks/get"`.
2. Route parses JSON body.
3. Route builds `ServerCallContext`.
4. Route creates:
   - `GetTaskRequest(**message["params"])`
5. Route calls:
   - `await Config.a2a_server.request_handler.on_get_task(get_request, context)`
6. SDK handler calls task store:
   - `DynamoDBA2ATaskStore.get(task_id, context)`

### 5.2 Task Store Get Path

Function: `handlers/a2a_taskstore.py::DynamoDBA2ATaskStore.get(task_id, context=None)`

Step-by-step:

1. Imports `get_a2a_task` from `handlers/a2a_utility.py`.
2. Calls:
   - `await get_a2a_task(partition_key=self.partition_key, task_id=task_id)`
3. `a2a_utility.get_a2a_task(...)` calls the GraphQL/DynamoDB data path.
4. If no task is found, returns `None`.
5. If a task is found, calls:
   - `self._dict_to_task(task_dict)`
6. Returns SDK-compatible `Task`.

### 5.3 Serverless / Direct Path

Entry point:

1. `main.py::A2ADaemonEngine.a2a(**params)`

Step-by-step:

1. Caller passes JSON-RPC `method="tasks/get"`.
2. `a2a()` applies partition defaults.
3. It builds `ServerCallContext`.
4. It builds `GetTaskRequest`.
5. It calls:
   - `_run_async(request_handler.on_get_task(get_request, context))`
6. It returns serialized response.

---

## 6. JSON-RPC `tasks/cancel`

### 6.1 Compatibility Route Path

Entry point:

1. `handlers/a2a_app.py::a2a_jsonrpc_endpoint(request)`

Step-by-step:

1. Client sends `POST /rest/a2a-jsonrpc` with `method = "tasks/cancel"`.
2. Route parses body.
3. Route builds `ServerCallContext`.
4. Route creates:
   - `CancelTaskRequest(**message["params"])`
5. Route calls:
   - `await request_handler.on_cancel_task(cancel_request, context)`
6. SDK handler calls executor cancellation:
   - `A2ADaemonExecutor.cancel(task_id)`

### 6.2 Executor Cancel Path

Function: `handlers/a2a_executor.py::A2ADaemonExecutor.cancel(task_id)`

Step-by-step:

1. Checks whether `self.task_store` exists.
2. Calls:
   - `await self.task_store.get(task_id)`
3. If task is missing:
   - logs and returns without raising.
4. Reads current task state.
5. If task is terminal:
   - leaves it unchanged.
6. If task is cancellable:
   - sets task state to `CANCELED`.
   - persists with `await self.task_store.save(task)`.

### 6.3 Task Store Save Path

Function: `handlers/a2a_taskstore.py::DynamoDBA2ATaskStore.save(task, context=None)`

Step-by-step:

1. Imports:
   - `get_a2a_task`
   - `insert_a2a_task`
   - `update_a2a_task`
2. Extracts `task_id`.
3. Converts SDK `Task` to internal dict with `_task_to_dict(task)`.
4. Checks existing task:
   - `await get_a2a_task(partition_key=self.partition_key, task_id=task_id)`
5. If task exists:
   - calls `await update_a2a_task(...)`.
6. If task does not exist:
   - calls `await insert_a2a_task(...)`.
   - initializes event cache with `_touch_task_cache(task_id)`.

### 6.4 Serverless / Direct Path

Entry point:

1. `main.py::A2ADaemonEngine.a2a(**params)`

Step-by-step:

1. Caller passes JSON-RPC `method="tasks/cancel"`.
2. `a2a()` applies partition defaults.
3. It builds `ServerCallContext`.
4. It builds `CancelTaskRequest`.
5. It calls:
   - `_run_async(request_handler.on_cancel_task(cancel_request, context))`
6. It returns serialized response.

---

## 7. Task List

Task list is implemented at the task-store layer and can be used by SDK handlers or compatibility code that needs cursor pagination.

Function:

1. `handlers/a2a_taskstore.py::DynamoDBA2ATaskStore.list_tasks(...)`

Step-by-step:

1. Receives:
   - `partition_key`
   - `limit`
   - `cursor`
2. Calls GraphQL/list helper path for task records.
3. Converts stored task rows into SDK-compatible tasks.
4. Returns:
   - list of tasks
   - next cursor

Related lower-level helpers:

1. `handlers/a2a_utility.py::list_a2a_tasks(...)`
2. `queries/a2a_task.py::resolve_a2a_task_list(...)`
3. `models/a2a_task.py`

---

## 8. Streaming Message and SSE

The concrete streaming helper path is implemented in `handlers/a2a_sse.py`.

### 8.1 SSE Setup During Startup

Starting from `main.py`:

1. `main.py::main()`
2. `A2ADaemonEngine.__init__()`
3. `Config.initialize()`
4. `Config._initialize_a2a_server()`
5. `A2AProtocolServer._initialize_a2a_server()`
6. Creates:
   - `SSEEventQueue(task_store, max_events_per_task=100)`
   - `StreamingTaskManager(event_queue)`
7. Passes `streaming_manager` into:
   - `A2ADaemonExecutor(...)`
8. Calls:
   - `create_sse_endpoints(self.app, self.streaming_manager)`

### 8.2 `GET /tasks/{task_id}/stream`

Entry function:

1. `handlers/a2a_sse.py::create_sse_endpoints(app, streaming_manager)`
2. Inner route function:
   - `subscribe_to_task(request)`

Step-by-step:

1. Client sends `GET /tasks/{task_id}/stream`.
2. `subscribe_to_task(request)` reads:
   - `task_id = request.path_params["task_id"]`
   - `last_event_id = request.headers.get("Last-Event-ID")`
3. Calls:
   - `streaming_manager.create_sse_response(task_id, last_event_id)`
4. `StreamingTaskManager.create_sse_response(...)` creates `event_generator()`.
5. `event_generator()` calls:
   - `async for event in self.event_queue.subscribe(task_id, last_event_id)`
6. Each `SSEEvent` is converted to bytes with:
   - `event.to_sse_format().encode("utf-8")`
7. Returns `StreamingResponse` with:
   - `media_type="text/event-stream"`
   - `Cache-Control: no-cache`
   - `Connection: keep-alive`
   - `X-Accel-Buffering: no`

### 8.3 `SSEEventQueue.subscribe(...)`

Function:

1. `handlers/a2a_sse.py::SSEEventQueue.subscribe(task_id, last_event_id=None)`

Step-by-step:

1. Creates an `asyncio.Queue`.
2. Adds that queue to `self._subscriptions[task_id]`.
3. If `last_event_id` exists:
   - looks in `self._event_buffers[task_id]`
   - replays events after the matching event ID
4. Waits on `queue.get()`.
5. Yields each `SSEEvent`.
6. On cancellation/disconnect:
   - removes the queue from `self._subscriptions[task_id]`.

### 8.4 Emitting Task Status Events

Function:

1. `handlers/a2a_sse.py::StreamingTaskManager.emit_task_status(...)`

Step-by-step:

1. Builds an `SSEEvent`:
   - `event_type="task_status"`
   - data includes `task_id`, `state`, `message`, `artifacts`, `timestamp`
2. Calls:
   - `await self.event_queue.put(task_id, event)`
3. `SSEEventQueue.put(...)`:
   - creates the task buffer if missing
   - appends the event to replay buffer
   - broadcasts to active subscriber queues

### 8.5 Emitting Task Artifacts

Function:

1. `handlers/a2a_sse.py::StreamingTaskManager.emit_task_artifact(...)`

Step-by-step:

1. Builds an `SSEEvent`:
   - `event_type="task_artifact"`
   - data includes `task_id`, artifact payload, timestamp
2. Calls:
   - `await self.event_queue.put(task_id, event)`

### 8.6 `INPUT_REQUIRED`

Function:

1. `handlers/a2a_sse.py::StreamingTaskManager.emit_input_required(...)`

Step-by-step:

1. Receives:
   - `task_id`
   - `prompt`
   - optional `options`
2. Calls:
   - `emit_task_status(state="input_required", message=prompt, artifacts=[...])`
3. Event is written to `SSEEventQueue`.
4. Active subscribers receive a `task_status` SSE frame.

### 8.7 `AUTH_REQUIRED`

Function:

1. `handlers/a2a_sse.py::StreamingTaskManager.emit_auth_required(...)`

Step-by-step:

1. Receives:
   - `task_id`
   - `auth_url`
   - optional scopes
2. Calls:
   - `emit_task_status(state="auth_required", message="Authentication required", artifacts=[...])`
3. Event is written to `SSEEventQueue`.
4. Active subscribers receive a `task_status` SSE frame.

---

## 9. Push Notification Config Methods

Module:

1. `handlers/a2a_pushconfig.py`

Status:

- Manager/helper logic exists.
- Public route/RPC exposure should be verified per deployment.

### 9.1 Create Push Config

Expected call flow:

1. A JSON-RPC or REST route receives create config request.
2. Route calls `PushNotificationManager.create_push_config(...)`.
3. Manager validates webhook URL using `WebhookUrlValidator.validate(...)`.
4. Validator checks:
   - URL scheme
   - private network / loopback / reserved ranges
   - allowlist rules
   - bypass patterns
5. Manager persists or caches the config.
6. Returns config metadata.

### 9.2 Get Push Config

Expected call flow:

1. Route receives get request.
2. Route calls `PushNotificationManager.get_push_config(...)`.
3. Manager checks in-memory config/cache first.
4. Manager falls back to persistent store where configured.
5. Returns config or not-found result.

### 9.3 List Push Configs

Expected call flow:

1. Route receives list request.
2. Route calls `PushNotificationManager.list_push_configs(...)`.
3. Manager queries configs for the task.
4. Applies cursor/limit handling where available.
5. Returns configs and next cursor.

### 9.4 Delete Push Config

Expected call flow:

1. Route receives delete request.
2. Route calls `PushNotificationManager.delete_push_config(...)`.
3. Manager removes config from cache/store.
4. Returns deletion status.

### 9.5 Deliver Push Notification

Expected call flow:

1. Task or message event occurs.
2. Worker/handler calls notification delivery helper.
3. Manager loads matching push configs.
4. Manager revalidates webhook target.
5. Manager sends HTTP POST to webhook endpoint.
6. Manager records success/failure status.

---

## 10. Extended Agent Card Methods

Module:

1. `handlers/a2a_extended_card.py`

### 10.1 Get Extended Agent Card

Expected call flow:

1. Route receives authenticated extended-card request.
2. Route calls:
   - `ExtendedAgentCardManager.get_extended_card(request, require_auth=True)`
3. Manager checks authentication.
4. Manager reads the base Agent Card.
5. Manager adds:
   - rate-limit config
   - security policy
   - traceability extension
   - provider/metadata fields
6. Manager returns the extended card.

### 10.2 Conditional Cache Handling

Expected call flow:

1. Route receives request with `If-None-Match` or `If-Modified-Since`.
2. Manager calls cache validation helper.
3. If current:
   - route can return `304 Not Modified`.
4. Otherwise:
   - route returns full extended card with cache headers.

---

## 11. REST / Lambda Compatibility Actions

These paths are not the primary A2A SDK protocol path, but they are still part of the daemon's public compatibility surface.

### 11.1 `action=register_agent`

Entry point:

1. `main.py::A2ADaemonEngine.a2a(**params)`

Step-by-step:

1. Applies partition defaults.
2. Pops `action`.
3. Validates `agent_id`.
4. Imports:
   - `handlers/a2a_handlers.py::handle_agent_handshake`
5. Calls:
   - `_run_async(handle_agent_handshake(partition_key=partition_key, agent_info=params))`
6. `handle_agent_handshake(...)`:
   - validates `agent_id`, `agent_name`, `capabilities`
   - calls `Config.a2a_server.handle_handshake(...)` when server is available
7. `A2AProtocolServer.handle_handshake(...)`:
   - splits `partition_key` into endpoint/part
   - builds GraphQL mutation `insertUpdateA2aAgent`
   - calls `Config.a2a_core.a2a_core_graphql(...)`
   - maps GraphQL result to compatibility response
8. `a2a()` returns JSON.

### 11.2 `action=assign_task`

Entry point:

1. `main.py::A2ADaemonEngine.a2a(**params)`

Step-by-step:

1. Applies partition defaults.
2. Pops `action`.
3. Imports `handle_task_assignment`.
4. Calls:
   - `_run_async(handle_task_assignment(partition_key=partition_key, task=params))`
5. `handle_task_assignment(...)`:
   - generates `task_id` if missing
   - uses mock/server compatibility path when `Config.a2a_core` is unavailable but `Config.a2a_server.assign_task` exists
   - otherwise finds/validates assigned agent
   - calls `Config.a2a_core.insert_update_a2a_task(...)`
6. Returns status/data.
7. `a2a()` serializes response.

### 11.3 `action=route_message`

Entry point:

1. `main.py::A2ADaemonEngine.a2a(**params)`

Step-by-step:

1. Applies partition defaults.
2. Pops `action`.
3. Validates `from_agent_id` and `to_agent_id`.
4. Imports `handle_message_routing`.
5. Calls:
   - `_run_async(handle_message_routing(partition_key=partition_key, message=params))`
6. `handle_message_routing(...)`:
   - generates `message_id` if missing
   - uses mock/server compatibility path when `Config.a2a_core` is unavailable but `Config.a2a_server.route_message` exists
   - otherwise calls `Config.a2a_core.insert_update_a2a_message(...)`
7. Returns status/data.
8. `a2a()` serializes response.

### 11.4 `action=execute_task`

Entry point:

1. `main.py::A2ADaemonEngine.a2a(**params)`

Step-by-step:

1. Applies partition defaults.
2. Pops `action`.
3. Validates `task_id`.
4. Imports:
   - `handlers/a2a_utility.py::execute_a2a_task`
5. Calls:
   - `execute_a2a_task(partition_key=partition_key, task_id=task_id, task_params=params)`
6. Builds response:
   - `status="success"`
   - message indicating task execution initiation
7. Serializes response.

---

## 12. GraphQL Direct Method

### 12.1 `A2ADaemonEngine.a2a_core_graphql(...)`

Entry point:

1. `main.py::A2ADaemonEngine.a2a_core_graphql(**params)`

Step-by-step:

1. Calls `_apply_partition_defaults(params)`.
2. Calls:
   - `Config.a2a_core.a2a_core_graphql(**params)`
3. GraphQL layer routes to:
   - `handlers/schema.py::Query`
   - `handlers/schema.py::Mutations`
4. Query paths call modules under:
   - `queries/`
5. Mutation paths call modules under:
   - `mutations/`
6. Model paths call modules under:
   - `models/`
7. Response returns to caller.

Common GraphQL operations:

| Operation | Schema Module | Data Module |
|---|---|---|
| `a2aAgent` / `a2aAgentList` | `handlers/schema.py` | `queries/a2a_agent.py`, `models/a2a_agent.py` |
| `a2aTask` / `a2aTaskList` | `handlers/schema.py` | `queries/a2a_task.py`, `models/a2a_task.py` |
| `a2aMessage` / `a2aMessageList` | `handlers/schema.py` | `queries/a2a_message.py`, `models/a2a_message.py` |
| `a2aSetting` | `handlers/schema.py` | `queries/a2a_setting.py`, `models/a2a_setting.py` |
| `insertUpdateA2aAgent` / `deleteA2aAgent` | `handlers/schema.py` | `mutations/a2a_agent.py`, `models/a2a_agent.py` |
| `insertUpdateA2aTask` / `deleteA2aTask` | `handlers/schema.py` | `mutations/a2a_task.py`, `models/a2a_task.py` |
| `insertUpdateA2aMessage` / `deleteA2aMessage` | `handlers/schema.py` | `mutations/a2a_message.py`, `models/a2a_message.py` |
| `insertUpdateA2aSetting` | `handlers/schema.py` | `mutations/a2a_setting.py`, `models/a2a_setting.py` |

---

## 13. Experimental gRPC Protocol Methods

Module:

1. `handlers/a2a_grpc.py`

Runtime path:

1. `main.py::A2ADaemonEngine.daemon()`
2. `transport == "grpc"`
3. `A2AGRPCServer.start()`
4. `grpc.aio.server(...)`
5. `_create_handlers()`
6. `grpc.method_handlers_generic_handler("a2a.A2AService", ...)`

### 13.1 gRPC `SendMessage`

Function:

1. `A2AGRPCServicer.SendMessage(request, context)`

Step-by-step:

1. Receives JSON-decoded dict request.
2. Creates `asyncio.Queue`.
3. Wraps request with `_RequestContextAdapter`.
4. Calls:
   - `await self.agent_executor.execute(request_context=adapter, event_queue=queue)`
5. Drains queue into an `events` list.
6. Returns:
   - `task_id`
   - `status`
   - `result`
   - `events`

### 13.2 gRPC `SendMessageStream`

Function:

1. `A2AGRPCServicer.SendMessageStream(request, context)`

Step-by-step:

1. Reads `task_id`.
2. Creates stream queue.
3. Registers queue in `_active_streams`.
4. Yields an accepted event.
5. Starts executor task:
   - `self.agent_executor.execute(_RequestContextAdapter(request), queue)`
6. While executor is active or queue has data:
   - reads events from queue
   - converts event with `_event_to_dict(...)`
   - yields JSON stream update
7. On timeout, yields keepalive.
8. On completion/disconnect, unregisters queue.

### 13.3 gRPC `GetTask`

Function:

1. `A2AGRPCServicer.GetTask(request, context)`

Step-by-step:

1. Reads `task_id = request["id"]`.
2. Calls:
   - `await self.task_store.get(task_id)`
3. If missing:
   - sets gRPC `NOT_FOUND`
4. If found:
   - calls `_task_to_proto(task)`
5. Returns JSON-compatible task dict.

### 13.4 gRPC `ListTasks`

Function:

1. `A2AGRPCServicer.ListTasks(request, context)`

Step-by-step:

1. Reads:
   - `partition_key`
   - `limit`
   - `cursor`
2. Calls:
   - `await self.task_store.list_tasks(partition_key=..., limit=..., cursor=...)`
3. Converts each task with `_task_to_proto(...)`.
4. Returns:
   - `tasks`
   - `next_cursor`

### 13.5 gRPC `CancelTask`

Function:

1. `A2AGRPCServicer.CancelTask(request, context)`

Step-by-step:

1. Reads `task_id = request["id"]`.
2. Calls:
   - `await self.agent_executor.cancel(task_id)`
3. Returns:
   - `task_id`
   - `status="CANCELED"`

### 13.6 gRPC `SubscribeToTask`

Function:

1. `A2AGRPCServicer.SubscribeToTask(request, context)`

Step-by-step:

1. Reads `task_id`.
2. Creates queue.
3. Registers queue in `_active_streams[task_id]`.
4. While context is not cancelled:
   - waits for queue event
   - yields converted event
   - yields keepalive on timeout
5. Unregisters queue on disconnect.

### 13.7 gRPC `GetAgentCard`

Function:

1. `A2AGRPCServicer.GetAgentCard(request, context)`

Step-by-step:

1. Returns static JSON-compatible Agent Card-like metadata.
2. Current response includes:
   - name
   - description
   - URL
   - version
   - capabilities
   - skills

---

## 14. Legacy JSON-RPC Compatibility Shim

Module:

1. `handlers/a2a_jsonrpc.py`

Status:

- Deprecated.
- Import emits a deprecation warning.
- Prefer SDK `DefaultRequestHandler`.

Function:

1. `process_a2a_jsonrpc_message(partition_key, message)`

Step-by-step:

1. Reads JSON-RPC `method`.
2. Supports legacy/simple methods such as:
   - `ping`
   - `agent.getCard`
   - `agent.listSkills`
   - placeholder `agent.executeSkill`
3. Reads data from `Config.a2a_server.agent_card` where needed.
4. Returns JSON-RPC response or error.

Use this module only for compatibility, not for release certification of primary A2A protocol traffic.

---

## 15. Detailed Function-Call Chains By Path

This section is the shortest route from an incoming protocol call to the project functions that process it. It is intentionally more explicit than the earlier walkthroughs so debugging can follow the same order as the runtime stack.

### 15.1 HTTP SDK Startup Path

```text
main.py::main()
  -> A2ADaemonEngine.__init__(endpoint_config, serverless=False)
    -> Config.initialize(endpoint_config=...)
    -> Config.a2a_server / Config.a2a_core setup
  -> A2ADaemonEngine.daemon(port=...)
    -> Config.a2a_server.app.build()
      -> A2AProtocolServer._initialize_a2a_server()
        -> A2AProtocolServer._create_agent_card()
        -> A2AProtocolServer._initialize_executor()
        -> A2AProtocolServer._initialize_task_store()
        -> DefaultRequestHandler(...)
        -> A2AStarletteApplication(...)
    -> uvicorn.Config(...)
    -> uvicorn.Server.serve()
```

Runtime effect:

1. `main.py` creates the engine and initializes global configuration.
2. The daemon delegates Starlette app creation to the configured A2A protocol server.
3. The SDK app owns the protocol routes and calls `DefaultRequestHandler` methods for JSON-RPC requests.

### 15.2 gRPC Startup Path

```text
main.py::main()
  -> A2ADaemonEngine.__init__(...)
    -> Config.initialize(...)
  -> A2ADaemonEngine.daemon(protocol="grpc", port=...)
    -> A2AGRPCServer(host, port, agent_executor, task_store)
    -> A2AGRPCServer.start()
      -> A2AGRPCServer._create_handlers()
      -> grpc.method_handlers_generic_handler("a2a.v1.A2AService", handlers)
      -> server.add_generic_rpc_handlers(...)
      -> server.start()
      -> server.wait_for_termination()
```

Runtime effect:

1. The daemon builds an experimental gRPC server instead of a Starlette app.
2. `_create_handlers()` maps method names such as `SendMessage`, `GetTask`, and `SubscribeToTask` to `A2AGRPCServicer` coroutine methods.
3. Each gRPC method adapts request payloads into the same executor/task-store concepts used by the HTTP path.

### 15.3 Agent Card Discovery Path

```text
HTTP GET /.well-known/agent-card.json
  -> SDK Starlette route generated by A2AStarletteApplication
    -> A2AProtocolServer._create_agent_card()
      -> AgentCard(...)
        -> AgentSkill(...)
        -> AgentCapabilities(...)
```

Extended card route:

```text
HTTP GET /...extended-card...
  -> create_extended_card_route(...).get_extended_card_endpoint(request)
    -> ExtendedAgentCardManager.get_extended_card(request, include_sensitive)
      -> ExtendedAgentCardManager._authenticate_request(request)
      -> ExtendedAgentCardManager._extract_capabilities()
      -> ExtendedAgentCardManager._extract_skills()
      -> ExtendedAgentCardManager._extract_provider()
      -> ExtendedAgentCardManager._build_extensions()
      -> ExtendedAgentCardManager._update_card_metadata()
      -> ExtendedAgentCardManager.get_card_headers(card)
      -> ExtendedAgentCardManager.check_not_modified(request, card)
```

Runtime effect:

1. Standard discovery returns the public SDK `AgentCard`.
2. Extended discovery enriches that base metadata with deployment, support, authentication, rate-limit, extension, and cache information.
3. `_authenticate_request()` decides whether sensitive extended fields can be included.

### 15.4 JSON-RPC `message/send` Path

Primary SDK HTTP path:

```text
HTTP POST /
  -> A2AStarletteApplication JSON-RPC route
    -> DefaultRequestHandler.on_message_send(request, context)
      -> A2ADaemonExecutor.execute(context, event_queue)
        -> RequestContext.get_user_input()
        -> RequestContext.get("partition_key")
        -> RequestContext.get("operation", "task_execution")
        -> operation dispatch
          -> A2ADaemonExecutor._handle_task_execution(...)
            -> a2a_utility.execute_a2a_task(...)
            -> a2a_utility.get_a2a_task(...)
            -> a2a_utility.update_a2a_task(...) or insert_a2a_task(...)
          -> A2ADaemonExecutor._handle_message_routing(...)
            -> a2a_handlers.handle_message_routing(...)
          -> A2ADaemonExecutor._handle_agent_registration(...)
            -> a2a_handlers.handle_agent_handshake(...)
        -> event_queue.enqueue_event(...)
```

Compatibility HTTP path:

```text
HTTP POST /rest/a2a-jsonrpc
  -> a2a_app.a2a_jsonrpc_endpoint(request)
    -> parse JSON-RPC envelope
    -> map method "message/send"
    -> DefaultRequestHandler.on_message_send(...)
    -> same executor path as SDK HTTP
```

Direct/serverless path:

```text
A2ADaemonEngine.a2a(event, context)
  -> A2ADaemonEngine._apply_partition_defaults(...)
  -> method == "message/send"
  -> request_handler.on_message_send(...)
  -> A2ADaemonExecutor.execute(...)
  -> same operation handlers as SDK HTTP
```

Runtime effect:

1. All `message/send` routes converge on `DefaultRequestHandler.on_message_send`.
2. The SDK request handler invokes `A2ADaemonExecutor.execute`.
3. The executor classifies the payload into task execution, message routing, agent registration, or fallback response handling.
4. Persistence and integration work then happens through `a2a_utility.py`, `a2a_handlers.py`, and `Config.a2a_core`.

### 15.5 JSON-RPC `tasks/get` Path

```text
HTTP POST / or /rest/a2a-jsonrpc or A2ADaemonEngine.a2a(...)
  -> DefaultRequestHandler.on_get_task(request, context)
    -> DynamoDBA2ATaskStore.get(task_id)
      -> a2a_utility.get_a2a_task(task_id=..., partition_key=...)
        -> Config.a2a_core.a2a_core_graphql(...)
          -> GraphQL task query resolver
          -> DynamoDB-backed model lookup
      -> DynamoDBA2ATaskStore._dict_to_task(task_dict)
        -> DynamoDBA2ATaskStore._map_status_to_taskstate(status)
        -> Task(...)
        -> Message(...) / Artifact(...) conversion
    -> JSON-RPC result serialization
```

Runtime effect:

1. `on_get_task` asks the task store for the requested task.
2. `DynamoDBA2ATaskStore.get` reads the persisted task through the GraphQL core.
3. `_dict_to_task` maps persisted dictionaries back to SDK `Task`, `Message`, and `Artifact` objects.
4. Missing records become protocol-level not-found responses through the SDK request handler.

### 15.6 JSON-RPC `tasks/cancel` Path

```text
HTTP POST / or /rest/a2a-jsonrpc or A2ADaemonEngine.a2a(...)
  -> DefaultRequestHandler.on_cancel_task(request, context)
    -> task_id extraction by SDK request handler
    -> A2ADaemonExecutor.cancel(task_id)
      -> DynamoDBA2ATaskStore.get(task_id)
      -> terminal-state check
      -> update task status to canceled
      -> DynamoDBA2ATaskStore.save(task)
        -> DynamoDBA2ATaskStore._task_to_dict(task)
        -> a2a_utility.get_a2a_task(...)
        -> a2a_utility.update_a2a_task(...) or insert_a2a_task(...)
```

Runtime effect:

1. Cancel requests go through the executor because cancellation is task lifecycle behavior, not a plain read.
2. The task is loaded first so the executor can avoid invalid transitions from terminal states.
3. The updated task is saved through the same persistence conversion path used by normal task updates.

### 15.7 JSON-RPC `tasks/list` Path

```text
HTTP POST / or compatible route
  -> task list request integration point
    -> DynamoDBA2ATaskStore.list_tasks(partition_key, limit, cursor)
      -> Config.a2a_core.a2a_core_graphql(...)
        -> GraphQL list query resolver
        -> DynamoDB-backed model scan/query
      -> DynamoDBA2ATaskStore._dict_to_task(...) for each item
      -> next_cursor extraction
    -> JSON-RPC result serialization
```

Runtime effect:

1. The task store owns pagination and conversion to SDK task objects.
2. The GraphQL/core layer owns backend access.
3. The protocol integration point should return both converted tasks and the next cursor when present.

### 15.8 Streaming `message/stream` And SSE Subscribe Path

Stream send path:

```text
HTTP POST / with method "message/stream"
  -> SDK streaming request route
    -> DefaultRequestHandler.on_message_send_stream(...)
      -> A2ADaemonExecutor.execute(context, event_queue)
      -> event_queue events emitted as stream items
```

SSE subscribe path:

```text
HTTP GET /tasks/{task_id}/stream
  -> a2a_sse.create_sse_endpoints(...).subscribe_to_task(task_id, request)
    -> StreamingTaskManager.create_sse_response(task_id, request)
      -> event_generator()
        -> SSEEventQueue.subscribe(task_id)
          -> replay buffered events from _event_buffers[task_id]
          -> register subscriber queue in _subscriptions[task_id]
        -> queue.get()
        -> SSEEvent.to_sse_format()
        -> StreamingResponse(...)
```

SSE event emission paths:

```text
StreamingTaskManager.emit_task_status(task_id, status, message)
  -> SSEEventQueue.put(task_id, SSEEvent(...))
  -> subscriber queues
  -> SSEEvent.to_sse_format()

StreamingTaskManager.emit_task_artifact(task_id, artifact)
  -> SSEEventQueue.put(task_id, SSEEvent(...))
  -> subscriber queues

StreamingTaskManager.emit_input_required(task_id, prompt)
  -> SSEEventQueue.put(task_id, SSEEvent(...))

StreamingTaskManager.emit_auth_required(task_id, auth_info)
  -> SSEEventQueue.put(task_id, SSEEvent(...))
```

Runtime effect:

1. `message/stream` is request/response streaming through the SDK request handler.
2. `/tasks/{task_id}/stream` is a subscriber path that turns queued task events into SSE frames.
3. `SSEEventQueue` buffers recent events, registers active subscribers, and fans out new events.

### 15.9 Push Notification Config Paths

Set/create config:

```text
JSON-RPC push config method or route integration
  -> PushNotificationManager.create_push_config(task_id, config, ...)
    -> WebhookUrlValidator.validate(url)
      -> DNS/IP/private-network checks
      -> optional challenge request
    -> PushNotificationConfig(...)
    -> store config in backend/in-memory integration
```

Get/list/delete config:

```text
push config get
  -> PushNotificationManager.get_push_config(task_id, config_id)

push config list
  -> PushNotificationManager.list_push_configs(task_id, limit, cursor)

push config delete
  -> PushNotificationManager.delete_push_config(task_id, config_id)
```

Notification delivery:

```text
task status/artifact change
  -> PushNotificationManager.send_push_notification(task_id, event)
    -> lookup matching push configs
    -> sign or decorate outgoing webhook request
    -> HTTP POST to configured webhook URL
    -> retry/error handling according to config
```

Runtime effect:

1. Create/set validates webhook destinations before they can receive task events.
2. Get/list/delete are config management paths and do not execute task work.
3. Delivery is event-driven and should be invoked by task lifecycle code when a subscribed task changes.

### 15.10 REST / Lambda Compatibility Action Paths

Agent registration:

```text
A2ADaemonEngine.a2a(event, context)
  -> action == "register_agent"
  -> a2a_handlers.handle_agent_handshake(partition_key, payload)
    -> A2AProtocolServer.handle_handshake(...)
    -> GraphQL agent registration mutation through Config.a2a_core
```

Task assignment:

```text
A2ADaemonEngine.a2a(event, context)
  -> action == "assign_task"
  -> a2a_handlers.handle_task_assignment(partition_key, payload)
    -> validate task payload
    -> Config.a2a_core.a2a_core_graphql(...)
    -> a2a_utility.insert_a2a_task(...) or server compatibility method
```

Message routing:

```text
A2ADaemonEngine.a2a(event, context)
  -> action == "route_message"
  -> a2a_handlers.handle_message_routing(partition_key, payload)
    -> validate sender/recipient/message fields
    -> Config.a2a_core.a2a_core_graphql(...)
    -> message mutation or server compatibility method
```

Task execution:

```text
A2ADaemonEngine.a2a(event, context)
  -> action == "execute_task"
  -> a2a_utility.execute_a2a_task(partition_key, task_id, ...)
    -> a2a_utility.get_a2a_task(...)
    -> execute/update task state
    -> a2a_utility.update_a2a_task(...)
```

Runtime effect:

1. These paths exist for direct invocation and serverless compatibility.
2. They bypass HTTP routing but still use the same handler and persistence modules.
3. `_apply_partition_defaults` is important because direct events may omit HTTP headers that normally carry tenant/partition data.

### 15.11 GraphQL Direct Method Path

```text
A2ADaemonEngine.a2a_core_graphql(query, variables, operation_name)
  -> Config.a2a_core.a2a_core_graphql(...)
    -> GraphQL schema execution
      -> Query resolver or Mutation.mutate(...)
      -> DynamoDB-backed model methods
      -> result dict / errors
  -> A2ADaemonEngine returns GraphQL response envelope
```

Runtime effect:

1. This path is not an A2A protocol route by itself.
2. A2A handlers use it as the persistence and model-operation boundary.
3. Debug persistence failures here before assuming the JSON-RPC or SSE layer is wrong.

### 15.12 gRPC Method Paths

Send message:

```text
gRPC A2AService/SendMessage
  -> A2AGRPCServicer.SendMessage(request, context)
    -> _RequestContextAdapter(request)
    -> A2ADaemonExecutor.execute(adapter, event_queue)
    -> _event_to_proto(...) / response dict
```

Stream message:

```text
gRPC A2AService/SendMessageStream
  -> A2AGRPCServicer.SendMessageStream(request, context)
    -> _RequestContextAdapter(request)
    -> A2ADaemonExecutor.execute(adapter, event_queue)
    -> async event yield loop
    -> _event_to_proto(event)
```

Task read/list/cancel:

```text
gRPC A2AService/GetTask
  -> A2AGRPCServicer.GetTask(request, context)
    -> DynamoDBA2ATaskStore.get(task_id)
    -> _task_to_proto(task)

gRPC A2AService/ListTasks
  -> A2AGRPCServicer.ListTasks(request, context)
    -> DynamoDBA2ATaskStore.list_tasks(...)
    -> _task_to_proto(task) for each task

gRPC A2AService/CancelTask
  -> A2AGRPCServicer.CancelTask(request, context)
    -> A2ADaemonExecutor.cancel(task_id)
```

Subscribe:

```text
gRPC A2AService/SubscribeToTask
  -> A2AGRPCServicer.SubscribeToTask(request, context)
    -> register queue in _active_streams[task_id]
    -> wait for queued events
    -> _event_to_proto(event)
    -> yield keepalive events on timeout
    -> unregister queue when context ends
```

Runtime effect:

1. gRPC is experimental but mirrors the same executor and task-store boundaries.
2. Request adapters bridge gRPC-shaped input to the executor's expected context interface.
3. Task conversion helpers produce JSON-compatible protocol dictionaries rather than generated protobuf classes in the current implementation.

### 15.13 Legacy JSON-RPC Shim Path

```text
handlers/a2a_jsonrpc.py::process_a2a_jsonrpc_message(partition_key, message)
  -> method dispatch
    -> ping
    -> agent.getCard
    -> agent.listSkills
    -> agent.executeSkill placeholder
  -> Config.a2a_server.agent_card for card/skills
  -> JSON-RPC response or JSON-RPC error
```

Runtime effect:

1. This path is compatibility-only.
2. It does not represent the certified primary A2A flow.
3. New protocol behavior should be traced through SDK `DefaultRequestHandler` and `A2ADaemonExecutor` instead.

---

## 16. Quick Method-To-Function Index

| Method / Action | First Project Function | Main Downstream Functions |
|---|---|---|
| CLI startup | `main.py::main()` | `A2ADaemonEngine.__init__`, `Config.initialize`, `A2ADaemonEngine.daemon` |
| HTTP runtime | `A2ADaemonEngine.daemon` | `Config.a2a_server.app.build`, `uvicorn.Server.serve` |
| Agent Card | SDK route | `A2AProtocolServer._create_agent_card` |
| `message/send` | SDK app or `a2a_app.a2a_jsonrpc_endpoint` or `A2ADaemonEngine.a2a` | `DefaultRequestHandler.on_message_send`, `A2ADaemonExecutor.execute`, internal operation handler |
| `tasks/get` | SDK app or `a2a_app.a2a_jsonrpc_endpoint` or `A2ADaemonEngine.a2a` | `DefaultRequestHandler.on_get_task`, `DynamoDBA2ATaskStore.get`, `a2a_utility.get_a2a_task` |
| `tasks/cancel` | SDK app or `a2a_app.a2a_jsonrpc_endpoint` or `A2ADaemonEngine.a2a` | `DefaultRequestHandler.on_cancel_task`, `A2ADaemonExecutor.cancel`, `DynamoDBA2ATaskStore.save` |
| SSE subscribe | `a2a_sse.create_sse_endpoints.<subscribe_to_task>` | `StreamingTaskManager.create_sse_response`, `SSEEventQueue.subscribe` |
| SSE emit status | `StreamingTaskManager.emit_task_status` | `SSEEventQueue.put`, `SSEEvent.to_sse_format` |
| SSE emit artifact | `StreamingTaskManager.emit_task_artifact` | `SSEEventQueue.put`, `SSEEvent.to_sse_format` |
| Push config create | route/RPC integration point | `PushNotificationManager.create_push_config`, `WebhookUrlValidator.validate` |
| Push config get/list/delete | route/RPC integration point | `PushNotificationManager.get_push_config`, `list_push_configs`, `delete_push_config` |
| Extended card | route integration point | `ExtendedAgentCardManager.get_extended_card` |
| `action=register_agent` | `A2ADaemonEngine.a2a` | `handle_agent_handshake`, `A2AProtocolServer.handle_handshake`, GraphQL agent mutation |
| `action=assign_task` | `A2ADaemonEngine.a2a` | `handle_task_assignment`, GraphQL task mutation or server compatibility method |
| `action=route_message` | `A2ADaemonEngine.a2a` | `handle_message_routing`, GraphQL message mutation or server compatibility method |
| `action=execute_task` | `A2ADaemonEngine.a2a` | `a2a_utility.execute_a2a_task` |
| gRPC `SendMessage` | `A2AGRPCServicer.SendMessage` | `_RequestContextAdapter`, `A2ADaemonExecutor.execute` |
| gRPC streaming | `A2AGRPCServicer.SendMessageStream` / `SubscribeToTask` | `_active_streams`, executor/event queues |
| gRPC task methods | `A2AGRPCServicer.GetTask/ListTasks/CancelTask` | task store, executor cancel |

---

## 17. Practical Debugging Checklist

When tracing a protocol issue:

1. Identify entry path:
   - SDK `POST /`
   - `/rest/a2a-jsonrpc`
   - direct `A2ADaemonEngine.a2a()`
   - SSE `/tasks/{task_id}/stream`
   - gRPC
2. Confirm `Config.initialize()` completed and `Config.a2a_server` exists.
3. Confirm `partition_key`:
   - direct invocation: `_apply_partition_defaults`
   - HTTP compatibility route: request headers
   - SDK route: `ServerCallContext`
4. For `message/send`, inspect `A2ADaemonExecutor.execute`.
5. For task state, inspect `DynamoDBA2ATaskStore.get/save/list_tasks`.
6. For SSE, inspect `SSEEventQueue._event_buffers` and `_subscriptions`.
7. For REST/serverless compatibility, inspect `a2a_handlers.py`.
8. For persistence failures, inspect GraphQL result errors before DynamoDB models.
9. For release validation, run live A2A client/TCK tests against a running daemon with the intended persistence backend.
