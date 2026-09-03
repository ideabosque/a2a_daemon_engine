#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Register the openclaw-agent fixture in the A2A daemon's PostgreSQL backend
with full metadata (module_name, class_name, openclaw_* connection details).

This script uses a direct SQL INSERT/UPDATE instead of GraphQL because
the silvaengine_utility JSON scalar has a compatibility issue with
graphql-core that prevents passing dict variables to GraphQL mutations.

Prerequisites:
    - PostgreSQL container running (silvaengine-postgres)
    - A2A daemon or SilvaEngine Gateway running with db_backend=postgresql
    - OpenCLAW Gateway running (default http://127.0.0.1:18789)

Usage:
    python a2a_daemon_engine/tests/register_openclaw_agent.py

Author: bibow
"""
from __future__ import print_function

__author__ = "bibow"

import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Load gateway .env for connection details
# ---------------------------------------------------------------------------
GATEWAY_ENV = Path(
    os.getenv("GATEWAY_ENV",
              "C:/Users/bibo7/gitrepo/silvaengine/silvaengine_gateway/silvaengine_gateway/tests/.env")
)

env = {}
if GATEWAY_ENV.exists():
    with open(GATEWAY_ENV) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if " #" in value:
                value = value.split(" #", 1)[0].strip()
            if key:
                env[key] = value

PG_HOST = env.get("PG_HOST", "localhost")
PG_PORT = env.get("PG_PORT", "5432")
PG_USER = env.get("PG_USER", "silvaengine")
PG_PASSWORD = env.get("PG_PASSWORD", "silvaengine")
PG_DB = env.get("PG_DB", "silvaengine")
ENDPOINT_ID = env.get("endpoint_id", "gpt")
PART_ID = env.get("part_id", "nestaging")
PARTITION_KEY = f"{ENDPOINT_ID}#{PART_ID}"

OPENCLAW_API_URL = env.get("OPENCLAW_API_URL", "http://127.0.0.1:18789")
OPENCLAW_API_KEY = env.get("OPENCLAW_API_KEY", "")
OPENCLAW_AGENT_ID = env.get("OPENCLAW_AGENT_ID", "")
OPENCLAW_AGENT_SELECTOR = env.get("OPENCLAW_AGENT_SELECTOR", "model")

# ---------------------------------------------------------------------------
# Register agent via direct SQL
# ---------------------------------------------------------------------------
METADATA = json.dumps({
    "module_name": "a2a_daemon_engine.handlers.openclaw_handler",
    "class_name": "OpenClawAgentHandler",
    "openclaw_api_url": OPENCLAW_API_URL,
    "openclaw_api_key": OPENCLAW_API_KEY,
    "openclaw_agent_id": OPENCLAW_AGENT_ID,
    "openclaw_agent_selector": OPENCLAW_AGENT_SELECTOR,
    "openclaw_timeout": 300.0,
})

import psycopg2

conn = psycopg2.connect(
    host=PG_HOST,
    port=PG_PORT,
    user=PG_USER,
    password=PG_PASSWORD,
    dbname=PG_DB,
)

cur = conn.cursor()

# Check if agent exists
cur.execute(
    "SELECT agent_id FROM a2a_agents WHERE partition_key = %s AND agent_id = %s",
    (PARTITION_KEY, "openclaw-agent"),
)
exists = cur.fetchone()

if exists:
    # Update existing agent with metadata
    cur.execute(
        """UPDATE a2a_agents
           SET metadata = %s,
               endpoint_url = %s,
               updated_at = NOW()
           WHERE partition_key = %s AND agent_id = %s""",
        (METADATA, "http://127.0.0.1:8765", PARTITION_KEY, "openclaw-agent"),
    )
    print(f"Updated openclaw-agent with handler metadata")
else:
    # Insert new agent
    cur.execute(
        """INSERT INTO a2a_agents
           (partition_key, agent_id, endpoint_id, part_id, agent_name,
            endpoint_url, status, metadata, updated_by, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())""",
        (PARTITION_KEY, "openclaw-agent", ENDPOINT_ID, PART_ID,
         "OpenCLAW Agent", "http://127.0.0.1:8765", "active",
         METADATA, "e2e-test"),
    )
    print(f"Inserted openclaw-agent with handler metadata")

conn.commit()

# Verify
cur.execute(
    "SELECT agent_id, agent_name, metadata FROM a2a_agents WHERE partition_key = %s AND agent_id = %s",
    (PARTITION_KEY, "openclaw-agent"),
)
row = cur.fetchone()
print(f"\nVerified:")
print(f"  agent_id:  {row[0]}")
print(f"  agent_name: {row[1]}")
print(f"  metadata:  {row[2]}")

cur.close()
conn.close()