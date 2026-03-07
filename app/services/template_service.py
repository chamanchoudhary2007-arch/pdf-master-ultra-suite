from __future__ import annotations

from pathlib import Path


class TemplateService:
    @staticmethod
    def generate_document(template_key: str, data: dict, output_path: str | Path) -> str:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pdf = canvas.Canvas(str(output_path), pagesize=A4)
        width, height = A4

        title_map = {
            "resume": "Resume Builder",
            "invoice": "Invoice Generator",
            "cover_letter": "Cover Letter Builder",
            "certificate": "Certificate Generator",
        }
        pdf.setTitle(title_map.get(template_key, "Document Template"))
        pdf.setFont("Helvetica-Bold", 22)
        pdf.drawString(48, height - 56, title_map.get(template_key, "Document"))
        pdf.setFont("Helvetica", 11)
        current_y = height - 96
        for key, value in data.items():
            pdf.drawString(48, current_y, f"{key.replace('_', ' ').title()}: {value}")
            current_y -= 20
            if current_y < 60:
                pdf.showPage()
                pdf.setFont("Helvetica", 11)
                current_y = height - 56
        pdf.save()
        return str(output_path)
