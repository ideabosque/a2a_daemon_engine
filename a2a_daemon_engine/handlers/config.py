#!/usr/bin/python
"""
Configuration Management for A2A Daemon Engine

Centralized Config class managing:
- Cache configuration
- AWS service initialization
- Authentication settings
- A2A Core initialization
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from passlib.context import CryptContext
from pydantic import AnyUrl

__author__ = "SilvaEngine Team"

# Password hashing context
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


@dataclass
class LocalUser:
    """Local user with bcrypt password hash"""

    username: str
    password_hash: str
    roles: list[str]

    def verify(self, plain: str) -> bool:
        """Verify plain password against stored hash"""
        return _pwd.verify(plain, self.password_hash)


class Config:
    """
    Centralized Configuration Class

    Manages shared configuration variables across the application.
    Initialized once at startup via Config.initialize().
    """

    # Cache Configuration
    CACHE_TTL = 1800  # 30 minutes
    CACHE_ENABLED = True

    CACHE_NAMES = {
        "models": "a2a_daemon_engine.models",
        "queries": "a2a_daemon_engine.queries",
    }

    CACHE_ENTITY_CONFIG = {
        "a2a_agent": {
            "module": "a2a_daemon_engine.models.a2a_agent",
            "model_class": "A2AAgentModel",
            "getter": "get_a2a_agent",
            "list_resolver": "a2a_daemon_engine.queries.a2a_agent.resolve_a2a_agent_list",
            "cache_keys": ["context:partition_key", "key:agent_id"],
        },
        "a2a_task": {
            "module": "a2a_daemon_engine.models.a2a_task",
            "model_class": "A2ATaskModel",
            "getter": "get_a2a_task",
            "list_resolver": "a2a_daemon_engine.queries.a2a_task.resolve_a2a_task_list",
            "cache_keys": ["context:partition_key", "key:task_id"],
        },
        "a2a_message": {
            "module": "a2a_daemon_engine.models.a2a_message",
            "model_class": "A2AMessageModel",
            "getter": "get_a2a_message",
            "list_resolver": "a2a_daemon_engine.queries.a2a_message.resolve_a2a_message_list",
            "cache_keys": ["context:partition_key", "key:message_id"],
        },
        "a2a_setting": {
            "module": "a2a_daemon_engine.models.a2a_setting",
            "model_class": "A2ASettingModel",
            "getter": "get_a2a_setting",
            "list_resolver": "a2a_daemon_engine.queries.a2a_setting.resolve_a2a_setting_list",
            "cache_keys": ["context:partition_key", "key:setting_id"],
        },
    }

    CACHE_RELATIONSHIPS = {
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

    # Application Settings
    setting: dict[str, Any] = {}

    # Server Configuration
    transport = None
    port = None
    a2a_configuration = {}
    logger = None
    a2a_core = None
    a2a_server = None  # A2A Protocol Server

    # AWS Services
    aws_s3 = None
    aws_cognito_idp = None
    aws_lambda = None

    # Authentication configuration
    auth_provider: str | None = None  # "local" | "cognito"
    jwt_secret_key: str | None = None
    jwt_algorithm: str | None = None
    access_token_exp: int | None = None  # minutes
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
    jwks_cache_ttl: int | None = None  # seconds

    @classmethod
    def initialize(cls, logger: logging.Logger, **setting: dict[str, Any]) -> None:
        """
        Initialize configuration settings.

        Args:
            logger (logging.Logger): Logger instance for logging.
            **setting (Dict[str, Any]): Configuration dictionary.
        """
        try:
            cls.logger = logger
            cls.setting = setting
            cls._set_parameters(setting)
            cls._initialize_a2a_core(logger, setting)
            cls._initialize_a2a_server(logger, setting)
            cls._initialize_aws_services(logger, setting)
            if setting.get("initialize_tables"):
                cls._initialize_tables(logger)
            logger.info("Configuration initialized successfully.")
        except Exception as e:
            logger.exception("Failed to initialize configuration.")
            raise e

    @classmethod
    def _set_parameters(cls, setting: dict[str, Any]) -> None:
        """Set application-level parameters."""
        cls.transport = setting.get("transport", "http")
        cls.port = setting.get("port", 8001)

        if setting.get("a2a_configuration") is not None:
            cls.a2a_configuration["default"] = setting["a2a_configuration"]
            cls.logger.info("A2A Configuration loaded successfully.")

        # Authentication settings
        cls.auth_provider = setting.get("auth_provider", "local")
        cls.jwt_secret_key = setting.get("jwt_secret_key", "CHANGEME")

        # Validate JWT secret key is not weak/default
        if cls.auth_provider == "local":
            weak_secrets = [
                "",
                "CHANGEME",
                "changeme",
                "secret",
                "password",
                "123456",
                "admin",
            ]
            if not cls.jwt_secret_key or cls.jwt_secret_key.strip() in weak_secrets:
                raise ValueError(
                    f"Invalid JWT_SECRET_KEY: '{cls.jwt_secret_key}'. "
                    "JWT secret key must be a strong, non-default value. "
                    "Set a secure JWT_SECRET_KEY environment variable with at least 32 characters."
                )
            if len(cls.jwt_secret_key) < 32:
                cls.logger.warning(
                    f"JWT_SECRET_KEY is only {len(cls.jwt_secret_key)} characters. "
                    "Consider using a stronger key (>= 32 characters) for production."
                )

        cls.jwt_algorithm = setting.get("jwt_algorithm", "HS256")
        cls.access_token_exp = int(setting.get("access_token_exp", 15))
        cls.local_user_file = setting.get("local_user_file", "users.json")
        cls.admin_username = setting.get("admin_username", "admin")
        cls.admin_password = setting.get("admin_password", "admin123")
        cls.admin_static_token = setting.get("admin_static_token", None)
        cls.cognito_app_client_id = setting.get("cognito_app_client_id", None)
        cls.cognito_app_secret = setting.get("cognito_app_secret", None)
        cls.jwks_cache_ttl = int(setting.get("jwks_cache_ttl", 3600))

        # Load local users if using local auth with HTTP transport
        if cls.transport == "http" and cls.auth_provider == "local":
            cls._USERS = cls._load_users()

    @classmethod
    def _initialize_a2a_core(
        cls, logger: logging.Logger, setting: dict[str, Any]
    ) -> None:
        """Initialize A2A Core with AWS credentials."""
        if all(
            setting.get(k)
            for k in ["region_name", "aws_access_key_id", "aws_secret_access_key"]
        ):
            from .a2a_core import A2ACore

            cls.a2a_core = A2ACore(logger, **setting)
            logger.info("A2A Core initialized successfully.")

    @classmethod
    def _initialize_a2a_server(
        cls, logger: logging.Logger, setting: dict[str, Any]
    ) -> None:
        """Initialize A2A Protocol Server."""
        try:
            from .a2a_server import A2AProtocolServer

            cls.a2a_server = A2AProtocolServer(logger, **setting)
            logger.info("A2A Protocol Server initialized successfully.")
        except Exception as e:
            logger.warning(f"A2A Protocol Server initialization skipped: {e}")
            cls.a2a_server = None

    @classmethod
    def _initialize_aws_services(
        cls, logger: logging.Logger, setting: dict[str, Any]
    ) -> None:
        """Initialize AWS services including S3, Cognito IDP, and Lambda clients."""
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

            # Initialize S3 client
            cls.aws_s3 = boto3.client(
                "s3",
                **aws_credentials,
                config=boto3.session.Config(signature_version="s3v4"),
            )

            # Initialize Cognito IDP if using Cognito auth
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

            # Initialize Lambda client
            cls.aws_lambda = boto3.client("lambda", **aws_credentials)
            logger.info("AWS services initialized successfully.")

        except Exception as e:
            logger.exception("Failed to initialize AWS services configuration.")
            raise e

    @classmethod
    def _initialize_tables(cls, logger: logging.Logger) -> None:
        """Initialize database tables."""
        from ..models import utils

        utils.initialize_tables(logger)

    @classmethod
    def _load_users(cls) -> dict[str, LocalUser]:
        """Load local users from file."""
        try:
            p = Path(cls.local_user_file).expanduser()
            if not p.exists():
                cls.logger.warning(f"Local user file not found: {cls.local_user_file}")
                return {}

            with p.open("r", encoding="utf-8") as f:
                raw = json.load(f)

            return {u["username"]: LocalUser(**u) for u in raw}
        except Exception as e:
            cls.logger.error(f"Error loading local users: {e}")
            return {}

    @classmethod
    def get_cache_name(cls, module_type: str, model_name: str) -> str:
        """Generate standardized cache names."""
        base_name = cls.CACHE_NAMES.get(
            module_type, f"a2a_daemon_engine.{module_type}"
        )
        return f"{base_name}.{model_name}"

    @classmethod
    def get_cache_ttl(cls) -> int:
        """Get the configured cache TTL."""
        return cls.CACHE_TTL

    @classmethod
    def is_cache_enabled(cls) -> bool:
        """Check if caching is enabled."""
        return cls.CACHE_ENABLED

    @classmethod
    def get_cache_entity_config(cls) -> dict[str, dict[str, Any]]:
        """Get cache configuration metadata for each entity type."""
        return cls.CACHE_ENTITY_CONFIG

    @classmethod
    def get_cache_relationships(cls) -> dict[str, list[dict[str, Any]]]:
        """Get entity cache dependency relationships."""
        return cls.CACHE_RELATIONSHIPS

    @classmethod
    def get_entity_children(cls, entity_type: str) -> list[dict[str, Any]]:
        """Get child entities for a specific entity type."""
        return cls.CACHE_RELATIONSHIPS.get(entity_type, [])
