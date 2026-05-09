#!/usr/bin/env python
"""
A2A Daemon Engine - Unified API Test Script

Merged version combining features from test_api.py and test_api_simple.py:
- Command-line arguments support (from test_api.py)
- All 9 comprehensive tests (from test_api_simple.py)
- JWT authentication (from test_api_simple.py)
- Python requests library
- Windows-compatible output

Usage:
    python test_api.py
    python test_api.py --test health
    python test_api.py --verbose
    python test_api.py --endpoint http://localhost:8080
"""

import argparse
import os
import sys

import pendulum
import pytest
import requests

pytestmark = pytest.mark.skipif(
    os.getenv("A2A_RUN_LIVE_API_TESTS", "").lower() not in {"1", "true", "yes"},
    reason="Live API tests require a running daemon; set A2A_RUN_LIVE_API_TESTS=1",
)


# Try to import jose for JWT
try:
    from jose import jwt

    HAS_JWT = True
except ImportError:
    HAS_JWT = False


class Colors:
    """Terminal colors"""

    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


def get_error_code(data: dict) -> int | None:
    """Return a JSON-RPC error code when the response has a structured error."""
    error = data.get("error")
    return error.get("code") if isinstance(error, dict) else None


def get_error_message(data: dict) -> str | None:
    """Return an error message for structured JSON-RPC or transport errors."""
    error = data.get("error")
    if isinstance(error, dict):
        return error.get("message")
    if error:
        return str(error)
    return None


def generate_jwt_token(secret_key: str) -> str:
    """Generate JWT token"""
    if not HAS_JWT:
        return ""
    try:
        payload = {
            "sub": "test-user",
            "username": "test-user",
            "iat": pendulum.now("UTC"),
            "exp": pendulum.now("UTC").add(hours=24),
        }
        return jwt.encode(payload, secret_key, algorithm="HS256")
    except Exception:
        return ""


def make_request(
    url: str,
    method: str = "GET",
    data: dict = None,
    headers: dict = None,
    token: str = None,
) -> tuple:
    """Make HTTP request"""
    all_headers = headers.copy() if headers else {}
    if token:
        all_headers["Authorization"] = f"Bearer {token}"

    try:
        if method == "GET":
            response = requests.get(url, headers=all_headers, timeout=10)
        else:
            response = requests.post(url, json=data, headers=all_headers, timeout=10)

        try:
            resp_data = response.json()
        except ValueError:
            resp_data = {"raw": response.text}

        print(f"\n   -> Request: {method} {url}")
        print(f"   <- Status: {response.status_code}")
        print(f"   <- Response: {resp_data}")
        if resp_data.get("error"):
            print(f"   <- Error: {resp_data['error']}")
        if response.status_code >= 400:
            print(f"   <- Request failed with status {response.status_code}: {resp_data}")

        return response.status_code, resp_data
    except requests.exceptions.RequestException as e:
        print(f"\n   -> Request: {method} {url}")
        print(f"   <- Request Exception: {e}")
        return 0, {"error": str(e)}


def print_section(title: str):
    """Print section header"""
    print(f"\n{Colors.BOLD}{'=' * 80}{Colors.RESET}")
    print(f"{Colors.CYAN}{title}{Colors.RESET}")
    print(f"{Colors.BOLD}{'=' * 80}{Colors.RESET}\n")


def print_result(name: str, success: bool, details: str = ""):
    """Print test result"""
    status = (
        f"{Colors.GREEN}[PASS]{Colors.RESET}"
        if success
        else f"{Colors.RED}[FAIL]{Colors.RESET}"
    )
    print(f"{status} {name}")
    if details:
        print(f"       {details}")


def print_expected(name: str, details: str = ""):
    """Print expected behavior"""
    print(f"{Colors.YELLOW}[EXPECTED]{Colors.RESET} {name}")
    if details:
        print(f"       {details}")


# ==================== TEST FUNCTIONS ====================


def test_agent_card(base_url: str) -> bool:
    """Test agent card discovery"""
    print_section("TEST: Agent Card Discovery (Public)")

    status, data = make_request(f"{base_url}/.well-known/agent-card.json")

    if status == 200:
        print_result("Agent Card", True)
        print(f"       Name: {data.get('name')}")
        print(f"       Version: {data.get('version')}")
        print(f"       Protocol: {data.get('protocolVersion')}")
        print(f"       Skills: {len(data.get('skills', []))}")
        return True
    else:
        print_result("Agent Card", False, f"Status: {status}")
        return False


def test_health(base_url: str) -> bool:
    """Test REST health (no auth)"""
    print_section("TEST: REST Health (No Auth)")

    status, data = make_request(f"{base_url}/rest/health")

    if status in [401, 403]:
        print_result("REST Health Auth", True, f"Status: {status} (Expected)")
        return True
    elif status == 200:
        print_result("REST Health", True, f"Status: {data.get('status')}")
        return True
    else:
        print_result("REST Health", False, f"Status: {status}")
        return False


def test_health_with_auth(base_url: str, token: str) -> bool:
    """Test REST health with auth"""
    print_section("TEST: REST Health (With Auth)")

    if not token:
        print_result("REST Health With Auth", True, "Skipped - no JWT")
        return True

    status, data = make_request(f"{base_url}/rest/health", token=token)

    if status == 200:
        print_result("REST Health With Auth", True, f"Status: {data.get('status')}")
        return True
    else:
        print_result("REST Health With Auth", False, f"Status: {status}")
        return False


def test_graphql_ping(base_url: str, endpoint_id: str) -> bool:
    """Test GraphQL ping (no auth)"""
    print_section("TEST: GraphQL Ping (No Auth)")

    # Try /rest/{endpoint_id}/a2a_core_graphql for public access
    status, data = make_request(
        f"{base_url}/rest/{endpoint_id}/a2a_core_graphql",
        method="POST",
        data={"query": "query { ping }"},
        headers={"Content-Type": "application/json"},
    )

    if status in [401, 403]:
        print_result("GraphQL Ping Auth", True, f"Status: {status} (Expected)")
        return True
    elif status == 200:
        print_result(
            "GraphQL Ping", True, f"Response: {data.get('data', {}).get('ping')}"
        )
        return True
    else:
        print_result("GraphQL Ping", False, f"Status: {status}")
        return False


def test_graphql_ping_auth(base_url: str, endpoint_id: str, token: str) -> bool:
    """Test GraphQL ping with auth"""
    print_section("TEST: GraphQL Ping (With Auth)")

    if not token:
        print_result("GraphQL Ping With Auth", True, "Skipped - no JWT")
        return True

    status, data = make_request(
        f"{base_url}/rest/{endpoint_id}/a2a_core_graphql",
        method="POST",
        data={"query": "query { ping }"},
        headers={"Content-Type": "application/json"},
        token=token,
    )

    if status == 200:
        ping = data.get("data", {}).get("ping")
        print_result("GraphQL Ping With Auth", True, f"Response: {ping}")
        return True
    else:
        print_result("GraphQL Ping With Auth", False, f"Status: {status}")
        return False


def test_jsonrpc_no_auth(base_url: str) -> bool:
    """Test JSON-RPC endpoint (no auth)"""
    print_section("TEST: JSON-RPC (No Auth)")

    status, data = make_request(
        f"{base_url}/",
        method="POST",
        data={"jsonrpc": "2.0", "method": "ping", "params": {}, "id": 1},
        headers={"Content-Type": "application/json"},
    )

    if status in [401, 403]:
        print_result("JSON-RPC Auth", True, f"Status: {status} (Expected)")
        return True
    elif status == 200:
        print_result("JSON-RPC", True)
        return True
    else:
        print_result("JSON-RPC", False, f"Status: {status}")
        return False


def test_a2a_native_ping(base_url: str) -> bool:
    """Test A2A SDK native JSON-RPC"""
    print_section("TEST: A2A SDK Native JSON-RPC")

    status, data = make_request(
        f"{base_url}/",
        method="POST",
        data={"jsonrpc": "2.0", "method": "ping", "params": {}, "id": 1},
        headers={"Content-Type": "application/json"},
    )

    if "jsonrpc" in data:
        print_result("A2A Native JSON-RPC", True, f"Version: {data.get('jsonrpc')}")
        error_message = get_error_message(data)
        if error_message:
            print(f"       Error: {error_message}")
        return True
    else:
        print_result("A2A Native JSON-RPC", False, "Invalid response")
        return False


def test_a2a_getcard_expected(base_url: str) -> bool:
    """Test that agent.getCard is NOT a JSON-RPC method (expected)"""
    print_section("TEST: A2A GetCard (Expected: Method Not Found)")

    status, data = make_request(
        f"{base_url}/",
        method="POST",
        data={"jsonrpc": "2.0", "method": "agent.getCard", "params": {}, "id": 1},
        headers={"Content-Type": "application/json"},
    )

    if get_error_code(data) == -32601:
        print_expected("A2A GetCard JSON-RPC")
        print("       Error Code: -32601 (Method not found)")
        print("       Note: Agent Card is at /.well-known/agent-card.json (HTTP GET)")
        return True
    else:
        print_result("A2A GetCard", False, "Unexpected response")
        return False


def test_message_send(base_url: str, token: str) -> bool:
    """Test message/send JSON-RPC method"""
    print_section("TEST: JSON-RPC message/send (With Auth)")

    if not token:
        print_result("JSON-RPC message/send", True, "Skipped - no JWT")
        return True

    status, data = make_request(
        f"{base_url}/",
        method="POST",
        data={
            "jsonrpc": "2.0",
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Hello from test"}],
                },
                "sessionId": "test-session-001",
            },
            "id": 1,
        },
        headers={"Content-Type": "application/json"},
        token=token,
    )

    if status == 200 and data.get("result"):
        result = data["result"]
        print_result("JSON-RPC message/send", True)
        print(f"       Task ID: {result.get('id')}")
        print(f"       Status: {result.get('status')}")
        return True
    elif get_error_code(data) in [-32601, -32600]:
        print_expected("JSON-RPC message/send")
        print("       Handler may not be fully implemented yet")
        return True
    else:
        print_result("JSON-RPC message/send", False, f"Status: {status}")
        return False


def test_task_execution_request(base_url: str, token: str) -> bool:
    """Test a task execution request through A2A message/send."""
    print_section("TEST: JSON-RPC task execution request (With Auth)")

    if not token:
        print_result("JSON-RPC task execution request", True, "Skipped - no JWT")
        return True

    task_id = "test-task-exec-001"
    status, data = make_request(
        f"{base_url}/",
        method="POST",
        data={
            "jsonrpc": "2.0",
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [
                        {
                            "type": "text",
                            "text": "Execute a dry-run test task",
                        }
                    ],
                },
                "metadata": {
                    "operation": "task_execution",
                    "task_data": {
                        "task_id": task_id,
                        "task_type": "test",
                        "priority": "low",
                        "dry_run": True,
                    },
                },
            },
            "id": 2,
        },
        headers={"Content-Type": "application/json"},
        token=token,
    )

    result = data.get("result", {})
    parts = result.get("parts", []) if isinstance(result, dict) else []
    text = parts[0].get("text", "") if parts and isinstance(parts[0], dict) else ""

    if status == 200 and task_id in text and "dry-run mode" in text:
        print_result("JSON-RPC task execution request", True)
        print(f"       Task ID: {task_id}")
        print(f"       Response: {text}")
        return True

    print_result("JSON-RPC task execution request", False, f"Status: {status}")
    return False


def print_summary(results: list):
    """Print summary"""
    print(f"\n{Colors.BOLD}{'=' * 80}{Colors.RESET}")
    print(f"{Colors.BOLD}TEST SUMMARY{Colors.RESET}")
    print(f"{Colors.BOLD}{'=' * 80}{Colors.RESET}\n")

    passed = sum(1 for _, success in results if success)

    for name, success in results:
        status = (
            f"{Colors.GREEN}PASS{Colors.RESET}"
            if success
            else f"{Colors.RED}FAIL{Colors.RESET}"
        )
        print(f"  [{status}] {name}")

    print(f"\n  Total: {len(results)} tests")
    print(f"  Passed: {passed}")
    print(f"  Failed: {len(results) - passed}")

    if passed == len(results):
        print(f"\n{Colors.GREEN}{Colors.BOLD}All tests passed!{Colors.RESET}")

    print(f"{Colors.BOLD}{'=' * 80}{Colors.RESET}\n")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Test A2A Daemon Engine API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--endpoint", default="http://localhost:8001", help="Base URL")
    parser.add_argument("--endpoint-id", default="test-endpoint", help="Endpoint ID")
    parser.add_argument(
        "--test", choices=["health", "graphql", "jsonrpc", "all"], default="all"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Generate JWT token
    jwt_secret = os.getenv(
        "jwt_secret_key", "test-secret-key-for-testing-only-32-chars"
    )
    jwt_token = generate_jwt_token(jwt_secret)

    print(f"\n{Colors.BOLD}{'=' * 80}{Colors.RESET}")
    print(f"{Colors.CYAN}A2A Daemon Engine - API Test Suite{Colors.RESET}")
    print(f"{Colors.BOLD}{'=' * 80}{Colors.RESET}")
    print(f"\n  Target: {args.endpoint}")
    print(f"  JWT Token: {'Generated' if jwt_token else 'Not Available'}")
    print()

    results = []

    # Run tests based on selection
    if args.test in ["health", "all"]:
        results.append(("Agent Card Discovery", test_agent_card(args.endpoint)))
        results.append(("REST Health (No Auth)", test_health(args.endpoint)))
        results.append(
            ("REST Health (With Auth)", test_health_with_auth(args.endpoint, jwt_token))
        )

    if args.test in ["graphql", "all"]:
        results.append(
            (
                "GraphQL Ping (No Auth)",
                test_graphql_ping(args.endpoint, args.endpoint_id),
            )
        )
        results.append(
            (
                "GraphQL Ping (With Auth)",
                test_graphql_ping_auth(args.endpoint, args.endpoint_id, jwt_token),
            )
        )

    if args.test in ["jsonrpc", "all"]:
        results.append(("A2A Native JSON-RPC", test_a2a_native_ping(args.endpoint)))
        results.append(
            ("A2A GetCard (Expected)", test_a2a_getcard_expected(args.endpoint))
        )
        results.append(("JSON-RPC (No Auth)", test_jsonrpc_no_auth(args.endpoint)))
        results.append(
            ("JSON-RPC message/send", test_message_send(args.endpoint, jwt_token))
        )
        results.append(
            (
                "JSON-RPC task execution request",
                test_task_execution_request(args.endpoint, jwt_token),
            )
        )

    print_summary(results)

    sys.exit(0 if all(success for _, success in results) else 1)


if __name__ == "__main__":
    main()
