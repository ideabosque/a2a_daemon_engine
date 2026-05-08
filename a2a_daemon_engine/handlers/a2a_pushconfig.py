#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
A2A Push Notification Configuration Manager

Phase 7 - Task 5: PushNotificationConfig CRUD Operations
Phase 7 - Task 7: Webhook URL Allowlist (Anti-SSRF)

Implements A2A-standard PushNotificationConfig operations:
- CreateTaskPushNotificationConfig
- GetTaskPushNotificationConfig
- ListTaskPushNotificationConfigs
- DeleteTaskPushNotificationConfig

Includes SSRF protection via URL allowlist/denylist validation.

Usage:
    from a2a_daemon_engine.handlers.a2a_pushconfig import PushNotificationManager
    
    manager = PushNotificationManager(task_store, logger)
    
    # Create push config
    config = await manager.create_push_config(
        task_id="task-123",
        webhook_url="https://example.com/webhook",
        partition_key="endpoint#part"
    )
    
    # Validate webhook URL against allowlist
    is_valid = manager.validate_webhook_url("https://example.com/webhook")
"""

import ipaddress
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import pendulum

from .config import Config

__author__ = "SilvaEngine Team"
__version__ = "1.0.0"


class WebhookValidationError(Exception):
    """Raised when webhook URL fails security validation."""
    pass


class PushNotificationConfig:
    """
    Represents an A2A PushNotificationConfig.
    
    Based on A2A v1.0 specification for push notification configuration.
    """
    
    def __init__(
        self,
        task_id: str,
        webhook_url: str,
        partition_key: str,
        config_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        scopes: Optional[List[str]] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
    ):
        """
        Initialize push notification config.
        
        Args:
            task_id: Associated task identifier
            webhook_url: URL to send push notifications
            partition_key: Composite partition key for multi-tenancy
            config_id: Unique config identifier (generated if not provided)
            headers: Optional custom headers for webhook requests
            scopes: Optional list of notification scopes
            created_at: Creation timestamp (ISO format)
            updated_at: Last update timestamp (ISO format)
        """
        self.task_id = task_id
        self.webhook_url = webhook_url
        self.partition_key = partition_key
        self.config_id = config_id or f"push-{task_id}-{pendulum.now('UTC').timestamp()}"
        self.headers = headers or {}
        self.scopes = scopes or ["task_status", "task_artifact"]
        self.created_at = created_at or pendulum.now("UTC").to_iso8601_string()
        self.updated_at = updated_at or self.created_at
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return {
            "config_id": self.config_id,
            "task_id": self.task_id,
            "webhook_url": self.webhook_url,
            "partition_key": self.partition_key,
            "headers": self.headers,
            "scopes": self.scopes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PushNotificationConfig":
        """Create config from dictionary."""
        return cls(
            task_id=data["task_id"],
            webhook_url=data["webhook_url"],
            partition_key=data["partition_key"],
            config_id=data.get("config_id"),
            headers=data.get("headers"),
            scopes=data.get("scopes"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )


class WebhookUrlValidator:
    """
    SSRF protection via URL allowlist/denylist validation.
    
    Phase 7 Task 7: Validates webhook URLs against security policies
    to prevent Server-Side Request Forgery attacks.
    """
    
    # Default denylisted private/reserved CIDRs
    PRIVATE_CIDRS: List[str] = [
        "127.0.0.0/8",      # Loopback
        "10.0.0.0/8",       # Private Class A
        "172.16.0.0/12",    # Private Class B
        "192.168.0.0/16",   # Private Class C
        "169.254.0.0/16",   # Link-local
        "0.0.0.0/8",        # Current network
        "224.0.0.0/4",      # Multicast
        "240.0.0.0/4",      # Reserved
        "::1/128",          # IPv6 loopback
        "fe80::/10",        # IPv6 link-local
        "fc00::/7",         # IPv6 unique local
    ]
    
    # Default denylisted hostname patterns
    DENYLISTED_HOSTS: List[str] = [
        "localhost",
        "*.localhost",
        "*.local",
        "metadata.google.internal",
        "169.254.169.254",  # AWS metadata service
        "metadata.google.internal",
    ]
    
    # Allowed URL schemes
    ALLOWED_SCHEMES: Set[str] = {"https", "http"}
    
    # URL with port pattern
    HOST_PORT_PATTERN = re.compile(r"^([a-zA-Z0-9\-\.]+|\[[0-9a-fA-F:\.]+\])(?::(\d+))?$")
    
    def __init__(
        self,
        allowlist: Optional[List[str]] = None,
        denylist: Optional[List[str]] = None,
        require_https: bool = True,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize webhook validator.
        
        Args:
            allowlist: List of allowed hostname patterns (e.g., ["*.example.com", "api.example.org"])
            denylist: List of blocked hostname patterns (overrides defaults if provided)
            require_https: If True, only HTTPS URLs are allowed
            logger: Optional logger instance
        """
        self.allowlist = allowlist or []
        self.denylist = denylist or self.DENYLISTED_HOSTS.copy()
        self.require_https = require_https
        self.logger = logger or logging.getLogger(__name__)
        
        # Compile denylisted CIDRs
        self._denylisted_networks = [
            ipaddress.ip_network(cidr) for cidr in self.PRIVATE_CIDRS
        ]
    
    def validate(self, url: str) -> Tuple[bool, Optional[str]]:
        """
        Validate webhook URL against security policies.
        
        Args:
            url: Webhook URL to validate
            
        Returns:
            Tuple of (is_valid, error_message)
            - is_valid: True if URL passes all checks
            - error_message: Description of validation failure, or None if valid
        """
        try:
            parsed = urlparse(url)
            
            # Check scheme
            if parsed.scheme not in self.ALLOWED_SCHEMES:
                return False, f"Invalid URL scheme '{parsed.scheme}'. Allowed: {self.ALLOWED_SCHEMES}"
            
            if self.require_https and parsed.scheme != "https":
                return False, "HTTPS required for webhook URLs. Configure require_https=False to allow HTTP."
            
            hostname = parsed.hostname

            if not hostname:
                return False, "URL must contain a valid hostname"
            
            # Check for denylisted hosts
            for pattern in self.denylist:
                if self._match_pattern(hostname, pattern):
                    return False, f"Hostname '{hostname}' matches denylist pattern '{pattern}'"
            
            # Check for private IP addresses
            try:
                ip = ipaddress.ip_address(hostname)
                for network in self._denylisted_networks:
                    if ip in network:
                        return False, f"IP address {hostname} is in private/reserved range {network}"
            except ValueError:
                # Not an IP address, continue with hostname checks
                pass
            
            # Check allowlist if configured
            if self.allowlist:
                allowed = False
                for pattern in self.allowlist:
                    if self._match_pattern(hostname, pattern):
                        allowed = True
                        break
                if not allowed:
                    return False, f"Hostname '{hostname}' not in allowlist"
            
            # Check for common SSRF bypasses
            if self._contains_ssrf_bypass(url):
                return False, "URL contains potential SSRF bypass patterns"
            
            self.logger.debug(f"Webhook URL validated: {url}")
            return True, None
            
        except Exception as e:
            self.logger.error(f"URL validation error: {e}")
            return False, f"URL parsing error: {str(e)}"
    
    def _match_pattern(self, hostname: str, pattern: str) -> bool:
        """
        Check if hostname matches pattern (supports wildcards).
        
        Args:
            hostname: Hostname to check
            pattern: Pattern to match (e.g., "*.example.com" or "api.example.org")
            
        Returns:
            True if hostname matches pattern
        """
        if pattern.startswith("*."):
            # Wildcard subdomain match
            domain = pattern[2:]
            return hostname == domain or hostname.endswith("." + domain)
        else:
            return hostname == pattern or hostname.endswith("." + pattern)
    
    def _contains_ssrf_bypass(self, url: str) -> bool:
        """
        Check for common SSRF bypass techniques.
        
        Args:
            url: URL to check
            
        Returns:
            True if URL contains potential bypass patterns
        """
        # Check for URL encoding tricks
        bypass_patterns = [
            "%00",           # Null byte
            "%2e",           # Encoded dot
            "%2f",           # Encoded slash
            "..",            # Path traversal
            "@",             # Credential injection (e.g., http://evil.com@good.com)
            "#",             # Fragment injection
        ]
        
        for pattern in bypass_patterns:
            if pattern in url.lower():
                return True
        
        # Check for decimal/octal/hex encoded IPs
        # These are common SSRF bypasses for IP-based restrictions
        parts = url.split("://")[-1].split("/")[0].split(":")[0]
        try:
            # Try to parse as integer (decimal IP encoding)
            int(parts)
            return True
        except ValueError:
            pass
        
        return False


class PushNotificationManager:
    """
    Manages PushNotificationConfig CRUD operations.
    
    Provides A2A-standard push notification configuration with SSRF protection.
    """
    
    def __init__(
        self,
        task_store: Any,
        logger: Optional[logging.Logger] = None,
        webhook_allowlist: Optional[List[str]] = None,
        require_https: bool = True,
    ):
        """
        Initialize push notification manager.
        
        Args:
            task_store: TaskStore instance for persistence
            logger: Optional logger instance
            webhook_allowlist: Optional list of allowed webhook URL patterns
            require_https: If True, only HTTPS webhook URLs are allowed
        """
        self.task_store = task_store
        self.logger = logger or logging.getLogger(__name__)
        self.webhook_validator = WebhookUrlValidator(
            allowlist=webhook_allowlist,
            require_https=require_https,
            logger=self.logger,
        )
        
        # In-memory cache for push configs (task_id -> PushNotificationConfig)
        # Production should use Redis or similar
        self._config_cache: Dict[str, PushNotificationConfig] = {}
    
    async def create_push_config(
        self,
        task_id: str,
        webhook_url: str,
        partition_key: str,
        headers: Optional[Dict[str, str]] = None,
        scopes: Optional[List[str]] = None,
    ) -> PushNotificationConfig:
        """
        CreateTaskPushNotificationConfig: Register webhook for task updates.
        
        Args:
            task_id: Task to receive notifications for
            webhook_url: URL to send push notifications
            partition_key: Composite partition key for multi-tenancy
            headers: Optional custom headers for webhook requests
            scopes: Optional notification scopes (default: task_status, task_artifact)
            
        Returns:
            PushNotificationConfig instance
            
        Raises:
            WebhookValidationError: If webhook URL fails security validation
            ValueError: If task does not exist
        """
        # Validate webhook URL (anti-SSRF)
        is_valid, error_msg = self.webhook_validator.validate(webhook_url)
        if not is_valid:
            raise WebhookValidationError(f"Invalid webhook URL: {error_msg}")
        
        # Verify task exists
        task = await self.task_store.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        
        # Create config
        config = PushNotificationConfig(
            task_id=task_id,
            webhook_url=webhook_url,
            partition_key=partition_key,
            headers=headers,
            scopes=scopes or ["task_status", "task_artifact"],
        )
        
        # Persist to DynamoDB (via GraphQL)
        await self._persist_config(config)
        
        # Cache for quick access
        self._config_cache[config.config_id] = config
        
        self.logger.info(f"Push config created: {config.config_id} for task {task_id}")
        return config
    
    async def get_push_config(
        self,
        config_id: str,
    ) -> Optional[PushNotificationConfig]:
        """
        GetTaskPushNotificationConfig: Retrieve push notification configuration.
        
        Args:
            config_id: Push notification config identifier
            
        Returns:
            PushNotificationConfig if found, None otherwise
        """
        # Check cache first
        if config_id in self._config_cache:
            return self._config_cache[config_id]
        
        # Load from persistence
        config = await self._load_config(config_id)
        if config:
            self._config_cache[config_id] = config
        
        return config
    
    async def list_push_configs(
        self,
        task_id: Optional[str] = None,
        partition_key: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[List[PushNotificationConfig], Optional[str]]:
        """
        ListTaskPushNotificationConfigs: List push notification configurations.
        
        Args:
            task_id: Filter by task ID (optional)
            partition_key: Filter by partition key (optional)
            limit: Maximum results to return
            cursor: Pagination cursor from previous query
            
        Returns:
            Tuple of (list of configs, next_cursor or None)
        """
        configs = []
        next_cursor = None
        
        try:
            # Query from DynamoDB via GraphQL
            query = """
                query ListPushConfigs(
                    $partitionKey: String,
                    $taskId: String,
                    $limit: Int,
                    $cursor: String
                ) {
                    a2aPushConfigList(
                        partitionKey: $partitionKey,
                        taskId: $taskId,
                        limit: $limit,
                        cursor: $cursor
                    ) {
                        a2aPushConfigList {
                            configId
                            taskId
                            webhookUrl
                            partitionKey
                            headers
                            scopes
                            createdAt
                            updatedAt
                        }
                        nextCursor
                    }
                }
            """
            
            variables = {
                "limit": limit,
                "cursor": cursor,
            }
            if partition_key:
                variables["partitionKey"] = partition_key
            if task_id:
                variables["taskId"] = task_id
            
            if Config.a2a_core:
                result = Config.a2a_core.a2a_core_graphql(
                    partition_key=partition_key or "default#default",
                    query=query,
                    variables=variables,
                )
                
                data = result.get("data", {}).get("a2aPushConfigList", {})
                items = data.get("a2aPushConfigList", [])
                next_cursor = data.get("nextCursor")
                
                for item in items:
                    config = PushNotificationConfig(
                        task_id=item["taskId"],
                        webhook_url=item["webhookUrl"],
                        partition_key=item["partitionKey"],
                        config_id=item["configId"],
                        headers=item.get("headers"),
                        scopes=item.get("scopes"),
                        created_at=item.get("createdAt"),
                        updated_at=item.get("updatedAt"),
                    )
                    configs.append(config)
                    self._config_cache[config.config_id] = config
        
        except Exception as e:
            self.logger.error(f"Failed to list push configs: {e}")
        
        return configs, next_cursor
    
    async def delete_push_config(
        self,
        config_id: str,
    ) -> bool:
        """
        DeleteTaskPushNotificationConfig: Remove push notification configuration.
        
        Args:
            config_id: Push notification config identifier
            
        Returns:
            True if deleted successfully, False otherwise
        """
        try:
            # Remove from cache
            if config_id in self._config_cache:
                del self._config_cache[config_id]
            
            # Delete from DynamoDB
            mutation = """
                mutation DeletePushConfig($configId: String!) {
                    deleteA2aPushConfig(configId: $configId) {
                        success
                    }
                }
            """
            
            if Config.a2a_core:
                Config.a2a_core.a2a_core_graphql(
                    partition_key="default#default",
                    query=mutation,
                    variables={"configId": config_id},
                )
            
            self.logger.info(f"Push config deleted: {config_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to delete push config {config_id}: {e}")
            return False
    
    async def send_push_notification(
        self,
        config_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> bool:
        """
        Send push notification to configured webhook.
        
        Args:
            config_id: Push notification config identifier
            event_type: Type of event (task_status, task_artifact, etc.)
            payload: Event payload
            
        Returns:
            True if notification sent successfully, False otherwise
        """
        import httpx
        
        config = await self.get_push_config(config_id)
        if not config:
            self.logger.warning(f"Push config not found: {config_id}")
            return False
        
        # Check if event type is in scopes
        if event_type not in config.scopes:
            return True  # Silently skip non-subscribed events
        
        # Re-validate URL before sending (security)
        is_valid, error_msg = self.webhook_validator.validate(config.webhook_url)
        if not is_valid:
            self.logger.error(f"Webhook URL no longer valid: {error_msg}")
            return False
        
        # Prepare notification payload
        notification = {
            "config_id": config_id,
            "task_id": config.task_id,
            "event_type": event_type,
            "timestamp": pendulum.now("UTC").to_iso8601_string(),
            "payload": payload,
        }
        
        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            "X-A2A-Push-Event": event_type,
        }
        headers.update(config.headers)
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    config.webhook_url,
                    json=notification,
                    headers=headers,
                )
                
                if response.status_code < 300:
                    self.logger.debug(f"Push notification sent to {config.webhook_url}")
                    return True
                else:
                    self.logger.warning(
                        f"Push notification failed: {response.status_code} - {response.text}"
                    )
                    return False
                    
        except Exception as e:
            self.logger.error(f"Push notification error: {e}")
            return False
    
    async def _persist_config(self, config: PushNotificationConfig) -> None:
        """Persist config to DynamoDB via GraphQL."""
        from silvaengine_utility.serializer import Serializer
        
        mutation = """
            mutation CreatePushConfig(
                $configId: String!,
                $taskId: String!,
                $webhookUrl: String!,
                $partitionKey: String!,
                $headers: String,
                $scopes: String,
                $createdAt: String!,
                $updatedAt: String!
            ) {
                insertUpdateA2aPushConfig(
                    configId: $configId,
                    taskId: $taskId,
                    webhookUrl: $webhookUrl,
                    partitionKey: $partitionKey,
                    headers: $headers,
                    scopes: $scopes,
                    createdAt: $createdAt,
                    updatedAt: $updatedAt
                ) {
                    configId
                    taskId
                    webhookUrl
                }
            }
        """
        
        variables = {
            "configId": config.config_id,
            "taskId": config.task_id,
            "webhookUrl": config.webhook_url,
            "partitionKey": config.partition_key,
            "headers": Serializer.json_dumps(config.headers) if config.headers else None,
            "scopes": Serializer.json_dumps(config.scopes) if config.scopes else None,
            "createdAt": config.created_at,
            "updatedAt": config.updated_at,
        }
        
        if Config.a2a_core:
            Config.a2a_core.a2a_core_graphql(
                partition_key=config.partition_key,
                query=mutation,
                variables=variables,
            )
    
    async def _load_config(self, config_id: str) -> Optional[PushNotificationConfig]:
        """Load config from DynamoDB via GraphQL."""
        from silvaengine_utility.serializer import Serializer
        
        query = """
            query GetPushConfig($configId: String!) {
                a2aPushConfig(configId: $configId) {
                    configId
                    taskId
                    webhookUrl
                    partitionKey
                    headers
                    scopes
                    createdAt
                    updatedAt
                }
            }
        """
        
        try:
            if Config.a2a_core:
                result = Config.a2a_core.a2a_core_graphql(
                    partition_key="default#default",
                    query=query,
                    variables={"configId": config_id},
                )
                
                data = result.get("data", {}).get("a2aPushConfig", {})
                if data:
                    return PushNotificationConfig(
                        task_id=data["taskId"],
                        webhook_url=data["webhookUrl"],
                        partition_key=data["partitionKey"],
                        config_id=data["configId"],
                        headers=Serializer.json_loads(data["headers"]) if data.get("headers") else None,
                        scopes=Serializer.json_loads(data["scopes"]) if data.get("scopes") else None,
                        created_at=data.get("createdAt"),
                        updated_at=data.get("updatedAt"),
                    )
        except Exception as e:
            self.logger.error(f"Failed to load push config {config_id}: {e}")
        
        return None
    
    def validate_webhook_url(self, url: str) -> Tuple[bool, Optional[str]]:
        """
        Public method to validate webhook URL.
        
        Args:
            url: URL to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        return self.webhook_validator.validate(url)


def get_webhook_allowlist_from_env() -> List[str]:
    """
    Load webhook allowlist from environment variable.
    
    Environment: A2A_PUSH_WEBHOOK_ALLOWLIST
    Format: Comma-separated list of hostname patterns
    Example: *.example.com,api.example.org,webhook.mycompany.io
    
    Returns:
        List of allowed hostname patterns
    """
    import os
    
    allowlist_str = os.environ.get("A2A_PUSH_WEBHOOK_ALLOWLIST", "")
    if not allowlist_str:
        return []
    
    return [pattern.strip() for pattern in allowlist_str.split(",") if pattern.strip()]


__all__ = [
    "PushNotificationConfig",
    "PushNotificationManager",
    "WebhookUrlValidator",
    "WebhookValidationError",
    "get_webhook_allowlist_from_env",
]
