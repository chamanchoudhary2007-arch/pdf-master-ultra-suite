from __future__ import annotations

import os

from dotenv import load_dotenv

from app import create_app
from app.dependency_check import assert_runtime_dependencies

load_dotenv()


config_name = os.environ.get("APP_CONFIG", "production")
assert_runtime_dependencies()
app = create_app(config_name)
