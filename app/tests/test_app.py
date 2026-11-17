"""Comprehensive test suite for the DevOps platform Flask application.

Covers all REST API endpoints, error handlers, Prometheus metric exposition,
Kubernetes probes, autoscaling load generation, CORS headers, and WSGI entrypoint.
"""

from __future__ import annotations

import json
import runpy
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from werkzeug.exceptions import (
    BadRequest,
    InternalServerError,
    MethodNotAllowed,
    NotFound,
)

# Ensure application directory is in sys.path
APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Clear any cached namespace package named 'app'
if "app" in sys.modules and not hasattr(sys.modules["app"], "create_app"):
    del sys.modules["app"]

try:
    import wsgi
    import app as app_module
    from app import APP_NAME, APP_VERSION, TEAM_MEMBERS, create_app
except (ImportError, AttributeError):
    import app.wsgi as wsgi
    import app.app as app_module
    from app.app import APP_NAME, APP_VERSION, TEAM_MEMBERS, create_app


# -----------------------------------------------------------------------------
# Root Endpoint Tests
# -----------------------------------------------------------------------------

def test_index_endpoint_returns_welcome_payload(client: FlaskClient) -> None:
    """Test that GET / returns 200 OK and expected discovery information."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.is_json

    data = response.get_json()
    assert data["message"] == "Welcome to the Intelligent Autonomous DevOps Deployment Platform API"
    assert data["app"] == APP_NAME
    assert data["version"] == APP_VERSION
    assert data["status"] == "online"
    assert "endpoints" in data

    endpoints = data["endpoints"]
    assert endpoints["root"] == "/"
    assert endpoints["health"] == "/health"
    assert endpoints["info"] == "/info"
    assert endpoints["metrics"] == "/metrics"
    assert endpoints["load"] == "/load"
    assert endpoints["ready"] == "/ready"
    assert endpoints["live"] == "/live"


# -----------------------------------------------------------------------------
# Health Check Endpoint Tests
# -----------------------------------------------------------------------------

def test_health_endpoint_success(client: FlaskClient) -> None:
    """Test that GET /health returns 200 OK and valid ISO-8601 timestamp."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.is_json

    data = response.get_json()
    assert data["status"] == "healthy"
    assert "timestamp" in data

    # Verify timestamp is valid ISO-8601 format
    parsed_time = datetime.fromisoformat(data["timestamp"])
    assert parsed_time is not None


# -----------------------------------------------------------------------------
# Info Endpoint Tests
# -----------------------------------------------------------------------------

def test_info_endpoint_metadata_and_team(client: FlaskClient) -> None:
    """Test that GET /info returns application metadata and all team members."""
    response = client.get("/info")
    assert response.status_code == 200
    assert response.is_json

    data = response.get_json()
    assert data["app"] == "devops-platform-demo"
    assert data["version"] == "1.0.0"
    assert "team" in data

    team = data["team"]
    assert len(team) == 3

    # Validate specific team members
    expected_members = {
        "Hari Charan V": "1RV25MC037",
        "M Srikanth": "1RV25MC053",
        "Anurag R Swamy": "1RV25MC016",
    }

    found_members = {member["name"]: member["usn"] for member in team}
    assert found_members == expected_members


# -----------------------------------------------------------------------------
# Kubernetes Probes Tests
# -----------------------------------------------------------------------------

def test_readiness_probe(client: FlaskClient) -> None:
    """Test that GET /ready returns 200 OK with ready status."""
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.is_json
    data = response.get_json()
    assert data == {"status": "ready"}


def test_liveness_probe(client: FlaskClient) -> None:
    """Test that GET /live returns 200 OK with alive status."""
    response = client.get("/live")
    assert response.status_code == 200
    assert response.is_json
    data = response.get_json()
    assert data == {"status": "alive"}


# -----------------------------------------------------------------------------
# Prometheus Metrics Endpoint Tests
# -----------------------------------------------------------------------------

def test_metrics_endpoint_exposition(client: FlaskClient) -> None:
    """Test that GET /metrics exposes Prometheus-formatted metrics."""
    # First make a regular request to ensure metrics contain counts
    client.get("/health")

    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.content_type

    metrics_text = response.get_data(as_text=True)
    assert "http_requests_total" in metrics_text
    assert "http_request_duration_seconds" in metrics_text
    assert "app_up 1.0" in metrics_text


# -----------------------------------------------------------------------------
# Load Stress Endpoint Tests (/load)
# -----------------------------------------------------------------------------

def test_load_generation_success(client: FlaskClient) -> None:
    """Test POST /load successfully generates CPU stress with small parameters."""
    payload = {"duration": 0.02, "intensity": 5}
    response = client.post("/load", json=payload)
    assert response.status_code == 200
    assert response.is_json

    data = response.get_json()
    assert data["status"] == "load_generated"
    assert data["duration"] == 0.02
    assert data["intensity"] == 5
    assert "actual_duration_seconds" in data
    assert "iterations" in data
    assert data["actual_duration_seconds"] >= 0


def test_load_generation_zero_duration(client: FlaskClient) -> None:
    """Test POST /load handles 0 duration immediately without looping."""
    payload = {"duration": 0, "intensity": 10}
    response = client.post("/load", json=payload)
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "load_generated"
    assert data["duration"] == 0
    assert data["iterations"] == 0


def test_load_generation_defaults_with_empty_json(client: FlaskClient) -> None:
    """Test POST /load falls back to sensible defaults when empty JSON is passed."""
    # Pass duration 0 to avoid waiting default 1.0s in unit tests
    response = client.post("/load", json={"duration": 0})
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "load_generated"
    assert data["duration"] == 0


def test_load_generation_non_json_content_type(client: FlaskClient) -> None:
    """Test POST /load rejects non-JSON body when data is submitted."""
    response = client.post(
        "/load",
        data="duration=1",
        content_type="text/plain",
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data["error"] == "Bad Request"
    assert "Content-Type must be application/json" in data["message"]


def test_load_generation_json_array_body(client: FlaskClient) -> None:
    """Test POST /load rejects JSON payload that is not a dictionary object."""
    response = client.post(
        "/load",
        data=json.dumps([1, 2, 3]),
        content_type="application/json",
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data["error"] == "Bad Request"
    assert "JSON request body must be an object" in data["message"]


@pytest.mark.parametrize(
    "invalid_duration,expected_msg",
    [
        ("invalid", "'duration' must be a numeric value in seconds"),
        (True, "'duration' must be a numeric value in seconds"),
        (-1, "'duration' must be greater than or equal to 0"),
        (65, "'duration' exceeds maximum allowable limit of 60 seconds"),
    ],
)
def test_load_generation_duration_validation_errors(
    client: FlaskClient, invalid_duration: Any, expected_msg: str
) -> None:
    """Test POST /load input validation for invalid duration values."""
    response = client.post("/load", json={"duration": invalid_duration})
    assert response.status_code == 400
    data = response.get_json()
    assert data["error"] == "Bad Request"
    assert expected_msg in data["message"]


@pytest.mark.parametrize(
    "invalid_intensity,expected_msg",
    [
        ("high", "'intensity' must be a numeric value"),
        (False, "'intensity' must be a numeric value"),
        (0, "'intensity' must be greater than 0"),
        (-10, "'intensity' must be greater than 0"),
        (15000, "'intensity' exceeds maximum allowable limit of 10000"),
    ],
)
def test_load_generation_intensity_validation_errors(
    client: FlaskClient, invalid_intensity: Any, expected_msg: str
) -> None:
    """Test POST /load input validation for invalid intensity values."""
    response = client.post(
        "/load", json={"duration": 0.01, "intensity": invalid_intensity}
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data["error"] == "Bad Request"
    assert expected_msg in data["message"]


def test_load_endpoint_method_not_allowed(client: FlaskClient) -> None:
    """Test GET /load returns 405 Method Not Allowed."""
    response = client.get("/load")
    assert response.status_code == 405
    data = response.get_json()
    assert data["error"] == "Method Not Allowed"
    assert data["status_code"] == 405


# -----------------------------------------------------------------------------
# Error Handling and Edge Cases Tests
# -----------------------------------------------------------------------------

def test_404_not_found_handler(client: FlaskClient) -> None:
    """Test requesting a nonexistent endpoint returns structured 404 JSON."""
    response = client.get("/nonexistent-endpoint-path")
    assert response.status_code == 404
    assert response.is_json
    data = response.get_json()
    assert data["error"] == "Not Found"
    assert data["status_code"] == 404


def test_405_method_not_allowed_handler(client: FlaskClient) -> None:
    """Test requesting an endpoint with unsupported HTTP method returns 405 JSON."""
    response = client.post("/health", json={})
    assert response.status_code == 405
    assert response.is_json
    data = response.get_json()
    assert data["error"] == "Method Not Allowed"
    assert data["status_code"] == 405


def test_500_internal_server_error_handler(client: FlaskClient) -> None:
    """Test that an unhandled internal exception is captured and returns 500 JSON."""
    response = client.get("/_test/error")
    assert response.status_code == 500
    assert response.is_json
    data = response.get_json()
    assert data["error"] == "Internal Server Error"
    assert data["status_code"] == 500


def test_http_exception_handling_in_unhandled_handler(app: Flask) -> None:
    """Test HTTPException dispatching in unhandled_exception_handler."""
    with app.test_request_context():
        # Test BadRequest
        bad_req = BadRequest("Custom bad request error")
        response, code = app.error_handler_spec[None][None][Exception](bad_req)
        assert code == 400
        assert response.get_json()["error"] == "Bad Request"

        # Test generic RuntimeError
        runtime_err = RuntimeError("Unexpected fatal error")
        response_500, code_500 = app.error_handler_spec[None][None][Exception](runtime_err)
        assert code_500 == 500
        assert response_500.get_json()["error"] == "Internal Server Error"


def test_explicit_error_handlers(app: Flask) -> None:
    """Test explicit error handlers bad_request, not_found, 405, 500 directly."""
    with app.test_request_context():
        # bad_request_handler
        res, code = app.error_handler_spec[None][400][BadRequest](BadRequest("Custom bad request"))
        assert code == 400
        assert res.get_json()["error"] == "Bad Request"

        # not_found_handler
        res404, code404 = app.error_handler_spec[None][404][NotFound](NotFound("Custom not found"))
        assert code404 == 404
        assert res404.get_json()["error"] == "Not Found"

        # method_not_allowed_handler
        res405, code405 = app.error_handler_spec[None][405][MethodNotAllowed](
            MethodNotAllowed("Custom method not allowed")
        )
        assert code405 == 405
        assert res405.get_json()["error"] == "Method Not Allowed"

        # internal_server_error_handler
        res500, code500 = app.error_handler_spec[None][500][InternalServerError](
            InternalServerError("Explicit 500 error")
        )
        assert code500 == 500
        assert res500.get_json()["error"] == "Internal Server Error"


# -----------------------------------------------------------------------------
# CORS and Middleware Tests
# -----------------------------------------------------------------------------

def test_cors_headers_present(client: FlaskClient) -> None:
    """Test CORS headers are returned for cross-origin requests."""
    response = client.get("/", headers={"Origin": "http://example.com"})
    assert response.status_code == 200
    assert response.headers.get("Access-Control-Allow-Origin") in ("*", "http://example.com")


def test_cors_preflight_options(client: FlaskClient) -> None:
    """Test CORS preflight OPTIONS request."""
    response = client.open(
        "/",
        method="OPTIONS",
        headers={
            "Origin": "http://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("Access-Control-Allow-Origin") in ("*", "http://example.com")


# -----------------------------------------------------------------------------
# Application Factory and WSGI Module Tests
# -----------------------------------------------------------------------------

def test_create_app_with_custom_config() -> None:
    """Test application factory with custom configuration dictionary."""
    custom_app = create_app({"CUSTOM_FLAG": True, "TESTING": True})
    assert custom_app.config["CUSTOM_FLAG"] is True
    assert custom_app.config["TESTING"] is True


def test_create_app_default_instance() -> None:
    """Test application factory with default parameters."""
    default_app = create_app()
    assert default_app is not None
    assert isinstance(default_app, Flask)
    assert default_app.config["JSON_SORT_KEYS"] is False


def test_wsgi_entrypoint_app_instance() -> None:
    """Test that wsgi.py exports a valid Flask application instance."""
    assert wsgi.app is not None
    assert isinstance(wsgi.app, Flask)


def test_app_main_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test python app.py execution branch using runpy."""
    called: dict[str, Any] = {}
    monkeypatch.setattr(Flask, "run", lambda self, **kwargs: called.update(kwargs))
    runpy.run_path(str(Path(app_module.__file__)), run_name="__main__")
    assert called.get("port") == 5000
    assert called.get("host") == "0.0.0.0"


def test_wsgi_main_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test python wsgi.py execution branch using runpy."""
    called: dict[str, Any] = {}
    monkeypatch.setattr(Flask, "run", lambda self, **kwargs: called.update(kwargs))
    runpy.run_path(str(Path(wsgi.__file__)), run_name="__main__")
    assert called.get("port") == 5000
    assert called.get("host") == "0.0.0.0"
