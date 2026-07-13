#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Configuration Management for A2A Daemon Engine.

Restructured to match mcp_daemon_engine's Config pattern:
- Backend selection via DB_BACKEND ("dynamodb" default)
- Backend-aware cache entity config and relationships
- No FastAPI/uvicorn/auth settings (gateway handles auth)
- A2A SDK server initialization kept for protocol surface
"""
from __future__ import print_function

__author__ = "bibow"

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import boto3
from passlib.context import CryptContext
from pydantic import AnyUrl

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


@dataclass
class LocalUser:
    username: str
    password_hash: str
    roles: List[str]

    def verify(self, plain: str) -> bool:
        return _pwd.verify(plain, self.password_hash)


class Config:
    """Centralized Configuration Class — gateway-dispatch pattern."""

    # Backend selection: "dynamodb" (default) or "postgresql"
    DB_BACKEND: str = "dynamodb"
    db_session = None

    # Cache Configuration
    CACHE_TTL = 1800
    CACHE_ENABLED = True

    CACHE_NAMES = {
        "models": "a2a_daemon_engine.models.dynamodb",
        "queries": "a2a_daemon_engine.queries",
    }

    # ------------------------------------------------------------------
    # Cache entity metadata — backend-aware.
    # ------------------------------------------------------------------
    CACHE_ENTITY_CONFIG_DYNAMODB = {
        "a2a_agent": {
            "module": "a2a_daemon_engine.models.dynamodb.a2a_agent",
            "model_class": "A2AAgentModel",
            "getter": "get_a2a_agent",
            "list_resolver": "a2a_daemon_engine.queries.a2a_agent.resolve_a2a_agent_list",
            "cache_keys": ["context:partition_key", "key:agent_id"],
        },
        "a2a_task": {
            "module": "a2a_daemon_engine.models.dynamodb.a2a_task",
            "model_class": "A2ATaskModel",
            "getter": "get_a2a_task",
            "list_resolver": "a2a_daemon_engine.queries.a2a_task.resolve_a2a_task_list",
            "cache_keys": ["context:partition_key", "key:task_id"],
        },
        "a2a_message": {
            "module": "a2a_daemon_engine.models.dynamodb.a2a_message",
            "model_class": "A2AMessageModel",
            "getter": "get_a2a_message",
            "list_resolver": "a2a_daemon_engine.queries.a2a_message.resolve_a2a_message_list",
            "cache_keys": ["context:partition_key", "key:message_id"],
        },
        "a2a_setting": {
            "module": "a2a_daemon_engine.models.dynamodb.a2a_setting",
            "model_class": "A2ASettingModel",
            "getter": "get_a2a_setting",
            "list_resolver": "a2a_daemon_engine.queries.a2a_setting.resolve_a2a_setting_list",
            "cache_keys": ["context:partition_key", "key:setting_id"],
        },
    }

    CACHE_ENTITY_CONFIG_POSTGRESQL: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def get_cache_entity_config(cls) -> Dict[str, Dict[str, Any]]:
        if cls.DB_BACKEND == "postgresql":
            return cls.CACHE_ENTITY_CONFIG_POSTGRESQL
        return cls.CACHE_ENTITY_CONFIG_DYNAMODB

    # ------------------------------------------------------------------
    # Entity cache dependency relationships — backend-aware.
    # ------------------------------------------------------------------
    CACHE_RELATIONSHIPS_DYNAMODB = {
        "a2a_agent": [
            {
                "entity_type": "a2a_task",
                "module": "a2a_task",
                "list_resolver": "resolve_a2a_task_list",
                "dependency_key": "assigned_agent_id",
                "parent_key": "agent_id",
            },
            {
                "entity_type": "a2a_message",
                "module": "a2a_message",
                "list_resolver": "resolve_a2a_message_list",
                "dependency_key": "from_agent_id",
                "parent_key": "agent_id",
            },
        ],
        "a2a_task": [
            {
                "entity_type": "a2a_message",
                "module": "a2a_message",
                "list_resolver": "resolve_a2a_message_list",
                "dependency_key": "task_id",
                "parent_key": "task_id",
            }
        ],
    }

    CACHE_RELATIONSHIPS_POSTGRESQL: Dict[str, List[Dict[str, Any]]] = {}

    @classmethod
    def get_cache_relationships(cls) -> Dict[str, List[Dict[str, Any]]]:
        if cls.DB_BACKEND == "postgresql":
            return cls.CACHE_RELATIONSHIPS_POSTGRESQL
        return cls.CACHE_RELATIONSHIPS_DYNAMODB

    # Application Settings
    setting: Dict[str, Any] = {}

    # Server Configuration
    transport = None
    port = None
    a2a_configuration = {}
    logger = None
    a2a_core = None
    a2a_server = None
    a2a_server_error = None

    # AWS Services
    aws_s3 = None
    aws_cognito_idp = None
    aws_lambda = None

    # Authentication (kept for A2A SDK server compatibility)
    auth_provider: str | None = None
    jwt_secret_key: str | None = None
    jwt_algorithm: str | None = None
    access_token_exp: int | None = None
    local_user_file: str | None = None
    _USERS = None
    admin_username: str | None = None
    admin_password: str | None = None
    admin_static_token: str | None = None

    # Cognito settings
    issuer = None
    cognito_app_client_id: str | None = None
    cognito_app_secret: str | None = None
    jwks_endpoint: AnyUrl | None = None
    jwks_cache_ttl: int | None = None

    # Phase 10: ai_agent_core_engine bridge settings
    a2a_ai_agent_module: str | None = None
    a2a_ai_agent_class: str | None = None
    a2a_default_agent_uuid: str | None = None
    a2a_stream_timeout: float = 120.0
    a2a_streaming_enabled: bool = True
    phase10_available: bool = False

    @classmethod
    def initialize(cls, logger: logging.Logger, **setting: Dict[str, Any]) -> None:
        try:
            cls.logger = logger
            cls.setting = setting
            cls._set_parameters(setting)
            # Initialize PostgreSQL session before a2a_core/server so that
            # PG-backed persistence is available when DB_BACKEND=postgresql.
            if cls.DB_BACKEND == "postgresql":
                cls._initialize_db_session(setting)
            cls._initialize_a2a_core(logger, setting)
            cls._initialize_a2a_server(logger, setting)
            cls._initialize_aws_services(logger, setting)
            if setting.get("initialize_tables"):
                cls._initialize_tables(logger)
            cls._evaluate_phase10_availability()
            logger.info("Configuration initialized successfully.")
        except Exception as e:
            logger.exception("Failed to initialize configuration.")
            raise e

    @classmethod
    def _set_parameters(cls, setting: Dict[str, Any]) -> None:
        cls.transport = setting.get("transport", "http")
        cls.port = setting.get("port", 8001)
        cls.auth_provider = setting.get("auth_provider", "local")
        cls.jwt_secret_key = setting.get("jwt_secret_key", "CHANGEME")
        cls.jwt_algorithm = setting.get("jwt_algorithm", "HS256")
        cls.access_token_exp = int(setting.get("access_token_exp", 15))
        cls.local_user_file = setting.get("local_user_file", "users.json")
        cls.admin_username = setting.get("admin_username", "admin")
        cls.admin_password = setting.get("admin_password", "admin123")
        cls.admin_static_token = setting.get("admin_static_token", None)
        cls.cognito_app_client_id = setting.get("cognito_app_client_id", None)
        cls.cognito_app_secret = setting.get("cognito_app_secret", None)
        cls.jwks_cache_ttl = int(setting.get("jwks_cache_ttl", 3600))

        # Read backend selection
        cls.DB_BACKEND = str(setting.get("db_backend", "dynamodb")).lower()

        if setting.get("a2a_configuration") is not None:
            cls.a2a_configuration["default"] = setting["a2a_configuration"]
            cls.logger.info("A2A Configuration loaded successfully.")

        if "cache_enabled" in setting:
            cls.CACHE_ENABLED = setting.get("cache_enabled", True)

        # Phase 10 settings
        cls.a2a_ai_agent_module = setting.get("A2A_AI_AGENT_MODULE") or setting.get("a2a_ai_agent_module")
        cls.a2a_ai_agent_class = setting.get("A2A_AI_AGENT_CLASS") or setting.get("a2a_ai_agent_class")
        cls.a2a_default_agent_uuid = setting.get("A2A_DEFAULT_AGENT_UUID") or setting.get("a2a_default_agent_uuid")
        cls.a2a_stream_timeout = float(setting.get("A2A_STREAM_TIMEOUT", setting.get("a2a_stream_timeout", 120.0)))
        cls.a2a_streaming_enabled = _truthy(
            setting.get("A2A_STREAMING_ENABLED", setting.get("a2a_streaming_enabled", True))
        )

    @classmethod
    def _evaluate_phase10_availability(cls) -> None:
        if cls.logger is None:
            return
        try:
            from .a2a_ai_agent_utility import AI_CORE_AVAILABLE
            cls.phase10_available = bool(AI_CORE_AVAILABLE and cls.a2a_core is not None)
        except Exception:
            cls.phase10_available = False

        if cls.phase10_available:
            cls.logger.info("Phase 10 ai_agent_core_engine bridge is available.")
        else:
            cls.logger.info(
                "Phase 10 ai_agent_core_engine bridge is NOT available "
                "(core engine not installed or a2a_core not initialized)."
            )

    @classmethod
    def _initialize_a2a_core(
        cls, logger: logging.Logger, setting: Dict[str, Any]
    ) -> None:
        if all(
            setting.get(k)
            for k in ["region_name", "aws_access_key_id", "aws_secret_access_key"]
        ):
            from .a2a_core import A2ACore

            cls.a2a_core = A2ACore(logger, **setting)
            logger.info("A2A Core initialized successfully.")

    @classmethod
    def _initialize_a2a_server(
        cls, logger: logging.Logger, setting: Dict[str, Any]
    ) -> None:
        try:
            from .a2a_server import A2AProtocolServer

            cls.a2a_server = A2AProtocolServer(logger, **setting)
            if not cls.a2a_server.app or not cls.a2a_server.request_handler:
                cls.a2a_server_error = getattr(
                    cls.a2a_server,
                    "initialization_error",
                    "A2A Protocol Server did not create an app/request handler",
                )
                cls.a2a_server = None
                logger.warning(
                    "A2A Protocol Server initialization skipped: "
                    f"{cls.a2a_server_error}"
                )
                return

            cls.a2a_server_error = None
            logger.info("A2A Protocol Server initialized successfully.")
        except Exception as e:
            cls.a2a_server_error = str(e)
            logger.warning(
                "A2A Protocol Server initialization skipped: "
                f"{cls.a2a_server_error}",
                exc_info=True,
            )
            cls.a2a_server = None

    @classmethod
    def _initialize_aws_services(
        cls, logger: logging.Logger, setting: Dict[str, Any]
    ) -> None:
        try:
            if all(
                setting.get(k)
                for k in ["region_name", "aws_access_key_id", "aws_secret_access_key"]
            ):
                aws_credentials = {
                    "region_name": setting["region_name"],
                    "aws_access_key_id": setting["aws_access_key_id"],
                    "aws_secret_access_key": setting["aws_secret_access_key"],
                }
            else:
                aws_credentials = {}

            cls.aws_s3 = boto3.client(
                "s3",
                **aws_credentials,
                config=boto3.session.Config(signature_version="s3v4"),
            )

            if (
                all(setting.get(k) for k in ["region_name", "cognito_user_pool_id"])
                and cls.auth_provider == "cognito"
            ):
                cls.issuer = f"https://cognito-idp.{setting['region_name']}.amazonaws.com/{setting['cognito_user_pool_id']}"
                cls.jwks_endpoint = (
                    setting.get("cognito_jwks_url")
                    or f"{cls.issuer}/.well-known/jwks.json"
                )
                cls.aws_cognito_idp = boto3.client(
                    "cognito-idp", region_name=setting["region_name"]
                )
                logger.info("AWS Cognito IDP client initialized.")

            cls.aws_lambda = boto3.client("lambda", **aws_credentials)
            logger.info("AWS services initialized successfully.")

        except Exception as e:
            logger.exception("Failed to initialize AWS services configuration.")
            raise e

    @classmethod
    def _initialize_tables(cls, logger: logging.Logger) -> None:
        if cls.DB_BACKEND == "dynamodb":
            from ..models.dynamodb.utils import initialize_tables

            initialize_tables(logger)
        elif cls.DB_BACKEND == "postgresql":
            from ..models.postgresql.utils import initialize_tables as pg_init

            pg_init(logger, cls.db_session)

    @classmethod
    def _initialize_db_session(cls, setting: Dict[str, Any]) -> None:
        """Initialize the PostgreSQL database session using SQLAlchemy.

        Expected setting keys: db_host, db_port, db_user, db_password, db_schema.
        """
        from urllib.parse import quote_plus

        from sqlalchemy import create_engine
        from sqlalchemy.orm import scoped_session, sessionmaker

        password = quote_plus(setting["db_password"])
        connection_string = (
            f"postgresql+psycopg2://{setting['db_user']}:{password}"
            f"@{setting['db_host']}:{setting['db_port']}/{setting['db_schema']}"
        )

        engine = create_engine(
            connection_string,
            pool_recycle=7200,
            pool_size=30,
            max_overflow=20,
            pool_timeout=60,
            pool_pre_ping=True,
            echo=False,
        )

        cls.db_session = scoped_session(
            sessionmaker(autocommit=False, autoflush=False, bind=engine)
        )

    @classmethod
    def get_cache_name(cls, module_type: str, model_name: str) -> str:
        base_name = cls.CACHE_NAMES.get(
            module_type, f"a2a_daemon_engine.{module_type}"
        )
        return f"{base_name}.{model_name}"

    @classmethod
    def get_cache_ttl(cls) -> int:
        return cls.CACHE_TTL

    @classmethod
    def is_cache_enabled(cls) -> bool:
        return cls.CACHE_ENABLED

    @classmethod
    def get_logger(cls) -> logging.Logger:
        return cls.logger

    @classmethod
    def get_setting(cls) -> Dict[str, Any]:
        return cls.setting


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)