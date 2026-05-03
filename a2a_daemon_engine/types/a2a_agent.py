#!/usr/bin/python
# -*- coding: utf-8 -*-
__author__ = "bibow"

from graphene import DateTime, List, ObjectType, String

from silvaengine_dynamodb_base import ListObjectType
from silvaengine_utility import JSON


class A2AAgentType(ObjectType):
    partition_key = String()
    agent_id = String()
    endpoint_id = String()
    part_id = String()
    agent_name = String()
    capabilities = String()
    endpoint_url = String()
    status = String()
    metadata = JSON()
    updated_by = String()
    created_at = DateTime()
    updated_at = DateTime()


class A2AAgentListType(ListObjectType):
    a2a_agent_list = List(A2AAgentType)
