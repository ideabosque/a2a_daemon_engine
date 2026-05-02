#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
Authentication Router for A2A Daemon Engine

Provides authentication endpoints for token-based authentication.
Supports both local JWT and AWS Cognito authentication.
"""

import base64
import hashlib
import hmac
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from .config import Config, LocalUser
from .jwt_local import create_local_jwt, get_or_create_admin_token

__author__ = "SilvaEngine Team"

# Create router
router = APIRouter(prefix="/auth", tags=["authentication"])


def authenticate(username: str, password: str) -> LocalUser | None:
    """
    Authenticate user against local user database.

    Args:
        username: Username
        password: Plain text password

    Returns:
        LocalUser if authentication successful, None otherwise
    """
    if not Config._USERS:
        return None

    user = Config._USERS.get(username)
    return user if user and user.verify(password) else None


class Token(BaseModel):
    """Token response model"""

    access_token: str
    token_type: str = "bearer"


@router.post("/token", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends()):
    """
    OAuth2 compatible token endpoint.

    Supports both local JWT and AWS Cognito authentication based on
    the configured auth_provider.

    Args:
        form: OAuth2 password request form (username, password)

    Returns:
        Token response with access_token

    Raises:
        HTTPException: If credentials are invalid
    """
    if Config.auth_provider == "cognito":
        return get_cognito_token(form.username, form.password)
    else:
        return get_local_token(form.username, form.password)


def get_local_token(username: str, password: str) -> Dict[str, Any]:
    """
    Get local JWT token.

    Args:
        username: Username
        password: Password

    Returns:
        Token response dict

    Raises:
        HTTPException: If credentials are invalid
    """
    # Check admin credentials first
    if (
        Config.admin_username
        and Config.admin_password
        and username == Config.admin_username
        and password == Config.admin_password
    ):
        return {"access_token": get_or_create_admin_token(), "token_type": "bearer"}

    # Check user file
    user = authenticate(username, password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_local_jwt({"username": user.username, "roles": user.roles})

    return {"access_token": token, "token_type": "bearer"}


def get_cognito_token(username: str, password: str) -> Dict[str, Any]:
    """
    Get Cognito JWT token.

    Args:
        username: Cognito username
        password: Cognito password

    Returns:
        Token response dict

    Raises:
        HTTPException: If authentication fails
    """
    if not Config.aws_cognito_idp:
        raise HTTPException(
            status_code=500, detail="Cognito authentication not configured"
        )

    try:
        resp = Config.aws_cognito_idp.initiate_auth(
            AuthFlow="USER_PASSWORD_AUTH",
            ClientId=Config.cognito_app_client_id,
            AuthParameters={
                "USERNAME": username,
                "PASSWORD": password,
                "SECRET_HASH": secret_hash(username),
            },
        )

        tokens = resp["AuthenticationResult"]

        return {"access_token": tokens["AccessToken"], "token_type": "bearer"}

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Cognito authentication error: {e}")
        raise HTTPException(status_code=401, detail="Invalid credentials")


def secret_hash(username: str) -> str:
    """
    Calculate Cognito SECRET_HASH.

    Cognito expects: Base64(HMAC-SHA256(key=client_secret, msg=username+client_id))

    Args:
        username: Cognito username

    Returns:
        Base64-encoded secret hash

    Raises:
        ValueError: If Cognito configuration is incomplete
    """
    if not Config.cognito_app_client_id or not Config.cognito_app_secret:
        raise ValueError("Cognito app client ID and secret must be configured")

    message = (username + Config.cognito_app_client_id).encode("utf-8")
    key = Config.cognito_app_secret.encode("utf-8")
    digest = hmac.new(key, message, hashlib.sha256).digest()

    return base64.b64encode(digest).decode()
