# -*- coding: utf-8 -*-
"""Repository abstraction boundary for dual-backend persistence.

This package wires backend-specific repositories (DynamoDB today, PostgreSQL
later) behind a single dispatch surface. See ``base.py`` for the abstract
``EntityRepository`` contract and ``dispatch.py`` for ``get_repo``/``register_repo``.
"""
from __future__ import print_function

__author__ = "bibow"

from .base import (
    DependencyExistsError,
    EntityNotFoundError,
    EntityRepository,
    RepositoryError,
)
from .dispatch import (
    clear_registry,
    get_loaders,
    get_repo,
    register_repo,
)

__all__ = [
    "EntityRepository",
    "RepositoryError",
    "EntityNotFoundError",
    "DependencyExistsError",
    "get_repo",
    "get_loaders",
    "register_repo",
    "clear_registry",
]