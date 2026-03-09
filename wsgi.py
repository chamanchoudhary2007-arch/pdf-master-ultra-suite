from __future__ import annotations

import os

from app import create_app
from app.dependency_check import assert_runtime_dependencies


config_name = os.environ.get("APP_CONFIG", "production")
try:
    assert_runtime_dependencies()
    app = create_app(config_name)
except Exception as exc:
    raise RuntimeError(f"WSGI startup failed: {exc}") from exc
