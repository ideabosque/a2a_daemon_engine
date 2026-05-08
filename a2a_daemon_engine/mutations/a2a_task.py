#!/usr/bin/python
__author__ = "bibow"

import traceback
from typing import Any

from graphene import Boolean, Field, Mutation, String
from silvaengine_utility import JSON

from ..models.a2a_task import delete_a2a_task, insert_update_a2a_task
from ..types.a2a_task import A2ATaskType


class InsertUpdateA2aTask(Mutation):
    a2a_task = Field(A2ATaskType)

    class Arguments:
        task_id = String(required=False)
        endpoint_id = String(required=True)
        part_id = String(required=True)
        task_type = String(required=True)
        assigned_agent_id = String(required=False)
        status = String(required=False)
        priority = String(required=False)
        input_data = JSON(required=False)
        output_data = JSON(required=False)
        updated_by = String(required=False)

    @staticmethod
    def mutate(
        root: Any, info: Any, **kwargs: dict[str, Any]
    ) -> "InsertUpdateA2aTask":
        try:
            a2a_task = insert_update_a2a_task(info, **kwargs)
        except Exception as e:
            log = traceback.format_exc()
            info.context.get("logger").error(log)
            raise e

        return InsertUpdateA2aTask(a2a_task=a2a_task)


class DeleteA2aTask(Mutation):
    ok = Boolean()

    class Arguments:
        task_id = String(required=True)

    @staticmethod
    def mutate(root: Any, info: Any, **kwargs: dict[str, Any]) -> "DeleteA2aTask":
        try:
            ok = delete_a2a_task(info, **kwargs)
        except Exception as e:
            log = traceback.format_exc()
            info.context.get("logger").error(log)
            raise e

        return DeleteA2aTask(ok=ok)
