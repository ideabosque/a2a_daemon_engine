# A2A Protocol Test Plan

**Document Version:** 1.0.0
**Last Updated:** 2026-05-03
**Status:** Draft

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Test Scope](#2-test-scope)
3. [Test Strategy](#3-test-strategy)
4. [Test Environments](#4-test-environments)
5. [Test Cases](#5-test-cases)
6. [Test Data Requirements](#6-test-data-requirements)
7. [Test Execution](#7-test-execution)
8. [Success Criteria](#8-success-criteria)
9. [Risk & Mitigation](#9-risk--mitigation)
10. [Appendices](#10-appendices)

---

## 1. Executive Summary

This document outlines the comprehensive test plan for the A2A (Agent-to-Agent) Protocol implementation in the A2A Daemon Engine. The test plan covers unit tests, integration tests, protocol compliance tests, and end-to-end validation to ensure full compatibility with A2A SDK v1.0.

### Objectives

- Validate A2A protocol compliance against SDK v1.0 specification
- Ensure multi-tenant data isolation
- Verify task lifecycle management (create, execute, cancel, query)
- Test agent registration and discovery
- Validate message routing with retry logic
- Confirm AI Agent Core Engine integration

---

## 2. Test Scope

### 2.1 In Scope

**Core Protocol Operations:**
- Agent Card discovery (`/.well-known/agent-card.json`)
- JSON-RPC endpoint handling
- Agent registration and management
- Task lifecycle (Create, Get, List, Cancel)
- Message routing and delivery
- Streaming (SSE) - Phase 7
- Push notifications - Phase 7

**Integration Points:**
- AI Agent Core Engine delegation
- DynamoDB persistence layer
- JWT authentication (local & Cognito)
- Cross-engine GraphQL communication

### 2.2 Out of Scope

- Load/performance testing (covered separately)
- Security penetration testing
- Third-party A2A client compatibility (reference client only)

---

## 3. Test Strategy

### 3.1 Test Pyramid

```
                    ┌─────────────┐
                    │   E2E Tests │  <- Full workflow validation
                    │   (~10%)    │
                   ┌───────────────┐
                   │ Protocol Comp │  <- A2A TCK, Inspector
                   │    (~20%)     │
                  ┌─────────────────┐
                  │  Integration    │  <- DB, auth, cross-engine
                  │    (~30%)       │
                 ┌───────────────────┐
                 │      Unit         │  <- Handlers, utilities
                 │     (~40%)        │
                 └───────────────────┘
```

### 3.2 Test Types

| Type | Tooling | Target Coverage |
|------|---------|-----------------|
| **Unit** | pytest + pytest-asyncio | ≥ 70% on handlers/ |
| **Integration** | pytest + local DynamoDB | All RPC happy-paths + auth + multi-tenant isolation |
| **Protocol Compliance** | A2A TCK + A2A Inspector | 100% pass on supported operations |
| **Contract** | GraphQL schema validation | All queries/mutations |

### 3.3 Test Categories

#### Category A: Core Protocol (P0 - Must Have)
- Agent Card validation
- JSON-RPC routing
- Task CRUD operations
- Agent registration

#### Category B: Advanced Features (P1 - Should Have)
- Streaming (SSE)
- Push notifications
- Multi-turn workflows
- Extended Agent Card

#### Category C: Integration (P1 - Should Have)
- AI Agent Core Engine delegation
- Cross-tenant isolation
- Authentication flows

#### Category D: Edge Cases (P2 - Nice to Have)
- Network failures
- Timeout handling
- Concurrent task execution
- Data migration scenarios

---

## 4. Test Environments

### 4.1 Local Development

```bash
# Setup
poetry install
docker run -d -p 8000:8000 amazon/dynamodb-local

# Run tests
poetry run pytest a2a_daemon_engine/tests/ -v
```

**Configuration:**
- DynamoDB Local on port 8000
- A2A Daemon on port 8001
- JWT secret: test-secret-key (not CHANGEME)

### 4.2 CI/CD Environment

- GitHub Actions / Jenkins pipeline
- Local DynamoDB via Docker
- Coverage reporting (pytest-cov)

### 4.3 Staging Environment

- AWS DynamoDB (dev instance)
- Cognito test user pool
- A2A Inspector validation

---

## 5. Test Cases

### 5.1 Unit Tests

#### 5.1.1 Agent Management

**Test Case: TC-AGENT-001**
```
ID: TC-AGENT-001
Title: Agent registration with valid data
Preconditions: Clean database, valid JWT
Steps:
  1. POST /rest/a2a/{endpoint_id}/agents/register
  2. Send valid agent payload
Expected:
  - HTTP 200/201
  - Agent persisted in DynamoDB
  - agent_uuid field populated if provided
Priority: P0
```

**Test Case: TC-AGENT-002**
```
ID: TC-AGENT-002
Title: Agent registration with duplicate agent_id
Preconditions: Existing agent with same ID
Steps:
  1. Register agent with existing agent_id
  2. Verify conflict handling
Expected:
  - HTTP 409 Conflict OR
  - Update existing agent (idempotent)
Priority: P0
```

**Test Case: TC-AGENT-003**
```
ID: TC-AGENT-003
Title: Agent lookup by capabilities
Preconditions: Multiple agents with different capabilities
Steps:
  1. Query agents with capability filter
  2. Verify matching results
Expected:
  - Only agents with matching capabilities returned
  - Empty list if no match
Priority: P1
```

**Test Case: TC-AGENT-004**
```
ID: TC-AGENT-004
Title: Agent update with agent_uuid association
Preconditions: Existing agent without agent_uuid
Steps:
  1. Update agent with agent_uuid
  2. Verify association persisted
Expected:
  - agent_uuid stored in DynamoDB
  - Can be used for AI Core Engine delegation
Priority: P1
```

#### 5.1.2 Task Management

**Test Case: TC-TASK-001**
```
ID: TC-TASK-001
Title: Task creation with valid parameters
Preconditions: Registered agent exists
Steps:
  1. POST /rest/a2a/{endpoint_id}/tasks/create
  2. Provide task_type, priority, input_data
Expected:
  - HTTP 200
  - Task persisted with generated task_id
  - Initial status: SUBMITTED
Priority: P0
```

**Test Case: TC-TASK-002**
```
ID: TC-TASK-002
Title: Task state transitions
Preconditions: Created task
Steps:
  1. Create task (status: SUBMITTED)
  2. Assign to agent (status: WORKING)
  3. Complete task (status: COMPLETED)
Expected:
  - State transitions valid per A2A spec
  - Timestamps updated correctly
Priority: P0
```

**Test Case: TC-TASK-003**
```
ID: TC-TASK-003
Title: Task cancellation
Preconditions: Task in WORKING state
Steps:
  1. Call CancelTask RPC
  2. Verify status change
Expected:
  - Status: CANCELED
  - Cannot transition to other states
Priority: P0
```

**Test Case: TC-TASK-004**
```
ID: TC-TASK-004
Title: Task with contextId grouping
Preconditions: None
Steps:
  1. Create task with contextId
  2. Create related task with same contextId
  3. Query tasks by contextId
Expected:
  - Both tasks returned
  - contextId persisted correctly
Priority: P1
```

**Test Case: TC-TASK-005**
```
ID: TC-TASK-005
Title: Task requiring AI processing
Preconditions: Agent with agent_uuid associated
Steps:
  1. Create task assigned to agent with agent_uuid
  2. Execute task
  3. Verify AI Agent Core Engine invocation
Expected:
  - invoke_ask_model called with correct agent_uuid
  - Task status updated based on AI response
Priority: P1
```

#### 5.1.3 Message Routing

**Test Case: TC-MSG-001**
```
ID: TC-MSG-001
Title: Message delivery to active agent
Preconditions: Source and target agents registered
Steps:
  1. Send message from agent A to agent B
  2. Verify delivery attempt
Expected:
  - HTTP POST to agent B endpoint_url
  - Message persisted with status
Priority: P0
```

**Test Case: TC-MSG-002**
```
ID: TC-MSG-002
Title: Message delivery retry on failure
Preconditions: Target agent endpoint returns 500
Steps:
  1. Send message
  2. Verify retry attempts (3 times)
  3. Verify exponential backoff (1s, 2s, 4s)
Expected:
  - 3 delivery attempts logged
  - Final status: FAILED after retries
Priority: P1
```

#### 5.1.4 AI Agent Core Engine Integration

**Test Case: TC-AI-001**
```
ID: TC-AI-001
Title: invoke_ask_model with valid agent_uuid
Preconditions: Mock ai_agent_core_engine
Steps:
  1. Call invoke_ask_model with valid parameters
  2. Verify GraphQL request structure
Expected:
  - Correct GraphQL query generated
  - agent_uuid passed correctly
  - Response parsed and returned
Priority: P0
```

**Test Case: TC-AI-002**
```
ID: TC-AI-002
Title: Async task polling
Preconditions: Mock async task in progress
Steps:
  1. Start async task
  2. Poll with get_async_task
  3. Verify status transitions
Expected:
  - Polling continues until completion
  - Status returned correctly
Priority: P1
```

**Test Case: TC-AI-003**
```
ID: TC-AI-003
Title: AI Core Engine error handling
Preconditions: Mock AI Core Engine returning error
Steps:
  1. Invoke ask_model
  2. Receive error response
Expected:
  - Error propagated correctly
  - A2A task status set to FAILED
Priority: P1
```

### 5.2 Integration Tests

#### 5.2.1 Multi-Tenant Isolation

**Test Case: TC-MT-001**
```
ID: TC-MT-001
Title: Cross-tenant data access prevention
Preconditions: Two tenants (tenant-a, tenant-b)
Steps:
  1. Create agent in tenant-a
  2. Attempt to access from tenant-b context
Expected:
  - HTTP 403 or 404
  - No data leakage between tenants
Priority: P0
```

**Test Case: TC-MT-002**
```
ID: TC-MT-002
Title: Composite partition key validation
Preconditions: endpoint_id and part_id combinations
Steps:
  1. Create agents with various partition key combinations
  2. Query with different partition keys
Expected:
  - Correct data returned per partition
  - Keys assembled correctly (endpoint_id#part_id)
Priority: P0
```

#### 5.2.2 Authentication Flows

**Test Case: TC-AUTH-001**
```
ID: TC-AUTH-001
Title: Local JWT authentication
Preconditions: AUTH_PROVIDER=local
Steps:
  1. Generate valid JWT
  2. Access protected endpoint
Expected:
  - HTTP 200 on valid token
  - HTTP 401 on invalid/missing token
Priority: P0
```

**Test Case: TC-AUTH-002**
```
ID: TC-AUTH-002
Title: Cognito JWT authentication
Preconditions: AUTH_PROVIDER=cognito, valid Cognito token
Steps:
  1. Access protected endpoint with Cognito token
Expected:
  - Token validated against JWKS
  - Proper user context extracted
Priority: P1
```

**Test Case: TC-AUTH-003**
```
ID: TC-AUTH-003
Title: Weak JWT secret rejection
Preconditions: JWT_SECRET_KEY="CHANGEME"
Steps:
  1. Start daemon with weak secret
Expected:
  - Startup rejected with clear error
  - Service does not start
Priority: P1
```

### 5.3 Protocol Compliance Tests

#### 5.3.1 Agent Card

**Test Case: TC-CARD-001**
```
ID: TC-CARD-001
Title: Agent Card schema validation
Preconditions: Server running
Steps:
  1. GET /.well-known/agent-card.json
  2. Validate against A2A schema
Expected:
  - HTTP 200
  - Valid JSON matching A2A spec
  - Required fields present
Priority: P0
```

**Test Case: TC-CARD-002**
```
ID: TC-CARD-002
Title: Agent Card via JSON-RPC
Preconditions: Server running
Steps:
  1. POST / with agent.getCard method
Expected:
  - Valid JSON-RPC response
  - Agent Card in result
Priority: P0
```

#### 5.3.2 JSON-RPC Operations

**Test Case: TC-RPC-001**
```
ID: TC-RPC-001
Title: SendMessage operation
Preconditions: Two registered agents
Steps:
  1. Send JSON-RPC SendMessage request
Expected:
  - Message delivered to target agent
  - Response with message ID
Priority: P0
```

**Test Case: TC-RPC-002**
```
ID: TC-RPC-002
Title: GetTask operation
Preconditions: Existing task
Steps:
  1. Send GetTask request
Expected:
  - Task details returned
  - Status matches stored state
Priority: P0
```

**Test Case: TC-RPC-003**
```
ID: TC-RPC-003
Title: ListTasks with cursor pagination
Preconditions: Multiple tasks exist
Steps:
  1. Request task list
  2. Verify cursor-based pagination
Expected:
  - Tasks returned in pages
  - Cursor for next page provided
Priority: P1
```

**Test Case: TC-RPC-004**
```
ID: TC-RPC-004
Title: CancelTask operation
Preconditions: Task in cancellable state
Steps:
  1. Send CancelTask request
Expected:
  - Task status: CANCELED
  - Cannot cancel already-cancelled tasks
Priority: P0
```

#### 5.3.3 Task State Machine

**Test Case: TC-STATE-001**
```
ID: TC-STATE-001
Title: Valid state transitions
Preconditions: Task in initial state
Steps:
  1. SUBMITTED → WORKING
  2. WORKING → INPUT_REQUIRED
  3. INPUT_REQUIRED → WORKING
  4. WORKING → COMPLETED
Expected:
  - All transitions succeed
  - Invalid transitions rejected
Priority: P0
```

**Test Case: TC-STATE-002**
```
ID: TC-STATE-002
Title: Terminal states handling
Preconditions: Tasks in COMPLETED, FAILED, CANCELED
Steps:
  1. Attempt to transition from terminal states
Expected:
  - All transitions rejected
  - Error message clear
Priority: P1
```

### 5.4 Edge Cases & Negative Tests

**Test Case: TC-EDGE-001**
```
ID: TC-EDGE-001
Title: Concurrent task execution
Preconditions: Single agent
Steps:
  1. Create multiple tasks simultaneously
  2. Assign to same agent
Expected:
  - All tasks processed correctly
  - No race conditions
Priority: P2
```

**Test Case: TC-EDGE-002**
```
ID: TC-EDGE-002
Title: Large message payload
Preconditions: None
Steps:
  1. Send message with large payload (>1MB)
Expected:
  - Handled gracefully
  - Or rejected with clear error
Priority: P2
```

**Test Case: TC-EDGE-003**
```
ID: TC-EDGE-003
Title: Network timeout during delivery
Preconditions: Simulated slow agent endpoint
Steps:
  1. Send message to slow endpoint
Expected:
  - Timeout handled correctly
  - Retry logic applied
Priority: P2
```

**Test Case: TC-EDGE-004**
```
ID: TC-EDGE-004
Title: Malformed JSON-RPC request
Preconditions: None
Steps:
  1. Send invalid JSON to JSON-RPC endpoint
Expected:
  - HTTP 400 with error details
  - No server crash
Priority: P1
```

---

## 6. Test Data Requirements

### 6.1 Test Fixtures

**Agents:**
```json
{
  "agent_id": "test-agent-001",
  "agent_name": "Test Agent Alpha",
  "capabilities": ["text-processing", "data-analysis"],
  "endpoint_url": "http://localhost:9001",
  "agent_uuid": "uuid-from-ai-core-engine-001"
}
```

**Tasks:**
```json
{
  "task_id": "task-001",
  "task_type": "data-processing",
  "priority": "high",
  "input_data": {"query": "process test data"},
  "assigned_agent_id": "test-agent-001"
}
```

**Messages:**
```json
{
  "from_agent_id": "test-agent-001",
  "to_agent_id": "test-agent-002",
  "message_type": "text",
  "payload": {"text": "Hello from test"}
}
```

### 6.2 Mock Services

- **AI Agent Core Engine:** Mock GraphQL responses
- **External Agent Endpoints:** HTTP mock server for message delivery
- **Cognito:** Test user pool with known credentials

---

## 7. Test Execution

### 7.1 Unit Test Execution

```bash
# Run all unit tests
poetry run pytest a2a_daemon_engine/tests/ -v --tb=short

# Run specific test file
poetry run pytest a2a_daemon_engine/tests/test_a2a_mock_actions.py -v

# Run with coverage
poetry run pytest --cov=a2a_daemon_engine --cov-report=html

# Run specific category
poetry run pytest -m "unit" -v
```

### 7.2 Integration Test Execution

```bash
# Start dependencies
docker run -d -p 8000:8000 --name dynamodb amazon/dynamodb-local

# Run integration tests
poetry run pytest -m "integration" -v

# Cleanup
docker stop dynamodb && docker rm dynamodb
```

### 7.3 Protocol Compliance

```bash
# A2A Inspector (requires installation)
a2a-inspector validate --endpoint http://localhost:8001

# A2A TCK (when available)
a2a-tck run --target http://localhost:8001 --report junit
```

### 7.4 Test Matrix

| Test Category | Frequency | Environment | Owner |
|--------------|-----------|-------------|-------|
| Unit Tests | Every commit | Local/CI | Developer |
| Integration Tests | PR merge | CI | QA |
| Protocol Compliance | Release | Staging | QA |
| Multi-tenant Tests | Weekly | Staging | Security |

---

## 8. Success Criteria

### 8.1 Coverage Targets

| Component | Target | Current |
|-----------|--------|---------|
| handlers/a2a_executor.py | ≥ 70% | TBD |
| handlers/a2a_handlers.py | ≥ 70% | TBD |
| handlers/a2a_taskstore.py | ≥ 70% | TBD |
| handlers/a2a_ai_agent_utility.py | ≥ 80% | TBD |
| models/ | ≥ 60% | TBD |

### 8.2 Protocol Compliance

- **A2A Inspector:** 0 schema violations
- **A2A TCK:** 100% pass on implemented operations
- **JSON-RPC:** All 11 v1.0 operations pass reference tests

### 8.3 Functional Criteria

- All P0 test cases pass
- No P1 test failures
- < 5% P2 test failures

### 8.4 Performance Criteria

- Task creation: < 100ms (p95)
- Message routing: < 500ms (p95)
- AI delegation: < 30s timeout

---

## 9. Risk & Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| A2A SDK v1.0 breaking changes | High | High | Pin to minor version; gate on TCK |
| DynamoDB Local vs AWS differences | Medium | Medium | Test on real DynamoDB in staging |
| Mock AI Core Engine differs from real | Medium | High | Integration tests against real service |
| Test data contamination | Low | Medium | Fresh DB per test run |
| Flaky async tests | Medium | Medium | Retry logic; deterministic waits |

---

## 10. Appendices

### Appendix A: Test File Organization

```
a2a_daemon_engine/tests/
├── conftest.py                    # Shared fixtures
├── test_a2a_mock_actions.py       # Core handler tests
├── test_ai_a2a_daemon_engine.py   # Integration tests
├── test_ai_agent_integration.py   # AI Core Engine integration (NEW)
├── test_auth.py                   # Authentication tests
├── test_multi_tenant.py           # Multi-tenancy tests
├── test_protocol_compliance.py    # A2A protocol tests
└── fixtures/
    ├── agents.json
    ├── tasks.json
    └── messages.json
```

### Appendix B: Environment Variables for Testing

```bash
# Test-specific overrides
A2A_TEST_MODE=true
DYNAMODB_ENDPOINT=http://localhost:8000
JWT_SECRET_KEY=test-secret-key-for-testing-only
AUTH_PROVIDER=local
REGION_NAME=us-east-1
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
```

### Appendix C: Test Utilities

```python
# conftest.py - Shared fixtures example

@pytest.fixture
def mock_ai_core_engine():
    """Mock AI Agent Core Engine responses."""
    with patch('a2a_daemon_engine.handlers.a2a_ai_agent_utility._execute_graphql') as mock:
        mock.return_value = {
            "askModel": {
                "agentUuid": "test-agent-uuid",
                "threadUuid": "test-thread-uuid",
                "asyncTaskUuid": "test-async-uuid",
                "currentRunUuid": "test-run-uuid"
            }
        }
        yield mock

@pytest.fixture
def test_agent_with_uuid():
    """Create test agent with agent_uuid association."""
    return {
        "agent_id": "test-agent",
        "agent_name": "Test Agent",
        "capabilities": ["ai-processing"],
        "endpoint_url": "http://localhost:9001",
        "agent_uuid": "ai-core-agent-uuid"
    }
```

### Appendix D: Related Documents

- [A2A_DEVELOPMENT_PLAN.md](A2A_DEVELOPMENT_PLAN.md) - Development roadmap
- [a2a-protocol-analysis.md](a2a-protocol-analysis.md) - Protocol analysis
- [../README.md](../README.md) - Engine overview

---

**Document Control:**
- Author: SilvaEngine Team
- Reviewers: TBD
- Approval: TBD
- Next Review Date: 2026-06-03
