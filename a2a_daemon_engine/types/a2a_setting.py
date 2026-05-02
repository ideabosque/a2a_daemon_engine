#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import print_function

__author__ = "bibow"

from graphene import Boolean, DateTime, Int, List, ObjectType, String

from silvaengine_dynamodb_base import ListObjectType
from silvaengine_utility import JSON


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
