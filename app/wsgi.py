"""WSGI entrypoint for the Intelligent Autonomous DevOps Deployment Platform.

This file exposes the Flask application instance for production WSGI servers
such as Gunicorn:
    gunicorn --workers 4 --bind 0.0.0.0:5000 wsgi:app
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure application module directory is present in sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from app import create_app
except (ImportError, AttributeError):
    from app.app import create_app  # type: ignore[no-redef]

# Instantiate the production WSGI application
app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0")
    app.run(host=host, port=port, debug=False)
