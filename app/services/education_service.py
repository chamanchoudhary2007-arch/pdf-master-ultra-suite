from __future__ import annotations

import re

from app.services.ai_service import AIDocumentService


class EducationService:
    @staticmethod
    def _sanitize_text(text: str) -> str:
        normalized = re.sub(r"\s+", " ", (text or "")).strip()
        if not normalized:
            raise ValueError("Unable to extract readable text from the PDF.")
        return normalized

    @staticmethod
    def _formula_lines(text: str, limit: int = 20) -> list[str]:
        lines = []
        seen = set()
        for raw_line in (text or "").splitlines():
            line = raw_line.strip()
            if not line or line in seen:
                continue
            if re.search(r"[\d=+\-*/%^<>]{2,}", line):
                lines.append(line)
                seen.add(line)
            if len(lines) >= limit:
                break
        return lines

    @staticmethod
    def _revision_plan(notes: list[str], days: int = 7) -> list[str]:
        days = max(1, min(days, 30))
        if not notes:
            return [f"Day {day}: Read and summarize one section." for day in range(1, days + 1)]
        plan = []
        for day in range(1, days + 1):
            topic = notes[(day - 1) % len(notes)]
            plan.append(f"Day {day}: Review '{topic[:80]}' and write 5 key points.")
        return plan

    @staticmethod
    def build_study_pack(
        text: str,
        flashcard_limit: int = 12,
        quiz_limit: int = 10,
        revision_days: int = 7,
    ) -> dict:
        cleaned = EducationService._sanitize_text(text)
        summary = AIDocumentService.summarize_text(cleaned, max_sentences=6)
        notes = AIDocumentService.generate_notes(cleaned)[:18]
        keywords = AIDocumentService.extract_keywords(cleaned, limit=25)
        flashcards = AIDocumentService.generate_flashcards(
            cleaned, limit=max(4, min(flashcard_limit, 40))
        )
        quiz = AIDocumentService.generate_quiz(cleaned, limit=max(3, min(quiz_limit, 30)))
        formula_sheet = EducationService._formula_lines(text, limit=25)
        revision_plan = EducationService._revision_plan(notes, days=revision_days)
        return {
            "summary": summary,
            "notes": notes,
            "keywords": keywords,
            "flashcards": flashcards,
            "quiz": quiz,
            "formula_sheet": formula_sheet,
            "revision_plan": revision_plan,
        }

    @staticmethod
    def build_teacher_toolkit(
        text: str,
        objective_count: int = 10,
        subjective_count: int = 5,
        total_marks: int = 100,
        class_duration_minutes: int = 45,
    ) -> dict:
        cleaned = EducationService._sanitize_text(text)
        notes = AIDocumentService.generate_notes(cleaned)
        keywords = AIDocumentService.extract_keywords(cleaned, limit=40)

        objective_count = max(5, min(objective_count, 40))
        subjective_count = max(2, min(subjective_count, 20))
        total_marks = max(20, min(total_marks, 300))
        class_duration_minutes = max(20, min(class_duration_minutes, 180))

        objective_questions = []
        for idx in range(1, objective_count + 1):
            keyword = keywords[(idx - 1) % len(keywords)] if keywords else f"topic {idx}"
            objective_questions.append(
                f"Q{idx}. Write a short definition of '{keyword}' and one practical example."
            )

        subjective_questions = []
        for idx in range(1, subjective_count + 1):
            note = notes[(idx - 1) % len(notes)] if notes else f"core concept {idx}"
            subjective_questions.append(
                f"Q{objective_count + idx}. Explain in detail: {note[:140]}"
            )

        objective_mark = max(1, total_marks // (objective_count + subjective_count * 2))
        subjective_mark = max(objective_mark + 1, (total_marks - objective_count * objective_mark) // subjective_count)
        rubric_lines = [
            f"Objective section: {objective_count} questions x {objective_mark} marks",
            f"Subjective section: {subjective_count} questions x {subjective_mark} marks",
            "Evaluation criteria: concept clarity, structure, examples, and technical accuracy.",
            "Late submission policy: -10% marks after deadline.",
            "Re-evaluation policy: request within 3 working days.",
        ]

        answer_key_lines = []
        for idx, _ in enumerate(objective_questions, start=1):
            keyword = keywords[(idx - 1) % len(keywords)] if keywords else "concept"
            answer_key_lines.append(f"Q{idx}: definition + example focused on '{keyword}'.")
        for idx, _ in enumerate(subjective_questions, start=1):
            source = notes[(idx - 1) % len(notes)] if notes else "detailed conceptual explanation"
            answer_key_lines.append(f"Q{objective_count + idx}: {source}")

        lesson_plan = [
            f"Class duration: {class_duration_minutes} minutes",
            "0-10 min: Warm-up and recap of previous class.",
            "10-25 min: Core concept explanation with examples.",
            "25-35 min: Student practice and group discussion.",
            "35-42 min: Quick formative assessment.",
            "42-45 min: Homework and next-topic briefing.",
        ]

        question_paper = "\n".join(
            [
                "Teacher Toolkit Question Paper",
                "",
                "Section A - Objective",
                *objective_questions,
                "",
                "Section B - Subjective",
                *subjective_questions,
            ]
        )
        answer_key = "\n".join(["Teacher Toolkit Answer Key", "", *answer_key_lines])
        rubric = "\n".join(["Marking Rubric", "", *rubric_lines])
        lesson_plan_text = "\n".join(["Lesson Plan", "", *lesson_plan])

        return {
            "question_paper": question_paper,
            "answer_key": answer_key,
            "rubric": rubric,
            "lesson_plan": lesson_plan_text,
        }
