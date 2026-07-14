#!/usr/bin/python
"""
A2A OpenTelemetry Instrumentation

Phase 8 - Task 3: OpenTelemetry instrumentation (HTTP + outbound httpx)

Provides distributed tracing for A2A protocol operations:
- HTTP request/response tracing
- Outbound httpx call tracing
- Task lifecycle tracing
- Message routing tracing

Usage:
    from a2a_daemon_engine.handlers.a2a_telemetry import A2ATelemetry

    telemetry = A2ATelemetry(service_name="a2a-daemon", logger=logger)
    telemetry.initialize()

    # Instrument httpx client
    async with httpx.AsyncClient() as client:
        instrumented_client = telemetry.instrument_httpx(client)
        response = await instrumented_client.get("https://api.example.com")
"""

import logging
from collections.abc import Callable
from typing import Any

__author__ = "SilvaEngine Team"
__version__ = "1.0.0"

# Optional OpenTelemetry imports - graceful degradation if not available
try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.propagate import extract, inject
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.trace import Span, SpanContext, TraceFlags

    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False
    trace = None
    Span = Any


class TelemetryError(Exception):
    """Raised when telemetry initialization or operation fails."""
    pass


class A2ATelemetry:
    """
    OpenTelemetry instrumentation manager for A2A daemon.

    Phase 8: Provides distributed tracing with OTLP export.
    """

    def __init__(
        self,
        service_name: str = "a2a-daemon-engine",
        service_version: str = "1.0.0",
        logger: logging.Logger | None = None,
        otlp_endpoint: str | None = None,
        console_export: bool = False,
    ):
        """
        Initialize telemetry manager.

        Args:
            service_name: Service name for traces
            service_version: Service version
            logger: Optional logger instance
            otlp_endpoint: OTLP collector endpoint (defaults to OTEL_EXPORTER_OTLP_ENDPOINT env var)
            console_export: If True, also export traces to console
        """
        self.service_name = service_name
        self.service_version = service_version
        self.logger = logger or logging.getLogger(__name__)
        self.otlp_endpoint = otlp_endpoint
        self.console_export = console_export

        self._tracer: Any | None = None
        self._provider: Any | None = None
        self._initialized = False

    def initialize(self) -> bool:
        """
        Initialize OpenTelemetry tracing.

        Returns:
            True if initialization successful, False otherwise
        """
        if not OPENTELEMETRY_AVAILABLE:
            self.logger.warning(
                "OpenTelemetry not available. Install with: "
                "pip install opentelemetry-api opentelemetry-sdk "
                "opentelemetry-instrumentation-httpx"
            )
            return False

        if self._initialized:
            return True

        try:
            # Get OTLP endpoint
            import os
            otlp_endpoint = self.otlp_endpoint or os.environ.get(
                "OTEL_EXPORTER_OTLP_ENDPOINT",
                "http://localhost:4318"
            )

            # Create tracer provider
            self._provider = TracerProvider(
                resource=self._create_resource()
            )

            # Add OTLP exporter
            try:
                otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
                self._provider.add_span_processor(
                    BatchSpanProcessor(otlp_exporter)
                )
                self.logger.info(f"OTLP exporter configured: {otlp_endpoint}")
            except Exception as e:
                self.logger.warning(f"Failed to configure OTLP exporter: {e}")

            # Add console exporter if requested
            if self.console_export:
                console_exporter = ConsoleSpanExporter()
                self._provider.add_span_processor(
                    BatchSpanProcessor(console_exporter)
                )
                self.logger.info("Console span exporter enabled")

            # Set global provider
            trace.set_tracer_provider(self._provider)
            self._tracer = trace.get_tracer(self.service_name, self.service_version)

            self._initialized = True
            self.logger.info(f"OpenTelemetry initialized for {self.service_name}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize OpenTelemetry: {e}")
            return False

    def _create_resource(self) -> Any:
        """Create OpenTelemetry resource with service metadata."""
        from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource

        return Resource.create({
            SERVICE_NAME: self.service_name,
            SERVICE_VERSION: self.service_version,
        })

    def instrument_httpx(self, client: Any) -> Any:
        """
        Instrument httpx client.

        Args:
            client: httpx.AsyncClient or httpx.Client instance

        Returns:
            Instrumented client
        """
        if not self._initialized or not OPENTELEMETRY_AVAILABLE:
            self.logger.warning("OpenTelemetry not initialized, returning uninstrumented client")
            return client

        try:
            HTTPXClientInstrumentor.instrument_client(client)
            return client
        except Exception as e:
            self.logger.error(f"Failed to instrument httpx client: {e}")
            return client

    def start_span(
        self,
        name: str,
        context: dict[str, Any] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Any:
        """
        Start a new trace span.

        Args:
            name: Span name
            context: Optional parent context
            attributes: Optional span attributes

        Returns:
            Span context manager
        """
        if not self._initialized or not self._tracer:
            return _NoOpSpan()

        return self._tracer.start_as_current_span(
            name,
            context=context,
            attributes=attributes,
        )

    def create_task_span(
        self,
        task_id: str,
        operation: str,
        partition_key: str | None = None,
        parent_trace_id: str | None = None,
    ) -> Any:
        """
        Create a span for task lifecycle tracing.

        Args:
            task_id: Task identifier
            operation: Operation name (e.g., "task.create", "task.execute", "task.complete")
            partition_key: Optional partition key for multi-tenant context
            parent_trace_id: Optional parent trace ID for distributed tracing

        Returns:
            Span context manager
        """
        if not self._initialized:
            return _NoOpSpan()

        attributes = {
            "a2a.task_id": task_id,
            "a2a.operation": operation,
        }

        if partition_key:
            attributes["a2a.partition_key"] = partition_key

        # Build context from parent trace if provided
        context = None
        if parent_trace_id and OPENTELEMETRY_AVAILABLE:
            # Create span context from parent trace
            trace_id_int = int(parent_trace_id.replace("-", ""), 16)
            span_context = SpanContext(
                trace_id=trace_id_int,
                span_id=0,  # Will be generated
                is_remote=True,
                trace_flags=TraceFlags(0x01),
            )
            context = trace.set_span_in_context(trace.NonRecordingSpan(span_context))

        return self.start_span(
            name=f"a2a.task.{operation}",
            context=context,
            attributes=attributes,
        )

    def create_message_span(
        self,
        message_id: str,
        from_agent: str,
        to_agent: str,
        operation: str = "route",
    ) -> Any:
        """
        Create a span for message routing tracing.

        Args:
            message_id: Message identifier
            from_agent: Source agent ID
            to_agent: Destination agent ID
            operation: Operation type (route, deliver, etc.)

        Returns:
            Span context manager
        """
        if not self._initialized:
            return _NoOpSpan()

        return self.start_span(
            name=f"a2a.message.{operation}",
            attributes={
                "a2a.message_id": message_id,
                "a2a.from_agent": from_agent,
                "a2a.to_agent": to_agent,
            },
        )

    def inject_trace_context(self, headers: dict[str, str]) -> dict[str, str]:
        """
        Inject trace context into HTTP headers.

        Args:
            headers: Existing HTTP headers

        Returns:
            Headers with trace context injected
        """
        if not self._initialized or not OPENTELEMETRY_AVAILABLE:
            return headers

        inject(headers)
        return headers

    def extract_trace_context(self, headers: dict[str, str]) -> Any | None:
        """
        Extract trace context from HTTP headers.

        Args:
            headers: HTTP headers containing trace context

        Returns:
            Extracted context or None
        """
        if not OPENTELEMETRY_AVAILABLE:
            return None

        return extract(headers)

    def shutdown(self) -> None:
        """Shutdown telemetry and flush spans."""
        if self._provider:
            try:
                self._provider.shutdown()
                self.logger.info("OpenTelemetry shutdown complete")
            except Exception as e:
                self.logger.error(f"Error during OpenTelemetry shutdown: {e}")


class _NoOpSpan:
    """No-op span for when OpenTelemetry is not available."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def add_event(self, name: str, attributes: dict | None = None) -> None:
        pass

    def record_exception(self, exception: Exception) -> None:
        pass


class TelemetryMiddleware:
    """
    ASGI middleware for automatic request tracing.

    Adds traceparent/tracestate propagation to all requests.
    """

    def __init__(
        self,
        app: Any,
        telemetry: A2ATelemetry,
        exclude_paths: list | None = None,
    ):
        """
        Initialize telemetry middleware.

        Args:
            app: ASGI application
            telemetry: A2ATelemetry instance
            exclude_paths: Paths to exclude from tracing (e.g., ["/health", "/metrics"])
        """
        self.app = app
        self.telemetry = telemetry
        self.exclude_paths = exclude_paths or ["/health", "/.well-known/agent-card.json"]

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        """ASGI middleware entry point."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Skip excluded paths
        if any(path.startswith(excluded) for excluded in self.exclude_paths):
            await self.app(scope, receive, send)
            return

        # Extract trace context from headers
        headers = dict(scope.get("headers", []))
        context = self.telemetry.extract_trace_context(headers)

        # Start span for request
        with self.telemetry.start_span(
            name=f"http.{scope.get('method', 'GET').lower()}",
            context=context,
            attributes={
                "http.method": scope.get("method"),
                "http.url": scope.get("path"),
                "http.target": scope.get("path"),
                "http.scheme": scope.get("scheme", "http"),
            },
        ):
            # Inject trace context into response headers
            async def wrapped_send(message: dict) -> None:
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    trace_headers = {}
                    self.telemetry.inject_trace_context(trace_headers)
                    for key, value in trace_headers.items():
                        headers.append((key.encode(), value.encode()))
                    message["headers"] = headers
                await send(message)

            await self.app(scope, receive, wrapped_send)


def get_telemetry() -> A2ATelemetry:
    """
    Get or create global telemetry instance.

    Returns:
        A2ATelemetry instance
    """
    import os

    service_name = os.environ.get("OTEL_SERVICE_NAME", "a2a-daemon-engine")
    service_version = os.environ.get("OTEL_SERVICE_VERSION", "1.0.0")
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    console_export = os.environ.get("OTEL_CONSOLE_EXPORT", "false").lower() == "true"

    telemetry = A2ATelemetry(
        service_name=service_name,
        service_version=service_version,
        otlp_endpoint=otlp_endpoint,
        console_export=console_export,
    )
    telemetry.initialize()

    return telemetry


__all__ = [
    "A2ATelemetry",
    "TelemetryMiddleware",
    "TelemetryError",
    "get_telemetry",
    "OPENTELEMETRY_AVAILABLE",
]
