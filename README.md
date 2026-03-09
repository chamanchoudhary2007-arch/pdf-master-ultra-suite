# PDFMaster Ultra Suite

Production-ready Flask platform for PDF tools, OCR, conversion, AI helpers, billing, and advanced workspace automation.

## Major Capabilities
- Core PDF suite: merge/split/rotate/compress/convert/security/sign/share/scanner
- Auth + admin + subscriptions + demo/live payment modes
- Advanced workspace modules:
  - Chat with PDF (document Q&A + source pages)
  - Bulk processing queue (pending/processing/completed/failed)
  - Signature request workflow (multi-signer order, reminders, status)
  - Fillable forms + shared fill links + filled PDF export
  - Cloud integration center (Google Drive / Dropbox / OneDrive architecture with graceful fallback)
  - Advanced OCR suite (eng+hin ready, searchable PDF/TXT/DOCX/CSV/XLSX)
  - Version history + document compare reports
  - PDF archive/compliance reports (check + archive/print-safe modes)
  - Team workspaces (shared projects, comments, approvals, activity feed)
  - API keys + REST automation + webhook callbacks + usage logs
  - Privacy center (retention, private mode flags, delete-all, access/share logs)
  - PWA support (manifest + service worker + install prompt)

## Architecture
- Flask app factory with blueprints
- Service-layer business modules in `app/services/`
- SQLAlchemy models + Alembic migrations
- Jinja templates + modular static assets

## Project Structure
- `app/` application package
- `app/blueprints/` route modules (`main`, `auth`, `tools`, `admin`, `workspace`, `api`)
- `app/services/` domain/service logic
- `app/templates/` Jinja templates
- `app/static/` CSS/JS/images/PWA assets
- `migrations/` Alembic migration scripts
- `scripts/build_release_zip.py` clean packaging helper
- `run.py` local entrypoint
- `wsgi.py` production entrypoint

## Requirements
- Python 3.11+
- OCR-related system tools when required (Tesseract/Poppler stack)

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

## Environment Setup
1. Copy `.env.example` to `.env`
2. Set minimum required values:
- `SECRET_KEY`
- `DATABASE_URL`
  - fallback also supported: `DATABASE_URI`

Optional feature settings:
- Google OAuth: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, optional `GOOGLE_REDIRECT_URI`
- Admin allowlist: `ADMIN_ALLOWED_EMAILS`
- Payments: `PAYMENT_MODE`, Razorpay keys
- SMTP (signature reminders): `SMTP_HOST`, `SMTP_FROM_EMAIL`, etc.
- Cloud providers:
  - `GOOGLE_DRIVE_CLIENT_ID`, `GOOGLE_DRIVE_CLIENT_SECRET`
  - `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`
  - `ONEDRIVE_CLIENT_ID`, `ONEDRIVE_CLIENT_SECRET`
- API/webhook controls:
  - `API_DEFAULT_RATE_LIMIT_PER_MINUTE`
  - `WEBHOOK_MAX_FAILURES`
- Privacy defaults:
  - `PRIVACY_DEFAULT_AUTO_DELETE_HOURS`

## Migrations
Apply all migrations:

```bash
flask --app run.py db upgrade
```

Seed catalog/admin data:

```bash
flask --app run.py seed-data
```

One-shot init:

```bash
flask --app run.py init-app
```

## Local Run
```bash
python run.py
```

Default URL: `http://127.0.0.1:5000`

## API Automation Quick Start
1. Login, open `Workspace -> API Automation`, create API key
2. Use key in `X-API-Key` or `Authorization: Bearer <key>`
3. Call endpoints:
- `GET /api/v1/keys/me`
- `POST /api/v1/jobs/compress` (multipart file field `document`)
- `GET /api/v1/jobs/<job_id>`
- `GET /api/v1/jobs/<job_id>/download`
- `POST /api/v1/webhooks/test`

## Security Notes
- CSRF protection for web forms
- Security headers + CSP enabled
- Admin routes role/allowlist protected
- API keys hashed in database
- Share/download access logging available in privacy center
- Secrets stay in environment variables (do not commit `.env`)

## Cleanup and Retention
Manual cleanup command:

```bash
flask --app run.py cleanup-files
```

User-level retention is configurable in `Workspace -> Privacy`.

## Build Clean Release Zip
Creates `dist/pdfmaster_ultra_suite_release.zip` and excludes `.env`, `.git`, DB files, runtime outputs, and caches.

```bash
python scripts/build_release_zip.py
```

## Production Startup
```bash
gunicorn wsgi:app
```

Use `APP_CONFIG=production` with production-safe env values.
