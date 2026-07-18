# A2A Protocol Compliance — Audit and Implementation Plan

> Audit of `a2a_daemon_engine` against the published A2A specification, scoped to
> how the module is actually reachable **through silvaengine_gateway**, plus the
> plan to close the gaps. Companion to [`A2A_DEVELOPMENT_PLAN.md`](A2A_DEVELOPMENT_PLAN.md)
> (Phases 1–10); this is **Phase 11**.

**Status:** Implemented in code; pending live gateway verification
**Date:** 2026-07-16

---

## 1. Why this document exists

Phases 1–9 built a spec-complete A2A daemon **as a standalone server**. Phase 10
registered the module into silvaengine_gateway. The gateway does not mount the
SDK's Starlette app — it only invokes `dispatch_*` functions named in
`routes.yaml`. Everything the SDK app served implicitly (the Agent Card, the
full JSON-RPC method table) silently stopped being reachable, because only the
dispatch functions that were explicitly ported still exist.

The headline symptom: **the Agent Card returns 404**. Agent Card discovery is
how every other A2A agent learns this agent's skills, capabilities, protocol
bindings and auth requirements. Without it the daemon is not discoverable, and
a conformant client cannot bootstrap a conversation.

## 2. How this audit was performed

Three independent sources, cross-checked — no assumptions from memory:

1. **The published spec**, both versions that matter here (see §8 References).
   The installed SDK targets v1.0 while enabling v0.3 compatibility
   (`create_jsonrpc_routes(..., enable_v0_3_compat=True)` in `a2a_server.py`),
   and the two revisions renamed several methods.
2. **The installed SDK**, introspected directly — the authoritative list of what
   this codebase *can* expose, extracted from the `a2a` package source rather
   than from documentation.
3. **The running gateway**, probed live for each candidate path.

## 3. Current state

### 3.1 Agent Card — missing (the priority gap)

| Probe (live gateway) | Result |
| --- | --- |
| `GET /.well-known/agent-card.json` | **404** |
| `GET /{endpoint_id}/.well-known/agent-card.json` | **404** |
| `GET /{endpoint_id}/agent_card` | **404** |

The card is *built* — `a2a_server.py:259` constructs `self.agent_card` with
`supported_interfaces`, `capabilities`, `skills`, and `provider`, and
`a2a_server.py:329` registers `create_agent_card_routes(self.agent_card)`. But
those routes live on `self.app`, the SDK's own Starlette instance, which was
only ever served by the standalone daemon entrypoint (`tests/start_daemon.py`,
now deleted). In gateway mode nothing mounts it.

`a2a_extended_card.py` implements a full `ExtendedAgentCardManager`
(`get_extended_agent_card`, auth gating) that is likewise unreachable.

### 3.2 JSON-RPC method coverage

The installed SDK dispatches **12** methods. `A2ADaemonEngine.a2a()`
(`main.py:131-170`) routes **4**; everything else falls through to
`-32601 Method not found`.

| Spec method (as dispatched by the installed SDK) | SDK handler | Gateway status |
| --- | --- | --- |
| `message/send` | `on_message_send` | ✅ routed |
| `message/stream` | `on_message_send_stream` | ✅ routed (+ `message/sendStream` alias) |
| `tasks/get` | `on_get_task` | ✅ routed |
| `tasks/cancel` | `on_cancel_task` | ✅ routed |
| `tasks/list` | `on_list_tasks` | ❌ **not routed** |
| `tasks/resubscribe` | `on_subscribe_to_task` | ❌ **not routed** |
| `tasks/pushNotificationConfig/set` | *(v0.3 alias of create)* | ❌ **not routed** |
| `tasks/pushNotificationConfig/create` | `on_create_task_push_notification_config` | ❌ **not routed** |
| `tasks/pushNotificationConfig/get` | `on_get_task_push_notification_config` | ❌ **not routed** |
| `tasks/pushNotificationConfig/list` | `on_list_task_push_notification_configs` | ❌ **not routed** |
| `tasks/pushNotificationConfig/delete` | `on_delete_task_push_notification_config` | ❌ **not routed** |
| `agent/getAuthenticatedExtendedCard` | `on_get_extended_agent_card` | ❌ **not routed** |
| **Agent Card** (`GET /.well-known/agent-card.json`) | `create_agent_card_routes` | ❌ **404** |

### 3.3 Latent wiring defects (routing alone will not fix these)

Discovered by reading `DefaultRequestHandler.__init__` against what
`a2a_server.py:315` actually passes:

```python
DefaultRequestHandler(
    agent_executor=..., task_store=..., agent_card=...,   # only these three
)
# available but never supplied:
#   push_config_store, push_sender, extended_agent_card, extended_card_modifier
```

- **`push_config_store` is never supplied.** All five
  `tasks/pushNotificationConfig/*` methods would fail *even once routed*. The
  module has its own `a2a_pushconfig.py` (`PushNotificationConfig`,
  `WebhookUrlValidator` with the Phase 7 anti-SSRF allowlist); the SDK also
  ships `InMemoryPushNotificationConfigStore` / `DatabasePushNotificationConfigStore`.
  These are two parallel implementations that must be reconciled (§6.1).
- **`extended_agent_card` / `extended_card_modifier` are never supplied.**
  `agent/getAuthenticatedExtendedCard` would return the base card, bypassing
  `ExtendedAgentCardManager` entirely.

### 3.4 Card content is wrong for gateway deployment

```python
# a2a_server.py:246
server_url = self.settings.get(
    "a2a_server_url", f"http://localhost:{self.settings.get('port', 8001)}/"
)
...
supported_interfaces=[AgentInterface(url=server_url, protocol_binding="JSONRPC", ...)]
```

The card advertises `http://localhost:8001/` by default. Behind the gateway the
agent actually lives at `{gateway_url}/{endpoint_id}`. **Serving a card that
advertises the wrong URL is worse than serving no card**: discovery would appear
to succeed and every subsequent client call would go to a dead address. The URL
must be derived per-request (endpoint_id + gateway host), not from a static
setting — one deployment serves many tenants.

## 4. Gap summary

| # | Gap | Severity | Notes |
| --- | --- | --- | --- |
| G1 | Agent Card returns 404 | **Critical** | Blocks all A2A discovery |
| G2 | Card advertises `localhost`, not the gateway endpoint | **Critical** | Must ship with G1 |
| G3 | `agent/getAuthenticatedExtendedCard` unrouted + unwired | High | Manager exists, unused |
| G4 | `tasks/list` unrouted | Medium | `DynamoDBA2ATaskStore.list()` support landed in Phase 10 |
| G5 | `tasks/resubscribe` unrouted | Medium | SSE reconnection path |
| G6 | 5 × `tasks/pushNotificationConfig/*` unrouted **and** no `push_config_store` | Medium | Two impls to reconcile |
| G7 | v1.0 method renames unhandled | Low | Forward-compat; see §6.3 |

## 5. Phase 11 — Implementation plan

Ordered so each step is independently shippable and verifiable.


### 5.1 Implementation Result (2026-07-16)

Implemented in the daemon code:

- Gateway-facing Agent Card dispatch: `dispatch_agent_card()` and
  `A2ADaemonEngine.agent_card()` return the SDK `AgentCard` with
  `supportedInterfaces[].url` rewritten to `{gateway_base_url}/{endpoint_id}/a2a`.
- `deploy()` now advertises the public GET route metadata for
  `/{endpoint_id}/.well-known/agent-card.json`.
- Serverless/gateway JSON-RPC routing now covers `tasks/list`,
  `tasks/resubscribe`, `tasks/subscribe`, push-notification config create/get/list/delete,
  and authenticated extended-card methods.
- v0.3 and v1-style aliases are accepted where the installed SDK can support them by
  routing to the same request-handler method.
- `DefaultRequestHandler` now receives an SDK-compatible push-config store and an
  SDK `extended_agent_card`.
- Push config writes go through the existing webhook URL validator before being stored.
- Focused Phase 11 tests cover Agent Card URL rewriting, the expanded JSON-RPC method
  table, v1 aliases, and webhook rejection.

Still pending outside this code pass:

- Live gateway verification against an actual `silvaengine_gateway` route manifest.
- Replacing the current SDK in-memory push-config store with a durable repository-backed
  store if push notification config must survive process restarts.
### 11.1 Agent Card (G1 + G2) — do first

| Sub-task | Description |
| --- | --- |
| 11.1.1 | `Config` / `a2a_server.py`: make the card URL injectable per request rather than read once from `a2a_server_url`. Add a helper that builds `{gateway_url}/{endpoint_id}` from the dispatch params. |
| 11.1.2 | `main.py`: add `agent_card(**params)` + `dispatch_agent_card(**params)` returning the serialized card, with `supported_interfaces[].url` rewritten to the caller's gateway endpoint. Reuse `sdk_response_to_dict` (`a2a_jsonrpc_bridge.py`) for protobuf→JSON. |
| 11.1.3 | `routes.yaml`: register `GET /{endpoint_id}/.well-known/agent-card.json`, `handler_type: rest`, **`auth: false`** (see §6.2). |
| 11.1.4 | Verify `capabilities` reflects reality in gateway mode: `streaming=true` is correct (SSE + `message/stream`), but `pushNotifications` must not advertise true until 11.4 lands. Advertising an unimplemented capability is a conformance failure. |
| 11.1.5 | Live test: `GET /{ep}/.well-known/agent-card.json` → 200, unauthenticated, `url` equals the gateway endpoint, JSON validates against the AgentCard schema. |

### 11.2 Extended Agent Card (G3)

| Sub-task | Description |
| --- | --- |
| 11.2.1 | Pass `extended_card_modifier` (or `extended_agent_card`) into `DefaultRequestHandler`, bridged to `ExtendedAgentCardManager.get_extended_agent_card()`. |
| 11.2.2 | Route `agent/getAuthenticatedExtendedCard` in `a2a()` → `request_handler.on_get_extended_agent_card`. Authenticated (gateway default). |
| 11.2.3 | Set `supportsAuthenticatedExtendedCard: true` on the base card **only** once 11.2.1–2 land. |

### 11.3 Task query methods (G4 + G5)

| Sub-task | Description |
| --- | --- |
| 11.3.1 | Route `tasks/list` → `on_list_tasks`. `DynamoDBA2ATaskStore.list()` already exists (Phase 10), and `ListTasksRequest.context_id` makes this the natural "list this conversation's tasks" query. |
| 11.3.2 | Route `tasks/resubscribe` → `on_subscribe_to_task`. Decide how the returned event stream maps onto the gateway's request/response dispatch — likely: drive the subscription and fan out to the existing `/{ep}/a2a_sse` channel, mirroring how `message/stream` already works. |
| 11.3.3 | Confirm `tasks/list` tenant scoping: the store is constructed per `partition_key`; ensure a caller cannot list another partition's tasks. |

### 11.4 Push notification config (G6)

| Sub-task | Description |
| --- | --- |
| 11.4.1 | **Decide the store** (§6.1): adopt the SDK's `PushNotificationConfigStore` interface backed by our tables, and keep `a2a_pushconfig.WebhookUrlValidator` as the SSRF gate. Do not maintain two config models. |
| 11.4.2 | Supply `push_config_store` (and `push_sender`) to `DefaultRequestHandler`. |
| 11.4.3 | Route all five `tasks/pushNotificationConfig/*` methods, including the v0.3 `set` ⇄ `create` alias. |
| 11.4.4 | Preserve the Phase 7 webhook allowlist on the create/set path — this is the anti-SSRF control; routing must not bypass it. |
| 11.4.5 | Flip `capabilities.pushNotifications` to true on the card (pairs with 11.1.4). |

### 11.5 Conformance verification

| Sub-task | Description |
| --- | --- |
| 11.5.1 | `tests/test_a2a_protocol_conformance.py` — one live test per method through the gateway asserting no `-32601`, driven by a table so a missing method fails loudly. |
| 11.5.2 | Agent Card schema validation + assert the advertised `url` is reachable and answers `message/send` (catches G2 regressions). |
| 11.5.3 | Assert every `capabilities.*: true` has a correspondingly routed method (catches over-advertising). |
| 11.5.4 | Re-run the existing live suites: `test_core_engine_gateway_live.py`, `test_core_engine_sse_live.py`, two-turn memory, concurrent-session isolation. |

## 6. Design decisions and open questions

### 6.1 Two push-notification implementations — reconcile before routing

The module has `a2a_pushconfig.py` (Phase 7, with the anti-SSRF webhook
allowlist); the SDK ships `PushNotificationConfigStore` implementations. The SDK
handlers only understand the SDK interface. **Recommendation:** implement the
SDK's `PushNotificationConfigStore` interface over our persistence, and keep
`WebhookUrlValidator` as a guard invoked on write. Routing the methods before
resolving this yields five endpoints that 500.

### 6.2 Should the base card be public? — needs a decision

The spec places the Agent Card at a well-known path for **pre-authentication**
discovery; `securitySchemes` on the card is what tells a client how to
authenticate for real calls. Our gateway defaults every route to `auth: true`.

- **Public (`auth: false`)** — spec-conformant, and the only way third-party
  agents can discover this one. Cost: skills/description/provider are readable
  unauthenticated by anyone who can reach the gateway.
- **Authenticated** — non-conformant discovery, but leaks nothing.

**Recommendation:** public, *if* the gateway is not internet-facing. This is a
deployment-security call, not a code call — it needs an explicit answer before
11.1.3.

### 6.3 Spec version drift — v0.3 names vs v1.0 renames

The installed SDK dispatches v0.3-style names. The current published spec
renames several:

| Installed SDK (v0.3 style) | Current spec (v1.0) |
| --- | --- |
| `tasks/pushNotificationConfig/create` | `tasks/push-notification-config/create` |
| `tasks/resubscribe` | `tasks/subscribe` |
| `agent/getAuthenticatedExtendedCard` | `agent/card/extended` |

**Recommendation:** implement against the installed SDK's names (that is what
this code can actually serve today), and accept both spellings where the cost is
a dict lookup — the same tactic already used for the `message/sendStream` alias.
Do not chase v1.0 names the SDK cannot dispatch.

### 6.4 Out of scope for Phase 11

`a2a_grpc.py` and `a2a_graphql_subscriptions.py` are also unreachable through the
gateway. The spec lists gRPC and HTTP+JSON/REST as additional bindings; JSON-RPC
is the baseline and is what we serve. Completing the JSON-RPC surface first is
the higher-value work — revisit per the existing Phase 9 gRPC promotion question.

## 7. Risks

| Risk | Mitigation |
| --- | --- |
| Card advertises an unreachable URL | 11.1.1 derives it per-request; 11.5.2 asserts the advertised URL actually answers |
| Card over-advertises capabilities | 11.1.4 / 11.4.5 gate each flag on its method being routed; 11.5.3 enforces |
| Routing push config without a store → five 500s | 11.4.1–2 land the store before 11.4.3 routes anything |
| Webhook allowlist bypassed via the new route | 11.4.4 keeps the validator on the write path |
| `tasks/list` leaks cross-tenant tasks | 11.3.3 explicit partition scoping check |

## 8. References

- A2A Protocol Specification (current): <https://a2a-protocol.org/latest/specification/>
- A2A Protocol Specification v0.3.0: <https://a2a-protocol.org/v0.3.0/specification/>
- AgentCard concept: <https://agent2agent.info/docs/concepts/agentcard/>
- Installed SDK: `a2a` package — method table extracted from source;
  well-known path `/.well-known/agent-card.json` served by
  `a2a.server.routes.create_agent_card_routes`.
