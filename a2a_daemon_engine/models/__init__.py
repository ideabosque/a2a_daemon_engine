#!/usr/bin/python
# -*- coding: utf-8 -*-
"""AI A2A Daemon Engine - Database Models Package

This package contains all DynamoDB model definitions using the partition_key architecture.
All models follow the composite partition key pattern: partition_key = "endpoint_id#part_id"
"""

from __future__ import print_function

__author__ = "SilvaEngine Team"
__version__ = "0.0.1"

# Model imports
from .a2a_agent import (
    A2AAgentModel,
    get_a2a_agent,
    insert_update_a2a_agent,
    delete_a2a_agent,
)
from .a2a_task import (
    A2ATaskModel,
    get_a2a_task,
    insert_update_a2a_task,
    delete_a2a_task,
)
from .a2a_message import (
    A2AMessageModel,
    get_a2a_message,
    insert_update_a2a_message,
    delete_a2a_message,
)
from .a2a_setting import (
    A2ASettingModel,
    get_a2a_setting,
    insert_update_a2a_setting,
    delete_a2a_setting,
)

__all__ = [
    # Agent
    "A2AAgentModel",
    "get_a2a_agent",
    "insert_update_a2a_agent",
    "delete_a2a_agent",
    # Task
    "A2ATaskModel",
    "get_a2a_task",
    "insert_update_a2a_task",
    "delete_a2a_task",
    # Message
    "A2AMessageModel",
    "get_a2a_message",
    "insert_update_a2a_message",
    "delete_a2a_message",
    # Setting
    "A2ASettingModel",
    "get_a2a_setting",
    "insert_update_a2a_setting",
    "delete_a2a_setting",
]
