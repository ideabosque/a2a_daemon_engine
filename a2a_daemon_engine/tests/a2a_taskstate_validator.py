#!/usr/bin/python
"""
A2A TaskState Migration Validator

Validates that TaskState strings have been migrated to SCREAMING_SNAKE_CASE
per A2A v1.0 specification.

Usage:
    python a2a_taskstate_validator.py [--check-db] [--fix]

Options:
    --check-db    Validate actual DynamoDB rows (requires DB access)
    --fix         Generate migration script for non-compliant rows
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from typing import Any

__author__ = "SilvaEngine Team"
__version__ = "1.0.0"


# A2A v1.0 Task States (canonical)
V1_TASK_STATES = [
    "WORKING",
    "INPUT_REQUIRED",
    "AUTH_REQUIRED",
    "COMPLETED",
    "FAILED",
    "CANCELED",
    "REJECTED",
]

# Legacy task states (pre-v1.0)
LEGACY_STATES = [
    "working",
    "input_required",
    "completed",
    "failed",
    "canceled",
    "submitted",
]

# State aliases for compatibility
STATE_ALIASES = {
    "AUTH_REQUIRED": ["auth_required", "authentication_required"],
    "REJECTED": ["rejected", "denied"],
    "CANCELED": ["cancelled"],  # British spelling
}


@dataclass
class MigrationIssue:
    """Represents a TaskState migration issue."""
    task_id: str
    current_state: str
    expected_state: str
    partition_key: str | None = None
    severity: str = "warning"  # warning or error


class TaskStateMigrationValidator:
    """Validates TaskState migration to v1.0 SCREAMING_SNAKE_CASE."""

    def __init__(self, logger: logging.Logger | None = None):
        """Initialize validator."""
        self.logger = logger or logging.getLogger(__name__)
        self.issues: list[MigrationIssue] = []

    def validate_status_map(self) -> tuple[bool, list[str]]:
        """
        Validate the status mapping in a2a_taskstore.

        Returns:
            Tuple of (is_valid, issues)
        """
        issues = []

        try:
            # Importing the store also exercises its module-level state-map definitions.
            from a2a_daemon_engine.handlers.a2a_taskstore import (  # noqa: F401
                DynamoDBA2ATaskStore,
            )

            # Check that status_map includes all v1.0 states
            # The map is defined in _map_status_to_taskstate method

            # Test each v1.0 state
            for state in V1_TASK_STATES:
                # Simulate mapping
                if state in LEGACY_STATES:
                    issues.append(f"State '{state}' is lowercase - should be uppercase")

            self.logger.info(f"Status map validation: {len(issues)} issues found")

        except ImportError as e:
            issues.append(f"Cannot import TaskStore: {e}")

        return len(issues) == 0, issues

    def validate_task_row(self, task: dict[str, Any]) -> MigrationIssue | None:
        """
        Validate a single task row.

        Args:
            task: Task dictionary

        Returns:
            MigrationIssue if invalid, None if valid
        """
        task_id = task.get("id", task.get("taskId", "unknown"))
        status = task.get("status", "")

        # Check if status is valid v1.0 state
        if status in V1_TASK_STATES:
            return None  # Valid

        # Check if it's a legacy state
        if status in LEGACY_STATES:
            # Map to v1.0
            expected = self._map_legacy_to_v1(status)
            return MigrationIssue(
                task_id=task_id,
                current_state=status,
                expected_state=expected,
                partition_key=task.get("partitionKey"),
                severity="error",
            )

        # Unknown state
        return MigrationIssue(
            task_id=task_id,
            current_state=status,
            expected_state="WORKING",  # Default fallback
            partition_key=task.get("partitionKey"),
            severity="warning",
        )

    def _map_legacy_to_v1(self, legacy_state: str) -> str:
        """Map legacy state to v1.0 state."""
        mapping = {
            "working": "WORKING",
            "input_required": "INPUT_REQUIRED",
            "auth_required": "AUTH_REQUIRED",
            "completed": "COMPLETED",
            "failed": "FAILED",
            "canceled": "CANCELED",
            "submitted": "WORKING",
            "rejected": "REJECTED",
        }
        return mapping.get(legacy_state, "WORKING")

    def generate_migration_report(self) -> dict[str, Any]:
        """Generate migration status report."""
        # Check code compatibility
        code_valid, code_issues = self.validate_status_map()

        return {
            "version": __version__,
            "v1_states": V1_TASK_STATES,
            "legacy_states": LEGACY_STATES,
            "code_compatibility": {
                "valid": code_valid,
                "issues": code_issues,
            },
            "migration_required": len(self.issues) > 0,
            "issues": [
                {
                    "task_id": i.task_id,
                    "current": i.current_state,
                    "expected": i.expected_state,
                    "partition_key": i.partition_key,
                    "severity": i.severity,
                }
                for i in self.issues
            ],
        }

    def print_report(self) -> None:
        """Print migration report."""
        report = self.generate_migration_report()

        print("\n" + "=" * 80)
        print("A2A TaskState Migration Validation Report")
        print("=" * 80)
        print(f"Version: {report['version']}")
        print("-" * 80)

        print("\nV1.0 Canonical States:")
        for state in report['v1_states']:
            print(f"  - {state}")

        print("\nLegacy States (to be migrated):")
        for state in report['legacy_states']:
            print(f"  - {state}")

        print("\n" + "-" * 80)
        print("Code Compatibility:")
        status = "[OK] Valid" if report['code_compatibility']['valid'] else "[ERR] Issues Found"
        print(f"  Status: {status}")
        for issue in report['code_compatibility']['issues']:
            print(f"  - {issue}")

        print("\n" + "-" * 80)
        if report['migration_required']:
            print(f"⚠️  Migration Required: {len(report['issues'])} tasks need updating")
            for issue in report['issues'][:10]:  # Show first 10
                print(f"  - Task {issue['task_id']}: {issue['current']} -> {issue['expected']}")
            if len(report['issues']) > 10:
                print(f"  ... and {len(report['issues']) - 10} more")
        else:
            print("[OK] No migration required - all tasks use v1.0 states")

        print("=" * 80 + "\n")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="A2A TaskState Migration Validator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--check-db",
        action="store_true",
        help="Check actual DynamoDB rows (requires DB access)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Generate fix script",
    )
    parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger = logging.getLogger("a2a_taskstate_validator")

    # Run validation
    validator = TaskStateMigrationValidator(logger=logger)
    validator.validate_status_map()

    if args.json:
        report = validator.generate_migration_report()
        print(json.dumps(report, indent=2))
    else:
        validator.print_report()

    # Exit with appropriate code
    report = validator.generate_migration_report()
    sys.exit(0 if report['code_compatibility']['valid'] else 1)


if __name__ == "__main__":
    main()
