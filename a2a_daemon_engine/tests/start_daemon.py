#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A2A Daemon Engine - Test Startup Script

Starts the A2A Daemon Engine for local testing with API calls.
Loads configuration from .env file in tests directory.

Usage:
    # Start with default settings (loads .env automatically)
    python start_daemon.py

    # Start with specific environment
    python start_daemon.py --env production

    # Start on different port
    python start_daemon.py --port 8080

    # Start with verbose logging
    python start_daemon.py --verbose

Environment Variables (from .env file):
    - region_name: AWS region
    - aws_access_key_id: AWS access key
    - aws_secret_access_key: AWS secret key
    - endpoint_id: Platform endpoint identifier
    - part_id: Partition identifier
    - port: Server port (default: 8001)
    - transport: http or grpc (default: http)
    - JWT_SECRET_KEY: Secret key for JWT tokens
    - AUTH_PROVIDER: local or cognito (default: local)

API Endpoints available at:
    - http://localhost:{port}/health
    - http://localhost:{port}/{endpoint_id}/a2a_core_graphql
    - http://localhost:{port}/rest/a2a/{endpoint_id}/agents/register
    - http://localhost:{port}/rest/a2a/{endpoint_id}/tasks/create
    - http://localhost:{port}/rest/a2a-jsonrpc
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure logging for the daemon."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("a2a_daemon")


def load_env_file(env: str = None) -> None:
    """
    Load environment variables from .env file.

    Args:
        env: Environment name (e.g., 'production', 'staging').
             Loads .env.{env} if specified, otherwise loads .env
    """
    script_dir = Path(__file__).parent.absolute()

    if env:
        env_file = script_dir / f".env.{env}"
    else:
        env_file = script_dir / ".env"

    if env_file.exists():
        print(f"Loading environment from: {env_file}")
        load_dotenv(env_file)
    else:
        print(f"Warning: {env_file} not found, using existing environment variables")


def validate_config() -> dict:
    """
    Validate and return configuration settings.

    Returns:
        Dictionary with validated configuration
    """
    required_vars = ["region_name", "aws_access_key_id", "aws_secret_access_key"]
    config = {}

    # Check required variables
    missing = []
    for var in required_vars:
        value = os.getenv(var)
        if not value:
            missing.append(var)
        config[var] = value

    if missing:
        print(f"ERROR: Missing required environment variables: {', '.join(missing)}")
        print("Please set them in your .env file or environment")
        sys.exit(1)

    # Optional variables with defaults
    config["endpoint_id"] = os.getenv("endpoint_id", "test-endpoint")
    config["part_id"] = os.getenv("part_id", "test-part")
    config["port"] = int(os.getenv("port", "8001"))
    config["transport"] = os.getenv("transport", "http")
    config["initialize_tables"] = os.getenv("initialize_tables", "0") == "1"
    config["jwt_secret_key"] = os.getenv(
        "jwt_secret_key", "test-secret-key-for-testing-only-32-chars"
    )
    config["auth_provider"] = os.getenv("auth_provider", "local")

    return config


def print_startup_info(config: dict) -> None:
    """Print startup information and available endpoints."""
    port = config["port"]
    endpoint_id = config["endpoint_id"]

    print("\n" + "=" * 80)
    print("A2A Daemon Engine - Test Server")
    print("=" * 80)
    print(f"\nConfiguration:")
    print(f"  Region:        {config['region_name']}")
    print(f"  Endpoint ID:   {endpoint_id}")
    print(f"  Part ID:       {config['part_id']}")
    print(f"  Port:          {port}")
    print(f"  Transport:     {config['transport']}")
    print(f"  Auth Provider: {config['auth_provider']}")
    print(f"\nAvailable Endpoints:")
    print(f"  Health Check:  http://localhost:{port}/health")
    print(f"  Server Info:   http://localhost:{port}/{endpoint_id}")
    print(f"  GraphQL:       http://localhost:{port}/{endpoint_id}/a2a_core_graphql")
    print(f"  REST API Base: http://localhost:{port}/rest/a2a/{endpoint_id}")
    print(f"  JSON-RPC:      http://localhost:{port}/rest/a2a-jsonrpc")
    print(f"\nExample curl commands:")
    print(f"  # Health check")
    print(f"  curl http://localhost:{port}/health")
    print(f"\n  # GraphQL ping")
    print(f"  curl -X POST http://localhost:{port}/{endpoint_id}/a2a_core_graphql \\\n")
    print(f'    -H "Content-Type: application/json" \\\n')
    print(f'    -d \'{{"query": "query {{ ping }}"}}\'')
    print(f"\n  # Register agent (requires JWT token)")
    print(
        f"  curl -X POST http://localhost:{port}/rest/a2a/{endpoint_id}/agents/register \\\n"
    )
    print(f'    -H "Content-Type: application/json" \\\n')
    print(f'    -H "Authorization: Bearer <your-jwt-token>" \\\n')
    print(
        f'    -d \'{{"agent_id": "agent-001", "agent_name": "Test Agent", "capabilities": ["text"], "endpoint_url": "http://localhost:9001"}}\''
    )
    print(f"\n  # JSON-RPC")
    print(f"  curl -X POST http://localhost:{port}/rest/a2a-jsonrpc \\\n")
    print(f'    -H "Content-Type: application/json" \\\n')
    print(
        f'    -d \'{{"jsonrpc": "2.0", "method": "agent.getCard", "params": {{}}, "id": 1}}\''
    )
    print("\n" + "=" * 80)
    print("Press Ctrl+C to stop the server")
    print("=" * 80 + "\n")


def generate_jwt_token(secret_key: str, username: str = "test-user") -> str:
    """
    Generate a test JWT token for API authentication.

    Args:
        secret_key: JWT secret key
        username: Username for the token

    Returns:
        JWT token string
    """
    try:
        from jose import jwt
        import datetime

        payload = {
            "sub": username,
            "username": username,
            "iat": datetime.datetime.utcnow(),
            "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=24),
        }

        token = jwt.encode(payload, secret_key, algorithm="HS256")
        return token
    except ImportError:
        print("Warning: python-jose not installed, cannot generate JWT token")
        return "<install-python-jose-to-generate-token>"


def main():
    """Main entry point for the startup script."""
    parser = argparse.ArgumentParser(
        description="Start A2A Daemon Engine for testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                           # Start with default .env
  %(prog)s --env production          # Use .env.production
  %(prog)s --port 8080               # Start on port 8080
  %(prog)s --verbose                 # Enable debug logging
        """,
    )
    parser.add_argument(
        "--env", type=str, help="Environment name (loads .env.{env} file)"
    )
    parser.add_argument("--port", type=int, help="Override server port")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose (debug) logging"
    )
    parser.add_argument(
        "--generate-token",
        action="store_true",
        help="Generate and display a test JWT token",
    )

    args = parser.parse_args()

    # Load environment
    load_env_file(args.env)

    # Validate configuration
    config = validate_config()

    # Override port if specified
    if args.port:
        config["port"] = args.port
        os.environ["port"] = str(args.port)

    # Setup logging
    logger = setup_logging(args.verbose)

    # Generate token if requested
    if args.generate_token:
        token = generate_jwt_token(config["jwt_secret_key"])
        print("\n" + "=" * 80)
        print("Test JWT Token:")
        print("=" * 80)
        print(token)
        print("=" * 80 + "\n")
        print("Use this token in API calls with: -H 'Authorization: Bearer <token>'")
        print()

    # Import and start the daemon
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))

        from a2a_daemon_engine.main import A2ADaemonEngine

        # Create engine instance
        engine = A2ADaemonEngine(
            logger,
            **{
                "region_name": config["region_name"],
                "aws_access_key_id": config["aws_access_key_id"],
                "aws_secret_access_key": config["aws_secret_access_key"],
                "transport": config["transport"],
                "port": config["port"],
                "endpoint_id": config["endpoint_id"],
                "part_id": config["part_id"],
                "initialize_tables": config["initialize_tables"],
                "jwt_secret_key": config["jwt_secret_key"],
                "auth_provider": config["auth_provider"],
            },
        )

        # Print startup info
        print_startup_info(config)

        # Start the daemon
        import asyncio

        asyncio.run(engine.daemon())

    except KeyboardInterrupt:
        print("\n\nShutting down gracefully...")
        sys.exit(0)
    except Exception as e:
        logger.exception("Failed to start daemon")
        sys.exit(1)


if __name__ == "__main__":
    main()
