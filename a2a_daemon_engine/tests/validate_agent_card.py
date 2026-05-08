#!/usr/bin/env python
"""
Agent Card Validator

Validates the A2A Daemon Engine Agent Card against A2A Inspector standards.
This script checks compliance with A2A v1.0 Agent Card schema requirements.
"""

import json
import sys
from typing import Any


class AgentCardValidator:
    """Validator for A2A Agent Card compliance."""

    REQUIRED_FIELDS = [
        "name",
        "version",
        "url",
        "capabilities",
        "skills",
        "defaultInputModes",
        "defaultOutputModes",
    ]

    OPTIONAL_BUT_RECOMMENDED_FIELDS = [
        "description",
        "protocolVersion",
        "provider",
        "iconUrl",
        "securitySchemes",
        "extensions",
        "supportedInterfaces",
    ]

    VALID_PROTOCOL_VERSIONS = ["0.3.0", "1.0.0"]

    VALID_INPUT_OUTPUT_MODES = [
        "text",
        "image",
        "audio",
        "video",
        "file",
        "json",
    ]

    def __init__(self, agent_card: dict[str, Any]):
        self.agent_card = agent_card
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.info: list[str] = []

    def validate(self) -> tuple[bool, list[str], list[str], list[str]]:
        """
        Run full validation of the Agent Card.

        Returns:
            Tuple of (is_valid, errors, warnings, info)
        """
        self._validate_required_fields()
        self._validate_capabilities()
        self._validate_skills()
        self._validate_input_output_modes()
        self._validate_protocol_version()
        self._validate_provider()
        self._validate_url()
        self._check_recommended_fields()
        self._check_v1_0_compliance()

        is_valid = len(self.errors) == 0
        return is_valid, self.errors, self.warnings, self.info

    def _validate_required_fields(self):
        """Check all required fields are present."""
        for field in self.REQUIRED_FIELDS:
            if field not in self.agent_card:
                self.errors.append(f"Missing required field: '{field}'")
            elif self.agent_card[field] is None or self.agent_card[field] == "":
                self.errors.append(f"Required field '{field}' is empty")

    def _validate_capabilities(self):
        """Validate capabilities structure."""
        if "capabilities" not in self.agent_card:
            return

        caps = self.agent_card["capabilities"]

        if not isinstance(caps, dict):
            self.errors.append("'capabilities' must be a dictionary")
            return

        # Check known capability fields
        valid_capabilities = [
            "streaming",
            "pushNotifications",
            "stateMachine",
        ]

        for key in caps.keys():
            if key not in valid_capabilities:
                self.warnings.append(f"Unknown capability: '{key}'")

        # Validate boolean values
        for key, value in caps.items():
            if not isinstance(value, bool):
                self.errors.append(f"Capability '{key}' must be boolean")

    def _validate_skills(self):
        """Validate skills array."""
        if "skills" not in self.agent_card:
            return

        skills = self.agent_card["skills"]

        if not isinstance(skills, list):
            self.errors.append("'skills' must be an array")
            return

        if len(skills) == 0:
            self.warnings.append("No skills declared - agent may not be discoverable")

        for i, skill in enumerate(skills):
            if not isinstance(skill, dict):
                self.errors.append(f"Skill {i} must be a dictionary")
                continue

            # Check required skill fields
            if "id" not in skill:
                self.warnings.append(f"Skill {i} missing 'id' field")
            if "name" not in skill:
                self.errors.append(f"Skill {i} missing required 'name' field")

    def _validate_input_output_modes(self):
        """Validate input/output modes."""
        for field in ["defaultInputModes", "defaultOutputModes"]:
            if field not in self.agent_card:
                continue

            modes = self.agent_card[field]

            if not isinstance(modes, list):
                self.errors.append(f"'{field}' must be an array")
                continue

            if len(modes) == 0:
                self.warnings.append(f"'{field}' is empty - agent may have limited functionality")

            for mode in modes:
                if mode not in self.VALID_INPUT_OUTPUT_MODES:
                    self.warnings.append(f"Unknown mode '{mode}' in {field}")

    def _validate_protocol_version(self):
        """Validate protocol version."""
        if "protocolVersion" not in self.agent_card:
            self.warnings.append("Missing 'protocolVersion' - will default to implementation-defined version")
            return

        version = self.agent_card["protocolVersion"]

        if version not in self.VALID_PROTOCOL_VERSIONS:
            self.warnings.append(f"Unrecognized protocol version: '{version}'")

    def _validate_provider(self):
        """Validate provider information."""
        if "provider" not in self.agent_card:
            self.info.append("No provider information - consider adding for discoverability")
            return

        provider = self.agent_card["provider"]

        if not isinstance(provider, dict):
            self.errors.append("'provider' must be a dictionary")
            return

        if "organization" not in provider:
            self.warnings.append("Provider missing 'organization' field")

    def _validate_url(self):
        """Validate URL format."""
        if "url" not in self.agent_card:
            return

        url = self.agent_card["url"]

        if not url.startswith("http://") and not url.startswith("https://"):
            self.warnings.append(f"URL should use http:// or https://: '{url}'")

        if not url.endswith("/"):
            self.info.append("URL does not end with '/' - ensure endpoints are constructed correctly")

    def _check_recommended_fields(self):
        """Check for recommended but optional fields."""
        for field in self.OPTIONAL_BUT_RECOMMENDED_FIELDS:
            if field not in self.agent_card:
                if field in ["description", "provider"]:
                    self.info.append(f"Consider adding '{field}' for better discoverability")

    def _check_v1_0_compliance(self):
        """Check A2A v1.0 specific requirements."""
        # Check for extensions declaration
        if "extensions" not in self.agent_card:
            self.info.append("No extensions declared - fine for basic agents")

        # Check for security schemes
        if "securitySchemes" not in self.agent_card:
            self.warnings.append("No securitySchemes declared - agent may not be accessible to authenticated clients")

        # Check supportsAuthenticatedExtendedCard
        if "supportsAuthenticatedExtendedCard" in self.agent_card:
            value = self.agent_card["supportsAuthenticatedExtendedCard"]
            if not isinstance(value, bool):
                self.errors.append("'supportsAuthenticatedExtendedCard' must be boolean")


def validate_agent_card_file(filepath: str) -> bool:
    """Validate an Agent Card from a JSON file."""
    try:
        with open(filepath) as f:
            agent_card = json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] File not found: {filepath}")
        return False
    except json.JSONDecodeError as e:
        print(f"[ERROR] Invalid JSON: {e}")
        return False

    return validate_agent_card(agent_card)


def validate_agent_card(agent_card: dict[str, Any]) -> bool:
    """Validate an Agent Card dictionary."""
    validator = AgentCardValidator(agent_card)
    is_valid, errors, warnings, info = validator.validate()

    print("=" * 80)
    print("A2A Agent Card Validation Report")
    print("=" * 80)
    print()

    # Print summary
    if is_valid:
        print("[PASS] Agent Card is VALID")
    else:
        print("[FAIL] Agent Card has ERRORS")
    print()

    # Print errors
    if errors:
        print(f"ERRORS ({len(errors)}):")
        for error in errors:
            print(f"  [ERROR] {error}")
        print()

    # Print warnings
    if warnings:
        print(f"WARNINGS ({len(warnings)}):")
        for warning in warnings:
            print(f"  [WARN] {warning}")
        print()

    # Print info
    if info:
        print(f"INFO ({len(info)}):")
        for item in info:
            print(f"  [INFO] {item}")
        print()

    # Print compliance level
    print("-" * 80)
    if is_valid and len(warnings) == 0:
        print("[PASS] Full A2A v1.0 Compliance")
    elif is_valid:
        print("[PASS] Valid with warnings (functional but not fully compliant)")
    else:
        print("[FAIL] Not compliant - fix errors before deployment")
    print("=" * 80)

    return is_valid


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Validate A2A Agent Card")
    parser.add_argument(
        "--file", "-f", help="Path to Agent Card JSON file"
    )
    parser.add_argument(
        "--test-current",
        action="store_true",
        help="Test current A2A Daemon Engine Agent Card",
    )

    args = parser.parse_args()

    if args.test_current:
        # Fetch current Agent Card from running daemon
        import requests

        try:
            response = requests.get("http://localhost:8001/.well-known/agent-card.json")
            response.raise_for_status()
            agent_card = response.json()
            print("Fetched Agent Card from http://localhost:8001/.well-known/agent-card.json")
            print()
            validate_agent_card(agent_card)
        except requests.exceptions.ConnectionError:
            print("❌ Could not connect to A2A Daemon at localhost:8001")
            print("   Make sure the daemon is running before testing.")
            sys.exit(1)
        except requests.exceptions.RequestException as e:
            print(f"❌ Error fetching Agent Card: {e}")
            sys.exit(1)

    elif args.file:
        validate_agent_card_file(args.file)

    else:
        # Run validation on a sample Agent Card
        sample_agent_card = {
            "name": "A2A Daemon Engine",
            "version": "1.0.0",
            "url": "http://localhost:8001/",
            "protocolVersion": "0.3.0",
            "description": "Agent-to-Agent protocol daemon for distributed agent communication",
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
            },
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [
                {
                    "id": "task-execution",
                    "name": "Task Execution",
                    "description": "Execute tasks assigned by other agents",
                },
                {
                    "id": "message-routing",
                    "name": "Message Routing",
                    "description": "Route messages between agents",
                },
            ],
            "supportsAuthenticatedExtendedCard": False,
            "provider": {
                "organization": "SilvaEngine",
                "url": "https://github.com/ideabosque/a2a_daemon_engine",
            },
        }

        print("Testing sample Agent Card...")
        print()
        validate_agent_card(sample_agent_card)


if __name__ == "__main__":
    main()
