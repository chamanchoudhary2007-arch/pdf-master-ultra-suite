from __future__ import annotations

from pathlib import Path


class GovernmentService:
    ACTION_TITLES = {
        "office_memo": "Office Memorandum",
        "official_letter": "Official Letter",
        "rti_reply": "RTI Reply Draft",
        "file_note": "File Note Draft",
    }

    @staticmethod
    def _split_points(raw_points: str) -> list[str]:
        chunks = []
        for line in (raw_points or "").replace(";", "\n").splitlines():
            point = line.strip(" -\t")
            if point:
                chunks.append(point)
        return chunks

    @staticmethod
    def build_document(action: str, payload: dict) -> dict:
        title = GovernmentService.ACTION_TITLES.get(action, "Government Document")
        department = (payload.get("department") or "Department Name").strip()
        reference_no = (payload.get("reference_no") or "REF/0000/2026").strip()
        subject = (payload.get("subject") or "Document Subject").strip()
        recipient = (payload.get("recipient") or "Concerned Officer").strip()
        signatory = (payload.get("signatory") or "Authorized Signatory").strip()
        points = GovernmentService._split_points(payload.get("points", ""))
        if not points:
            points = [
                "Summarize the purpose clearly.",
                "Add policy/legal reference where needed.",
                "Mention action owner and timeline.",
            ]

        lines = [
            title.upper(),
            "",
            f"Department: {department}",
            f"Reference No: {reference_no}",
            f"Subject: {subject}",
            "",
            f"To: {recipient}",
            "",
        ]

        if action == "rti_reply":
            lines.append("In reference to the RTI request, point-wise response is provided below:")
        elif action == "file_note":
            lines.append("File note for internal approval and next action:")
        elif action == "official_letter":
            lines.append("This is to formally communicate the following:")
        else:
            lines.append("The following instructions are issued for immediate compliance:")
        lines.append("")

        for idx, point in enumerate(points, start=1):
            lines.append(f"{idx}. {point}")

        lines.extend(
            [
                "",
                "Action Required:",
                "- Review and approve the above points.",
                "- Record compliance with date and officer remarks.",
                "",
                f"Issued by: {signatory}",
            ]
        )

        filename_prefix = action if action in GovernmentService.ACTION_TITLES else "gov_document"
        return {
            "title": title,
            "text": "\n".join(lines),
            "filename_prefix": filename_prefix,
        }

    @staticmethod
    def render_pdf(title: str, text: str, output_path: str | Path) -> str:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pdf = canvas.Canvas(str(output_path), pagesize=A4)
        _, height = A4
        y = height - 48

        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(42, y, title)
        y -= 26
        pdf.setFont("Helvetica", 10)
        for line in text.splitlines():
            if y < 48:
                pdf.showPage()
                pdf.setFont("Helvetica", 10)
                y = height - 48
            pdf.drawString(42, y, line[:150])
            y -= 15
        pdf.save()
        return str(output_path)
