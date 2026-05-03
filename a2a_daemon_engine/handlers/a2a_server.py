#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
A2A Protocol Server Implementation

Implements the Agent-to-Agent (A2A) protocol server using the a2a-sdk.
Based on the official A2A SDK documentation and examples:
- https://a2a-protocol.org/latest/sdk/python/api/
- https://github.com/a2aproject/a2a-python
- https://github.com/a2aproject/a2a-samples

This implementation provides:
1. A2A-compliant server with AgentCard exposure
2. Task execution through AgentExecutor pattern
3. Integration with existing DynamoDB-based handlers
4. Fallback implementation when a2a-sdk is not available
"""

import logging
from typing import Any, Dict, List, Optional

# A2A SDK is now required (version >=0.3.0 with http-server extras)
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCard,
    AgentSkill,
    AgentCapabilities,
    AgentProvider,
)


from .config import Config
from silvaengine_utility.serializer import Serializer

__author__ = "SilvaEngine Team"

# Note: Old A2ADaemonAgentExecutor class has been removed
# Now using canonical A2ADaemonExecutor from a2a_executor.py


class A2AProtocolServer:
    """
    A2A Protocol Server

    Manages agent-to-agent communication using the A2A protocol SDK.
    Provides both:
    1. A2A-compliant HTTP/REST server (via A2AStarletteApplication)
    2. Compatibility methods for existing daemon handlers

    Based on the official A2A SDK pattern:
    https://a2a-protocol.org/latest/tutorials/python/5-start-server/

    The server exposes:
    - /.well-known/agent-card.json - Agent discovery endpoint
    - JSON-RPC endpoints for A2A protocol operations
    - Integration with existing GraphQL-based storage layer
    """

    def __init__(self, logger: logging.Logger, **settings: Dict[str, Any]) -> None:
        """
        Initialize A2A Protocol Server.

        Args:
            logger: Logger instance
            **settings: Configuration settings including:
                - a2a_server_name: Server name (default: "A2A Daemon Engine")
                - a2a_server_description: Server description
                - a2a_server_url: Server base URL
                - a2a_server_version: Server version (default: "1.0.0")
                - a2a_capabilities: List of capability strings
                - port: Server port (default: 8001)
        """
        self.logger = logger
        self.settings = settings
        self.app = None
        self.agent_card = None
        self.request_handler = None
        self.task_store = None
        self.agent_executor = None

        try:
            self._initialize_a2a_server()
            self.logger.info("A2A Protocol Server initialized successfully")
        except Exception as e:
            self.logger.error(f"A2A Server initialization failed: {e}")
            self.app = None

    def _initialize_a2a_server(self) -> None:
        """
        Initialize A2A server with proper SDK components.

        Creates the complete A2A server stack:
        1. AgentCard - Describes server capabilities (exposed at /.well-known/agent-card.json)
        2. TaskStore - Manages task state (InMemoryTaskStore)
        3. AgentExecutor - Executes tasks (A2ADaemonAgentExecutor)
        4. RequestHandler - Routes A2A RPC calls (DefaultRequestHandler)
        5. A2AStarletteApplication - HTTP server implementation
        """
        # Extract configuration
        server_name = self.settings.get("a2a_server_name", "A2A Daemon Engine")
        server_description = self.settings.get(
            "a2a_server_description",
            "Agent-to-Agent protocol daemon for distributed agent communication and multi-agent orchestration",
        )
        server_url = self.settings.get(
            "a2a_server_url", f"http://localhost:{self.settings.get('port', 8001)}/"
        )
        server_version = self.settings.get("a2a_server_version", "1.0.0")
        capability_list = self.settings.get(
            "a2a_capabilities",
            ["task_execution", "message_routing", "agent_discovery"],
        )

        # Create AgentSkills from capabilities
        skills = self._create_agent_skills(capability_list)

        # Create public AgentCard (exposed at /.well-known/agent-card.json)
        self.agent_card = self._create_agent_card(
            name=server_name,
            description=server_description,
            url=server_url,
            version=server_version,
            skills=skills,
        )

        # Create DynamoDB-backed task store for persistent task management
        # Falls back to InMemoryTaskStore if partition_key not available
        from .a2a_taskstore import DynamoDBA2ATaskStore

        partition_key = self.settings.get("partition_key", "default#default")
        try:
            self.task_store = DynamoDBA2ATaskStore(
                partition_key=partition_key, logger=self.logger
            )
        except Exception as e:
            self.logger.warning(
                f"Failed to initialize DynamoDB task store: {e}. "
                "Falling back to InMemoryTaskStore."
            )
            self.task_store = InMemoryTaskStore()

        # Create agent executor using canonical A2A SDK pattern
        from .a2a_executor import A2ADaemonExecutor

        self.agent_executor = A2ADaemonExecutor(
            logger=self.logger, config=Config, task_store=self.task_store
        )

        # Create request handler - routes A2A RPC calls to executor
        self.request_handler = DefaultRequestHandler(
            agent_executor=self.agent_executor,
            task_store=self.task_store,
        )

        # Create A2A Starlette application
        # This exposes the agent at /.well-known/agent-card.json and handles JSON-RPC requests
        # TODO: INTEGRATION - Mount A2A SDK app alongside existing FastAPI app
        # Options for integration:
        # 1. Separate port: Run A2A app on different port (e.g., 8002 for A2A, 8001 for REST)
        #    - uvicorn.run(self.app, host="0.0.0.0", port=8002)
        # 2. Mount as sub-app: Add A2A routes to existing FastAPI app
        #    - In a2a_app.py: app.mount("/a2a-sdk", self.a2a_server.get_app())
        # 3. Merge routes: Add A2A routes() to FastAPI router
        #    - In a2a_router.py: router.routes.extend(Config.a2a_server.get_routes())
        # Current status: Server initialized but not exposed to HTTP traffic
        #
        # GAP ANALYSIS (2.1): Protocol Persistence & Execution
        # The current implementation uses InMemoryTaskStore which loses state on restart.
        # Required Action:
        # - Create DynamoDBTaskStore implementing a2a.server.tasks.TaskStore interface
        # - Connect it to a2a_task.py models via GraphQL
        # - Pass it to DefaultRequestHandler instead of InMemoryTaskStore
        #
        # See docs/A2A_GAP_ANALYSIS.md section 2.1 for details.

        self.app = A2AStarletteApplication(
            agent_card=self.agent_card,
            http_handler=self.request_handler,
            context_builder=None,  # Optional: custom context builder
            card_modifier=None,  # Optional: modify card per request
        )

        self.logger.info(f"A2A server '{server_name}' v{server_version} initialized")
        self.logger.info(
            f"Agent card available at: {server_url}.well-known/agent-card.json"
        )

    def _create_agent_skills(self, capability_list: List[str]) -> List[Any]:
        """
        Create AgentSkills from capability strings.

        AgentSkills describe what the agent can do and are exposed in the AgentCard.
        Each skill has: id, name, description, tags, and examples.

        Args:
            capability_list: List of capability identifiers

        Returns:
            List of AgentSkill objects
        """
        skills = []

        # Skill definitions for common capabilities
        skill_definitions = {
            "task_execution": {
                "name": "Task Execution",
                "description": "Execute tasks assigned by other agents in the A2A network",
                "tags": ["task", "execution", "processing"],
                "examples": [
                    "Execute data processing task",
                    "Run analysis on input data",
                    "Process assigned task",
                ],
            },
            "message_routing": {
                "name": "Message Routing",
                "description": "Route and deliver messages between agents in the network",
                "tags": ["message", "routing", "communication"],
                "examples": [
                    "Send message to agent-B",
                    "Route notification to task owner",
                    "Deliver update to subscriber",
                ],
            },
            "agent_discovery": {
                "name": "Agent Discovery",
                "description": "Discover, register, and manage agents in the A2A network",
                "tags": ["discovery", "registration", "network"],
                "examples": [
                    "Find agents with capability X",
                    "Register new agent",
                    "List active agents",
                ],
            },
        }

        for cap in capability_list:
            skill_def = skill_definitions.get(
                cap,
                {
                    "name": cap.replace("_", " ").title(),
                    "description": f"Provides {cap.replace('_', ' ')} functionality",
                    "tags": [cap],
                    "examples": [f"Use {cap}"],
                },
            )

            skill = AgentSkill(
                id=cap.replace("_", "-"),
                name=skill_def["name"],
                description=skill_def["description"],
                tags=skill_def["tags"],
                examples=skill_def["examples"],
            )
            skills.append(skill)

        return skills

    def _create_agent_card(
        self,
        name: str,
        description: str,
        url: str,
        version: str,
        skills: List[Any],
    ) -> Any:
        """
        Create an AgentCard following the A2A SDK pattern.

        The AgentCard is a digital business card exposed at /.well-known/agent-card.json
        that tells clients:
        - What the agent can do (skills)
        - What capabilities it supports (streaming, push notifications, etc.)
        - How to authenticate (security schemes)
        - Who provides it (provider info)

        Args:
            name: Agent name
            description: Agent description
            url: Agent base URL
            version: Agent version
            skills: List of AgentSkill objects

        Returns:
            AgentCard object
        """
        # Define capabilities (what the agent supports)
        # AgentCapabilities has optional boolean fields for each capability
        capabilities = AgentCapabilities(
            streaming=False,
            pushNotifications=False,
        )

        # Define provider information
        provider = AgentProvider(
            organization="SilvaEngine",
            url="https://github.com/ideabosque/a2a_daemon_engine",
        )

        # Create AgentCard
        # Note: supportsAuthenticatedExtendedCard indicates if the agent
        # can provide extended card information for authenticated requests
        agent_card = AgentCard(
            name=name,
            description=description,
            url=url,
            version=version,
            defaultInputModes=["text"],
            defaultOutputModes=["text"],
            capabilities=capabilities,
            skills=skills,
            supportsAuthenticatedExtendedCard=False,
            provider=provider,
        )

        return agent_card

    def get_app(self) -> Any:
        """
        Get the A2A Starlette application for integration with existing servers.

        The application can be:
        1. Run standalone with Uvicorn
        2. Integrated into an existing FastAPI/Starlette app via routes()

        Returns:
            A2AStarletteApplication instance or None if SDK not available
        """
        return self.app

    def get_routes(self) -> List[Any]:
        """
        Get the A2A server routes for integration with existing applications.

        Example usage:
            from starlette.applications import Starlette
            app = Starlette(routes=a2a_server.get_routes())

        Returns:
            List of Starlette Route objects
        """
        if self.app and hasattr(self.app, "routes"):
            return self.app.routes()
        return []

    # =========================================================================
    # Compatibility Methods for Existing Daemon Handlers
    # =========================================================================
    # These methods provide backward compatibility with the existing
    # DynamoDB-based handler system while the migration to full A2A SDK
    # integration is in progress.
    # =========================================================================

    async def handle_handshake(
        self, partition_key: str, agent_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle agent handshake/registration (compatibility method).

        This is a compatibility bridge for the existing a2a_handlers.
        In a full A2A SDK integration, handshakes would be handled through
        the DefaultRequestHandler and AgentExecutor pattern.

        Args:
            partition_key: Composite partition key (endpoint_id#part_id)
            agent_data: Agent registration data

        Returns:
            Handshake response
        """
        self.logger.info(f"Agent handshake: {agent_data.get('agent_id')}")

        # IMPLEMENTATION: Store agent registration in DynamoDB via GraphQL
        # Split partition_key back to endpoint_id and part_id
        parts = partition_key.split("#")
        endpoint_id = parts[0]
        part_id = parts[1] if len(parts) > 1 else ""

        mutation = """
            mutation RegisterAgent(
                $partitionKey: String!,
                $agentId: String!,
                $endpointId: String!,
                $partId: String!,
                $agentName: String!,
                $capabilities: String!,
                $endpointUrl: String!,
                $status: String!,
                $metadata: String,
                $updatedBy: String!
            ) {
                insertUpdateA2aAgent(
                    partitionKey: $partitionKey,
                    agentId: $agentId,
                    endpointId: $endpointId,
                    partId: $partId,
                    agentName: $agentName,
                    capabilities: $capabilities,
                    endpointUrl: $endpointUrl,
                    status: $status,
                    metadata: $metadata,
                    updatedBy: $updatedBy
                ) {
                    partitionKey
                    agentId
                    agentName
                    capabilities
                    endpointUrl
                    status
                }
            }
        """

        # Prepare capabilities as JSON string
        capabilities = agent_data.get("capabilities", [])
        capabilities_str = (
            Serializer.json_dumps(capabilities)
            if isinstance(capabilities, list)
            else capabilities
        )

        # Prepare metadata as JSON string
        metadata = agent_data.get("metadata", {})
        metadata_str = (
            Serializer.json_dumps(metadata)
            if isinstance(metadata, dict)
            else (metadata or "{}")
        )

        variables = {
            "partitionKey": partition_key,
            "agentId": agent_data["agent_id"],
            "endpointId": endpoint_id,
            "partId": part_id,
            "agentName": agent_data["agent_name"],
            "capabilities": capabilities_str,
            "endpointUrl": agent_data.get("endpoint_url", ""),
            "status": agent_data.get("status", "active"),
            "metadata": metadata_str,
            "updatedBy": agent_data.get("updated_by", "a2a_server"),
        }

        try:
            result = Config.a2a_core.a2a_core_graphql(
                partition_key=partition_key, query=mutation, variables=variables
            )

            data = Serializer.json_loads(result.get("body", result))

            if "errors" in data:
                self.logger.error(f"GraphQL errors: {data['errors']}")
                raise ValueError(f"Failed to register agent: {data['errors']}")

            agent = data.get("data", {}).get("insertUpdateA2aAgent", {})

            return {
                "status": "registered",
                "agent_id": agent.get("agentId"),
                "agent_name": agent.get("agentName"),
                "capabilities": Serializer.json_loads(agent.get("capabilities", "[]")),
                "endpoint_url": agent.get("endpointUrl"),
                "message": "Agent registered successfully",
            }

        except Exception as e:
            self.logger.error(f"Failed to store agent: {e}")
            raise

    async def assign_task(
        self, partition_key: str, task_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Assign a task to an agent (compatibility method).

        Args:
            partition_key: Composite partition key
            task_data: Task assignment data

        Returns:
            Task assignment response
        """
        self.logger.info(f"Task assignment: {task_data.get('task_id')}")

        # IMPLEMENTATION: Store task in DynamoDB via GraphQL
        parts = partition_key.split("#")
        endpoint_id = parts[0]
        part_id = parts[1] if len(parts) > 1 else ""

        mutation = """
            mutation AssignTask(
                $partitionKey: String!,
                $taskId: String!,
                $endpointId: String!,
                $partId: String!,
                $taskType: String!,
                $assignedAgentId: String,
                $status: String!,
                $priority: String!,
                $inputData: String,
                $updatedBy: String!
            ) {
                insertUpdateA2aTask(
                    partitionKey: $partitionKey,
                    taskId: $taskId,
                    endpointId: $endpointId,
                    partId: $partId,
                    taskType: $taskType,
                    assignedAgentId: $assignedAgentId,
                    status: $status,
                    priority: $priority,
                    inputData: $inputData,
                    updatedBy: $updatedBy
                ) {
                    partitionKey
                    taskId
                    taskType
                    assignedAgentId
                    status
                    priority
                }
            }
        """

        # Prepare input_data as JSON string
        input_data = task_data.get("input_data", {})
        input_data_str = (
            Serializer.json_dumps(input_data)
            if isinstance(input_data, dict)
            else (input_data or "{}")
        )

        variables = {
            "partitionKey": partition_key,
            "taskId": task_data["task_id"],
            "endpointId": endpoint_id,
            "partId": part_id,
            "taskType": task_data["task_type"],
            "assignedAgentId": task_data.get("assigned_agent_id"),
            "status": task_data.get("status", "SUBMITTED").upper(),
            "priority": task_data.get("priority", "medium"),
            "inputData": input_data_str,
            "updatedBy": task_data.get("updated_by", "a2a_server"),
        }

        try:
            result = Config.a2a_core.a2a_core_graphql(
                partition_key=partition_key, query=mutation, variables=variables
            )

            data = Serializer.json_loads(result.get("body", result))

            if "errors" in data:
                self.logger.error(f"GraphQL errors: {data['errors']}")
                raise ValueError(f"Failed to assign task: {data['errors']}")

            task = data.get("data", {}).get("insertUpdateA2aTask", {})

            # TODO: Trigger async task execution here
            # Options:
            # 1. SQS: boto3.client('sqs').send_message(QueueUrl=..., MessageBody=Serializer.json_dumps({task_id, partition_key}))
            # 2. EventBridge: boto3.client('events').put_events(Entries=[{...}])
            # 3. Async: asyncio.create_task(execute_a2a_task(partition_key, task_id, task_data))

            return {
                "status": "assigned",
                "task_id": task.get("taskId"),
                "task_type": task.get("taskType"),
                "assigned_agent_id": task.get("assignedAgentId"),
                "priority": task.get("priority"),
                "message": "Task assigned successfully",
            }

        except Exception as e:
            self.logger.error(f"Failed to store task: {e}")
            raise

    async def route_message(
        self, partition_key: str, message_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Route a message between agents (compatibility method).

        Args:
            partition_key: Composite partition key
            message_data: Message routing data

        Returns:
            Message routing response
        """
        from_agent = message_data.get("from_agent_id")
        to_agent = message_data.get("to_agent_id")
        self.logger.info(f"Message routing: {from_agent} -> {to_agent}")

        # IMPLEMENTATION: Store message in DynamoDB via GraphQL
        parts = partition_key.split("#")
        endpoint_id = parts[0]
        part_id = parts[1] if len(parts) > 1 else ""

        mutation = """
            mutation RouteMessage(
                $partitionKey: String!,
                $messageId: String!,
                $endpointId: String!,
                $partId: String!,
                $fromAgentId: String!,
                $toAgentId: String!,
                $messageType: String!,
                $payload: String!,
                $status: String!
            ) {
                insertUpdateA2aMessage(
                    partitionKey: $partitionKey,
                    messageId: $messageId,
                    endpointId: $endpointId,
                    partId: $partId,
                    fromAgentId: $fromAgentId,
                    toAgentId: $toAgentId,
                    messageType: $messageType,
                    payload: $payload,
                    status: $status
                ) {
                    partitionKey
                    messageId
                    fromAgentId
                    toAgentId
                    messageType
                    status
                }
            }
        """

        # Prepare payload as JSON string
        payload = message_data.get("payload", {})
        payload_str = (
            Serializer.json_dumps(payload)
            if isinstance(payload, dict)
            else (payload or "{}")
        )

        variables = {
            "partitionKey": partition_key,
            "messageId": message_data["message_id"],
            "endpointId": endpoint_id,
            "partId": part_id,
            "fromAgentId": from_agent,
            "toAgentId": to_agent,
            "messageType": message_data["message_type"],
            "payload": payload_str,
            "status": message_data.get("status", "sent"),
        }

        try:
            result = Config.a2a_core.a2a_core_graphql(
                partition_key=partition_key, query=mutation, variables=variables
            )

            data = Serializer.json_loads(result.get("body", result))

            if "errors" in data:
                self.logger.error(f"GraphQL errors: {data['errors']}")
                raise ValueError(f"Failed to route message: {data['errors']}")

            message = data.get("data", {}).get("insertUpdateA2aMessage", {})

            # TODO: Deliver message to recipient agent
            # Options:
            # 1. HTTP POST: Send to agent endpoint_url (requires get_agent() to fetch endpoint)
            # 2. Webhook: Trigger agent webhook if configured
            # 3. Queue: Push to agent-specific message queue
            # After delivery, update status to "delivered"

            return {
                "status": "routed",
                "message_id": message.get("messageId"),
                "from_agent_id": message.get("fromAgentId"),
                "to_agent_id": message.get("toAgentId"),
                "message_status": message.get("status"),
                "message": "Message routed successfully",
            }

        except Exception as e:
            self.logger.error(f"Failed to store message: {e}")
            raise

    async def discover_agents(
        self, partition_key: str, filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Discover available agents in the network (compatibility method).

        Args:
            partition_key: Composite partition key
            filters: Optional filters (e.g., {"status": "active"})

        Returns:
            List of agent dictionaries
        """
        try:
            # Query agents from GraphQL
            query = """
                query ListAgents($partitionKey: String!) {
                    a2aAgentList(partitionKey: $partitionKey) {
                        a2aAgentList {
                            partitionKey
                            agentId
                            agentName
                            capabilities
                            endpointUrl
                            status
                            metadata
                        }
                    }
                }
            """

            result = Config.a2a_core.a2a_core_graphql(
                partition_key=partition_key,
                query=query,
                variables={"partitionKey": partition_key},
            )

            # Extract agents from result
            agents = []
            if result and isinstance(result, dict):
                data = result.get("data", {})
                agent_list_data = data.get("a2aAgentList", {})
                agents = agent_list_data.get("a2aAgentList", [])

            # Apply filters if provided
            if filters and agents:
                status_filter = filters.get("status")
                if status_filter:
                    agents = [a for a in agents if a.get("status") == status_filter]

            self.logger.info(f"Discovered {len(agents)} agents")
            return agents

        except Exception as e:
            self.logger.error(f"Agent discovery failed: {e}")
            return []
