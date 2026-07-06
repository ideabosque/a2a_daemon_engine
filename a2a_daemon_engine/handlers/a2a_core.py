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

        # Execute GraphQL query/mutation
        return self.execute(schema, **params)
