from __future__ import annotations

import re
import time
from pathlib import Path

from flask import current_app

from app.extensions import db
from app.models import (
    DocumentChatChunk,
    DocumentChatMessage,
    DocumentChatSession,
    ManagedFile,
    utcnow,
)
from app.services.ai_service import AIDocumentService
from app.services.ocr_service import OCRService
from app.services.storage_service import StorageService


class DocumentChatService:
    CHUNK_SIZE = 1200
    CHUNK_OVERLAP = 180
    SUGGESTED_QUESTIONS = [
        "Give me a short summary of this document.",
        "List key points page-wise.",
        "Find invoice numbers and total amount.",
        "Show contract obligations and deadlines.",
        "Which page contains formulas or calculations?",
    ]

    @staticmethod
    def _extract_pages_text(pdf_path: Path, temp_dir: Path) -> list[tuple[int, str]]:
        try:
            from pypdf import PdfReader
        except Exception as exc:
            raise ValueError("pypdf is required for document chat.") from exc

        reader = PdfReader(str(pdf_path))
        page_rows: list[tuple[int, str]] = []
        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            page_rows.append((index, text))

        if any(len(text) > 30 for _, text in page_rows):
            return page_rows

        ocr_pdf = temp_dir / "ocr_searchable.pdf"
        ocr_txt = temp_dir / "ocr_text.txt"
        try:
            OCRService.ocr_pdf_to_searchable(
                input_pdf_path=str(pdf_path),
                output_pdf_path=str(ocr_pdf),
                output_text_path=str(ocr_txt),
                lang=(current_app.config.get("OCR_LANG") or "eng"),
            )
        except Exception:
            current_app.logger.exception("OCR fallback failed for chat extraction")
            return page_rows

        ocr_text = ocr_txt.read_text(encoding="utf-8", errors="ignore").strip()
        if not ocr_text:
            return page_rows
        fallback_chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", ocr_text) if chunk.strip()]
        return [(index + 1, chunk) for index, chunk in enumerate(fallback_chunks)]

    @staticmethod
    def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
        cleaned = (text or "").strip()
        if not cleaned:
            return []
        if len(cleaned) <= size:
            return [cleaned]

        chunks: list[str] = []
        cursor = 0
        while cursor < len(cleaned):
            end = min(len(cleaned), cursor + size)
            chunks.append(cleaned[cursor:end])
            if end >= len(cleaned):
                break
            cursor = max(end - overlap, cursor + 1)
        return chunks

    @staticmethod
    def create_session(
        *,
        user_id: int,
        source_file: ManagedFile,
        title: str = "",
    ) -> DocumentChatSession:
        source_path = StorageService.absolute_path(source_file)
        if source_path.suffix.lower() != ".pdf":
            raise ValueError("Chat with PDF currently supports PDF files only.")

        session = DocumentChatSession(
            user_id=user_id,
            source_file_id=source_file.id,
            title=(title or source_file.original_name or "Document Chat").strip()[:255],
            status="indexing",
            extraction_strategy="text_then_ocr",
            metadata_json={},
        )
        db.session.add(session)
        db.session.flush()

        temp_dir = (
            Path(current_app.config["OUTPUT_ROOT"])
            / str(user_id)
            / "chat"
            / f"session_{session.id}"
        )
        temp_dir.mkdir(parents=True, exist_ok=True)

        page_rows = DocumentChatService._extract_pages_text(source_path, temp_dir)
        total_chunks = 0
        total_chars = 0
        for page_number, text in page_rows:
            for chunk_index, chunk in enumerate(DocumentChatService._chunk_text(text)):
                keywords = AIDocumentService.extract_keywords(chunk, limit=12)
                row = DocumentChatChunk(
                    session_id=session.id,
                    page_number=max(1, int(page_number)),
                    chunk_index=chunk_index,
                    token_count=max(1, len(chunk.split())),
                    content=chunk,
                    keywords_json=keywords,
                )
                db.session.add(row)
                total_chunks += 1
                total_chars += len(chunk)

        if total_chunks == 0:
            session.status = "failed"
            session.metadata_json = {"reason": "No readable text found"}
            db.session.commit()
            raise ValueError(
                "Could not extract readable text from this document. Try OCR Suite first."
            )

        session.status = "ready"
        session.metadata_json = {
            "total_pages_detected": len(page_rows),
            "total_chunks": total_chunks,
            "total_characters": total_chars,
        }
        db.session.commit()
        return session

    @staticmethod
    def list_sessions(user_id: int, limit: int = 20) -> list[DocumentChatSession]:
        return (
            DocumentChatSession.query.filter_by(user_id=user_id)
            .order_by(DocumentChatSession.updated_at.desc())
            .limit(max(1, limit))
            .all()
        )

    @staticmethod
    def get_session_for_user(session_id: int, user_id: int) -> DocumentChatSession:
        session = DocumentChatSession.query.filter_by(id=session_id, user_id=user_id).first()
        if not session:
            raise ValueError("Chat session not found.")
        return session

    @staticmethod
    def clear_conversation(session_id: int, user_id: int) -> None:
        session = DocumentChatService.get_session_for_user(session_id, user_id)
        DocumentChatMessage.query.filter_by(session_id=session.id).delete(synchronize_session=False)
        session.last_asked_at = None
        db.session.commit()

    @staticmethod
    def _detect_page_filter(question: str) -> int | None:
        match = re.search(r"\bpage\s+(\d{1,4})\b", (question or "").lower())
        if not match:
            return None
        return int(match.group(1))

    @staticmethod
    def _rank_chunks(
        *,
        chunks: list[DocumentChatChunk],
        question: str,
        page_filter: int | None,
        top_n: int = 6,
    ) -> list[DocumentChatChunk]:
        lowered = (question or "").lower()
        tokens = [token for token in re.findall(r"[a-zA-Z]{3,}", lowered) if token not in AIDocumentService.STOP_WORDS]
        scored: list[tuple[int, int, DocumentChatChunk]] = []

        for chunk in chunks:
            if page_filter and chunk.page_number != page_filter:
                continue
            text = (chunk.content or "").lower()
            score = 0
            for token in tokens:
                score += text.count(token) * 4
                if token in [str(word).lower() for word in (chunk.keywords_json or [])]:
                    score += 6
            if page_filter:
                score += 5
            score += max(1, min(10, int(chunk.token_count / 80)))
            scored.append((score, -chunk.page_number, chunk))

        if not scored:
            return []
        scored.sort(key=lambda row: row[0], reverse=True)
        return [row[2] for row in scored[: max(1, top_n)]]

    @staticmethod
    def _extract_contract_invoice_bits(text: str) -> dict:
        raw = text or ""
        invoice_numbers = sorted(set(re.findall(r"\b(?:invoice|inv)\s*(?:no|#|number)?\s*[:\-]?\s*([A-Z0-9\-/]{4,})", raw, flags=re.IGNORECASE)))
        dates = sorted(set(re.findall(r"\b\d{1,2}[\-/]\d{1,2}[\-/]\d{2,4}\b", raw)))
        totals = sorted(set(re.findall(r"(?:INR|Rs\.?|USD|EUR|GBP)?\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})", raw)))
        parties = sorted(set(re.findall(r"\bbetween\s+([A-Za-z0-9 ,.&\-]{3,60})\s+and\s+([A-Za-z0-9 ,.&\-]{3,60})", raw, flags=re.IGNORECASE)))
        return {
            "invoice_numbers": invoice_numbers[:10],
            "dates": dates[:12],
            "totals": totals[:12],
            "parties": [" and ".join(pair) for pair in parties[:6]],
        }

    @staticmethod
    def _formula_lines(text: str, limit: int = 8) -> list[str]:
        lines = []
        for line in (text or "").splitlines():
            normalized = line.strip()
            if not normalized:
                continue
            if re.search(r"[=+\-*/^]", normalized) and re.search(r"\d", normalized):
                lines.append(normalized)
            if len(lines) >= limit:
                break
        return lines

    @staticmethod
    def ask_question(*, session_id: int, user_id: int, question: str) -> dict:
        started = time.perf_counter()
        query = (question or "").strip()
        if len(query) < 2:
            raise ValueError("Ask a complete question.")

        session = DocumentChatService.get_session_for_user(session_id, user_id)
        chunks = (
            DocumentChatChunk.query.filter_by(session_id=session.id)
            .order_by(DocumentChatChunk.page_number.asc(), DocumentChatChunk.chunk_index.asc())
            .all()
        )
        if not chunks:
            raise ValueError("No indexed content found for this chat session.")

        page_filter = DocumentChatService._detect_page_filter(query)
        selected_chunks = DocumentChatService._rank_chunks(
            chunks=chunks,
            question=query,
            page_filter=page_filter,
            top_n=6,
        )
        if not selected_chunks:
            selected_chunks = chunks[:6]

        context_text = "\n\n".join(chunk.content for chunk in selected_chunks if chunk.content)
        ql = query.lower()

        if any(token in ql for token in ("summary", "summarize", "overview")):
            answer = AIDocumentService.summarize_text(context_text, max_sentences=6)
        elif "key point" in ql or "bullet" in ql:
            notes = AIDocumentService.generate_notes(context_text)[:8]
            answer = "\n".join(f"- {note}" for note in notes) if notes else "No key points detected."
        elif "keyword" in ql:
            keywords = AIDocumentService.extract_keywords(context_text, limit=15)
            answer = "Keywords: " + ", ".join(keywords) if keywords else "No strong keywords found."
        elif any(token in ql for token in ("invoice", "contract", "amount", "clause")):
            bits = DocumentChatService._extract_contract_invoice_bits(context_text)
            lines = ["Invoice/contract extraction:"]
            if bits["invoice_numbers"]:
                lines.append("- Invoice numbers: " + ", ".join(bits["invoice_numbers"]))
            if bits["dates"]:
                lines.append("- Dates: " + ", ".join(bits["dates"][:8]))
            if bits["totals"]:
                lines.append("- Amount-like values: " + ", ".join(bits["totals"][:8]))
            if bits["parties"]:
                lines.append("- Parties: " + "; ".join(bits["parties"][:4]))
            if len(lines) == 1:
                lines.append("- No strong structured invoice/contract markers found in the selected text.")
            answer = "\n".join(lines)
        elif any(token in ql for token in ("formula", "equation", "important section")):
            formulas = DocumentChatService._formula_lines(context_text)
            if formulas:
                answer = "Detected formula-like lines:\n" + "\n".join(f"- {line}" for line in formulas)
            else:
                answer = "No formula-like lines were detected in the selected content."
        else:
            summary = AIDocumentService.summarize_text(context_text, max_sentences=4)
            keywords = AIDocumentService.extract_keywords(context_text, limit=8)
            answer = summary
            if keywords:
                answer += "\n\nKey terms: " + ", ".join(keywords)
            if page_filter:
                answer += f"\n\nScoped to page {page_filter}."

        source_rows = [
            {
                "chunk_id": chunk.id,
                "page": int(chunk.page_number),
                "preview": (chunk.content or "")[:180],
            }
            for chunk in selected_chunks
        ]
        latency_ms = int((time.perf_counter() - started) * 1000)

        message = DocumentChatMessage(
            session_id=session.id,
            role="assistant",
            question=query,
            answer=answer,
            sources_json=source_rows,
            latency_ms=latency_ms,
        )
        db.session.add(message)
        session.last_asked_at = utcnow()
        db.session.commit()

        return {
            "session": session,
            "answer": answer,
            "sources": source_rows,
            "latency_ms": latency_ms,
            "suggested_questions": DocumentChatService.SUGGESTED_QUESTIONS,
        }
