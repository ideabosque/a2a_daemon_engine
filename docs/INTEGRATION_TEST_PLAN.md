# Integration Test Plan - A2A Daemon Engine

**Document Version:** 1.0.0
**Last Updated:** 2026-05-02
**Status:** Draft - Phase 8 Implementation

---

## 1. Overview

This document outlines the comprehensive integration testing strategy for the A2A Daemon Engine, ensuring all components work together correctly in realistic deployment scenarios.

### 1.1 Scope

- **In Scope:** End-to-end testing of RPC operations, multi-tenancy, authentication flows, event delivery, and DynamoDB persistence
- **Out of Scope:** Unit tests (covered separately), load testing (see Phase 8 performance targets), A2A TCK compliance (separate harness)

### 1.2 Test Environment Requirements

| Component | Requirement |
|-----------|-------------|
| Python | 3.11+ |
| Database | DynamoDB Local (Docker) or AWS DynamoDB (test table) |
| HTTP Client | `httpx` or `requests` |
| A2A SDK | `a2a-sdk ^1.0.0` |
| Test Framework | `pytest` + `pytest-asyncio` |

---

## 2. Test Categories

### 2.1 Core RPC Operations

| Test Case | Method | Expected Result | Priority |
|-----------|--------|-----------------|----------|
| TC-001 | `SendMessage` | Task created, message queued for delivery | P0 |
| TC-002 | `GetTask` | Returns complete task state with artifacts | P0 |
| TC-003 | `ListTasks` | Returns paginated list with cursor | P0 |
| TC-004 | `CancelTask` | Task transitions to CANCELED state | P0 |
| TC-005 | `agent.getCard` | Returns valid Agent Card JSON | P0 |

### 2.2 Multi-Tenancy & Data Isolation

| Test Case | Scenario | Expected Result | Priority |
|-----------|----------|-----------------|----------|
| TC-010 | Create task in tenant A | Task stored with composite PK `endpoint_a#part_a` | P0 |
| TC-011 | Query tenant B for tenant A's task | Returns 404 / empty result | P0 |
| TC-012 | Cross-tenant GraphQL query | Rejected with 403 | P0 |
| TC-013 | JWT token scope validation | Token for tenant A cannot access tenant B | P0 |

### 2.3 Authentication Flows

| Test Case | Provider | Scenario | Expected Result | Priority |
|-----------|----------|----------|-----------------|----------|
| TC-020 | Local JWT | Valid HS256 token | Request succeeds | P0 |
| TC-021 | Local JWT | Invalid/expired token | Returns 401 | P0 |
| TC-022 | Cognito | Valid RS256 token with JWKS | Request succeeds | P0 |
| TC-023 | Cognito | Token with wrong audience | Returns 401 | P1 |
| TC-024 | Admin Token | Static admin bypass | Bypasses auth check | P1 |

### 2.4 Event Delivery & Retry

| Test Case | Scenario | Expected Result | Priority |
|-----------|----------|-----------------|----------|
| TC-030 | Successful HTTP POST delivery | Task updated to COMPLETED | P0 |
| TC-031 | Failed delivery with retry | 3 attempts with exponential backoff (1s, 2s, 4s) | P0 |
| TC-032 | Max retries exceeded | Task marked FAILED | P0 |
| TC-033 | Webhook SSRF attempt | Rejected if URL in private CIDR | P1 |

### 2.5 Task State Machine

| Test Case | Transition | Expected State | Priority |
|-----------|------------|----------------|----------|
| TC-040 | Create task | SUBMITTED → WORKING | P0 |
| TC-041 | Agent requires input | WORKING → INPUT_REQUIRED | P0 |
| TC-042 | Agent requires auth | WORKING → AUTH_REQUIRED | P0 |
| TC-043 | Task completes | WORKING → COMPLETED | P0 |
| TC-044 | Task fails | WORKING → FAILED | P0 |
| TC-045 | Cancel pending task | Any → CANCELED | P0 |
| TC-046 | Cancel terminal task | Returns error (already terminal) | P0 |
| TC-047 | Task rejected | SUBMITTED → REJECTED | P1 |

### 2.6 Streaming (Phase 7)

| Test Case | Method | Expected Result | Priority |
|-----------|--------|-----------------|----------|
| TC-050 | `SendStreamingMessage` | SSE stream established | P1 |
| TC-051 | `SubscribeToTask` with Last-Event-ID | Replay from event ID | P1 |
| TC-052 | Reconnect mid-stream | No event loss within buffer window | P1 |

---

## 3. Test Data Setup

### 3.1 Fixtures Required

```python
# pytest fixtures to be implemented

@pytest.fixture
def dynamodb_local():
    """Spin up DynamoDB local Docker container"""
    pass

@pytest.fixture
def test_endpoint():
    """Create test endpoint configuration"""
    return {"endpoint_id": "test-endpoint-001", "partition_key": "test#001"}

@pytest.fixture
def sample_agent():
    """Create sample agent record"""
    pass

@pytest.fixture
def sample_task():
    """Create sample task in various states"""
    pass

@pytest.fixture
def valid_jwt_token():
    """Generate valid test JWT"""
    pass

@pytest.fixture
def mock_webhook_server():
    """HTTP server to receive delivery callbacks"""
    pass
```

### 3.2 Test Configuration

See `a2a_daemon_engine/tests/.env.example` file for environment variable configuration. Copy this file to `.env` in the tests directory and customize for your test environment.

---

## 4. Execution Plan

### 4.1 Pre-Test Setup

1. Start DynamoDB Local: `docker run -p 8000:8000 amazon/dynamodb-local`
2. Create test tables with Terraform or AWS CLI
3. Verify test environment variables loaded
4. Run health check: `curl http://localhost:8001/rest/health`

### 4.2 Test Execution Order

```
Phase 1: Infrastructure (TC-001 to TC-005)
  → Verify basic RPC operations

Phase 2: Security (TC-010 to TC-024)
  → Verify multi-tenancy and auth isolation

Phase 3: Business Logic (TC-030 to TC-047)
  → Verify delivery, retry, and state transitions

Phase 4: Edge Cases & Streaming (TC-050 to TC-052)
  → Phase 7 features
```

### 4.3 Success Criteria

- **100%** of P0 test cases pass
- **≥ 90%** of P1 test cases pass
- All tests complete in < 5 minutes
- No flaky tests (3 consecutive runs must pass)

---

## 5. CI/CD Integration

### 5.1 GitHub Actions Workflow

```yaml
# .github/workflows/integration-tests.yml
name: Integration Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      dynamodb:
        image: amazon/dynamodb-local
        ports: ['8000:8000']
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install poetry
      - run: poetry install
      - run: poetry run pytest tests/integration/ -v --tb=short
```

### 5.2 Local Development

```bash
# Run specific test category
pytest tests/integration/test_rpc_operations.py -v

# Run with coverage
pytest tests/integration/ --cov=a2a_daemon_engine --cov-report=html

# Run in debug mode
pytest tests/integration/ -v --log-cli-level=DEBUG
```

---

## 6. Test Artifacts

### 6.1 Required Outputs

- JUnit XML report: `pytest --junitxml=integration-results.xml`
- Coverage report: `pytest --cov-report=xml`
- Test logs: Captured in `logs/integration-test.log`

### 6.2 Failure Analysis

When tests fail, capture:
1. Test case ID and name
2. Expected vs actual behavior
3. DynamoDB table state dump
4. Application logs from test execution
5. Network traces (if applicable)

---

## 7. Maintenance

### 7.1 Update Triggers

Update this plan when:
- New RPC operations added (check A2A_DEVELOPMENT_PLAN.md Phase 6-7)
- Authentication mechanisms changed
- Database schema modified
- New streaming features implemented

### 7.2 Review Schedule

- **Monthly:** Review test coverage gaps
- **Per-Release:** Validate all P0 tests pass before release
- **Phase Completion:** Update when Phase 8 (Production Hardening) completes

**Test Execution Status:** Cannot execute due to dependency conflicts (graphql package version mismatch). Tests defined but require dependency resolution before running.

---

## Appendix C - Test Execution Report

### Test Discovery Results

**Status:** ✅ Tests Discovered and Running

**Environment:** Python 3.12.10 with venv at `c:/Python312/env`

**Packages Installed:**
- silvaengine-constants 0.0.1 ✅
- silvaengine-utility 0.0.6 ✅
- SilvaEngine-DynamoDB-Base 0.0.1 ✅
- All other silvaengine dependencies ✅

### Test Execution Summary (Latest Run - 2026-05-02)

**Status:** ✅ Tests Running | **14 Passed, 9 Failed | 60.9% Pass Rate**

**Environment:** Python 3.12.10 with venv at `c:/Python312/env/Scripts/python.exe`

| Test File | Test Count | Type | Results |
|-----------|------------|------|---------|
| `test_a2a_mock_actions.py` | 14 tests | Mock/Unit | **14 passed** (100%) |
| `test_ai_a2a_daemon_engine.py` | 9 tests | Integration | **9 failed** (0%) |

---

### Mock Tests Results ✅

All 14 mock tests passing successfully:

| Test Name | Status | Notes |
|-----------|--------|-------|
| `test_register_agent_mock` | ✅ PASSED | Handshake completed successfully |
| `test_assign_task_mock` | ✅ PASSED | Task assigned successfully |
| `test_route_message_mock` | ✅ PASSED | Message routed successfully |
| `test_execute_task_mock` | ✅ PASSED | Task execution initiated |
| `test_missing_action` | ✅ PASSED | Error handling correct |
| `test_invalid_action` | ✅ PASSED | Error handling correct |
| `test_register_agent_flow` | ✅ PASSED | Full flow validated |
| `test_assign_task_flow` | ✅ PASSED | Full flow validated |
| `test_route_message_flow` | ✅ PASSED | Full flow validated |
| `test_execute_task_flow` | ✅ PASSED | Response validated |
| `test_a2a_actions_parametrized` (4 tests) | ✅ PASSED | All parameter combinations work |

**Total:** 14/14 mock tests passed (100%)

---

### Integration Tests Results ❌

All 9 GraphQL lifecycle flow tests failing with JSON scalar parsing error:

| Test Name | Error |
|-----------|-------|
| `test_graphql_ping` | ✅ **PASSED** |
| `test_agent_lifecycle_flow[test_data0]` | ❌ JSON.parse_value() missing argument |
| `test_agent_lifecycle_flow[test_data1]` | ❌ JSON.parse_value() missing argument |
| `test_task_lifecycle_flow[test_data0]` | ❌ JSON.parse_value() missing argument |
| `test_task_lifecycle_flow[test_data1]` | ❌ JSON.parse_value() missing argument |
| `test_message_lifecycle_flow[test_data0]` | ❌ JSON.parse_value() missing argument |
| `test_message_lifecycle_flow[test_data1]` | ❌ JSON.parse_value() missing argument |
| `test_setting_lifecycle_flow[test_data0]` | ❌ JSON.parse_value() missing argument |
| `test_setting_lifecycle_flow[test_data1]` | ❌ JSON.parse_value() missing argument |

**Error Message:**
```
GraphQLError: Variable '$setting' got invalid value {...}; 
Expected type 'JSON'. JSON.parse_value() missing 1 required positional argument: 'value'
```

---

### Issues Resolution Status

#### 1. ✅ RESOLVED - Dependency Chain
- **Issue:** `ExecutionResult` and `get_introspection_query` import errors from graphql package
- **Resolution:** Added compatibility imports in `silvaengine_utility/graphql.py` to handle both graphql v2 and v3
- **Status:** All imports working correctly

#### 2. ✅ RESOLVED - JWT Secret Key Configuration
- **Issue:** Tests failed with `ValueError: Invalid JWT_SECRET_KEY: 'CHANGEME'`
- **Resolution:** 
  - Updated `conftest.py` SETTING dictionary with `jwt_secret_key`
  - Updated `.env.example` with test JWT secret key
  - Reinstalled silvaengine_utility with JSON export
- **Status:** Configuration now passes validation

#### 3. ✅ RESOLVED - JSON Import from silvaengine_utility
- **Issue:** `ImportError: cannot import name 'JSON' from 'silvaengine_utility'`
- **Resolution:** Added `JSON` to `__all__` and import list in `silvaengine_utility/__init__.py`
- **Status:** JSON export working correctly

#### 4. ❌ ACTIVE - JSON Scalar parse_value() Bug - CRITICAL
- **Issue:** All GraphQL mutations with JSON-type fields fail
- **Root Cause:** `JSON.parse_value()` method signature mismatch with Graphene expectations
- **Impact:** All 9 lifecycle integration tests blocked
- **Error:** `JSON.parse_value() missing 1 required positional argument: 'value'`
- **Affected Fields:** `payload`, `setting`, `inputData`, `outputData`, `capabilities` in mutations
- **File:** `silvaengine_utility/silvaengine_utility/graphql.py` line ~791
- **Required Fix:** Update `JSON.parse_value()` to match Graphene scalar interface

---

### Current Blocker: JSON Scalar Bug

**Technical Details:**

The `JSON` scalar class at line 640 in `graphql.py` defines:
```python
@staticmethod
def parse_value(value: Any) -> Any:
    raw_value = JSON.identity(value)
    return raw_value
```

But Graphene is calling it without the `value` argument, causing:
```
TypeError: parse_value() missing 1 required positional argument: 'value'
```

**Solution Needed:**
Update the `JSON` scalar class to match Graphene's expected interface for custom scalars. The method should handle being called with the value from the GraphQL variable.

---

### Required Actions to Enable Full Testing

**Immediate Fixes Needed:**
1. ✅ JWT configuration - DONE
2. ✅ JSON import - DONE  
3. ⚠️ **Fix JSON scalar parse_value() bug in silvaengine_utility** (BLOCKING)
4. ✅ Mock tests all passing

**Run Tests:**
```bash
cd a2a_daemon_engine/tests

# Run all tests
AI_A2A_TEST_FUNCTION="" pytest -v --tb=short

# Run only mock tests (all passing)
pytest test_a2a_mock_actions.py -v

# Run integration tests (blocked by JSON bug)
pytest test_ai_a2a_daemon_engine.py -v
```

---

## References

- [A2A_DEVELOPMENT_PLAN.md](A2A_DEVELOPMENT_PLAN.md) - Phase 8 Testing Strategy
- [a2a_daemon_engine/tests/README.md](../a2a_daemon_engine/tests/README.md) - Test Suite Documentation
- [A2A Protocol Specification](https://a2a-protocol.org/v1.0.0/specification)
- [pytest documentation](https://docs.pytest.org/)
- [DynamoDB Local Guide](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/DynamoDBLocal.html)

---

**Last Updated:** 2026-05-02 (Test Run Completed)  
**Status:** 14/23 Tests Passing (60.9%) | Active Blocker: JSON Scalar Bug  
**Next Review:** After JSON scalar fix in silvaengine_utility
