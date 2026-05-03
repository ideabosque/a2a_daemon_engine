#!/usr/bin/python
# -*- coding: utf-8 -*-
__author__ = "bibow"

import traceback
from typing import Any, Dict

from graphene import Boolean, Field, Mutation, String

from silvaengine_utility import JSON

from ..models.a2a_message import delete_a2a_message, insert_update_a2a_message
from ..types.a2a_message import A2AMessageType


class InsertUpdateA2aMessage(Mutation):
    a2a_message = Field(A2AMessageType)

    class Arguments:
        message_id = String(required=False)
        endpoint_id = String(required=True)
        part_id = String(required=True)
        from_agent_id = String(required=True)
        to_agent_id = String(required=True)
        message_type = String(required=True)
        payload = JSON(required=False)
        status = String(required=False)

    @staticmethod
    def mutate(
        root: Any, info: Any, **kwargs: Dict[str, Any]
    ) -> "InsertUpdateA2aMessage":
        try:
            a2a_message = insert_update_a2a_message(info, **kwargs)
        except Exception as e:
            log = traceback.format_exc()
            info.context.get("logger").error(log)
            raise e

        return InsertUpdateA2aMessage(a2a_message=a2a_message)


class DeleteA2aMessage(Mutation):
    ok = Boolean()

    class Arguments:
        message_id = String(required=True)

    @staticmethod
    def mutate(root: Any, info: Any, **kwargs: Dict[str, Any]) -> "DeleteA2aMessage":
        try:
            ok = delete_a2a_message(info, **kwargs)
        except Exception as e:
            log = traceback.format_exc()
            info.context.get("logger").error(log)
            raise e

        return DeleteA2aMessage(ok=ok)
