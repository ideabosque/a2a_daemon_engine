# A2A Protocol Call Flow

The SDK Starlette app exposes two HTTP JSON-RPC entry points:

- `POST /` — compatibility endpoint accepting the slash-style method names
  (`message/send`, `tasks/get`, `tasks/cancel`).
- `POST /v1` — SDK native dispatcher accepting v1 method names (`SendMessage`,
  `GetTask`, `CancelTask`); v0.3 method aliases remain enabled.

Both paths reach the same `DefaultRequestHandler` and produce identical
responses. The flows below use `POST /` for clarity; substitute `POST /v1` and
the SDK-native method name to drive the same dispatch through the native
endpoint.

Operational routes under `/rest` are not alternate protocol bindings.

## `message/send`

```mermaid
sequenceDiagram
    participant C as Client
    participant SDK as SDK App
    participant RH as DefaultRequestHandler
    participant EX as A2ADaemonExecutor
    participant H as Business Handler
    participant Core as GraphQL/Core

    C->>SDK: POST / method=message/send
    SDK->>RH: SDK request
    RH->>EX: execute(context, event_queue)
    EX->>H: route operation
    H->>Core: persist/query data
    EX-->>RH: events/messages
    RH-->>SDK: SDK response
    SDK-->>C: JSON-RPC response
```

## `tasks/get`

```mermaid
sequenceDiagram
    participant C as Client
    participant SDK as SDK App
    participant RH as DefaultRequestHandler
    participant TS as DynamoDBA2ATaskStore
    participant Core as GraphQL/Core

    C->>SDK: POST / method=tasks/get
    SDK->>RH: SDK request
    RH->>TS: get(task_id)
    TS->>Core: query task
    Core-->>TS: persisted task row
    TS-->>RH: SDK Task
    RH-->>SDK: SDK response
    SDK-->>C: JSON-RPC response
```

## `tasks/cancel`

```mermaid
sequenceDiagram
    participant C as Client
    participant SDK as SDK App
    participant RH as DefaultRequestHandler
    participant EX as A2ADaemonExecutor
    participant TS as DynamoDBA2ATaskStore

    C->>SDK: POST / method=tasks/cancel
    SDK->>RH: SDK request
    RH->>EX: cancel(task_id)
    EX->>TS: get(task_id)
    EX->>TS: save(CANCELED)
    RH-->>SDK: SDK response
    SDK-->>C: JSON-RPC response
```

## Serverless JSON-RPC

`A2ADaemonEngine.a2a(**event)` accepts JSON-RPC 2.0 dictionaries only. It uses
`handlers/a2a_jsonrpc_bridge.py` to build SDK request objects and dispatches to
the same SDK request handler methods used by the HTTP path.

Non-JSON-RPC `action=...` payloads are rejected.
