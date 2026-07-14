# -*- coding: utf-8 -*-
"""Backend dispatch boundary for repository and loader selection.

``get_repo(entity_type)`` returns the active repository based on
``Config.DB_BACKEND``. ``get_loaders(context)`` is a forward-compatible
stub (no nested resolvers exist today).
"""
from __future__ import print_function

__author__ = "bibow"

from typing import Any, Dict

from ...handlers.config import Config
from .base import EntityRepository


# --- Repository registry -----------------------------------------------------

_repo_registry: Dict[str, Dict[str, EntityRepository]] = {
    "dynamodb": {},
    "postgresql": {},
}


def register_repo(backend: str, entity_type: str, repo: EntityRepository) -> None:
    """Register a repository instance for a backend + entity_type."""
    if backend not in _repo_registry:
        raise ValueError(f"Unknown backend: {backend}")
    _repo_registry[backend][entity_type] = repo


def get_repo(entity_type: str) -> EntityRepository:
    """Return the active repository for the given entity type.

    Raises KeyError if no repository is registered for the current
    backend + entity_type combination.
    """
    backend = Config.DB_BACKEND
    repo = _repo_registry.get(backend, {}).get(entity_type)
    if repo is None:
        # Lazily initialize repos on first access
        if backend == "dynamodb":
            _init_dynamodb_repos()
            repo = _repo_registry["dynamodb"].get(entity_type)
        elif backend == "postgresql":
            _init_postgresql_repos()
            repo = _repo_registry["postgresql"].get(entity_type)

    if repo is None:
        raise KeyError(
            f"No repository registered for entity '{entity_type}' "
            f"on backend '{backend}'"
        )
    return repo


def get_loaders(context: Dict[str, Any]) -> Any:
    """Return request-scoped loaders for the active backend.

    No nested resolvers exist today, so this is a stub.
    Returns None — implement when a nested-resolver surface is added.
    """
    return None


# --- Lazy initialization -----------------------------------------------------

_dynamodb_repos_initialized = False
_postgresql_repos_initialized = False


def _init_dynamodb_repos() -> None:
    """Lazily register all DynamoDB repositories."""
    global _dynamodb_repos_initialized
    if _dynamodb_repos_initialized:
        return
    _dynamodb_repos_initialized = True

    from .dynamodb import register_all as register_dynamodb

    register_dynamodb(_repo_registry["dynamodb"])


def _init_postgresql_repos() -> None:
    """Lazily register all PostgreSQL repositories."""
    global _postgresql_repos_initialized
    if _postgresql_repos_initialized:
        return
    _postgresql_repos_initialized = True

    from .postgresql import register_all as register_postgresql

    register_postgresql(_repo_registry["postgresql"])


def clear_registry() -> None:
    """Clear all registered repositories (useful for tests)."""
    global _dynamodb_repos_initialized, _postgresql_repos_initialized
    _repo_registry["dynamodb"].clear()
    _repo_registry["postgresql"].clear()
    _dynamodb_repos_initialized = False
    _postgresql_repos_initialized = False