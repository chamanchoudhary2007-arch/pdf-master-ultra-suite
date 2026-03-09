from __future__ import annotations

import os
from pathlib import Path


class OCRService:
    @staticmethod
    def _tesseract():
        try:
            import pytesseract  # type: ignore

            configured_cmd = os.environ.get("TESSERACT_CMD", "").strip()
            candidate_paths = [
                configured_cmd,
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            ]
            for candidate in candidate_paths:
                if candidate and Path(candidate).exists():
                    pytesseract.pytesseract.tesseract_cmd = candidate
                    break
            pytesseract.get_tesseract_version()
            return pytesseract
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise ValueError(
                "Tesseract OCR is not available. Install Tesseract and add it to system PATH."
            ) from exc

    @staticmethod
    def ocr_image_paths(image_paths: list[str], output_text_path: str, lang: str = "eng") -> str:
        pytesseract = OCRService._tesseract()
        from PIL import Image

        chunks = []
        for image_path in image_paths:
            chunks.append(pytesseract.image_to_string(Image.open(image_path), lang=lang))
        Path(output_text_path).write_text("\n\n".join(chunks), encoding="utf-8")
        return output_text_path

    @staticmethod
    def ocr_pdf_to_searchable(
        input_pdf_path: str,
        output_pdf_path: str,
        output_text_path: str,
        lang: str = "eng",
    ) -> tuple[str, str]:
        pytesseract = OCRService._tesseract()
        from PIL import Image
        try:
            import fitz  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise ValueError("PyMuPDF is required for OCR PDF conversion.") from exc

        document = fitz.open(input_pdf_path)
        text_chunks = []
        pdf_bytes = []
        for page in document:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            text_chunks.append(pytesseract.image_to_string(image, lang=lang))
            pdf_bytes.append(pytesseract.image_to_pdf_or_hocr(image, extension="pdf", lang=lang))
        Path(output_text_path).write_text("\n\n".join(text_chunks), encoding="utf-8")
        merged = fitz.open()
        for content in pdf_bytes:
            temp = fitz.open("pdf", content)
            merged.insert_pdf(temp)
        merged.save(output_pdf_path)
        merged.close()
        document.close()
        return output_pdf_path, output_text_path
