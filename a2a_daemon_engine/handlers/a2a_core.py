#!/usr/bin/python
"""
A2A Core GraphQL Handler

Manages GraphQL operations for A2A daemon engine.
Inherits from silvaengine_utility.graphql.Graphql for GraphQL execution.
"""

import logging
from typing import Any

from graphene import Schema
from silvaengine_dynamodb_base import BaseModel
from silvaengine_utility.graphql import Graphql

__author__ = "SilvaEngine Team"


class A2ACore(Graphql):
    """
    A2A Core GraphQL Handler

    Manages A2A protocol GraphQL operations including queries and mutations
    for agents, tasks, messages, and settings.

    Note: partition_key should already be assembled by caller (main.py or a2a_app.py).
    This class only executes GraphQL operations - it does not assemble partition_key.
    """

    def __init__(self, logger: logging.Logger, **setting: dict[str, Any]) -> None:
        """
        Initialize A2A Core handler.

        Args:
            logger: Logger instance
            **setting: Configuration settings including AWS credentials
        """
        # Initialize parent Graphql class
        Graphql.__init__(self, logger, **setting)

        # Configure AWS credentials for DynamoDB if provided
        if (
            setting.get("region_name")
            and setting.get("aws_access_key_id")
            and setting.get("aws_secret_access_key")
        ):
            BaseModel.Meta.region = setting.get("region_name")
            BaseModel.Meta.aws_access_key_id = setting.get("aws_access_key_id")
            BaseModel.Meta.aws_secret_access_key = setting.get("aws_secret_access_key")
            logger.info(f"DynamoDB configured for region: {setting.get('region_name')}")

    def a2a_core_graphql(self, **params: dict[str, Any]) -> Any:
        """
        Execute GraphQL queries/mutations for A2A operations.

        Note: partition_key should already be assembled by caller.
        The caller (main.py or a2a_app.py) is responsible for:
        1. Extracting endpoint_id from URL/context
        2. Extracting part_id from header/context (if provided)
        3. Assembling partition_key = "endpoint_id#part_id"
        4. Passing partition_key in params

        This method only executes the GraphQL operation.

        Args:
            **params: GraphQL parameters including:
                - query: GraphQL query string
                - variables: Query variables
                - partition_key: Pre-assembled composite key
                - endpoint_id: Platform partition
                - part_id: Business partition (optional)

        Returns:
            GraphQL execution result
        """
        # Import schema components
        from ..schema import Mutations, Query, type_class

        # Create GraphQL schema
        schema = Schema(
            query=Query,
            mutation=Mutations,
            types=type_class(),
        )

        # Ensure partition_key is available in the GraphQL context dict
        # so PG repos can access it via info.context.get("partition_key").
        partition_key = params.get("partition_key")
        if partition_key:
            if params.get("context") is None:
                params["context"] = {}
            if isinstance(params["context"], dict):
                params["context"]["partition_key"] = partition_key

        # Execute GraphQL query/mutation
        return self.execute(schema, **params)

    # ------------------------------------------------------------------
    # Convenience methods for Phase 10 persistence
    # ------------------------------------------------------------------

    async def insert_update_a2a_message(self, **kwargs):
        """Insert/update an A2A message directly via the PG repository.

        Bypasses Graphql.execute to avoid event loop and JSON scalar issues.
        """
        from ..models.repositories.dispatch import get_repo

        partition_key = kwargs.pop("partition_key", "")
        endpoint_id, _, part_id = partition_key.partition("#")
        role = kwargs.get("role", "agent")

        # The message body lives in the jsonb ``payload`` column. Without this
        # the row persists with an empty payload and the LLM content is lost.
        payload = kwargs.get("payload")
        if payload is None:
            payload = {}
            content = kwargs.get("content")
            metadata = kwargs.get("metadata")
            if content is not None:
                payload["content"] = content
            if metadata:
                payload["metadata"] = metadata

        # Build a fake info object with context for the repo
        class _FakeInfo:
            def __init__(self, ctx):
                self.context = ctx

        info = _FakeInfo({
            "partition_key": partition_key,
            "logger": self.logger,
        })

        try:
            repo = get_repo("a2a_message")
            result = repo.insert_update(
                info,
                partition_key=partition_key,
                message_id=kwargs.get("message_id"),
                endpoint_id=endpoint_id,
                part_id=part_id,
                from_agent_id=kwargs.get("from_agent_id", "core-engine-agent"),
                to_agent_id=kwargs.get("to_agent_id", "user"),
                message_type=role,
                task_id=kwargs.get("task_id"),
                payload=payload,
                status="delivered",
            )
            return result
        except Exception as e:
            if self.logger:
                self.logger.warning(f"A2A message persist (direct repo) failed: {e}")
            raise

    async def insert_update_a2a_task(self, **kwargs):
        """Insert/update an A2A task via in-process GraphQL.

        ``input_data`` / ``output_data`` are passed as JSON variables. The
        earlier silvaengine_utility JSON scalar bug only affects *output*
        serialization, and this mutation selects nothing but ``taskId``.

        A JSON variable is only included when the caller supplied it: sending
        an explicit null would set the column to NULL, so the completion
        update (which passes only ``output_data``) would otherwise wipe the
        ``input_data`` written by the in-progress insert.
        """
        from ..schema import Mutations, Query, type_class
        schema = Schema(query=Query, mutation=Mutations, types=type_class())
        partition_key = kwargs.pop("partition_key", "")
        mutation = """
            mutation InsertUpdateA2aTask(
                $endpointId: String!, $partId: String!,
                $taskId: String, $taskType: String!,
                $assignedAgentId: String, $status: String,
                $inputData: JSON, $outputData: JSON,
                $contextId: String,
                $updatedBy: String
            ) {
                insertUpdateA2aTask(
                    endpointId: $endpointId
                    partId: $partId
                    taskId: $taskId
                    taskType: $taskType
                    assignedAgentId: $assignedAgentId
                    status: $status
                    inputData: $inputData
                    outputData: $outputData
                    contextId: $contextId
                    updatedBy: $updatedBy
                ) {
                    a2aTask { taskId }
                }
            }
        """
        endpoint_id, _, part_id = partition_key.partition("#")
        variables = {
            "endpointId": endpoint_id,
            "partId": part_id,
            "taskId": kwargs.get("task_id"),
            "taskType": kwargs.get("task_type", "ai_agent"),
            "assignedAgentId": kwargs.get("assigned_agent_id"),
            "status": kwargs.get("status", "completed"),
            "updatedBy": kwargs.get("updated_by", "a2a_daemon"),
        }
        for gql_key, kw_key in (
            ("inputData", "input_data"),
            ("outputData", "output_data"),
            ("contextId", "context_id"),
        ):
            value = kwargs.get(kw_key)
            if value is not None:
                variables[gql_key] = value

        return self.execute(
            schema,
            query=mutation,
            variables=variables,
            context={"partition_key": partition_key},
        )

    async def get_a2a_messages(self, **kwargs):
        """Fetch conversation history for an A2A context, oldest first.

        ``build_input_messages`` calls this to give the LLM prior turns.

        Each completed task is one turn. The user side lives in
        ``a2a_tasks.input_data.user_query``; the agent side is read from
        ``a2a_messages.payload.content`` rather than ``output_data.content``
        because the latter is stored truncated to 500 chars. There is no
        thread/context column on ``a2a_messages``, so the conversation is
        reached via the existing ``a2a_messages.task_id -> a2a_tasks.task_id``
        link, filtered by the task's ``context_id``.

        Only COMPLETED tasks are returned, which excludes the in-flight task
        for the current turn — ``build_input_messages`` appends that
        ``user_query`` itself.

        Returns ``{"items": [{"role": ..., "content": ...}, ...]}`` — the shape
        ``build_input_messages`` expects.
        """
        from .config import Config

        partition_key = kwargs.get("partition_key", "")
        context_id = kwargs.get("thread_id") or kwargs.get("context_id")
        limit = int(kwargs.get("limit") or 10)

        if not context_id or not partition_key:
            return {"items": []}

        session = Config.db_session
        if session is None:
            return {"items": []}

        from sqlalchemy import text

        try:
            # Newest-first so LIMIT keeps the most recent turns; reversed below.
            rows = session.execute(
                text(
                    """
                    SELECT t.input_data, m.payload
                    FROM a2a_tasks t
                    LEFT JOIN a2a_messages m
                      ON m.task_id = t.task_id
                     AND m.partition_key = t.partition_key
                    WHERE t.partition_key = :pk
                      AND t.context_id = :ctx
                      AND t.status = 'COMPLETED'
                    ORDER BY t.created_at DESC
                    LIMIT :lim
                    """
                ),
                {"pk": partition_key, "ctx": context_id, "lim": max(1, limit // 2)},
            ).fetchall()

            items: list[dict[str, Any]] = []
            for input_data, payload in reversed(rows):
                user_query = (
                    input_data.get("user_query") if isinstance(input_data, dict) else None
                )
                if user_query:
                    items.append({"role": "user", "content": user_query})
                content = payload.get("content") if isinstance(payload, dict) else None
                if content:
                    items.append({"role": "assistant", "content": content})
            return {"items": items}
        except Exception as e:
            if self.logger:
                self.logger.warning(f"A2A message history fetch failed: {e}")
            return {"items": []}
        finally:
            Config.db_session.remove()


__all__ = ["A2ACore"]
