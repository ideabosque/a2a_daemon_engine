# A2A (Agent-to-Agent) Protocol — Analysis & Suggestions

> **Date:** 2026-05-02 (Updated 2026-05-02 with Phase 6 findings)  
> **Author:** Willow (AI assistant for Bibo W.)  
> **Source:** [google/A2A](https://github.com/google/A2A) — v1.0.0 specification  
> **Engine Version:** a2a_daemon_engine 0.2.0 (Phase 6 in progress)  

---

## 1. Executive Summary

A2A is an open protocol standard (Apache 2.0) led by Google that defines how autonomous AI agents discover, negotiate with, and delegate work to each other over HTTP-based transports. Version 1.0 was released in early 2026 and represents a significant maturation from the earlier 0.x drafts — stronger type safety (protobuf as normative source of truth, SCREAMING_SNAKE_CASE enums), enterprise security (JWS agent-card signing, mTLS, OAuth 2.0 with PKCE), and a richer operation set (ListTasks with cursor pagination, Extended Agent Cards, multi-tenancy in gRPC).

This document reviews the protocol's architecture, identifies strengths and gaps, and provides concrete suggestions for anyone building on top of A2A — whether you're shipping an A2A server, writing a client/orchestrator, or extending the protocol.

---

## 2. Protocol Architecture Overview

### 2.1 Core Model

| Concept | Role |
|---|---|
| **Agent Card** | JSON "business card" served at `/.well-known/agent-card.json`; declares identity, skills, capabilities, auth schemes, extensions |
| **Client Agent** | The caller; sends messages and manages tasks |
| **Remote Agent (Server)** | The callee; processes messages, creates tasks, produces artifacts |
| **Message** | Stateless communication unit (text, data parts) — used for negotiation, trivial replies, or pre-task chatter |
| **Task** | Stateful work unit with lifecycle: `WORKING → INPUT_REQUIRED / AUTH_REQUIRED → COMPLETED / FAILED / CANCELED / REJECTED` |
| **Artifact** | Output from a task (files, structured data); can be streamed in chunks |
| **Context ID** | Groups related tasks & messages into a conversational session |
| **Extension** | Protocol add-on identified by URI; can add data, methods, state-machine transitions |

### 2.2 Transport Bindings

A2A defines **three standard bindings**, all functionally equivalent:

| Binding | Use Case |
|---|---|
| **JSON-RPC over HTTP(S)** | Primary; simplest to implement; SSE for streaming |
| **gRPC** | High-performance, strongly-typed, multi-tenancy native |
| **HTTP+JSON/REST** | Familiar REST semantics; cursor-based pagination |

Custom bindings (WebSocket, MQTT, etc.) are explicitly supported through the `supportedInterfaces` field.

### 2.3 Core Operations (v1.0)

| Operation | Description |
|---|---|
| `SendMessage` | Send a message; returns Task or Message |
| `SendStreamingMessage` | Same with SSE stream |
| `GetTask` | Poll task state |
| `ListTasks` | Cursor-paginated task listing (new in v1.0) |
| `CancelTask` | Request cancellation |
| `SubscribeToTask` | Re-subscribe to SSE stream |
| `CreateTaskPushNotificationConfig` | Register webhook for async updates |
| `GetTaskPushNotificationConfig` | Retrieve push config |
| `ListTaskPushNotificationConfigs` | List push configs |
| `DeleteTaskPushNotificationConfig` | Remove push config |
| `GetExtendedAgentCard` | Authenticated, richer agent card (new in v1.0) |

---

## 3. Key Strengths

### 3.1 Opaque Agent Model
A2A's design philosophy — agents are opaque and don't share internal memory or tools — is its greatest architectural strength. It means:
- No requirement to expose internal LLM context, prompts, or tool schemas
- Natural alignment with client-server security models
- Agents can be swapped, upgraded, or replaced without breaking protocol contracts

### 3.2 Task Lifecycle & State Machine
The well-defined task state machine (with `INPUT_REQUIRED` and `AUTH_REQUIRED` as interrupt states) cleanly handles the reality of multi-turn, human-in-the-loop AI work. Task immutability at terminal states is a sound design choice — it forces clean follow-up patterns via new tasks rather than mutation.

### 3.3 Discovery Layer (Agent Card)
The Agent Card at `/.well-known/agent-card.json` (RFC 8615) plus authenticated Extended Agent Cards provides a pragmatic discovery story:
- Public agents: well-known URI, no auth needed
- Enterprise agents: curated registries with access control
- Private agents: direct configuration

### 3.4 A2A ⟡ MCP Complementarity
The docs explicitly position A2A (agent-to-agent) and MCP (agent-to-tool) as complementary layers. This is the right framing — trying to use MCP for agent delegation would be like using REST for real-time streaming. The "auto repair shop" example in the docs illustrates this well.

### 3.5 Enterprise Readiness
v1.0 adds serious enterprise features:
- JWS + JSON Canonicalization for Agent Card integrity
- OAuth 2.0 with PKCE and Device Code flow
- mTLS support
- OpenTelemetry-compatible distributed tracing
- Cursor-based pagination for scalable task listing
- Data-minimization and GDPR-compliance guidance

### 3.6 Extension System
Extensions (identified by URIs, declared in Agent Cards) provide a clean extensibility path without fragmenting the core:
- Data-only extensions (metadata)
- Profile extensions (narrow allowed values)
- Method extensions (new RPCs)
- State machine extensions (new states/transitions)
- Required vs. optional semantics for strict agents

---

## 4. Gaps & Concerns

### 4.1 No Standard Registry API
The discovery docs describe three strategies (well-known URI, curated registries, direct config) but the spec **does not define a standard registry API**. This means:
- Every vendor will build their own registry
- Cross-organization discovery will remain ad-hoc
- There's no standard way to search agents by capability, trust level, or compliance

**Suggestion:** The A2A project should define a minimal, optional Registry API spec (e.g., `POST /v1/registry/search` with skill/tag/extension filters). This could be a v1.1 extension.

### 4.2 No Built-In Rate Limiting / Backpressure Protocol
The spec mentions rate limiting as an API management concern but provides **no protocol-level mechanism** for an agent to signal "I'm busy, try again later" beyond generic error codes.

**Suggestion:** Define a standard `Retry-After` service parameter or a `429` response convention with A2A-specific semantics (e.g., per-skill rate limits in the Agent Card). An extension could also advertise rate-limit budgets.

### 4.3 Task Refinement Is Under-Specified
While `referenceTaskIds` exists for linking follow-up work to prior tasks, the protocol doesn't define:
- How an agent should resolve ambiguity when multiple referenced tasks produce conflicting context
- Whether artifact versioning expectations exist beyond "use consistent `artifact-name`"

**Suggestion:** Add a non-normative "Refinement Best Practices" section with examples of multi-artifact disambiguation and version tracking patterns.

### 4.4 No Cancellation Propagation
When a client cancels a task, the spec doesn't address whether the server agent should propagate cancellation to downstream agents it delegated to via A2A. This can leave orphaned tasks consuming resources.

**Suggestion:** Define a `CancelledByUpstream` extension or convention where cancellation metadata can flow through the context chain.

### 4.5 Streaming Reconnection Fragility
The `SubscribeToTask` RPC allows reconnection to a stream, but:
- There's no defined event replay buffer — if the client disconnects and reconnects, it may miss events
- No `Last-Event-ID` or sequence numbering is mandated

**Suggestion:** Require servers to maintain a short replay buffer (e.g., last N events) and support `Last-Event-ID` per SSE spec. This is critical for production reliability.

### 4.6 Missing: Agent Health / Liveness
There's no standard health-check or readiness endpoint. Clients have no way to know if a remote agent is responsive before sending a message.

**Suggestion:** Define a `GET /health` convention (or A2A-specific liveness indicator in the Agent Card) so orchestrators can implement circuit-breakers and failover.

### 4.7 Missing: Cost / Quota Visibility
The Agent Card describes skills but not:
- Expected latency profiles
- Cost per invocation
- Quota or daily limits

**Suggestion:** An extension could define a `pricing` or `quota` namespace with fields like `estimatedLatencyP50`, `estimatedLatencyP99`, `costPerInvocation`, `dailyQuota`.

### 4.8 Agent Card Caching vs. Dynamism Tension
The caching guidance says "Agent Cards change infrequently" and suggests `Cache-Control` with `max-age`. But skills, auth requirements, and extension availability could change dynamically. The spec doesn't address cache invalidation signals.

**Suggestion:** Require or strongly recommend that Agent Cards include a `version` field (they do have one) and that clients use `ETag`/`If-None-Match` for conditional fetches. Additionally, consider a `X-A2A-Card-Version` service parameter that servers can return in any response to signal "your cached card is stale."

---

## 5. Comparison with Alternatives

| Dimension | A2A v1.0 | MCP (Model Context Protocol) | ANS (Agent Network Protocol) |
|---|---|---|---|
| **Primary Use** | Agent-to-agent delegation & collaboration | Agent-to-tool/resource integration | Agent swarm coordination |
| **Transport** | HTTP(S), gRPC, REST + SSE | JSON-RPC over stdio / HTTP | P2P (libp2p) + HTTP |
| **Discovery** | Agent Card (well-known URI / registry) | Tool/resource manifest | DHT + capability advertisements |
| **State Model** | Stateful tasks with lifecycle FSM | Stateless tool calls | Ephemeral swarms |
| **Security** | TLS + OAuth 2.0 + mTLS + JWS | Local trust / API keys | DID-based auth |
| **Maturity** | v1.0 (Google + 50+ partners) | v2025 (Anthropic) | Pre-release |
| **Streaming** | SSE + push notifications | Stdio streaming | Gossip protocol |

**Bottom line:** A2A is the most mature open standard for *inter-agent delegation* specifically. MCP is the standard for *tool integration*. They're complementary, not competing.

---

## 6. Implementation Suggestions

### 6.1 For A2A Server Implementers

1. **Start with the JSON-RPC binding.** It's the simplest to implement and has the broadest SDK support. Add gRPC or REST only when you have specific performance or compatibility needs.

2. **Implement both `SendMessage` and `SendStreamingMessage` from day one.** Clients will expect streaming for any non-trivial agent. Declaring `streaming: true` in your Agent Card without implementing it is a bad experience.

3. **Use the Extended Agent Card.** The public card should be minimal (identity, endpoint, capabilities). Authenticated clients should see the full skill set, auth details, and extension support. This keeps your public surface small and secure.

4. **Implement proper `INPUT_REQUIRED` handling.** Most real-world AI agent interactions need multi-turn clarification. Don't just return `COMPLETED` with a "I don't know" message — use `INPUT_REQUIRED` to loop.

5. **Set realistic `Cache-Control` headers on your Agent Card.** Start with `max-age=3600` (1 hour) and use `ETag` based on the card's `version` field.

6. **Add OpenTelemetry headers** (`traceparent`, `tracestate`) to all requests for distributed tracing. This costs almost nothing and pays off enormously in debugging.

7. **Implement the Secure Passport Extension** if your agents handle PII or operate across trust boundaries. It provides a trusted, contextual layer for identity delegation.

### 6.2 For A2A Client / Orchestrator Implementers

1. **Always fetch and cache the Agent Card** before sending messages. Validate it against the JSON schema and check `version` before each session.

2. **Implement exponential backoff with jitter** for retries. The spec allows transient errors; your client should handle `503` and `429` gracefully.

3. **Use `contextId` for multi-turn conversations.** Don't create orphaned tasks — group related work into a context.

4. **Use push notifications** for long-running tasks rather than polling. Register a webhook and use `return_immediately: true` so your client thread isn't blocked.

5. **Handle both `Task` and `Message` responses.** Your client code should branch on the response type, not assume it always gets a Task.

6. **Implement the `referenceTaskIds` pattern** when refining previous work. This helps the remote agent understand which prior artifacts are relevant.

7. **Validate Agent Card signatures** (JWS) when operating in zero-trust environments. Don't trust Agent Cards from unverified sources.

### 6.3 For A2A Extension Authors

1. **Use the `https://a2a-protocol.org/extensions/` namespace** only if you intend to propose a community/official extension. Private extensions should use your own domain URI.

2. **Don't modify core data structures.** Extensions must use `metadata` fields for custom data — adding new top-level fields to Task or Message will break spec compliance.

3. **Mark extensions as `required: true` only when they genuinely affect request processing.** Data-only extensions should never be required.

4. **Document your extension's behavior under all three transport bindings.** An extension that only works with JSON-RPC limits adoption.

---

## 7. Relevance to a2a_daemon_engine

This analysis directly informs the a2a_daemon_engine project:

### 7.1 Core Protocol Alignment
The daemon engine should implement the **JSON-RPC binding** as its primary transport (simplest, best SDK coverage) and expose an Agent Card at `/.well-known/agent-card.json`. The v1.0 operation set (`SendMessage`, `GetTask`, `CancelTask`, streaming, push notifications) should be the baseline.

### 7.2 Task State Machine
The daemon's internal task lifecycle should map directly to the A2A states:
- `WORKING` → daemon is processing
- `INPUT_REQUIRED` → daemon needs clarification from the client
- `AUTH_REQUIRED` → daemon needs additional credentials
- `COMPLETED` / `FAILED` / `CANCELED` / `REJECTED` → terminal states

### 7.3 Extensions to Consider
For this project, the most relevant extensions are:
- **Traceability Extension** — essential for debugging multi-agent chains
- **Secure Passport Extension** — if the daemon handles PII or crosses trust boundaries
- **Custom health-check extension** — until the spec adds one (gap identified in §4.6)

### 7.4 Architectural Recommendations
- Implement the daemon as a **hybrid agent** (returns both Messages and Tasks per v1.0 semantics)
- Use `contextId` for session management across daemon instances
- Implement SSE streaming (`SendStreamingMessage`) for long-running inference tasks
- Use push notification webhooks for truly async operations (e.g., batch processing)
- Expose an Extended Agent Card with auth-required skill details

### 7.5 Integration with Existing Projects
Given the SilvaEngine ecosystem (Knowledge Graph Engine, MCP KG Inquirer), the daemon engine should:
- Use A2A for inter-agent delegation (agent-to-agent)
- Use MCP for tool integration (agent-to-tool, via existing MCP KG Inquirer)
- Position itself as an A2A Server that other agents can discover and delegate tasks to

## 8. Implementation Status (Updated 2026-05-02)

### 8.1 Phase 6 Migration (In Progress)

The a2a_daemon_engine is currently undergoing Phase 6 migration to A2A SDK v1.0. Key findings from code audit:

#### Completed Pre-Migration Fixes

| ID | Fix | Location | Status |
|----|-----|----------|--------|
| CLI-2 | `handle_agent_registration` import corrected - now via `_handle_agent_registration` wrapper | `a2a_executor.py:224-245` | ✅ Complete |
| CLI-3 | `cancel()` now uses `TaskState.canceled` enum instead of string | `a2a_executor.py:289,309,311` | ✅ Complete |
| CLI-7 | Weak JWT secrets rejected at startup with clear error message (CHANGEME, password, etc.) | `config.py:190-210` | ✅ Complete |
| CLI-PY2 | Python 2 `__future__` imports removed from all 14 handler files | `handlers/*.py` | ✅ Complete |

#### Pending Phase 6 Tasks

| ID | Task | Location | Priority |
|----|------|----------|----------|
| CLI-6 | `a2a_jsonrpc.py:213` still uses `asyncio.run()` in sync wrapper | `a2a_jsonrpc.py:213` | Medium |
| CLI-8 | TaskState enum still uses lowercase v0.3 names (`input_required`) - needs SCREAMING_SNAKE_CASE | `a2a_taskstore.py:206-208` | **High** |
| CLI-9 | Missing `AUTH_REQUIRED` and `REJECTED` states in status map | `a2a_taskstore.py:_map_status_to_taskstate` | **High** |
| CLI-11 | `list_tasks()` now returns `(tasks, next_token)` using offset tokens over the current GraphQL wrapper; SDK RPC exposure and integration tests remain | `a2a_taskstore.py:352+` | Medium |
| CLI-13 | Task model missing `contextId` field for session management | `models/a2a_task.py` | Medium |

#### Task State Migration (v0.3 → v1.0)

| v0.3 Format | v1.0 Format |
|-------------|-------------|
| `submitted` | `SUBMITTED` |
| `working` | `WORKING` |
| `input_required` | `INPUT_REQUIRED` |
| `completed` | `COMPLETED` |
| `canceled` | `CANCELED` |
| `failed` | `FAILED` |
| N/A | `AUTH_REQUIRED` (new) |
| N/A | `REJECTED` (new) |

### 8.2 Architecture Decisions for Migration

**Keep:**
- AgentExecutor pattern (canonical)
- `DynamoDBA2ATaskStore` (persistent task state)
- Multi-tenant composite partition keys
- JWT model (local + Cognito)
- EventQueue integration

**Change:**
- Remove hand-rolled JSON-RPC routing → use SDK `DefaultRequestHandler` (deprecate `a2a_jsonrpc.py`)
- Demote `/rest` to admin-only API (clear Auth + scoping)
- Migrate task-state strings to `SCREAMING_SNAKE_CASE` (rewrite `a2a_taskstore.py:_map_status_to_taskstate`)
- Replace bespoke push notifications with A2A-standard `PushNotificationConfig`
- Remove `from __future__ import print_function` (Python 2 cruft) across handlers

**Add:**
- `contextId` plumbing through executor and store
- SSE endpoints (`SendStreamingMessage`, `SubscribeToTask`) with replay buffer
- `ListTasks` with opaque cursor pagination
- `INPUT_REQUIRED` / `AUTH_REQUIRED` state transitions
- Traceability extension registration in Agent Card
- `ETag` / `Last-Modified` on Agent Card responses

### 8.3 Related Documents

- [A2A_DEVELOPMENT_PLAN.md](A2A_DEVELOPMENT_PLAN.md) — Detailed development roadmap
- [PHASE6_UPGRADE_CHECKLIST.md](PHASE6_UPGRADE_CHECKLIST.md) — Step-by-step Phase 6 implementation guide
- [scripts/migrate_taskstate_v1.py](../scripts/migrate_taskstate_v1.py) — Data migration script for TaskState enum
- [a2a-protocol-analysis.md](a2a-protocol-analysis.md) — This document (protocol analysis)

---

## 9. Ecosystem Observations

### 9.1 SDK Landscape
The A2A project maintains SDKs in **five languages**: Python, Go, JavaScript/TypeScript, Java, and .NET. Python and JS are the most mature. All SDKs are open-source (Apache 2.0).

### 9.2 Validation Tooling
- **A2A Inspector** — validates Agent Cards against the schema
- **A2A TCK (Technology Compatibility Kit)** — verifies protocol compliance for servers
- Both are community-launched and under active development

### 9.3 Sample Extensions (v1)
| Extension | Purpose |
|---|---|
| Secure Passport | Trusted contextual identity delegation |
| Timestamp | Simple metadata augmentation demo |
| Traceability | End-to-end tracing across agent chains |
| AGP (Agent Gateway Protocol) | Autonomous Squads routing + intent dispatch |

### 9.4 Partner Ecosystem
Google lists 50+ partners supporting A2A, including major cloud providers, enterprise platforms, and AI frameworks. The momentum is real, but actual production deployments are still early.

---

## 10. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Registry fragmentation (no standard API) | High | Medium | Define optional Registry API spec |
| Extension incompatibility (conflicting extensions) | Medium | High | Extension governance process exists; needs enforcement |
| Agent Card staleness | Medium | Medium | `X-A2A-Card-Version` service parameter; proper `ETag` usage |
| Streaming event loss on reconnection | Medium | High | Mandate replay buffer + `Last-Event-ID` |
| Orphaned tasks from unpropagated cancellation | Medium | Medium | Cancellation propagation convention |
| Security: SSRF via push notification URLs | High | High | Webhook URL validation/allowlisting (spec mentions this) |
| SDK v1.0 breaking changes | High | High | Pin to specific minor; integration tests; gate upgrade |
| Data migration failure (TaskState casing) | Medium | High | Full backup; dry-run migration; idempotent script |

---

## 11. Recommendations Summary

1. **Adopt A2A v1.0 as the inter-agent protocol standard** for any multi-agent system. It's the most mature, well-specified, and enterprise-ready option available.

2. **Use A2A alongside MCP, not instead of it.** A2A for agent delegation; MCP for tool integration. They solve different problems.

3. **Implement the JSON-RPC binding first**, add others as needed.

4. **Contribute to the Registry API specification** — this is the biggest gap for real-world multi-agent discovery.

5. **Push for a health-check convention** — critical for production orchestration.

6. **Invest in the A2A Inspector and TCK** before going to production. Protocol compliance matters more than feature velocity.

7. **Watch the extension ecosystem** — the Secure Passport and Traceability extensions are worth adopting early. The AGP extension is ambitious but may not stabilize quickly.

8. **Plan for streaming robustness** — implement `SubscribeToTask` reconnection with event replay, not just naive SSE reconnect.

9. **Use Extended Agent Cards** to keep your public skill surface minimal and authenticated surface rich.

10. **Track the roadmap** — the 3-6 month horizon includes SDK improvements and community best practices, which will significantly lower the implementation bar.

11. **For a2a_daemon_engine specifically:**
    - Complete Phase 6 TaskState migration before v1.0 SDK bump
    - Use the migration script `scripts/migrate_taskstate_v1.py` for data migration
    - Follow the `PHASE6_UPGRADE_CHECKLIST.md` for step-by-step implementation
    - Run A2A Inspector validation after each major change

---

## 12. References

- **Spec:** https://a2a-protocol.org/v1.0.0/specification
- **Repo:** https://github.com/google/A2A
- **Samples:** https://github.com/a2aproject/a2a-samples
- **Inspector:** https://github.com/a2aproject/a2a-inspector
- **TCK:** https://github.com/a2aproject/a2a-tck
- **Roadmap:** https://github.com/google/A2A/blob/main/docs/roadmap.md
- **What's New v1.0:** https://a2a-protocol.org/latest/whats-new-v1/
- **A2A + MCP Comparison:** https://a2a-protocol.org/v1.0.0/topics/a2a-and-mcp
- **Enterprise Guide:** https://a2a-protocol.org/v1.0.0/topics/enterprise-ready

---

*End of document.*
