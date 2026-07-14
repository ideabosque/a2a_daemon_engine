__author__ = "bibow"

import logging


def initialize_tables(logger: logging.Logger) -> None:
    from .a2a_agent import A2AAgentModel
    from .a2a_message import A2AMessageModel
    from .a2a_setting import A2ASettingModel
    from .a2a_task import A2ATaskModel

    models: list = [A2AAgentModel, A2ATaskModel, A2AMessageModel, A2ASettingModel]

    for model in models:
        if model.exists():
            continue

        table_name = model.Meta.table_name
        # Create with on-demand billing (PAY_PER_REQUEST)
        model.create_table(billing_mode="PAY_PER_REQUEST", wait=True)
        logger.info(f"The {table_name} table has been created.")
