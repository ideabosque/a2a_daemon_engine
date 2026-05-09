#!/usr/bin/python
"""
Load Sample A2A Data Script

This script generates fake A2A (Agent-to-Agent) data using Faker and loads it
into the system for testing purposes. It creates agents, tasks, messages, and
relationships between them.

Usage:
    python load_sample_data.py

Requirements:
    pip install faker python-dotenv
"""

__author__ = "bibow"

import json
import logging
import os
import random
import sys
import uuid

import pendulum
from dotenv import load_dotenv

# Load .env from current directory (tests folder) before setting up paths
env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(env_path)

# Get base directory from environment
BASE_DIR = os.getenv("base_dir") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)

# Add local packages to the beginning of sys.path for priority
local_paths = [
    BASE_DIR,
    os.path.join(BASE_DIR, "silvaengine_utility"),
    os.path.join(BASE_DIR, "silvaengine_constants"),
    os.path.join(BASE_DIR, "silvaengine_dynamodb_base"),
    os.path.join(BASE_DIR, "a2a_daemon_engine"),
]

# Insert at position 0 (prepend) so local packages are found first
for path in local_paths:
    if path not in sys.path:
        sys.path.insert(0, path)

# Debug: print sys.path to see the order
if os.getenv("DEBUG_PATH"):
    print("sys.path:")
    for i, p in enumerate(sys.path[:10]):
        print(f"  {i}: {p}")

from silvaengine_utility.serializer import Serializer  # noqa: E402

from a2a_daemon_engine.main import A2ADaemonEngine  # noqa: E402

try:
    from faker import Faker

    fake = Faker()
except ModuleNotFoundError:
    print(
        "The 'faker' package is not installed. Please install it by running 'pip install faker'"
    )
    exit(1)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("load_sample_data")

# --- CONFIGURATION ---
endpoint_id = os.getenv("endpoint_id", "test-endpoint")
part_id = os.getenv("part_id", "test-part")
UPDATED_BY = "data_loader_script"
TEST_DATA_FILE = os.path.join(os.path.dirname(__file__), "test_data.json")

SETTING = {
    "region_name": os.getenv("region_name", "us-east-1"),
    "aws_access_key_id": os.getenv("aws_access_key_id", "test"),
    "aws_secret_access_key": os.getenv("aws_secret_access_key", "test"),
    "endpoint_id": endpoint_id,
    "part_id": part_id,
    "transport": os.getenv("transport", "http"),
    "port": int(os.getenv("port", "8001")),
    "initialize_tables": int(os.getenv("initialize_tables", "0")),
    "jwt_secret_key": os.getenv(
        "jwt_secret_key", "sample-data-loader-secret-key-32chars"
    ),
}

# Sample data generation configuration
NUM_AGENTS = 10
NUM_TASKS_PER_AGENT = 5
NUM_MESSAGES = 20
NUM_TASK_EXECUTIONS = 15

# Agent capability options
CAPABILITY_OPTIONS = [
    "text-processing",
    "data-analysis",
    "image-processing",
    "ml-inference",
    "natural-language-understanding",
    "code-generation",
    "document-parsing",
    "translation",
    "sentiment-analysis",
    "entity-extraction",
]

# Task types
TASK_TYPES = [
    "analysis",
    "transformation",
    "validation",
    "enrichment",
    "classification",
    "summarization",
    "extraction",
    "generation",
]

# Message types
MESSAGE_TYPES = [
    "text",
    "json",
    "binary",
    "notification",
    "command",
    "query",
    "response",
]


def create_engine():
    """Instantiate A2ADaemonEngine using environment-driven settings."""
    try:
        engine = A2ADaemonEngine(logger, **SETTING)
        setattr(engine, "__is_real__", True)
        return engine
    except Exception as exc:
        logger.error(f"Failed to initialize A2ADaemonEngine: {exc}", exc_info=True)
        raise


def call_a2a_action(engine, action, **params):
    """Execute an A2A action through the engine."""
    try:
        params["action"] = action
        response = engine.a2a(**params)
        parsed = (
            Serializer.json_loads(response)
            if isinstance(response, (str, bytes))
            else response
        )
    except Exception as exc:
        logger.error(f"A2A action '{action}' failed: {exc}")
        return None

    # Handle Lambda-style response format with body field
    if "body" in parsed and isinstance(parsed["body"], str):
        try:
            body_data = Serializer.json_loads(parsed["body"])
            parsed = body_data
        except Exception:
            pass

    if parsed.get("errors"):
        logger.error(f"A2A Error: {Serializer.json_dumps(parsed['errors'])}")
        return None

    if parsed.get("status") != "success":
        logger.warning(f"A2A action '{action}' returned non-success status: {parsed}")
        return None

    logger.info(f"  -> Success: {action}")
    return parsed.get("data", parsed)


def persist_test_data(test_data_updates):
    """Override test_data.json with newly generated data."""
    # For each entity type, randomly select one entry for test data
    final_data = {}

    for key, records in test_data_updates.items():
        if not records:
            continue
        final_data[key] = records

    with open(TEST_DATA_FILE, "w") as f:
        json.dump(final_data, f, indent=2)
    logger.info(f"\nTest data written to: {TEST_DATA_FILE}")


def generate_and_load_data(engine):
    """Main function to generate and load all A2A data."""

    # --- DATA STORAGE ---
    agent_map = {}  # Maps local_id to agent_id
    task_map = {}  # Maps local_id to task_id
    test_data_updates = {
        "agents": [],
        "tasks": [],
        "messages": [],
        "task_executions": [],
    }

    # 1. Generate and Register Agents
    logger.info("--- Registering A2A Agents ---")
    local_agents = []

    for i in range(NUM_AGENTS):
        num_capabilities = random.randint(2, 5)
        capabilities = random.sample(CAPABILITY_OPTIONS, num_capabilities)

        local_agents.append(
            {
                "local_id": str(uuid.uuid4()),
                "agent_id": f"agent-{uuid.uuid4().hex[:8]}",
                "agent_name": f"{fake.company()} AI Agent",
                "capabilities": capabilities,
                "endpoint_url": f"http://{fake.domain_name()}:{random.randint(8000, 9000)}",
            }
        )

    for agent_data in local_agents:
        logger.info(f"Registering Agent: {agent_data['agent_name']}...")

        result = call_a2a_action(
            engine,
            "register_agent",
            agent_id=agent_data["agent_id"],
            agent_name=agent_data["agent_name"],
            capabilities=agent_data["capabilities"],
            endpoint_url=agent_data["endpoint_url"],
        )

        if result:
            agent_map[agent_data["local_id"]] = agent_data["agent_id"]
            logger.info(f"  -> Registered: {agent_data['agent_id']}")

            test_data_updates["agents"].append(
                {
                    "agent_id": agent_data["agent_id"],
                    "agent_name": agent_data["agent_name"],
                    "capabilities": agent_data["capabilities"],
                    "endpoint_url": agent_data["endpoint_url"],
                    "status": "active",
                }
            )

    if not agent_map:
        logger.error("No agents were registered. Exiting.")
        return

    # 2. Generate Tasks
    logger.info("\n--- Creating Tasks ---")
    local_tasks = []
    agent_ids = list(agent_map.values())

    for i in range(NUM_AGENTS * NUM_TASKS_PER_AGENT):
        task_type = random.choice(TASK_TYPES)
        assigned_agent = random.choice(agent_ids)

        local_tasks.append(
            {
                "local_id": str(uuid.uuid4()),
                "task_id": f"task-{uuid.uuid4().hex[:8]}",
                "task_type": task_type,
                "assigned_to": assigned_agent,
                "input_data": {
                    "source": fake.file_path(),
                    "parameters": {
                        "threshold": round(random.uniform(0.5, 0.95), 2),
                        "max_results": random.randint(10, 100),
                    },
                    "description": fake.sentence(),
                },
            }
        )

    for task_data in local_tasks:
        logger.info(
            f"Creating Task: {task_data['task_id']} ({task_data['task_type']}) for {task_data['assigned_to']}..."
        )

        result = call_a2a_action(
            engine,
            "assign_task",
            task_id=task_data["task_id"],
            task_type=task_data["task_type"],
            assigned_to=task_data["assigned_to"],
            input_data=task_data["input_data"],
        )

        if result:
            task_map[task_data["local_id"]] = task_data["task_id"]
            logger.info(f"  -> Created: {task_data['task_id']}")

            test_data_updates["tasks"].append(
                {
                    "task_id": task_data["task_id"],
                    "task_type": task_data["task_type"],
                    "assigned_to": task_data["assigned_to"],
                    "input_data": task_data["input_data"],
                    "status": "SUBMITTED",
                }
            )

    # 3. Generate Messages Between Agents
    logger.info("\n--- Creating Inter-Agent Messages ---")

    for i in range(NUM_MESSAGES):
        from_agent = random.choice(agent_ids)
        # Pick a different agent to send to
        to_agents = [a for a in agent_ids if a != from_agent]
        if not to_agents:
            continue
        to_agent = random.choice(to_agents)

        message_type = random.choice(MESSAGE_TYPES)

        # Generate appropriate payload based on message type
        if message_type == "json":
            payload = {
                "data": {
                    "result": fake.word(),
                    "confidence": round(random.uniform(0.7, 0.99), 2),
                    "metadata": {"timestamp": pendulum.now("UTC").to_iso8601_string()},
                }
            }
        elif message_type == "text":
            payload = {"content": fake.sentence()}
        elif message_type == "command":
            payload = {
                "command": random.choice(["start", "stop", "pause", "resume", "reset"]),
                "parameters": {"force": random.choice([True, False])},
            }
        else:
            payload = {"message": fake.text(max_nb_chars=100)}

        logger.info(
            f"Sending Message: {from_agent} -> {to_agent} (type: {message_type})..."
        )

        result = call_a2a_action(
            engine,
            "route_message",
            from_agent_id=from_agent,
            to_agent_id=to_agent,
            message_type=message_type,
            payload=payload,
        )

        if result:
            logger.info("  -> Message sent")

            test_data_updates["messages"].append(
                {
                    "from_agent_id": from_agent,
                    "to_agent_id": to_agent,
                    "message_type": message_type,
                    "payload": payload,
                    "timestamp": pendulum.now("UTC").to_iso8601_string(),
                }
            )

    # 4. Task execution now uses the A2A message/send executor path.
    logger.info("\n--- Skipping legacy execute_task action ---")

    # Persist generated data for tests
    persist_test_data(test_data_updates)


if __name__ == "__main__":
    logger.info("=== A2A Sample Data Loader ===\n")
    logger.info(f"Endpoint ID: {endpoint_id}")
    logger.info(f"Part ID: {part_id}")
    logger.info(f"Region: {SETTING['region_name']}\n")

    engine_instance = create_engine()
    generate_and_load_data(engine_instance)

    logger.info("\n--- Data Loading Complete ---")
    logger.info("Generated:")
    logger.info(f"  - {NUM_AGENTS} agents")
    logger.info(f"  - {NUM_AGENTS * NUM_TASKS_PER_AGENT} tasks")
    logger.info(f"  - {NUM_MESSAGES} messages")
    logger.info(f"  - {NUM_TASK_EXECUTIONS} task executions")
    logger.info(f"\nTest data saved to: {TEST_DATA_FILE}")
