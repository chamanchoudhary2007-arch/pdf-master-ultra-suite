from __future__ import annotations

from pathlib import Path

from app.services.pdf_service import PDFService


class ScannerService:
    @staticmethod
    def _safe_bbox(image):
        from PIL import ImageOps

        inverted = ImageOps.invert(image.convert("L"))
        return inverted.getbbox()

    @staticmethod
    def _cv2_crop(image):
        from PIL import Image

        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except Exception:
            return image
        frame = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 75, 200)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]
        for contour in contours:
            perimeter = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
            if len(approx) == 4:
                x, y, w, h = cv2.boundingRect(approx)
                cropped = frame[y : y + h, x : x + w]
                return Image.fromarray(cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB))
        return image

    @staticmethod
    def enhance_image(
        input_path: str,
        output_path: str,
        brightness: float = 1.1,
        contrast: float = 1.25,
        black_white: bool = False,
        auto_crop: bool = True,
        perspective_correction: bool = True,
    ) -> str:
        from PIL import Image, ImageEnhance

        image = Image.open(input_path).convert("RGB")
        if perspective_correction:
            image = ScannerService._cv2_crop(image)
        elif auto_crop:
            bbox = ScannerService._safe_bbox(image)
            if bbox:
                image = image.crop(bbox)
        image = ImageEnhance.Brightness(image).enhance(brightness)
        image = ImageEnhance.Contrast(image).enhance(contrast)
        if black_white:
            image = image.convert("L")
            image = image.point(lambda value: 255 if value > 140 else 0)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)
        return output_path

    @staticmethod
    def batch_scan(
        input_paths: list[str],
        working_dir: str,
        brightness: float = 1.1,
        contrast: float = 1.25,
        black_white: bool = False,
        auto_crop: bool = True,
        perspective_correction: bool = True,
        export_type: str = "pdf",
    ) -> dict:
        working_directory = Path(working_dir)
        working_directory.mkdir(parents=True, exist_ok=True)
        enhanced_paths = []
        for index, input_path in enumerate(input_paths, start=1):
            output_path = working_directory / f"scan_{index:03d}.png"
            enhanced_paths.append(
                ScannerService.enhance_image(
                    input_path=input_path,
                    output_path=str(output_path),
                    brightness=brightness,
                    contrast=contrast,
                    black_white=black_white,
                    auto_crop=auto_crop,
                    perspective_correction=perspective_correction,
                )
            )
        result = {"image_paths": enhanced_paths}
        if export_type == "pdf":
            pdf_path = working_directory / "scanned_document.pdf"
            PDFService.images_to_pdf(enhanced_paths, str(pdf_path))
            result["pdf_path"] = str(pdf_path)
        return result
