#!/usr/bin/python
# -*- coding: utf-8 -*-
"""A2A Daemon Engine models package.

Restructured to match mcp_daemon_engine layout:
- models/dynamodb/    — PynamoDB model files
- models/repositories/ — dispatch boundary (base.py, dispatch.py)
- models/postgresql/   — SQLAlchemy models (stub for future dual-backend)
"""
from __future__ import print_function

__author__ = "bibow"