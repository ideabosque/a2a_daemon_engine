#!/usr/bin/python
# -*- coding: utf-8 -*-
"""AI A2A Daemon Engine"""

__author__ = "SilvaEngine Team"
__version__ = "0.0.1"

# Lazy import to avoid circular import issues during testing
def __getattr__(name):
    if name == "A2ADaemonEngine":
        from .main import A2ADaemonEngine
        return A2ADaemonEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["A2ADaemonEngine"]
