from __future__ import annotations

import re
from collections import Counter


class AIDocumentService:
    STOP_WORDS = {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "your",
        "have",
        "will",
        "into",
        "about",
        "there",
        "were",
        "their",
        "when",
        "which",
        "while",
        "where",
        "also",
        "them",
        "they",
        "been",
        "being",
    }
    LANGUAGE_ALIASES = {
        "hi": "hi",
        "hin": "hi",
        "hind": "hi",
        "hindi": "hi",
        "hnd": "hi",
        "hindi-india": "hi",
        "hindi india": "hi",
        "english": "en",
        "eng": "en",
        "en": "en",
        "bengali": "bn",
        "bangla": "bn",
        "bn": "bn",
        "marathi": "mr",
        "mr": "mr",
        "gujarati": "gu",
        "gu": "gu",
        "tamil": "ta",
        "ta": "ta",
        "telugu": "te",
        "te": "te",
        "kannada": "kn",
        "kn": "kn",
        "malayalam": "ml",
        "ml": "ml",
        "punjabi": "pa",
        "pa": "pa",
        "urdu": "ur",
        "ur": "ur",
    }

    @staticmethod
    def split_sentences(text: str) -> list[str]:
        return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]

    @staticmethod
    def summarize_text(text: str, max_sentences: int = 4) -> str:
        sentences = AIDocumentService.split_sentences(text)
        if len(sentences) <= max_sentences:
            return text.strip()
        words = re.findall(r"[A-Za-z]{3,}", text.lower())
        frequency = Counter(word for word in words if word not in AIDocumentService.STOP_WORDS)
        scored = []
        for sentence in sentences:
            sentence_words = re.findall(r"[A-Za-z]{3,}", sentence.lower())
            score = sum(frequency.get(word, 0) for word in sentence_words)
            scored.append((score, sentence))
        top = [sentence for _, sentence in sorted(scored, reverse=True)[:max_sentences]]
        ordered = [sentence for sentence in sentences if sentence in top]
        return " ".join(ordered)

    @staticmethod
    def extract_keywords(text: str, limit: int = 10) -> list[str]:
        words = re.findall(r"[A-Za-z]{4,}", text.lower())
        counts = Counter(word for word in words if word not in AIDocumentService.STOP_WORDS)
        return [word for word, _ in counts.most_common(limit)]

    @staticmethod
    def generate_notes(text: str) -> list[str]:
        summary = AIDocumentService.summarize_text(text, max_sentences=6)
        return [sentence for sentence in AIDocumentService.split_sentences(summary)]

    @staticmethod
    def generate_flashcards(text: str, limit: int = 8) -> list[dict]:
        notes = AIDocumentService.generate_notes(text)[:limit]
        flashcards = []
        for note in notes:
            tokens = note.split()
            subject = " ".join(tokens[: min(5, len(tokens))])
            flashcards.append(
                {
                    "front": f"What is the key point about '{subject}'?",
                    "back": note,
                }
            )
        return flashcards

    @staticmethod
    def generate_quiz(text: str, limit: int = 5) -> list[dict]:
        notes = AIDocumentService.generate_notes(text)[:limit]
        quiz = []
        for index, note in enumerate(notes, start=1):
            keywords = AIDocumentService.extract_keywords(note, limit=2)
            answer = ", ".join(keywords) if keywords else note[:24]
            quiz.append(
                {
                    "question": f"Question {index}: What are the main terms from this note?",
                    "prompt": note,
                    "answer": answer,
                }
            )
        return quiz

    @staticmethod
    def normalize_language_code(target_language: str) -> str:
        raw = (target_language or "").strip().lower().replace("_", "-")
        if not raw:
            return "hi"
        normalized = AIDocumentService.LANGUAGE_ALIASES.get(raw, raw)
        if re.fullmatch(r"[a-z]{2,3}", normalized):
            return normalized
        if re.fullmatch(r"[a-z]{4,12}", normalized):
            return normalized[:2]
        return "hi"

    @staticmethod
    def _chunk_text(text: str, limit: int) -> list[str]:
        chunks: list[str] = []
        current = ""
        for line in text.splitlines(keepends=True):
            if len(current) + len(line) > limit and current:
                chunks.append(current)
                current = line
            else:
                current += line
        if current:
            chunks.append(current)
        return chunks or [text]

    @staticmethod
    def _translate_with_deep_translator(text: str, target_language: str) -> str:
        from deep_translator import GoogleTranslator  # type: ignore

        translator = GoogleTranslator(source="auto", target=target_language)
        pieces = []
        for chunk in AIDocumentService._chunk_text(text, 3000):
            if chunk.strip():
                pieces.append(translator.translate(chunk))
            else:
                pieces.append(chunk)
        return "".join(pieces)

    @staticmethod
    def _translate_with_googletrans(text: str, target_language: str) -> str:
        from googletrans import Translator  # type: ignore

        translator = Translator()
        pieces = []
        for chunk in AIDocumentService._chunk_text(text, 4000):
            if chunk.strip():
                pieces.append(translator.translate(chunk, dest=target_language).text)
            else:
                pieces.append(chunk)
        return "".join(pieces)

    @staticmethod
    def translate_text(text: str, target_language: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            raise ValueError("No text available for translation.")
        target_code = AIDocumentService.normalize_language_code(target_language)
        translators = (
            AIDocumentService._translate_with_deep_translator,
            AIDocumentService._translate_with_googletrans,
        )
        last_error = None
        for translator in translators:
            try:
                translated = translator(cleaned, target_code).strip()
                if translated:
                    return translated
            except Exception as exc:
                last_error = exc
                continue
        error_message = "Translation service unavailable. Install deep-translator/googletrans or enable internet."
        if last_error:
            raise ValueError(f"{error_message} Last error: {last_error}") from last_error
        raise ValueError(error_message)
