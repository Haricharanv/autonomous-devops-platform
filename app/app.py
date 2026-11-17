"""Intelligent Autonomous DevOps Deployment Platform - Sample Flask Application.

This module provides a production-grade Flask REST API with observability,
Kubernetes health/liveness/readiness probes, autoscaling CPU load simulation,
CORS support, structured logging, and Prometheus metrics exposition.
"""

from __future__ import annotations

import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Mapping

from flask import Flask, Response, g, jsonify, request
from flask_cors import CORS
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from werkzeug.exceptions import HTTPException

# Configure standard structured logging
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("devops-platform")

# Application and Team Metadata
APP_NAME = "devops-platform-demo"
APP_VERSION = "1.0.0"
TEAM_MEMBERS: list[dict[str, str]] = [
    {"name": "Hari Charan V", "usn": "1RV25MC037"},
    {"name": "M Srikanth", "usn": "1RV25MC053"},
    {"name": "Anurag R Swamy", "usn": "1RV25MC016"},
]


def _get_or_create_metric(
    collector_cls: type[Any],
    name: str,
    documentation: str,
    registry: Any = REGISTRY,
    **kwargs: Any,
) -> Any:
    """Safely get an existing Prometheus collector or register a new one idempotently.

    Args:
        collector_cls: Prometheus collector class (Counter, Histogram, Gauge).
        name: Name of the metric.
        documentation: Metric documentation string.
        registry: Collector registry (defaults to default REGISTRY).
        **kwargs: Additional kwargs passed to the collector constructor.

    Returns:
        The registered or retrieved collector instance.
    """
    if name in registry._names_to_collectors:
        return registry._names_to_collectors[name]
    return collector_cls(name, documentation, registry=registry, **kwargs)


# Prometheus Metrics Definitions
# Request counter partitioned by method, endpoint, and HTTP status code
REQUEST_COUNT = _get_or_create_metric(
    Counter,
    "http_requests_total",
    "Total HTTP requests handled by the application",
    labelnames=["method", "endpoint", "status"],
)

# Latency histogram partitioned by method and endpoint
REQUEST_LATENCY = _get_or_create_metric(
    Histogram,
    "http_request_duration_seconds",
    "Histogram of HTTP request processing latency in seconds",
    labelnames=["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 10.0),
)

# Gauge indicating whether the application is operational (1 = healthy/up, 0 = down)
APP_UP = _get_or_create_metric(
    Gauge,
    "app_up",
    "Operational state of the application (1 = up, 0 = down)",
)
APP_UP.set(1)


def create_app(test_config: Mapping[str, Any] | None = None) -> Flask:
    """Application factory for the DevOps Platform Flask application.

    Args:
        test_config: Optional mapping containing custom configuration overrides.

    Returns:
        Configured Flask application instance ready for WSGI or test runner.
    """
    app = Flask(__name__)

    # Default configuration
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "devops-platform-secret-key-default"),
        JSON_SORT_KEYS=False,
    )

    if test_config is not None:
        app.config.update(test_config)

    # Enable Cross-Origin Resource Sharing (CORS) across all endpoints
    CORS(app, resources={r"/*": {"origins": "*"}})

    # Request Lifecycle Hooks for Metrics and Logging
    @app.before_request
    def before_request_hook() -> None:
        """Record the start time before handling an incoming request."""
        g.start_time = time.perf_counter()

    @app.after_request
    def after_request_hook(response: Response) -> Response:
        """Record metrics and log information after handling a request."""
        if hasattr(g, "start_time"):
            duration = time.perf_counter() - g.start_time
            endpoint = request.endpoint or "unknown"
            method = request.method
            status = str(response.status_code)

            REQUEST_COUNT.labels(method=method, endpoint=endpoint, status=status).inc()
            REQUEST_LATENCY.labels(method=method, endpoint=endpoint).observe(duration)

            logger.info(
                "%s %s %s %.4fs client=%s",
                method,
                request.path,
                status,
                duration,
                request.remote_addr,
            )
        return response

    # -------------------------------------------------------------------------
    # REST Endpoints
    # -------------------------------------------------------------------------

    @app.route("/", methods=["GET"])
    def index() -> Response:
        """Root welcome page and service index."""
        return jsonify({
            "message": "Welcome to the Intelligent Autonomous DevOps Deployment Platform API",
            "app": APP_NAME,
            "version": APP_VERSION,
            "status": "online",
            "endpoints": {
                "root": "/",
                "health": "/health",
                "info": "/info",
                "metrics": "/metrics",
                "load": "/load",
                "ready": "/ready",
                "live": "/live",
            },
        }), 200

    @app.route("/health", methods=["GET"])
    def health() -> Response:
        """Health check endpoint.

        Returns:
            JSON object with current operational status and ISO-8601 UTC timestamp.
        """
        return jsonify({
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }), 200

    @app.route("/info", methods=["GET"])
    def info() -> Response:
        """Application metadata and team info endpoint.

        Returns:
            JSON object containing application name, version, and team members.
        """
        return jsonify({
            "app": APP_NAME,
            "version": APP_VERSION,
            "team": TEAM_MEMBERS,
        }), 200

    @app.route("/metrics", methods=["GET"])
    def metrics() -> Response:
        """Prometheus-compatible metrics exposition endpoint.

        Returns:
            Prometheus text exposition format containing counters, gauges,
            and histograms for scraping.
        """
        return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

    @app.route("/ready", methods=["GET"])
    def ready() -> Response:
        """Kubernetes readiness probe endpoint.

        Returns:
            HTTP 200 with readiness status.
        """
        return jsonify({"status": "ready"}), 200

    @app.route("/live", methods=["GET"])
    def live() -> Response:
        """Kubernetes liveness probe endpoint.

        Returns:
            HTTP 200 with liveness status.
        """
        return jsonify({"status": "alive"}), 200

    @app.route("/load", methods=["POST"])
    def load() -> tuple[Response, int]:
        """CPU stress simulation endpoint for autoscaling verification.

        Accepts JSON payload:
            duration (float | int): Stress loop duration in seconds (default: 1.0, max: 60.0).
            intensity (float | int): CPU workload multiplier per tick (default: 100, max: 10000).

        Returns:
            HTTP 200 with load generation status and duration, or HTTP 400 on validation failure.
        """
        # Validate Content-Type if payload is present
        if not request.is_json and request.content_length and request.content_length > 0:
            return jsonify({
                "error": "Bad Request",
                "message": "Content-Type must be application/json",
                "status_code": 400,
            }), 400

        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({
                "error": "Bad Request",
                "message": "JSON request body must be an object",
                "status_code": 400,
            }), 400

        duration_val = payload.get("duration", 1.0)
        intensity_val = payload.get("intensity", 100)

        # Validate duration
        if isinstance(duration_val, bool) or not isinstance(duration_val, (int, float)):
            return jsonify({
                "error": "Bad Request",
                "message": "'duration' must be a numeric value in seconds",
                "status_code": 400,
            }), 400

        if duration_val < 0:
            return jsonify({
                "error": "Bad Request",
                "message": "'duration' must be greater than or equal to 0",
                "status_code": 400,
            }), 400

        if duration_val > 60:
            return jsonify({
                "error": "Bad Request",
                "message": "'duration' exceeds maximum allowable limit of 60 seconds",
                "status_code": 400,
            }), 400

        # Validate intensity
        if isinstance(intensity_val, bool) or not isinstance(intensity_val, (int, float)):
            return jsonify({
                "error": "Bad Request",
                "message": "'intensity' must be a numeric value",
                "status_code": 400,
            }), 400

        if intensity_val <= 0:
            return jsonify({
                "error": "Bad Request",
                "message": "'intensity' must be greater than 0",
                "status_code": 400,
            }), 400

        if intensity_val > 10000:
            return jsonify({
                "error": "Bad Request",
                "message": "'intensity' exceeds maximum allowable limit of 10000",
                "status_code": 400,
            }), 400

        # Execute CPU stress loop
        target_duration = float(duration_val)
        step_ops = int(intensity_val) * 100
        start_time = time.perf_counter()
        iterations = 0

        if target_duration > 0:
            while (time.perf_counter() - start_time) < target_duration:
                # Math operations to load the CPU
                for _ in range(min(step_ops, 50000)):
                    _ = math.sqrt(12345.6789) * math.tan(0.54321)
                iterations += 1

        actual_elapsed = round(time.perf_counter() - start_time, 4)

        return jsonify({
            "status": "load_generated",
            "duration": duration_val,
            "intensity": intensity_val,
            "actual_duration_seconds": actual_elapsed,
            "iterations": iterations,
        }), 200

    # Test route for verifying 500 internal server error handler in test environments
    if app.config.get("TESTING"):
        @app.route("/_test/error", methods=["GET"])
        def test_error_trigger() -> None:
            """Internal route to simulate an unexpected server exception."""
            raise RuntimeError("Deliberate test server exception")

    # -------------------------------------------------------------------------
    # Error Handlers
    # -------------------------------------------------------------------------

    @app.errorhandler(400)
    def bad_request_handler(error: Any) -> tuple[Response, int]:
        """Handle 400 Bad Request errors."""
        message = getattr(error, "description", str(error))
        return jsonify({
            "error": "Bad Request",
            "message": message,
            "status_code": 400,
        }), 400

    @app.errorhandler(404)
    def not_found_handler(error: Any) -> tuple[Response, int]:
        """Handle 404 Not Found errors."""
        message = getattr(error, "description", "The requested resource was not found on the server.")
        return jsonify({
            "error": "Not Found",
            "message": message,
            "status_code": 404,
        }), 404

    @app.errorhandler(405)
    def method_not_allowed_handler(error: Any) -> tuple[Response, int]:
        """Handle 405 Method Not Allowed errors."""
        message = getattr(error, "description", "The method is not allowed for the requested URL.")
        return jsonify({
            "error": "Method Not Allowed",
            "message": message,
            "status_code": 405,
        }), 405

    @app.errorhandler(500)
    def internal_server_error_handler(error: Any) -> tuple[Response, int]:
        """Handle 500 Internal Server errors."""
        logger.error("Internal server error: %s", error, exc_info=True)
        return jsonify({
            "error": "Internal Server Error",
            "message": "An unexpected error occurred on the server.",
            "status_code": 500,
        }), 500

    @app.errorhandler(Exception)
    def unhandled_exception_handler(error: Exception) -> tuple[Response, int]:
        """Fallback exception handler for unhandled exceptions."""
        if isinstance(error, HTTPException):
            return jsonify({
                "error": error.name,
                "message": error.description,
                "status_code": error.code,
            }), error.code or 500

        logger.error("Unhandled exception: %s", error, exc_info=True)
        return jsonify({
            "error": "Internal Server Error",
            "message": "An unexpected error occurred on the server.",
            "status_code": 500,
        }), 500

    return app


# Default application instance for WSGI servers (Gunicorn, uWSGI)
app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
