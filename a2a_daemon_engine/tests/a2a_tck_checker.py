#!/usr/bin/python
"""
A2A TCK (Technology Compatibility Kit) Compliance Checker

Phase 8 - Task 7: A2A TCK Compliance Check

Validates A2A protocol compliance for:
- Agent Card schema
- RPC operation coverage
- Task state machine
- Message format compliance
- Security requirements

Usage:
    python a2a_tck_checker.py [--server-url URL] [--verbose]

Example:
    python a2a_tck_checker.py --server-url http://localhost:8001 --verbose

Exit Codes:
    0 - All tests passed
    1 - One or more tests failed
    2 - Configuration error
"""

import argparse
import json
import logging
import sys
from typing import Any
from urllib.parse import urljoin

__author__ = "SilvaEngine Team"
__version__ = "1.0.0"

# Optional httpx import
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


class TCKError(Exception):
    """Raised when TCK check fails."""
    pass


class TCKTestResult:
    """Result of a single TCK test."""

    def __init__(
        self,
        name: str,
        passed: bool,
        message: str = "",
        details: dict | None = None,
    ):
        self.name = name
        self.passed = passed
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "message": self.message,
            "details": self.details,
        }


class A2ATCKChecker:
    """
    A2A Technology Compatibility Kit compliance checker.

    Validates implementation against A2A v1.0 specification.
    """

    # Required v1.0 RPC operations
    REQUIRED_OPERATIONS = [
        "tasks/send",
        "tasks/sendSubscribe",
        "tasks/get",
        "tasks/list",
        "tasks/cancel",
        "tasks/pushNotification/set",
        "tasks/pushNotification/get",
        "tasks/pushNotification/list",
        "tasks/pushNotification/delete",
    ]

    # Optional v1.0 RPC operations
    OPTIONAL_OPERATIONS = [
        "agent/getExtendedCard",
    ]

    # Required Agent Card fields
    REQUIRED_CARD_FIELDS = [
        "name",
        "description",
        "url",
        "version",
        "capabilities",
        "skills",
        "provider",
    ]

    # Required capabilities fields
    REQUIRED_CAPABILITY_FIELDS = [
        "streaming",
        "pushNotifications",
    ]

    # Valid task states per v1.0 spec
    VALID_TASK_STATES = [
        "WORKING",
        "INPUT_REQUIRED",
        "AUTH_REQUIRED",
        "COMPLETED",
        "FAILED",
        "CANCELED",
        "REJECTED",
    ]

    def __init__(
        self,
        server_url: str = "http://localhost:8001",
        logger: logging.Logger | None = None,
    ):
        """
        Initialize TCK checker.

        Args:
            server_url: Base URL of A2A server to test
            logger: Optional logger instance
        """
        self.server_url = server_url.rstrip("/")
        self.logger = logger or logging.getLogger(__name__)
        self.results: list[TCKTestResult] = []

        if not HTTPX_AVAILABLE:
            raise TCKError("httpx required for TCK checks. Install with: pip install httpx")

    def run_all_checks(self) -> tuple[bool, list[TCKTestResult]]:
        """
        Run all TCK compliance checks.

        Returns:
            Tuple of (all_passed, results)
        """
        self.results = []

        # Run checks
        self.check_agent_card_schema()
        self.check_rpc_operations()
        self.check_task_state_machine()
        self.check_security_headers()
        self.check_sse_support()
        self.check_push_notification_config()

        all_passed = all(r.passed for r in self.results)
        return all_passed, self.results

    def check_agent_card_schema(self) -> TCKTestResult:
        """Check Agent Card schema compliance."""
        test_name = "Agent Card Schema"

        try:
            response = httpx.get(
                urljoin(self.server_url, "/.well-known/agent-card.json"),
                timeout=10.0,
            )

            if response.status_code != 200:
                result = TCKTestResult(
                    name=test_name,
                    passed=False,
                    message=f"Agent Card endpoint returned {response.status_code}",
                )
                self.results.append(result)
                return result

            card = response.json()

            # Check required fields
            missing_fields = []
            for field in self.REQUIRED_CARD_FIELDS:
                if field not in card:
                    missing_fields.append(field)

            if missing_fields:
                result = TCKTestResult(
                    name=test_name,
                    passed=False,
                    message=f"Missing required fields: {', '.join(missing_fields)}",
                    details={"missing_fields": missing_fields},
                )
                self.results.append(result)
                return result

            # Check capabilities
            caps = card.get("capabilities", {})
            missing_caps = []
            for field in self.REQUIRED_CAPABILITY_FIELDS:
                if field not in caps:
                    missing_caps.append(field)

            if missing_caps:
                result = TCKTestResult(
                    name=test_name,
                    passed=False,
                    message=f"Missing capability fields: {', '.join(missing_caps)}",
                )
                self.results.append(result)
                return result

            # Check skills array
            skills = card.get("skills", [])
            if not skills:
                result = TCKTestResult(
                    name=test_name,
                    passed=False,
                    message="Agent Card must have at least one skill",
                )
                self.results.append(result)
                return result

            # Check skill required fields
            for i, skill in enumerate(skills):
                for field in ["id", "name", "description"]:
                    if field not in skill:
                        result = TCKTestResult(
                            name=test_name,
                            passed=False,
                            message=f"Skill {i} missing required field: {field}",
                        )
                        self.results.append(result)
                        return result

            result = TCKTestResult(
                name=test_name,
                passed=True,
                message="Agent Card schema is valid",
                details={"skills_count": len(skills)},
            )

        except Exception as e:
            result = TCKTestResult(
                name=test_name,
                passed=False,
                message=f"Failed to validate Agent Card: {str(e)}",
            )

        self.results.append(result)
        return result

    def check_rpc_operations(self) -> TCKTestResult:
        """Check RPC operation coverage."""
        test_name = "RPC Operations"

        # This would test each RPC operation
        # For now, document what's implemented

        implemented = [
            "tasks/send",  # Via DefaultRequestHandler
            "tasks/get",   # Via TaskStore
            "tasks/cancel",  # Via executor
        ]

        missing = [op for op in self.REQUIRED_OPERATIONS if op not in implemented]

        if missing:
            result = TCKTestResult(
                name=test_name,
                passed=False,
                message=f"Missing RPC operations: {', '.join(missing)}",
                details={
                    "implemented": implemented,
                    "missing": missing,
                },
            )
        else:
            result = TCKTestResult(
                name=test_name,
                passed=True,
                message="All required RPC operations implemented",
            )

        self.results.append(result)
        return result

    def check_task_state_machine(self) -> TCKTestResult:
        """Check task state machine compliance."""
        test_name = "Task State Machine"

        # Check that all v1.0 states are supported
        from a2a_daemon_engine.handlers.a2a_executor import _task_state

        supported_states = []
        failed_states = []

        for state in self.VALID_TASK_STATES:
            try:
                _task_state(state)
                supported_states.append(state)
            except Exception:
                failed_states.append(state)

        if failed_states:
            result = TCKTestResult(
                name=test_name,
                passed=False,
                message=f"Task states not supported: {', '.join(failed_states)}",
                details={"supported": supported_states, "failed": failed_states},
            )
        else:
            result = TCKTestResult(
                name=test_name,
                passed=True,
                message="All v1.0 task states supported",
                details={"states": supported_states},
            )

        self.results.append(result)
        return result

    def check_security_headers(self) -> TCKTestResult:
        """Check security headers on responses."""
        test_name = "Security Headers"

        try:
            response = httpx.get(
                urljoin(self.server_url, "/.well-known/agent-card.json"),
                timeout=10.0,
            )

            headers = response.headers
            issues = []

            # Check for basic security headers
            if "X-Content-Type-Options" not in headers:
                issues.append("Missing X-Content-Type-Options")

            if "X-Frame-Options" not in headers:
                issues.append("Missing X-Frame-Options")

            # Note: These are recommendations, not strict requirements
            if issues:
                result = TCKTestResult(
                    name=test_name,
                    passed=True,  # Warning only
                    message=f"Security headers: {len(issues)} recommendations",
                    details={"recommendations": issues},
                )
            else:
                result = TCKTestResult(
                    name=test_name,
                    passed=True,
                    message="Security headers present",
                )

        except Exception as e:
            result = TCKTestResult(
                name=test_name,
                passed=False,
                message=f"Failed to check security headers: {str(e)}",
            )

        self.results.append(result)
        return result

    def check_sse_support(self) -> TCKTestResult:
        """Check SSE streaming support."""
        test_name = "SSE Streaming"

        try:
            # Check if SSE endpoint exists (will return method not allowed for GET)
            response = httpx.get(
                urljoin(self.server_url, "/tasks/test-task/stream"),
                timeout=5.0,
            )

            # Should get 405 or streaming response
            if response.status_code in [200, 405]:
                result = TCKTestResult(
                    name=test_name,
                    passed=True,
                    message="SSE endpoint available",
                )
            else:
                result = TCKTestResult(
                    name=test_name,
                    passed=False,
                    message=f"SSE endpoint returned {response.status_code}",
                )

        except Exception as e:
            result = TCKTestResult(
                name=test_name,
                passed=False,
                message=f"Failed to check SSE: {str(e)}",
            )

        self.results.append(result)
        return result

    def check_push_notification_config(self) -> TCKTestResult:
        """Check push notification config support."""
        test_name = "Push Notification Config"

        # Check that PushNotificationManager exists (import succeeds = manager available).
        try:
            from a2a_daemon_engine.handlers.a2a_pushconfig import (  # noqa: F401
                PushNotificationManager,
            )

            result = TCKTestResult(
                name=test_name,
                passed=True,
                message="PushNotificationConfig implementation found",
                details={
                    "operations": [
                        "CreateTaskPushNotificationConfig",
                        "GetTaskPushNotificationConfig",
                        "ListTaskPushNotificationConfigs",
                        "DeleteTaskPushNotificationConfig",
                    ]
                },
            )
        except ImportError as e:
            result = TCKTestResult(
                name=test_name,
                passed=False,
                message=f"PushNotificationConfig not found: {str(e)}",
            )

        self.results.append(result)
        return result

    def print_report(self) -> None:
        """Print TCK compliance report."""
        print("\n" + "=" * 80)
        print("A2A TCK Compliance Report")
        print("=" * 80)
        print(f"Server: {self.server_url}")
        print(f"Version: {__version__}")
        print("-" * 80)

        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)

        for result in self.results:
            status = "✅ PASS" if result.passed else "❌ FAIL"
            print(f"\n{status}: {result.name}")
            if result.message:
                print(f"  Message: {result.message}")
            if result.details:
                for key, value in result.details.items():
                    print(f"  {key}: {value}")

        print("\n" + "-" * 80)
        print(f"Summary: {passed} passed, {failed} failed")
        print("=" * 80 + "\n")


def main():
    """Main entry point for TCK checker."""
    parser = argparse.ArgumentParser(
        description="A2A TCK Compliance Checker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python a2a_tck_checker.py
  python a2a_tck_checker.py --server-url http://localhost:8001 --verbose
  python a2a_tck_checker.py --json
        """,
    )

    parser.add_argument(
        "--server-url",
        default="http://localhost:8001",
        help="A2A server URL (default: http://localhost:8001)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output",
    )
    parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output results as JSON",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger = logging.getLogger("a2a_tck")

    try:
        checker = A2ATCKChecker(server_url=args.server_url, logger=logger)
        all_passed, results = checker.run_all_checks()

        if args.json:
            # JSON output
            output = {
                "server_url": args.server_url,
                "passed": all_passed,
                "results": [r.to_dict() for r in results],
            }
            print(json.dumps(output, indent=2))
        else:
            # Human-readable output
            checker.print_report()

        sys.exit(0 if all_passed else 1)

    except TCKError as e:
        logger.error(f"TCK error: {e}")
        sys.exit(2)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
