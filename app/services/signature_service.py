from __future__ import annotations

from pathlib import Path

from app.services.pdf_service import PDFService


class SignatureService:
    @staticmethod
    def apply_signatures(
        input_path: str,
        output_path: str,
        signature_path: str,
        placements: list[dict],
    ) -> str:
        from PIL import Image
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(input_path)
        writer = PdfWriter()
        signature_image = Image.open(signature_path)
        placements_by_page: dict[int, list[dict]] = {}
        for placement in placements:
            page_index = max(0, int(placement.get("page", 1)) - 1)
            placements_by_page.setdefault(page_index, []).append(placement)

        for page_index, page in enumerate(reader.pages):
            page_placements = placements_by_page.get(page_index, [])
            if page_placements:
                overlay_page = PDFService._overlay_page(
                    page.mediabox.width,
                    page.mediabox.height,
                    lambda pdf, width, height, page_placements=page_placements: SignatureService._draw_placements(
                        pdf,
                        width,
                        height,
                        signature_image,
                        page_placements,
                    ),
                )
                page.merge_page(overlay_page)
            writer.add_page(page)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    @staticmethod
    def _draw_placements(pdf, width: float, height: float, image, placements: list[dict]) -> None:
        from reportlab.lib.utils import ImageReader

        for placement in placements:
            draw_width = float(placement.get("width", 140))
            draw_height = float(placement.get("height", 60))
            x = float(placement.get("x", width - draw_width - 36))
            y = float(placement.get("y", 36))
            signature_image = image.copy().resize((int(draw_width), int(draw_height)))
            pdf.drawImage(
                ImageReader(signature_image),
                x,
                y,
                width=draw_width,
                height=draw_height,
                mask="auto",
            )
            if placement.get("date_stamp"):
                pdf.setFont("Helvetica", 9)
                pdf.drawString(x, max(12, y - 12), str(placement["date_stamp"]))
