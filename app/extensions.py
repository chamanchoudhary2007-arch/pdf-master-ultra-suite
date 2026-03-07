from flask_login import LoginManager
from flask_mail import Mail
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

try:
    from authlib.integrations.flask_client import OAuth as AuthlibOAuth
except Exception:  # pragma: no cover - optional dependency fallback
    AuthlibOAuth = None


class OAuthStub:
    is_stub = True
    _registry = {}

    def init_app(self, app):
        return None

    def register(self, *args, **kwargs):
        raise RuntimeError("Authlib is not installed. Install dependencies from requirements.txt")

    def __getattr__(self, _name):
        raise RuntimeError("Authlib is not installed. Install dependencies from requirements.txt")


db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()
mail = Mail()
oauth = AuthlibOAuth() if AuthlibOAuth else OAuthStub()
