from __future__ import annotations

import os

from dotenv import load_dotenv

from app import create_app
from app.dependency_check import assert_runtime_dependencies

load_dotenv()


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


config_name = os.environ.get("APP_CONFIG", "default")
assert_runtime_dependencies()
app = create_app(config_name)


if __name__ == "__main__":
    host = os.environ.get("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("FLASK_RUN_PORT", "5000")))
    debug_default = config_name == "development"
    debug = _env_flag("FLASK_DEBUG", debug_default)
    app.run(host=host, port=port, debug=debug)
