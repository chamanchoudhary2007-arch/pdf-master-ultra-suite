from __future__ import annotations

import difflib
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Iterable


class PDFService:
    @staticmethod
    def _pdf_objects():
        from pypdf import PdfReader, PdfWriter

        return PdfReader, PdfWriter

    @staticmethod
    def parse_page_selection(selection: str, total_pages: int) -> list[int]:
        if not selection or selection.strip().lower() in {"all", "*"}:
            return list(range(total_pages))
        pages: set[int] = set()
        for chunk in selection.split(","):
            part = chunk.strip()
            if not part:
                continue
            if "-" in part:
                start_str, end_str = part.split("-", 1)
                start = int(start_str)
                end = int(end_str)
                if start > end:
                    start, end = end, start
                for page_number in range(start, end + 1):
                    pages.add(page_number - 1)
            else:
                pages.add(int(part) - 1)
        if not pages:
            raise ValueError("No pages were selected.")
        if min(pages) < 0 or max(pages) >= total_pages:
            raise ValueError("Selected pages exceed the document size.")
        return sorted(pages)

    @staticmethod
    def merge_pdfs(input_paths: Iterable[str], output_path: str) -> str:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        writer = PdfWriter()
        for input_path in input_paths:
            reader = PdfReader(input_path)
            for page in reader.pages:
                writer.add_page(page)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    @staticmethod
    def split_pdf(
        input_path: str,
        output_dir: str,
        mode: str = "range",
        selection: str = "",
        every_n: int = 1,
    ) -> list[str]:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        output_directory = Path(output_dir)
        output_directory.mkdir(parents=True, exist_ok=True)
        results = []
        if mode == "range":
            selected_pages = PDFService.parse_page_selection(selection, len(reader.pages))
            writer = PdfWriter()
            for page_index in selected_pages:
                writer.add_page(reader.pages[page_index])
            result_path = output_directory / "split_range.pdf"
            with open(result_path, "wb") as handle:
                writer.write(handle)
            results.append(str(result_path))
            return results

        chunk_size = max(1, every_n)
        for offset in range(0, len(reader.pages), chunk_size):
            writer = PdfWriter()
            chunk = reader.pages[offset : offset + chunk_size]
            for page in chunk:
                writer.add_page(page)
            result_path = output_directory / f"split_{offset + 1:03d}.pdf"
            with open(result_path, "wb") as handle:
                writer.write(handle)
            results.append(str(result_path))
        return results

    @staticmethod
    def rotate_pdf(input_path: str, output_path: str, selection: str, angle: int) -> str:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        writer = PdfWriter()
        selected_pages = set(PDFService.parse_page_selection(selection, len(reader.pages)))
        for index, page in enumerate(reader.pages):
            if index in selected_pages:
                page.rotate(angle)
            writer.add_page(page)
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    @staticmethod
    def delete_pages(input_path: str, output_path: str, selection: str) -> str:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        delete_set = set(PDFService.parse_page_selection(selection, len(reader.pages)))
        writer = PdfWriter()
        for index, page in enumerate(reader.pages):
            if index not in delete_set:
                writer.add_page(page)
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    @staticmethod
    def reorder_pages(input_path: str, output_path: str, order: str) -> str:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        target_order = [int(item.strip()) - 1 for item in order.split(",") if item.strip()]
        if len(target_order) != len(reader.pages):
            raise ValueError("Provide a full page order covering every page once.")
        if sorted(target_order) != list(range(len(reader.pages))):
            raise ValueError("Page order must contain each page exactly once.")
        writer = PdfWriter()
        for page_index in target_order:
            writer.add_page(reader.pages[page_index])
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    @staticmethod
    def _position_coordinates(position: str, width: float, height: float) -> tuple[float, float]:
        positions = {
            "top_left": (36, height - 36),
            "top_center": (width / 2 - 60, height - 36),
            "top_right": (width - 180, height - 36),
            "bottom_left": (36, 28),
            "bottom_center": (width / 2 - 30, 28),
            "bottom_right": (width - 180, 28),
            "center": (width / 2 - 60, height / 2),
            "diagonal": (width / 3, height / 2),
        }
        return positions.get(position, positions["bottom_center"])

    @staticmethod
    def _overlay_page(
        page_width: float,
        page_height: float,
        draw_callback,
    ):
        from pypdf import PdfReader
        from reportlab.pdfgen import canvas

        buffer = BytesIO()
        overlay = canvas.Canvas(buffer, pagesize=(float(page_width), float(page_height)))
        draw_callback(overlay, float(page_width), float(page_height))
        overlay.save()
        buffer.seek(0)
        return PdfReader(buffer).pages[0]

    @staticmethod
    def add_text_watermark(
        input_path: str,
        output_path: str,
        text: str,
        opacity: float = 0.2,
        position: str = "center",
    ) -> str:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        writer = PdfWriter()
        for page in reader.pages:
            overlay_page = PDFService._overlay_page(
                page.mediabox.width,
                page.mediabox.height,
                lambda pdf, width, height: PDFService._draw_text(
                    pdf,
                    width,
                    height,
                    text=text,
                    font_size=28,
                    position=position,
                    opacity=opacity,
                ),
            )
            page.merge_page(overlay_page)
            writer.add_page(page)
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    @staticmethod
    def add_image_watermark(
        input_path: str,
        output_path: str,
        image_path: str,
        opacity: float = 0.25,
        position: str = "center",
        scale_ratio: float = 0.25,
    ) -> str:
        from PIL import Image

        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        writer = PdfWriter()
        source = Image.open(image_path)
        for page in reader.pages:
            overlay_page = PDFService._overlay_page(
                page.mediabox.width,
                page.mediabox.height,
                lambda pdf, width, height: PDFService._draw_image(
                    pdf,
                    width,
                    height,
                    source,
                    opacity=opacity,
                    position=position,
                    scale_ratio=scale_ratio,
                ),
            )
            page.merge_page(overlay_page)
            writer.add_page(page)
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    @staticmethod
    def _draw_text(
        pdf,
        width: float,
        height: float,
        text: str,
        font_size: int,
        position: str,
        opacity: float,
    ) -> None:
        from reportlab.lib.colors import Color

        x, y = PDFService._position_coordinates(position, width, height)
        pdf.saveState()
        pdf.setFillColor(Color(0.18, 0.22, 0.28, alpha=max(0.05, min(opacity, 0.95))))
        try:
            pdf.setFillAlpha(max(0.05, min(opacity, 0.95)))
        except Exception:
            pass
        pdf.setFont("Helvetica-Bold", font_size)
        if position == "diagonal":
            pdf.translate(x, y)
            pdf.rotate(32)
            pdf.drawString(0, 0, text)
        else:
            pdf.drawString(x, y, text)
        pdf.restoreState()

    @staticmethod
    def _draw_image(
        pdf,
        width: float,
        height: float,
        image,
        opacity: float,
        position: str,
        scale_ratio: float,
    ) -> None:
        from reportlab.lib.utils import ImageReader

        max_width = width * scale_ratio
        max_height = height * scale_ratio
        rendered = image.copy()
        rendered.thumbnail((max_width, max_height))
        image_reader = ImageReader(rendered)
        x, y = PDFService._position_coordinates(position, width, height)
        pdf.saveState()
        try:
            pdf.setFillAlpha(max(0.05, min(opacity, 0.95)))
        except Exception:
            pass
        pdf.drawImage(image_reader, x, y, width=rendered.width, height=rendered.height, mask="auto")
        pdf.restoreState()

    @staticmethod
    def add_page_numbers(
        input_path: str,
        output_path: str,
        position: str = "bottom_center",
        font_size: int = 10,
    ) -> str:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        writer = PdfWriter()
        total_pages = len(reader.pages)
        for index, page in enumerate(reader.pages, start=1):
            overlay_page = PDFService._overlay_page(
                page.mediabox.width,
                page.mediabox.height,
                lambda pdf, width, height, label=f"{index}/{total_pages}": PDFService._draw_text(
                    pdf,
                    width,
                    height,
                    text=label,
                    font_size=font_size,
                    position=position,
                    opacity=0.95,
                ),
            )
            page.merge_page(overlay_page)
            writer.add_page(page)
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    @staticmethod
    def add_header_footer(
        input_path: str,
        output_path: str,
        header: str = "",
        footer: str = "",
    ) -> str:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        writer = PdfWriter()
        for page in reader.pages:
            overlay_page = PDFService._overlay_page(
                page.mediabox.width,
                page.mediabox.height,
                lambda pdf, width, height: (
                    PDFService._draw_text(pdf, width, height, header, 10, "top_left", 0.95)
                    if header
                    else None,
                    PDFService._draw_text(pdf, width, height, footer, 10, "bottom_left", 0.95)
                    if footer
                    else None,
                ),
            )
            page.merge_page(overlay_page)
            writer.add_page(page)
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    @staticmethod
    def protect_pdf(input_path: str, output_path: str, password: str) -> str:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.encrypt(password)
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    @staticmethod
    def remove_password(input_path: str, output_path: str, password: str) -> str:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        if reader.is_encrypted and not reader.decrypt(password):
            raise ValueError("Incorrect PDF password.")
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    @staticmethod
    def pdf_to_text(input_path: str, output_path: str) -> str:
        PdfReader, _ = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        text_chunks = [(page.extract_text() or "").strip() for page in reader.pages]
        Path(output_path).write_text("\n\n".join(chunk for chunk in text_chunks if chunk), encoding="utf-8")
        return output_path

    @staticmethod
    def images_to_pdf(image_paths: list[str], output_path: str) -> str:
        if not image_paths:
            raise ValueError("No images were provided.")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            from PIL import Image
        except Exception:
            try:
                import fitz  # type: ignore
            except Exception as exc:
                raise ValueError(
                    "Image to PDF conversion requires Pillow (PIL) or PyMuPDF. Please install dependencies."
                ) from exc
            merged_pdf = fitz.open()
            try:
                for image_path in image_paths:
                    image_doc = fitz.open(image_path)
                    pdf_bytes = image_doc.convert_to_pdf()
                    image_pdf = fitz.open("pdf", pdf_bytes)
                    merged_pdf.insert_pdf(image_pdf)
                    image_pdf.close()
                    image_doc.close()
                merged_pdf.save(output_path)
            finally:
                merged_pdf.close()
            return output_path

        images = [Image.open(path).convert("RGB") for path in image_paths]
        try:
            first, rest = images[0], images[1:]
            first.save(output_path, save_all=True, append_images=rest)
        finally:
            for image in images:
                image.close()
        return output_path

    @staticmethod
    def pdf_to_images(input_path: str, output_dir: str, image_format: str = "png") -> list[str]:
        try:
            import fitz  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise ValueError("PyMuPDF is required for PDF to image conversion.") from exc

        document = fitz.open(input_path)
        output_directory = Path(output_dir)
        output_directory.mkdir(parents=True, exist_ok=True)
        image_paths = []
        for page_index, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            image_path = output_directory / f"page_{page_index:03d}.{image_format.lower()}"
            pixmap.save(str(image_path))
            image_paths.append(str(image_path))
        document.close()
        return image_paths

    @staticmethod
    def remove_metadata(input_path: str, output_path: str) -> str:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.add_metadata({})
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    @staticmethod
    def compress_pdf(input_path: str, output_path: str, level: str = "balanced") -> dict:
        original_size = Path(input_path).stat().st_size
        try:
            import fitz  # type: ignore
        except Exception:
            PdfReader, PdfWriter = PDFService._pdf_objects()
            reader = PdfReader(input_path)
            writer = PdfWriter()
            for page in reader.pages:
                try:
                    page.compress_content_streams()
                except Exception:
                    pass
                writer.add_page(page)
            with open(output_path, "wb") as handle:
                writer.write(handle)
        else:
            document = fitz.open(input_path)
            save_args = {
                "garbage": 3 if level == "low" else 4,
                "deflate": True,
                "clean": level != "low",
            }
            document.save(output_path, **save_args)
            document.close()
        final_size = Path(output_path).stat().st_size
        reduction = max(0, original_size - final_size)
        reduction_pct = (reduction / original_size * 100) if original_size else 0
        return {
            "output_path": output_path,
            "original_size": original_size,
            "final_size": final_size,
            "reduction_pct": round(reduction_pct, 2),
        }

    @staticmethod
    def _rasterize_pdf_for_compression(input_path: str, output_path: str, scale: float, quality: int) -> str:
        import fitz  # type: ignore

        source = fitz.open(input_path)
        target = fitz.open()
        matrix = fitz.Matrix(max(0.35, min(scale, 2.0)), max(0.35, min(scale, 2.0)))
        for page in source:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            try:
                image_bytes = pix.tobytes("jpg", jpg_quality=max(20, min(quality, 98)))
            except TypeError:
                image_bytes = pix.tobytes("jpg")
            out_page = target.new_page(width=page.rect.width, height=page.rect.height)
            out_page.insert_image(page.rect, stream=image_bytes)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        target.save(output_path, garbage=4, clean=True, deflate=True)
        target.close()
        source.close()
        return output_path

    @staticmethod
    def compress_pdf_to_target_size(input_path: str, output_path: str, target_kb: int) -> dict:
        target_bytes = max(8 * 1024, int(target_kb * 1024))
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        original_size = Path(input_path).stat().st_size

        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            candidates: list[tuple[Path, int]] = []

            baseline = temp / "baseline.pdf"
            PDFService.compress_pdf(input_path, str(baseline), level="strong")
            baseline_size = baseline.stat().st_size
            candidates.append((baseline, baseline_size))

            if baseline_size > target_bytes:
                try:
                    import fitz  # type: ignore  # noqa: F401
                except Exception:
                    pass
                else:
                    scales = [1.25, 1.1, 1.0, 0.92, 0.84, 0.76, 0.68, 0.6]
                    qualities = [90, 82, 74, 66, 58, 50, 42, 34]
                    closest_hit: tuple[Path, int] | None = None
                    best_over: tuple[Path, int] | None = None

                    for scale in scales:
                        for quality in qualities:
                            candidate_path = temp / f"r_{int(scale * 100)}_{quality}.pdf"
                            PDFService._rasterize_pdf_for_compression(
                                input_path,
                                str(candidate_path),
                                scale=scale,
                                quality=quality,
                            )
                            candidate_size = candidate_path.stat().st_size
                            candidates.append((candidate_path, candidate_size))
                            if candidate_size <= target_bytes:
                                if not closest_hit or candidate_size > closest_hit[1]:
                                    closest_hit = (candidate_path, candidate_size)
                                if candidate_size >= int(target_bytes * 0.97):
                                    break
                            elif not best_over or candidate_size < best_over[1]:
                                best_over = (candidate_path, candidate_size)
                        if closest_hit and closest_hit[1] >= int(target_bytes * 0.97):
                            break

            below = [item for item in candidates if item[1] <= target_bytes]
            if below:
                selected_path, selected_size = max(below, key=lambda item: item[1])
            else:
                selected_path, selected_size = min(candidates, key=lambda item: item[1])

            output = Path(output_path)
            output.write_bytes(selected_path.read_bytes())

        reduction = max(0, original_size - selected_size)
        reduction_pct = (reduction / original_size * 100) if original_size else 0
        return {
            "output_path": output_path,
            "original_size": original_size,
            "final_size": selected_size,
            "target_kb": target_kb,
            "achieved_target": selected_size <= target_bytes,
            "reduction_pct": round(reduction_pct, 2),
        }

    @staticmethod
    def increase_pdf_size(input_path: str, output_path: str, target_kb: int) -> dict:
        target_bytes = max(8 * 1024, int(target_kb * 1024))
        source = Path(input_path)
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(source.read_bytes())

        current_size = output.stat().st_size
        if current_size < target_bytes:
            padding_needed = target_bytes - current_size
            with output.open("ab") as handle:
                handle.write(b"\n%PDFMASTER-PADDING-START\n")
                chunk = b"%PAD-" + (b"X" * 1017) + b"\n"
                while padding_needed > 0:
                    write_size = min(len(chunk), padding_needed)
                    handle.write(chunk[:write_size])
                    padding_needed -= write_size

        final_size = output.stat().st_size
        growth = max(0, final_size - current_size)
        growth_pct = (growth / current_size * 100) if current_size else 0
        return {
            "output_path": str(output),
            "original_size": current_size,
            "final_size": final_size,
            "target_kb": target_kb,
            "growth_pct": round(growth_pct, 2),
            "achieved_target": final_size >= target_bytes,
        }

    @staticmethod
    def highlight_text(input_path: str, output_path: str, query: str) -> str:
        try:
            import fitz  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise ValueError("PyMuPDF is required for text highlighting.") from exc
        document = fitz.open(input_path)
        for page in document:
            for rect in page.search_for(query):
                page.add_highlight_annot(rect)
        document.save(output_path)
        document.close()
        return output_path

    @staticmethod
    def extract_pages_by_keywords(input_path: str, output_path: str, keywords: list[str]) -> str:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        normalized = [keyword.lower().strip() for keyword in keywords if keyword.strip()]
        if not normalized:
            raise ValueError("At least one keyword is required.")
        reader = PdfReader(input_path)
        writer = PdfWriter()
        for page in reader.pages:
            text = (page.extract_text() or "").lower()
            if any(keyword in text for keyword in normalized):
                writer.add_page(page)
        if not writer.pages:
            raise ValueError("No pages matched those keywords.")
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    @staticmethod
    def stamp_pdf(input_path: str, output_path: str, stamp_text: str, position: str = "top_right") -> str:
        return PDFService.add_text_watermark(
            input_path=input_path,
            output_path=output_path,
            text=stamp_text,
            opacity=0.85,
            position=position,
        )

    @staticmethod
    def crop_pdf(input_path: str, output_path: str, margin_percent: float = 5.0) -> str:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        writer = PdfWriter()
        margin_ratio = max(0.0, min(0.45, margin_percent / 100.0))
        for page in reader.pages:
            left = float(page.mediabox.left)
            right = float(page.mediabox.right)
            bottom = float(page.mediabox.bottom)
            top = float(page.mediabox.top)
            width = right - left
            height = top - bottom
            x_margin = width * margin_ratio
            y_margin = height * margin_ratio
            page.cropbox.lower_left = (left + x_margin, bottom + y_margin)
            page.cropbox.upper_right = (right - x_margin, top - y_margin)
            writer.add_page(page)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    @staticmethod
    def flatten_pdf(input_path: str, output_path: str, grayscale: bool = False) -> str:
        try:
            import fitz  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise ValueError("PyMuPDF is required for flatten operation.") from exc
        source = fitz.open(input_path)
        flattened = fitz.open()
        matrix = fitz.Matrix(2, 2)
        colorspace = fitz.csGRAY if grayscale else fitz.csRGB
        for page in source:
            pix = page.get_pixmap(matrix=matrix, colorspace=colorspace, alpha=False)
            flattened_page = flattened.new_page(width=pix.width, height=pix.height)
            flattened_page.insert_image(
                fitz.Rect(0, 0, pix.width, pix.height),
                pixmap=pix,
            )
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        flattened.save(output_path, deflate=True)
        flattened.close()
        source.close()
        return output_path

    @staticmethod
    def extract_images(input_path: str, output_dir: str, output_format: str = "png") -> list[str]:
        try:
            import fitz  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise ValueError("PyMuPDF is required for image extraction.") from exc
        document = fitz.open(input_path)
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        extracted_paths: list[str] = []
        for page_index, page in enumerate(document, start=1):
            images = page.get_images(full=True)
            if images:
                for image_index, image in enumerate(images, start=1):
                    xref = image[0]
                    base = document.extract_image(xref)
                    ext = base.get("ext", output_format).lower()
                    image_path = target_dir / f"page_{page_index:03d}_img_{image_index:02d}.{ext}"
                    image_path.write_bytes(base["image"])
                    extracted_paths.append(str(image_path))
            else:
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                image_path = target_dir / f"page_{page_index:03d}.{output_format.lower()}"
                pix.save(str(image_path))
                extracted_paths.append(str(image_path))
        document.close()
        return extracted_paths

    @staticmethod
    def remove_annotations(input_path: str, output_path: str) -> str:
        try:
            import fitz  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise ValueError("PyMuPDF is required for annotation cleanup.") from exc
        document = fitz.open(input_path)
        for page in document:
            annotations = list(page.annots() or [])
            for annotation in annotations:
                page.delete_annot(annotation)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        document.save(output_path)
        document.close()
        return output_path

    @staticmethod
    def repair_pdf(input_path: str, output_path: str) -> str:
        try:
            import fitz  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise ValueError("PyMuPDF is required for PDF repair.") from exc
        document = fitz.open(input_path)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        document.save(output_path, garbage=4, clean=True, deflate=True)
        document.close()
        return output_path

    @staticmethod
    def redact_pdf(input_path: str, output_path: str, terms: list[str]) -> str:
        try:
            import fitz  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise ValueError("PyMuPDF is required for redaction.") from exc
        tokens = [term.strip() for term in terms if term.strip()]
        if not tokens:
            raise ValueError("Enter at least one keyword to redact.")
        document = fitz.open(input_path)
        for page in document:
            for token in tokens:
                for rect in page.search_for(token):
                    page.add_redact_annot(rect, fill=(0, 0, 0))
            page.apply_redactions()
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        document.save(output_path)
        document.close()
        return output_path

    @staticmethod
    def compare_pdfs(input_a: str, input_b: str, output_text_path: str) -> str:
        PdfReader, _ = PDFService._pdf_objects()
        reader_a = PdfReader(input_a)
        reader_b = PdfReader(input_b)
        text_a = "\n".join((page.extract_text() or "") for page in reader_a.pages)
        text_b = "\n".join((page.extract_text() or "") for page in reader_b.pages)
        diff_lines = list(
            difflib.unified_diff(
                text_a.splitlines(),
                text_b.splitlines(),
                fromfile="document_a",
                tofile="document_b",
                lineterm="",
            )
        )
        output = "\n".join(diff_lines) or "No textual differences detected."
        Path(output_text_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_text_path).write_text(output, encoding="utf-8")
        return output_text_path

    @staticmethod
    def add_bates_numbers(
        input_path: str,
        output_path: str,
        prefix: str = "DOC",
        start_number: int = 1,
        position: str = "bottom_right",
    ) -> str:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        writer = PdfWriter()
        for index, page in enumerate(reader.pages, start=start_number):
            label = f"{prefix}-{index:06d}"
            overlay_page = PDFService._overlay_page(
                page.mediabox.width,
                page.mediabox.height,
                lambda pdf, width, height, text=label: PDFService._draw_text(
                    pdf,
                    width,
                    height,
                    text=text,
                    font_size=9,
                    position=position,
                    opacity=0.95,
                ),
            )
            page.merge_page(overlay_page)
            writer.add_page(page)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    @staticmethod
    def create_bookmarks(input_path: str, output_path: str, bookmarks: list[tuple[str, int]]) -> str:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        total_pages = len(reader.pages)
        for title, page_number in bookmarks:
            page_index = max(0, min(total_pages - 1, int(page_number) - 1))
            writer.add_outline_item(title.strip() or f"Section {page_number}", page_index)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    @staticmethod
    def split_pdf_by_text(input_path: str, output_dir: str, delimiter: str) -> list[str]:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        token = (delimiter or "").strip().lower()
        if not token:
            raise ValueError("Text delimiter is required.")
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        outputs: list[str] = []
        current = PdfWriter()
        current_count = 0
        for page_index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").lower()
            if current_count > 0 and token in text:
                out_path = target / f"split_text_{len(outputs)+1:03d}.pdf"
                with open(out_path, "wb") as handle:
                    current.write(handle)
                outputs.append(str(out_path))
                current = PdfWriter()
                current_count = 0
            current.add_page(page)
            current_count += 1
        if current_count > 0:
            out_path = target / f"split_text_{len(outputs)+1:03d}.pdf"
            with open(out_path, "wb") as handle:
                current.write(handle)
            outputs.append(str(out_path))
        return outputs

    @staticmethod
    def split_pdf_by_size(input_path: str, output_dir: str, max_size_mb: float) -> list[str]:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        max_bytes = max(256_000, int(max_size_mb * 1024 * 1024))
        outputs: list[str] = []
        current_indexes: list[int] = []

        def _writer_for(indexes: list[int]):
            writer = PdfWriter()
            for page_index in indexes:
                writer.add_page(reader.pages[page_index])
            return writer

        for page_index in range(len(reader.pages)):
            candidate_indexes = [*current_indexes, page_index]
            candidate_writer = _writer_for(candidate_indexes)
            probe = BytesIO()
            candidate_writer.write(probe)
            if current_indexes and probe.tell() > max_bytes:
                out_path = target / f"split_size_{len(outputs)+1:03d}.pdf"
                writer = _writer_for(current_indexes)
                with open(out_path, "wb") as handle:
                    writer.write(handle)
                outputs.append(str(out_path))
                current_indexes = [page_index]
            else:
                current_indexes = candidate_indexes

        if current_indexes:
            out_path = target / f"split_size_{len(outputs)+1:03d}.pdf"
            writer = _writer_for(current_indexes)
            with open(out_path, "wb") as handle:
                writer.write(handle)
            outputs.append(str(out_path))
        return outputs

    @staticmethod
    def update_metadata(input_path: str, output_path: str, metadata: dict[str, str]) -> str:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)

        sanitized: dict[str, str] = {}
        field_map = {
            "title": "/Title",
            "author": "/Author",
            "subject": "/Subject",
            "keywords": "/Keywords",
            "creator": "/Creator",
            "producer": "/Producer",
        }
        for key, value in metadata.items():
            normalized = (value or "").strip()
            if normalized and key in field_map:
                sanitized[field_map[key]] = normalized
        if sanitized:
            writer.add_metadata(sanitized)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    @staticmethod
    def split_pdf_in_half(input_path: str, output_dir: str) -> list[str]:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        total_pages = len(reader.pages)
        midpoint = max(1, total_pages // 2)
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        slices = [(0, midpoint), (midpoint, total_pages)]
        for index, (start, end) in enumerate(slices, start=1):
            writer = PdfWriter()
            for page_idx in range(start, end):
                writer.add_page(reader.pages[page_idx])
            if len(writer.pages) == 0:
                continue
            out_path = output / f"split_half_{index:02d}.pdf"
            with open(out_path, "wb") as handle:
                writer.write(handle)
            paths.append(str(out_path))
        return paths

    @staticmethod
    def split_pdf_by_bookmarks(input_path: str, output_dir: str) -> list[str]:
        PdfReader, PdfWriter = PDFService._pdf_objects()
        reader = PdfReader(input_path)
        outlines = reader.outline
        starts: list[tuple[str, int]] = []
        for item in outlines:
            if isinstance(item, list):
                continue
            title = getattr(item, "title", "") or "section"
            page_index = reader.get_destination_page_number(item)
            starts.append((title, page_index))
        if not starts:
            raise ValueError("No top-level bookmarks found in this PDF.")
        starts = sorted(starts, key=lambda entry: entry[1])
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        outputs: list[str] = []
        for index, (title, start_page) in enumerate(starts):
            end_page = starts[index + 1][1] if index + 1 < len(starts) else len(reader.pages)
            writer = PdfWriter()
            for page_idx in range(start_page, end_page):
                writer.add_page(reader.pages[page_idx])
            safe_title = "".join(ch for ch in title if ch.isalnum() or ch in {"-", "_", " "}).strip() or f"part_{index+1}"
            out_path = target / f"{index+1:02d}_{safe_title.replace(' ', '_')}.pdf"
            with open(out_path, "wb") as handle:
                writer.write(handle)
            outputs.append(str(out_path))
        return outputs

    @staticmethod
    def alternate_mix_pdfs(input_paths: list[str], output_path: str) -> str:
        if len(input_paths) < 2:
            raise ValueError("At least two PDF files are required for alternate mix.")
        PdfReader, PdfWriter = PDFService._pdf_objects()
        readers = [PdfReader(path) for path in input_paths]
        writer = PdfWriter()
        max_pages = max(len(reader.pages) for reader in readers)
        for page_index in range(max_pages):
            for reader in readers:
                if page_index < len(reader.pages):
                    writer.add_page(reader.pages[page_index])
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    @staticmethod
    def apply_editor_actions(input_path: str, output_path: str, actions: list[dict], image_paths: dict[str, str] | None = None) -> str:
        try:
            import fitz  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise ValueError("PyMuPDF is required for PDF editor actions.") from exc

        document = fitz.open(input_path)
        image_paths = image_paths or {}

        def _color(raw: str | None, fallback: tuple[float, float, float] = (0.09, 0.19, 0.16)) -> tuple[float, float, float]:
            value = (raw or "").strip().lstrip("#")
            if len(value) == 6:
                try:
                    return tuple(int(value[index : index + 2], 16) / 255.0 for index in (0, 2, 4))  # type: ignore[return-value]
                except Exception:
                    return fallback
            return fallback

        for action in actions:
            action_type = (action.get("type") or "").strip().lower()
            page_index = max(0, int(action.get("page", 1)) - 1)
            if page_index >= len(document):
                continue
            page = document[page_index]
            color = _color(action.get("color"))

            if action_type == "text":
                text = str(action.get("text", "")).strip()
                if not text:
                    continue
                x = float(action.get("x", 72))
                y = float(action.get("y", 72))
                font_size = max(6.0, min(96.0, float(action.get("font_size", 14))))
                page.insert_text(
                    fitz.Point(x, y),
                    text,
                    fontsize=font_size,
                    color=color,
                )
            elif action_type == "line":
                x1 = float(action.get("x1", 72))
                y1 = float(action.get("y1", 72))
                x2 = float(action.get("x2", x1 + 120))
                y2 = float(action.get("y2", y1))
                width = max(0.5, min(12.0, float(action.get("width", 2))))
                page.draw_line(
                    fitz.Point(x1, y1),
                    fitz.Point(x2, y2),
                    color=color,
                    width=width,
                )
            elif action_type == "rect":
                x = float(action.get("x", 72))
                y = float(action.get("y", 72))
                width = max(8.0, float(action.get("width", 160)))
                height = max(8.0, float(action.get("height", 80)))
                stroke_width = max(0.5, min(12.0, float(action.get("stroke_width", 2))))
                fill = action.get("fill", False)
                fill_color = _color(action.get("fill_color"), fallback=(0.92, 0.96, 0.94))
                rect = fitz.Rect(x, y, x + width, y + height)
                page.draw_rect(
                    rect,
                    color=color,
                    fill=fill_color if fill else None,
                    width=stroke_width,
                )
            elif action_type == "image":
                image_key = str(action.get("image_key", "")).strip()
                image_path = image_paths.get(image_key)
                if not image_path:
                    continue
                x = float(action.get("x", 72))
                y = float(action.get("y", 72))
                width = max(8.0, float(action.get("width", 140)))
                height = max(8.0, float(action.get("height", 64)))
                page.insert_image(
                    fitz.Rect(x, y, x + width, y + height),
                    filename=image_path,
                    keep_proportion=False,
                )

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        document.save(output_path)
        document.close()
        return output_path

    @staticmethod
    def create_form_layout(input_path: str, output_path: str, fields: list[dict]) -> str:
        writer_path = Path(output_path)
        writer_path.parent.mkdir(parents=True, exist_ok=True)
        PdfReaderCls, PdfWriterCls = PDFService._pdf_objects()
        base_reader = PdfReaderCls(input_path)
        writer = PdfWriterCls()

        fields_by_page: dict[int, list[dict]] = {}
        for field in fields:
            page_index = max(0, int(field.get("page", 1)) - 1)
            fields_by_page.setdefault(page_index, []).append(field)

        for page_index, page in enumerate(base_reader.pages):
            page_fields = fields_by_page.get(page_index, [])
            if page_fields:
                overlay_page = PDFService._overlay_page(
                    page.mediabox.width,
                    page.mediabox.height,
                    lambda pdf, width, height, page_fields=page_fields: PDFService._draw_form_fields(
                        pdf, page_fields
                    ),
                )
                page.merge_page(overlay_page)
            writer.add_page(page)

        with writer_path.open("wb") as handle:
            writer.write(handle)
        return str(writer_path)

    @staticmethod
    def _draw_form_fields(pdf, fields: list[dict]) -> None:
        for field in fields:
            label = str(field.get("label", "")).strip()
            x = float(field.get("x", 72))
            y = float(field.get("y", 72))
            width = max(30.0, float(field.get("width", 180)))
            height = max(18.0, float(field.get("height", 22)))
            if label:
                pdf.setFont("Helvetica", 9)
                pdf.drawString(x, y + height + 4, label)
            pdf.setLineWidth(1)
            pdf.rect(x, y, width, height, stroke=1, fill=0)

    @staticmethod
    def n_up_pdf(input_path: str, output_path: str, pages_per_sheet: int = 2) -> str:
        try:
            import fitz  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise ValueError("PyMuPDF is required for N-up conversion.") from exc

        layout_map = {2: (2, 1), 4: (2, 2), 6: (3, 2), 8: (4, 2), 9: (3, 3)}
        cols, rows = layout_map.get(pages_per_sheet, (2, 1))
        source = fitz.open(input_path)
        if len(source) == 0:
            raise ValueError("PDF has no pages.")
        base_rect = source[0].rect
        sheet_width = base_rect.width
        sheet_height = base_rect.height
        cell_width = sheet_width / cols
        cell_height = sheet_height / rows

        output = fitz.open()
        for start in range(0, len(source), pages_per_sheet):
            sheet = output.new_page(width=sheet_width, height=sheet_height)
            for offset in range(pages_per_sheet):
                source_index = start + offset
                if source_index >= len(source):
                    break
                row = offset // cols
                col = offset % cols
                x0 = col * cell_width
                y0 = sheet_height - (row + 1) * cell_height
                x1 = x0 + cell_width
                y1 = y0 + cell_height
                target_rect = fitz.Rect(x0, y0, x1, y1)
                sheet.show_pdf_page(target_rect, source, source_index, keep_proportion=True)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        output.save(output_path)
        output.close()
        source.close()
        return output_path

    @staticmethod
    def resize_pdf_pages(input_path: str, output_path: str, scale_percent: float = 100.0, margin_points: float = 0.0) -> str:
        try:
            import fitz  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise ValueError("PyMuPDF is required for resize operation.") from exc

        source = fitz.open(input_path)
        target = fitz.open()
        scale = max(10.0, min(400.0, scale_percent)) / 100.0
        margin = max(0.0, min(500.0, margin_points))
        for page_index in range(len(source)):
            page = source[page_index]
            original = page.rect
            content_width = original.width * scale
            content_height = original.height * scale
            out_width = content_width + margin * 2
            out_height = content_height + margin * 2
            out_page = target.new_page(width=out_width, height=out_height)
            placement = fitz.Rect(margin, margin, margin + content_width, margin + content_height)
            out_page.show_pdf_page(placement, source, page_index, keep_proportion=True)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        target.save(output_path)
        target.close()
        source.close()
        return output_path

    @staticmethod
    def deskew_pdf(input_path: str, output_path: str) -> str:
        try:
            import fitz  # type: ignore
            import cv2  # type: ignore
            import numpy as np  # type: ignore
            from PIL import Image
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise ValueError("Deskew requires PyMuPDF, OpenCV, numpy, and Pillow.") from exc

        def _estimated_angle(gray_frame):
            inverted = cv2.bitwise_not(gray_frame)
            _, threshold = cv2.threshold(inverted, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
            coordinates = np.column_stack(np.where(threshold > 0))
            if coordinates.size == 0:
                return 0.0
            angle = cv2.minAreaRect(coordinates)[-1]
            if angle < -45:
                angle = -(90 + angle)
            else:
                angle = -angle
            return float(angle)

        with tempfile.TemporaryDirectory() as temp_dir:
            source = fitz.open(input_path)
            image_paths: list[str] = []
            for page_index, page in enumerate(source, start=1):
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                bgr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY if pix.n == 3 else cv2.COLOR_BGRA2GRAY)
                angle = _estimated_angle(gray)
                pil_image = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB if pix.n == 3 else cv2.COLOR_BGRA2RGB))
                corrected = pil_image.rotate(angle, expand=True, fillcolor="white")
                image_path = Path(temp_dir) / f"deskew_{page_index:03d}.png"
                corrected.save(image_path)
                image_paths.append(str(image_path))
            source.close()
            return PDFService.images_to_pdf(image_paths, output_path)
