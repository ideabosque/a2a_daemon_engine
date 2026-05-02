# A2A Daemon Engine Tests

Comprehensive test suite for the A2A Daemon Engine, following the **lifecycle flow pattern** from ai_agent_core_engine.

## Overview

This test suite uses **lifecycle flow tests** that validate complete CRUD operations for each entity:
- Insert → Get → List → Delete

Each test is parameterized with data from `test_data.json`, allowing comprehensive coverage with minimal code.

## Test Files

### Main Test Suites

- **test_a2a_daemon_engine.py** - **Lifecycle flow tests** (NEW PATTERN):
  - GraphQL ping test
  - Agent lifecycle: Insert → Get → List → Delete (2 parameterized tests)
  - Task lifecycle: Insert → Get → List → Delete (2 parameterized tests)
  - Message lifecycle: Insert → Get → List → Delete (2 parameterized tests)
  - Setting lifecycle: Insert → Get → List → Delete (2 parameterized tests)
  - **Total: 9 tests** covering all 4 models following ai_agent_core_engine pattern

- **test_serverless.py** - Integration tests for serverless/Lambda environments:
  - Agent registration and handshake
  - Task assignment and execution
  - Message routing
  - Error handling

- **test_a2a_mock_actions.py** - Mock-based action tests
- **test_helpers.py** - Helper functions (`call_method()`, `@log_test_result`)

### Configuration Files

- **conftest.py** - Pytest fixtures and configuration
- **pytest.ini** - Pytest settings
- **.env.example** - Example environment variables
- **test_data.json** - Test data samples

## Test Markers

Tests are organized with pytest markers for flexible execution:

- `unit` - Unit tests (no external dependencies)
- `integration` - Integration tests (DB, API)
- `slow` - Tests taking significant time
- `a2a` - A2A protocol tests
- `agent` - Agent-related tests
- `task` - Task handling tests
- `message` - Message routing tests
- `server` - A2A server tests
- `graphql` - GraphQL schema tests
- `cache` - Cache configuration tests

## Running Tests

### Basic Test Execution

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_serverless.py -v
pytest tests/test_a2a_daemon_engine.py -v

# Run with coverage
pytest --cov=a2a_daemon_engine --cov-report=html
```

### Run Tests by Marker

```bash
# Run only unit tests
pytest -m unit

# Run only GraphQL and cache tests
pytest -m "graphql or cache"

# Run agent-related tests
pytest -m agent

# Run integration tests
pytest -m integration
```

### Run Specific Tests

```bash
# Run specific test function
pytest --test-function test_ping_query

# Run tests matching markers
pytest --test-markers "unit,graphql"

# Using environment variables
export AI_A2A_TEST_FUNCTION=test_ping_query
pytest

export AI_A2A_TEST_MARKERS="graphql,cache"
pytest
```

## Test Coverage

The test suite includes **38+ comprehensive tests** (with parameterization) in test_a2a_daemon_engine.py:

### GraphQL Schema Execution Tests (23+ parameterized tests)
All tests execute actual GraphQL queries using dynamically generated queries from schema via `Graphql.generate_graphql_operation()`.
Tests are **parameterized with data from test_data.json** to test multiple scenarios.

**Query Tests (12 tests):**
- ✅ Ping query functionality (1 test)
- ✅ A2AAgent query - **parameterized with 2 agents** from test_data.json (2 tests)
- ✅ A2AAgentList query with pagination (1 test)
- ✅ A2ATask query - **parameterized with 2 tasks** from test_data.json (2 tests)
- ✅ A2ATaskList query with pagination (1 test)
- ✅ A2AMessage query - **parameterized with 2 messages** from test_data.json (2 tests)
- ✅ A2AMessageList query with pagination (1 test)
- ✅ A2ASetting query (1 test)
- ✅ A2ASettingList query with pagination (1 test)

**Mutation Tests (11 tests):**
- ✅ InsertUpdateA2aAgent mutation - **parameterized with 2 agents** from test_data.json (2 tests)
- ✅ DeleteA2aAgent mutation (1 test)
- ✅ InsertUpdateA2aTask mutation - **parameterized with 2 tasks** from test_data.json (2 tests)
- ✅ DeleteA2aTask mutation (1 test)
- ✅ InsertUpdateA2aMessage mutation - **parameterized with 2 messages** from test_data.json (2 tests)
- ✅ DeleteA2aMessage mutation (1 test)
- ✅ InsertUpdateA2aSetting mutation (1 test)
- ✅ DeleteA2aSetting mutation (1 test)

**Key Features:**
- All GraphQL queries and mutations are dynamically generated using `Graphql.generate_graphql_operation()` following the MCP pattern
- Test data is loaded from [test_data.json](test_data.json) and used for parameterization
- Each parameterized test runs with actual data from the JSON file, testing multiple scenarios automatically

### Cache Configuration Tests (4 tests)
- ✅ Entity configuration for all models
- ✅ Cache relationships between entities (agent→task, agent→message, task→message)
- ✅ Entity children resolution
- ✅ Cache configuration methods (TTL, enabled, names)

### Utility Tests (11 tests)
- ✅ Cache purge function exists
- ✅ Initialize tables function exists
- ✅ All models importable
- ✅ All types importable
- ✅ All query modules importable
- ✅ All mutation modules importable
- ✅ All model helper functions exist
- ✅ Agent model table name validation
- ✅ Task model indexes validation (TaskStatusIndex, TaskPriorityIndex)
- ✅ Message model index validation (MessageStatusIndex)
- ✅ Setting model table name validation

### Integration Tests (test_serverless.py)
- ✅ Agent registration and handshake
- ✅ Task assignment
- ✅ Message routing
- ✅ Task execution
- ✅ Error handling (missing action, invalid action)

## Environment Setup

Create a `.env` file based on `.env.example`:

```bash
cp .env.example .env
```

Edit `.env` with your test configuration:

```env
# AWS Configuration
region_name=us-east-1
aws_access_key_id=test-key
aws_secret_access_key=test-secret

# A2A Configuration
endpoint_id=test-endpoint
part_id=test-part
transport=http
port=8001

# Database
initialize_tables=0
```

## Sample Test Data

The `test_data.json` file contains sample data for testing. You can load sample data using:

```bash
python load_sample_data.py
```

## Continuous Integration

Tests are designed to run in CI/CD pipelines:

```yaml
# Example GitHub Actions workflow
- name: Run tests
  run: |
    pytest -v --cov=a2a_daemon_engine

- name: Run unit tests only
  run: |
    pytest -m unit -v
```

## Writing New Tests

When adding new tests:

1. Use appropriate markers
2. Follow the existing naming conventions
3. Add docstrings explaining what the test does
4. Update this README with new test coverage

Example:

```python
@pytest.mark.unit
@pytest.mark.agent
def test_new_agent_feature():
    """Test description here."""
    # Test implementation
    pass
```

## Troubleshooting

### Common Issues

**Import errors:**
```bash
# Ensure you're in the correct directory
cd a2a_daemon_engine
pytest tests/
```

**Missing dependencies:**
```bash
pip install -r requirements.txt
pip install pytest pytest-cov python-dotenv
```

**DynamoDB connection errors:**
- Check `.env` file configuration
- Ensure AWS credentials are valid
- Set `initialize_tables=0` for unit tests

## Test Results

After running tests with coverage:

```bash
pytest --cov=a2a_daemon_engine --cov-report=html
```

View HTML coverage report:
```bash
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
start htmlcov/index.html  # Windows
```
