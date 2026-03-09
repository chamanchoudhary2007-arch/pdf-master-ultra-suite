from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = PROJECT_ROOT / "dist"
DIST_DIR.mkdir(parents=True, exist_ok=True)

EXCLUDE_DIR_NAMES = {
    ".git",
    ".github",
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "instance",
    "outputs",
    "uploads",
    "cloud",
    "dist",
}

EXCLUDE_FILE_NAMES = {
    ".env",
}

EXCLUDE_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pyc",
    ".pyo",
    ".log",
    ".tmp",
}


def should_exclude(path: Path) -> bool:
    relative_parts = path.relative_to(PROJECT_ROOT).parts
    if any(part in EXCLUDE_DIR_NAMES for part in relative_parts[:-1]):
        return True

    if path.name in EXCLUDE_FILE_NAMES:
        return True

    lower_name = path.name.lower()
    if lower_name.startswith(".env.") and lower_name != ".env.example":
        return True

    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return True

    return False


def build_release_zip() -> Path:
    zip_path = DIST_DIR / "pdfmaster_ultra_suite_release.zip"
    if zip_path.exists():
        zip_path.unlink()

    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        for path in PROJECT_ROOT.rglob("*"):
            if path.is_dir() or should_exclude(path):
                continue
            archive.write(path, arcname=path.relative_to(PROJECT_ROOT).as_posix())

    return zip_path


if __name__ == "__main__":
    output = build_release_zip()
    print(f"Release zip created: {output}")
