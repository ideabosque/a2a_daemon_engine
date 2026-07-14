#!/usr/bin/python
__author__ = "bibow"

from graphene import DateTime, List, ObjectType, String
from silvaengine_dynamodb_base import ListObjectType
from silvaengine_utility.graphql import JSON


class A2ASettingType(ObjectType):
    partition_key = String()
    setting_id = String()
    endpoint_id = String()
    part_id = String()
    setting = JSON()
    updated_by = String()
    created_at = DateTime()
    updated_at = DateTime()


class A2ASettingListType(ListObjectType):
    a2a_setting_list = List(A2ASettingType)
