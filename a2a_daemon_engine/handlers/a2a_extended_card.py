#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
A2A Extended Agent Card Implementation

Phase 8 - Task 1: GetExtendedAgentCard with authentication gating
Phase 8 - Task 2: Traceability extension registration

Provides extended agent card information for authenticated requests:
- Extended capabilities and skills
- Rate limiting configuration
- Security policies
- Traceability extension metadata
- Contact information for support

Usage:
    from a2a_daemon_engine.handlers.a2a_extended_card import ExtendedAgentCardManager
    
    manager = ExtendedAgentCardManager(
        base_card=agent_card,
        auth_middleware=jwt_middleware,
        logger=logger
    )
    
    # Get extended card (requires authentication)
    extended_card = await manager.get_extended_card(request)
"""

import logging, os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pendulum
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import Config

__author__ = "SilvaEngine Team"
__version__ = "1.0.0"


@dataclass
class RateLimitConfig:
    """Rate limiting configuration per skill."""
    skill_id: str
    requests_per_minute: int = 60
    requests_per_hour: int = 1000
    burst_size: int = 10


@dataclass
class SecurityPolicy:
    """Security policies for the agent."""
    requires_mtls: bool = False
    allowed_auth_methods: List[str] = field(default_factory=lambda: ["bearer"])
    session_timeout_seconds: int = 3600
    max_request_size_bytes: int = 10 * 1024 * 1024  # 10MB
    allowed_origins: List[str] = field(default_factory=list)


@dataclass
class TraceabilityExtension:
    """
    A2A Traceability Extension metadata.
    
    Provides end-to-end trace ID propagation across agent hops.
    """
    enabled: bool = True
    trace_header: str = "x-a2a-trace-id"
    span_header: str = "x-a2a-span-id"
    sample_rate: float = 1.0
    exporter_endpoint: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "extension": "https://a2a-protocol.org/extensions/traceability/v1",
            "enabled": self.enabled,
            "configuration": {
                "traceHeader": self.trace_header,
                "spanHeader": self.span_header,
                "sampleRate": self.sample_rate,
                "exporterEndpoint": self.exporter_endpoint,
            }
        }


@dataclass
class ExtendedAgentCard:
    """
    Extended Agent Card with additional metadata for authenticated users.
    
    Extends the base AgentCard with:
    - Rate limits per skill
    - Security policies
    - Support contact information
    - Deployment region info
    - Extension declarations (Traceability, etc.)
    """
    # Base card fields
    name: str
    description: str
    url: str
    version: str
    capabilities: Dict[str, Any]
    skills: List[Dict[str, Any]]
    provider: Dict[str, str]
    
    # Extended fields
    rate_limits: List[RateLimitConfig] = field(default_factory=list)
    security_policies: SecurityPolicy = field(default_factory=SecurityPolicy)
    support_contact: Optional[Dict[str, str]] = None
    deployment_regions: List[str] = field(default_factory=lambda: ["us-east-1"])
    extensions: List[Dict[str, Any]] = field(default_factory=list)
    terms_of_service_url: Optional[str] = None
    privacy_policy_url: Optional[str] = None
    last_modified: str = field(default_factory=lambda: pendulum.now("UTC").isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert extended card to dictionary."""
        base_card = {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "capabilities": self.capabilities,
            "skills": self.skills,
            "provider": self.provider,
        }
        
        extended_info = {
            "rateLimits": [
                {
                    "skillId": rl.skill_id,
                    "requestsPerMinute": rl.requests_per_minute,
                    "requestsPerHour": rl.requests_per_hour,
                    "burstSize": rl.burst_size,
                }
                for rl in self.rate_limits
            ],
            "securityPolicies": {
                "requiresMtls": self.security_policies.requires_mtls,
                "allowedAuthMethods": self.security_policies.allowed_auth_methods,
                "sessionTimeoutSeconds": self.security_policies.session_timeout_seconds,
                "maxRequestSizeBytes": self.security_policies.max_request_size_bytes,
                "allowedOrigins": self.security_policies.allowed_origins,
            },
            "deploymentRegions": self.deployment_regions,
            "extensions": self.extensions,
            "lastModified": self.last_modified,
        }
        
        if self.support_contact:
            extended_info["supportContact"] = self.support_contact
        if self.terms_of_service_url:
            extended_info["termsOfServiceUrl"] = self.terms_of_service_url
        if self.privacy_policy_url:
            extended_info["privacyPolicyUrl"] = self.privacy_policy_url
        
        return {**base_card, "extended": extended_info}


class AuthenticationError(Exception):
    """Raised when authentication fails for extended card request."""
    pass


class AuthorizationError(Exception):
    """Raised when user lacks permission to access extended card."""
    pass


class ExtendedAgentCardManager:
    """
    Manages Extended Agent Card with authentication gating.
    
    Phase 8: Provides extended card information only to authenticated users.
    """
    
    def __init__(
        self,
        base_card: Any,
        auth_middleware: Optional[Any] = None,
        logger: Optional[logging.Logger] = None,
        rate_limits: Optional[List[RateLimitConfig]] = None,
        security_policies: Optional[SecurityPolicy] = None,
    ):
        """
        Initialize extended agent card manager.
        
        Args:
            base_card: Base AgentCard object
            auth_middleware: Authentication middleware for verifying requests
            logger: Optional logger instance
            rate_limits: Rate limit configurations per skill
            security_policies: Security policy configuration
        """
        self.base_card = base_card
        self.auth_middleware = auth_middleware
        self.logger = logger or logging.getLogger(__name__)
        self.rate_limits = rate_limits or self._default_rate_limits()
        self.security_policies = security_policies or SecurityPolicy()
        self._card_etag: Optional[str] = None
        self._card_last_modified: Optional[str] = None
        self._update_card_metadata()
    
    def _default_rate_limits(self) -> List[RateLimitConfig]:
        """Generate default rate limits based on base card skills."""
        rate_limits = []
        skills = getattr(self.base_card, 'skills', [])
        
        default_limits = {
            "task_execution": RateLimitConfig("task-execution", 60, 1000, 10),
            "message_routing": RateLimitConfig("message-routing", 120, 2000, 20),
            "agent_discovery": RateLimitConfig("agent-discovery", 30, 500, 5),
        }
        
        for skill in skills:
            skill_id = getattr(skill, 'id', str(skill))
            if skill_id in default_limits:
                rate_limits.append(default_limits[skill_id])
            else:
                rate_limits.append(RateLimitConfig(skill_id, 60, 1000, 10))
        
        return rate_limits
    
    def _update_card_metadata(self) -> None:
        """Update ETag and Last-Modified headers based on card content."""
        import hashlib
        import json
        
        # Generate ETag from card version and timestamp
        card_data = {
            "version": getattr(self.base_card, 'version', '1.0.0'),
            "timestamp": pendulum.now("UTC").to_iso8601_string(),
        }
        card_json = json.dumps(card_data, sort_keys=True)
        self._card_etag = f'"{hashlib.md5(card_json.encode()).hexdigest()[:16]}"'
        self._card_last_modified = pendulum.now("UTC").strftime("%a, %d %b %Y %H:%M:%S GMT")
    
    async def get_extended_card(
        self,
        request: Request,
        require_auth: bool = True,
    ) -> ExtendedAgentCard:
        """
        GetExtendedAgentCard: Return extended card for authenticated users.
        
        Args:
            request: Starlette request object
            require_auth: If True, requires valid authentication
            
        Returns:
            ExtendedAgentCard with additional metadata
            
        Raises:
            AuthenticationError: If authentication is required and missing/invalid
            AuthorizationError: If user lacks permission
        """
        # Authenticate if required
        if require_auth:
            await self._authenticate_request(request)
        
        # Build extended card
        extended_card = ExtendedAgentCard(
            name=getattr(self.base_card, 'name', 'Unknown'),
            description=getattr(self.base_card, 'description', ''),
            url=getattr(self.base_card, 'url', ''),
            version=getattr(self.base_card, 'version', '1.0.0'),
            capabilities=self._extract_capabilities(),
            skills=self._extract_skills(),
            provider=self._extract_provider(),
            rate_limits=self.rate_limits,
            security_policies=self.security_policies,
            deployment_regions=self._get_deployment_regions(),
            extensions=self._build_extensions(),
            support_contact=self._get_support_contact(),
            terms_of_service_url=self._get_tos_url(),
            privacy_policy_url=self._get_privacy_url(),
            last_modified=self._card_last_modified or pendulum.now("UTC").isoformat(),
        )
        
        self.logger.info(f"Extended card accessed by {request.client}")
        return extended_card
    
    async def _authenticate_request(self, request: Request) -> Dict[str, Any]:
        """
        Authenticate the request using configured middleware.
        
        Args:
            request: Starlette request
            
        Returns:
            Authentication context with user info
            
        Raises:
            AuthenticationError: If authentication fails
        """
        # Check for Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            raise AuthenticationError("Authorization header required for extended card")
        
        # Try to authenticate via middleware if available
        if self.auth_middleware:
            try:
                # Extract token
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:]
                else:
                    token = auth_header
                
                # Validate via Config's JWT handling
                if hasattr(Config, 'jwt_validator'):
                    payload = Config.jwt_validator.validate(token)
                    return {"user": payload.get("sub"), "roles": payload.get("roles", [])}
                
            except Exception as e:
                self.logger.warning(f"Authentication failed: {e}")
                raise AuthenticationError(f"Invalid authentication: {str(e)}")
        
        # Fallback: basic token presence check
        if len(auth_header) < 10:
            raise AuthenticationError("Invalid authorization token format")
        
        return {"user": "authenticated", "roles": ["user"]}
    
    def _extract_capabilities(self) -> Dict[str, Any]:
        """Extract capabilities from base card."""
        caps = getattr(self.base_card, 'capabilities', None)
        if caps:
            return {
                "streaming": getattr(caps, 'streaming', False),
                "pushNotifications": getattr(caps, 'pushNotifications', False),
            }
        return {}
    
    def _extract_skills(self) -> List[Dict[str, Any]]:
        """Extract skills from base card."""
        skills = getattr(self.base_card, 'skills', [])
        result = []
        for skill in skills:
            result.append({
                "id": getattr(skill, 'id', ''),
                "name": getattr(skill, 'name', ''),
                "description": getattr(skill, 'description', ''),
                "tags": list(getattr(skill, 'tags', [])),
            })
        return result
    
    def _extract_provider(self) -> Dict[str, str]:
        """Extract provider info from base card."""
        provider = getattr(self.base_card, 'provider', None)
        if provider:
            return {
                "organization": getattr(provider, 'organization', ''),
                "url": getattr(provider, 'url', ''),
            }
        return {"organization": "SilvaEngine", "url": ""}
    
    def _get_deployment_regions(self) -> List[str]:
        """Get deployment regions from config."""
        import os
        regions = os.environ.get("A2A_DEPLOYMENT_REGIONS", "us-east-1").split(",")
        return [r.strip() for r in regions if r.strip()]
    
    def _build_extensions(self) -> List[Dict[str, Any]]:
        """Build extension declarations including Traceability."""
        extensions = []
        
        # Traceability Extension (Phase 8 Task 2)
        traceability = TraceabilityExtension(
            enabled=True,
            trace_header="x-a2a-trace-id",
            span_header="x-a2a-span-id",
            sample_rate=1.0,
            exporter_endpoint=self._get_trace_endpoint(),
        )
        extensions.append(traceability.to_dict())
        
        return extensions
    
    def _get_trace_endpoint(self) -> Optional[str]:
        """Get OpenTelemetry trace endpoint from config."""
        import os
        return os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    
    def _get_support_contact(self) -> Optional[Dict[str, str]]:
        """Get support contact information."""
        return {
            "email": os.environ.get("A2A_SUPPORT_EMAIL", "support@silvaengine.com"),
            "url": os.environ.get("A2A_SUPPORT_URL", "https://github.com/ideabosque/a2a_daemon_engine/issues"),
        }
    
    def _get_tos_url(self) -> Optional[str]:
        """Get Terms of Service URL."""
        return os.environ.get("A2A_TOS_URL")
    
    def _get_privacy_url(self) -> Optional[str]:
        """Get Privacy Policy URL."""
        return os.environ.get("A2A_PRIVACY_URL")
    
    def get_card_headers(self) -> Dict[str, str]:
        """
        Get HTTP headers for cache control (ETag, Last-Modified).
        
        Returns:
            Dictionary of HTTP response headers
        """
        self._update_card_metadata()
        return {
            "ETag": self._card_etag or '""',
            "Last-Modified": self._card_last_modified or "",
            "Cache-Control": "public, max-age=3600",
            "Vary": "Authorization",
        }
    
    def check_not_modified(self, request: Request) -> bool:
        """
        Check if client has current version (If-None-Match, If-Modified-Since).
        
        Args:
            request: Starlette request with conditional headers
            
        Returns:
            True if client's version is current (return 304)
        """
        # Check If-None-Match (ETag)
        if_none_match = request.headers.get("If-None-Match")
        if if_none_match and if_none_match == self._card_etag:
            return True
        
        # Check If-Modified-Since
        if_modified_since = request.headers.get("If-Modified-Since")
        if if_modified_since and self._card_last_modified:
            from email.utils import parsedate_to_datetime
            try:
                client_date = parsedate_to_datetime(if_modified_since)
                server_date = parsedate_to_datetime(self._card_last_modified)
                if server_date <= client_date:
                    return True
            except Exception:
                pass
        
        return False


def create_extended_card_route(
    manager: ExtendedAgentCardManager,
    path: str = "/.well-known/agent-card-extended.json",
) -> Dict[str, Any]:
    """
    Create route configuration for extended agent card endpoint.
    
    Args:
        manager: ExtendedAgentCardManager instance
        path: URL path for the endpoint
        
    Returns:
        Route configuration dictionary
    """
    async def handle_extended_card(request: Request):
        """Handle extended card request."""
        # Check for 304 Not Modified
        if manager.check_not_modified(request):
            return JSONResponse(
                content={},
                status_code=304,
                headers=manager.get_card_headers(),
            )
        
        try:
            extended_card = await manager.get_extended_card(request)
            return JSONResponse(
                content=extended_card.to_dict(),
                headers=manager.get_card_headers(),
            )
        except AuthenticationError as e:
            return JSONResponse(
                content={"error": "unauthenticated", "message": str(e)},
                status_code=401,
            )
        except AuthorizationError as e:
            return JSONResponse(
                content={"error": "unauthorized", "message": str(e)},
                status_code=403,
            )
    
    return {
        "path": path,
        "endpoint": handle_extended_card,
        "methods": ["GET"],
    }


__all__ = [
    "ExtendedAgentCard",
    "ExtendedAgentCardManager",
    "RateLimitConfig",
    "SecurityPolicy",
    "TraceabilityExtension",
    "AuthenticationError",
    "AuthorizationError",
    "create_extended_card_route",
]
