# Documentation Index - A2A Daemon Engine

**Last Updated:** 2026-05-09
**Package Version:** 0.0.1

This index maps the current documentation set after the 2026-05-09 cleanup that
removed legacy A2A surfaces and condensed the docs around the v1-only
architecture.

---

## Documents

| File | Purpose |
|------|---------|
| [README.md](../README.md) | Project overview, quick start, configuration, authentication, multi-tenancy, deployment |
| [AGENTS.md](../AGENTS.md) | Day-to-day contributor reference: environment, entrypoints, key files, JSON-RPC method rules, dry-run shape |
| [A2A_ARCHITECTURE.md](A2A_ARCHITECTURE.md) | HTTP surface table, runtime components, request-flow sequence diagram |
| [A2A_PROTOCOL_CALL_FLOW.md](A2A_PROTOCOL_CALL_FLOW.md) | Per-method call paths for `message/send`, `tasks/get`, `tasks/cancel`, and serverless dispatch |
| [A2A_DEVELOPMENT_PLAN.md](A2A_DEVELOPMENT_PLAN.md) | Current state, implementation notes, release gates |
| [A2A_TEST_PLAN.md](A2A_TEST_PLAN.md) | Unit / live / release-gate coverage |
| [INTEGRATION_TEST_PLAN.md](INTEGRATION_TEST_PLAN.md) | End-to-end / integration playbook |
| [a2a-protocol-analysis.md](a2a-protocol-analysis.md) | Protocol background and design suggestions |

---

## Find Documentation by Need

### "How do I get started?"
→ [README — Run](../README.md#run) and [README — Verify](../README.md#verify)

### "What HTTP surface does the daemon expose?"
→ [README — Architecture](../README.md#architecture) or
[A2A_ARCHITECTURE.md — HTTP Surface](A2A_ARCHITECTURE.md#http-surface)

### "Which functions handle each protocol method?"
→ [A2A_PROTOCOL_CALL_FLOW.md](A2A_PROTOCOL_CALL_FLOW.md)

### "What's the current implementation status?"
→ [A2A_DEVELOPMENT_PLAN.md — Current State](A2A_DEVELOPMENT_PLAN.md#current-state)
and [A2A_DEVELOPMENT_PLAN.md — Implementation Notes](A2A_DEVELOPMENT_PLAN.md#implementation-notes)

### "What still needs to ship before release?"
→ [A2A_DEVELOPMENT_PLAN.md — Release Gates](A2A_DEVELOPMENT_PLAN.md#release-gates)

### "How do I configure authentication and multi-tenancy?"
→ [README — Authentication](../README.md#authentication) and
[README — Multi-Tenancy](../README.md#multi-tenancy)

### "How do I deploy to Lambda?"
→ [README — Deployment](../README.md#deployment)

### "Where do I find live/API tests and pytest entrypoints?"
→ [AGENTS.md — Entrypoints](../AGENTS.md#entrypoints) and
[A2A_TEST_PLAN.md — Live/API Tests](A2A_TEST_PLAN.md#liveapi-tests)

---

## Current Surfaces (Reference)

The HTTP daemon serves both a SDK Starlette app (primary) and a FastAPI
operations app mounted under `/rest`.

| Path | Purpose |
|------|---------|
| `GET /.well-known/agent-card.json` | Agent Card discovery |
| `POST /` | JSON-RPC compatibility endpoint (`message/send`, `tasks/get`, `tasks/cancel`) |
| `POST /v1` | SDK native dispatcher (`SendMessage`, `GetTask`, `CancelTask`) |
| `GET /tasks/{task_id}/stream` | SSE task event stream + replay buffer |
| `GET /rest/health` | Health check |
| `GET /rest/me` | Authenticated user claims |
| `GET /rest/{endpoint_id}` | Operational endpoint metadata |
| `POST /rest/{endpoint_id}/a2a_core_graphql` | GraphQL access |
| `POST /rest/auth/token` | OAuth2 password-grant token endpoint |

Removed surfaces (do not re-document as active):

- `/rest/a2a-jsonrpc`
- `/rest/a2a/{endpoint_id}/...`
- direct `action=...` dispatch via `A2ADaemonEngine.a2a()`
- `handlers/a2a_jsonrpc.py`
- `handlers/a2a_sdk_compat.py`

---

## External Resources

### Official A2A Protocol
- [A2A Protocol Specification](https://a2a-protocol.org/latest/specification/)
- [A2A SDK Python API](https://a2a-protocol.org/latest/sdk/python/api/)
- [A2A Samples](https://github.com/a2aproject/a2a-samples)

### Reference Implementations
- [HelloWorld Agent](https://github.com/a2aproject/a2a-samples/tree/main/samples/python/agents/helloworld)
- [Travel Planner Agent](https://github.com/a2aproject/a2a-samples/tree/main/samples/python/agents/travel_planner_agent)
- [Multi-Agent Host](https://github.com/a2aproject/a2a-samples/tree/main/samples/python/hosts/a2a_multiagent_host)

---

**Maintained By:** SilvaEngine Team
