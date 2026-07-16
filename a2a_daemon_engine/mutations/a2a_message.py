#!/usr/bin/python
__author__ = "bibow"

import traceback
from typing import Any

from graphene import Boolean, Field, Mutation, String
from silvaengine_utility.graphql import JSON

from ..models.repositories.dispatch import get_repo
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
        task_id = String(required=False)
        payload = JSON(required=False)
        status = String(required=False)

    @staticmethod
    def mutate(
        root: Any, info: Any, **kwargs: dict[str, Any]
    ) -> "InsertUpdateA2aMessage":
        try:
            repo = get_repo("a2a_message")
            a2a_message = repo.insert_update(info, **kwargs)
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
    def mutate(root: Any, info: Any, **kwargs: dict[str, Any]) -> "DeleteA2aMessage":
        try:
            repo = get_repo("a2a_message")
            ok = repo.delete(info, **kwargs)
        except Exception as e:
            log = traceback.format_exc()
            info.context.get("logger").error(log)
            raise e

        return DeleteA2aMessage(ok=ok)
