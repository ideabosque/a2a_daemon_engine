#!/usr/bin/python
__author__ = "bibow"

import traceback
from typing import Any

from graphene import Boolean, Field, List, Mutation, String
from silvaengine_utility.graphql import JSON

from ..models.repositories.dispatch import get_repo
from ..types.a2a_agent import A2AAgentType


class InsertUpdateA2aAgent(Mutation):
    a2a_agent = Field(A2AAgentType)

    class Arguments:
        agent_id = String(required=False)
        endpoint_id = String(required=True)
        part_id = String(required=True)
        agent_name = String(required=True)
        capabilities = List(String, required=False)
        endpoint_url = String(required=True)
        status = String(required=False)
        metadata = JSON(required=False)
        updated_by = String(required=True)

    @staticmethod
    def mutate(
        root: Any, info: Any, **kwargs: dict[str, Any]
    ) -> "InsertUpdateA2aAgent":
        try:
            repo = get_repo("a2a_agent")
            a2a_agent = repo.insert_update(info, **kwargs)
        except Exception as e:
            log = traceback.format_exc()
            info.context.get("logger").error(log)
            raise e

        return InsertUpdateA2aAgent(a2a_agent=a2a_agent)


class DeleteA2aAgent(Mutation):
    ok = Boolean()

    class Arguments:
        agent_id = String(required=True)

    @staticmethod
    def mutate(root: Any, info: Any, **kwargs: dict[str, Any]) -> "DeleteA2aAgent":
        try:
            repo = get_repo("a2a_agent")
            ok = repo.delete(info, **kwargs)
        except Exception as e:
            log = traceback.format_exc()
            info.context.get("logger").error(log)
            raise e

        return DeleteA2aAgent(ok=ok)
