#!/usr/bin/python
__author__ = "bibow"

import traceback
from typing import Any

from graphene import Boolean, Field, Mutation, String
from silvaengine_utility import JSON

from ..models.a2a_setting import delete_a2a_setting, insert_update_a2a_setting
from ..types.a2a_setting import A2ASettingType


class InsertUpdateA2aSetting(Mutation):
    a2a_setting = Field(A2ASettingType)

    class Arguments:
        setting_id = String(required=False)
        endpoint_id = String(required=True)
        part_id = String(required=True)
        setting = JSON(required=False)
        updated_by = String(required=True)

    @staticmethod
    def mutate(
        root: Any, info: Any, **kwargs: dict[str, Any]
    ) -> "InsertUpdateA2aSetting":
        try:
            a2a_setting = insert_update_a2a_setting(info, **kwargs)
        except Exception as e:
            log = traceback.format_exc()
            info.context.get("logger").error(log)
            raise e

        return InsertUpdateA2aSetting(a2a_setting=a2a_setting)


class DeleteA2aSetting(Mutation):
    ok = Boolean()

    class Arguments:
        setting_id = String(required=True)

    @staticmethod
    def mutate(root: Any, info: Any, **kwargs: dict[str, Any]) -> "DeleteA2aSetting":
        try:
            ok = delete_a2a_setting(info, **kwargs)
        except Exception as e:
            log = traceback.format_exc()
            info.context.get("logger").error(log)
            raise e

        return DeleteA2aSetting(ok=ok)
