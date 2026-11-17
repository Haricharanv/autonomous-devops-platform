"""Pytest fixtures and test configuration for the DevOps platform application."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Generator

import pytest
from flask import Flask
from flask.testing import FlaskClient, FlaskCliRunner

# Ensure application package directory is at the very beginning of sys.path
APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Clear any cached namespace package named 'app'
if "app" in sys.modules and not hasattr(sys.modules["app"], "create_app"):
    del sys.modules["app"]

try:
    from app import create_app  # noqa: E402
except (ImportError, AttributeError):
    from app.app import create_app  # noqa: E402


@pytest.fixture
def app() -> Generator[Flask, None, None]:
    """Create and configure an isolated Flask app instance for testing."""
    test_app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-for-unit-testing",
    })
    yield test_app


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    """Fixture providing a test client configured for the application."""
    return app.test_client()


@pytest.fixture
def runner(app: Flask) -> FlaskCliRunner:
    """Fixture providing a test CLI runner for Flask CLI commands."""
    return app.test_cli_runner()
