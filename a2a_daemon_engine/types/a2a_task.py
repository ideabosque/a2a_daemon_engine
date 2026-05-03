#!/usr/bin/python
# -*- coding: utf-8 -*-
__author__ = "bibow"

from graphene import DateTime, List, ObjectType, String

from silvaengine_dynamodb_base import ListObjectType
from silvaengine_utility import JSON


class A2ATaskType(ObjectType):
    partition_key = String()
    task_id = String()
    endpoint_id = String()
    part_id = String()
    task_type = String()
    assigned_agent_id = String()
    status = String()
    priority = String()
    input_data = JSON()
    output_data = JSON()
    updated_by = String()
    created_at = DateTime()
    updated_at = DateTime()
    completed_at = DateTime()


class A2ATaskListType(ListObjectType):
    a2a_task_list = List(A2ATaskType)
