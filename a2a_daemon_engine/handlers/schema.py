#!/usr/bin/python

__author__ = "bibow"

import time
from typing import Any

from graphene import Field, Int, ObjectType, ResolveInfo, String

from ..mutations.a2a_agent import DeleteA2aAgent, InsertUpdateA2aAgent
from ..mutations.a2a_message import DeleteA2aMessage, InsertUpdateA2aMessage
from ..mutations.a2a_setting import DeleteA2aSetting, InsertUpdateA2aSetting
from ..mutations.a2a_task import DeleteA2aTask, InsertUpdateA2aTask
from ..queries.a2a_agent import resolve_a2a_agent, resolve_a2a_agent_list
from ..queries.a2a_message import resolve_a2a_message, resolve_a2a_message_list
from ..queries.a2a_setting import resolve_a2a_setting, resolve_a2a_setting_list
from ..queries.a2a_task import resolve_a2a_task, resolve_a2a_task_list
from ..types.a2a_agent import A2AAgentListType, A2AAgentType
from ..types.a2a_message import A2AMessageListType, A2AMessageType
from ..types.a2a_setting import A2ASettingListType, A2ASettingType
from ..types.a2a_task import A2ATaskListType, A2ATaskType


def type_class():
    return [
        A2AAgentType,
        A2AAgentListType,
        A2ATaskType,
        A2ATaskListType,
        A2AMessageType,
        A2AMessageListType,
        A2ASettingType,
        A2ASettingListType,
    ]


class Query(ObjectType):
    ping = String()

    a2a_agent = Field(
        A2AAgentType,
        agent_id=String(required=True),
    )

    a2a_agent_list = Field(
        A2AAgentListType,
        page_number=Int(required=False),
        limit=Int(required=False),
        status=String(required=False),
        agent_name=String(required=False),
    )

    a2a_task = Field(
        A2ATaskType,
        task_id=String(required=True),
    )

    a2a_task_list = Field(
        A2ATaskListType,
        page_number=Int(required=False),
        limit=Int(required=False),
        status=String(required=False),
        priority=String(required=False),
        task_type=String(required=False),
        assigned_agent_id=String(required=False),
    )

    a2a_message = Field(
        A2AMessageType,
        message_id=String(required=True),
    )

    a2a_message_list = Field(
        A2AMessageListType,
        page_number=Int(required=False),
        limit=Int(required=False),
        status=String(required=False),
        message_type=String(required=False),
        from_agent_id=String(required=False),
        to_agent_id=String(required=False),
    )

    a2a_setting = Field(
        A2ASettingType,
        setting_id=String(required=True),
    )

    a2a_setting_list = Field(
        A2ASettingListType,
        page_number=Int(required=False),
        limit=Int(required=False),
        setting_id=String(required=False),
    )

    def resolve_ping(self, info: ResolveInfo) -> str:
        return f"Hello at {time.strftime('%X')}!!"

    def resolve_a2a_agent(
        self, info: ResolveInfo, **kwargs: dict[str, Any]
    ) -> A2AAgentType:
        return resolve_a2a_agent(info, **kwargs)

    def resolve_a2a_agent_list(
        self, info: ResolveInfo, **kwargs: dict[str, Any]
    ) -> A2AAgentListType:
        return resolve_a2a_agent_list(info, **kwargs)

    def resolve_a2a_task(
        self, info: ResolveInfo, **kwargs: dict[str, Any]
    ) -> A2ATaskType:
        return resolve_a2a_task(info, **kwargs)

    def resolve_a2a_task_list(
        self, info: ResolveInfo, **kwargs: dict[str, Any]
    ) -> A2ATaskListType:
        return resolve_a2a_task_list(info, **kwargs)

    def resolve_a2a_message(
        self, info: ResolveInfo, **kwargs: dict[str, Any]
    ) -> A2AMessageType:
        return resolve_a2a_message(info, **kwargs)

    def resolve_a2a_message_list(
        self, info: ResolveInfo, **kwargs: dict[str, Any]
    ) -> A2AMessageListType:
        return resolve_a2a_message_list(info, **kwargs)

    def resolve_a2a_setting(
        self, info: ResolveInfo, **kwargs: dict[str, Any]
    ) -> A2ASettingType:
        return resolve_a2a_setting(info, **kwargs)

    def resolve_a2a_setting_list(
        self, info: ResolveInfo, **kwargs: dict[str, Any]
    ) -> A2ASettingListType:
        return resolve_a2a_setting_list(info, **kwargs)


class Mutations(ObjectType):
    insert_update_a2a_agent = InsertUpdateA2aAgent.Field()
    delete_a2a_agent = DeleteA2aAgent.Field()
    insert_update_a2a_task = InsertUpdateA2aTask.Field()
    delete_a2a_task = DeleteA2aTask.Field()
    insert_update_a2a_message = InsertUpdateA2aMessage.Field()
    delete_a2a_message = DeleteA2aMessage.Field()
    insert_update_a2a_setting = InsertUpdateA2aSetting.Field()
    delete_a2a_setting = DeleteA2aSetting.Field()
