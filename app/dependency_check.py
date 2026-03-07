from __future__ import annotations

import importlib


REQUIRED_DEPENDENCIES = {
    "Pillow": "PIL",
    "PyMuPDF": "fitz",
    "pypdf": "pypdf",
    "pdfplumber": "pdfplumber",
    "reportlab": "reportlab",
    "python-docx": "docx",
    "python-pptx": "pptx",
    "openpyxl": "openpyxl",
    "opencv-python-headless": "cv2",
    "pytesseract": "pytesseract",
}


def assert_runtime_dependencies() -> None:
    missing: list[str] = []
    for package_name, module_name in REQUIRED_DEPENDENCIES.items():
        try:
            importlib.import_module(module_name)
        except Exception:
            missing.append(package_name)
    if not missing:
        return
    missing_list = ", ".join(sorted(missing))
    raise RuntimeError(
        "Missing runtime dependencies: "
        f"{missing_list}. Run `python -m pip install -r requirements.txt` and restart the app."
    )
