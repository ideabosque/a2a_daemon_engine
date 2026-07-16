#!/usr/bin/python
__author__ = "bibow"

from graphene import DateTime, List, ObjectType, String
from silvaengine_dynamodb_base import ListObjectType
from silvaengine_utility.graphql import JSON


class A2AMessageType(ObjectType):
    partition_key = String()
    message_id = String()
    endpoint_id = String()
    part_id = String()
    from_agent_id = String()
    to_agent_id = String()
    message_type = String()
    task_id = String()
    payload = JSON()
    status = String()
    created_at = DateTime()
    delivered_at = DateTime()


class A2AMessageListType(ListObjectType):
    a2a_message_list = List(A2AMessageType)
