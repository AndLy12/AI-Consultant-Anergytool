#!/usr/bin/env python3
"""Stage-1 entry point for the Energytool AI consultant.

The evaluator can run this file without installing third-party packages:

    python solution.py

Paths and LLM settings are configured through environment variables; see
README.md.  The public interfaces are intentionally small so the retrieval and
prompt implementations can be replaced independently by roles 2 and 3.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


LOGGER = logging.getLogger("energytool")
QUESTION_COLUMNS = ("question", "query", "text", "message")
HISTORY_COLUMNS = ("history", "dialogue", "context")
SUPPORTED_KNOWLEDGE_SUFFIXES = {".txt", ".md", ".csv", ".json"}
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{2,}")


@dataclass(frozen=True)
class Question:
    """A normalized evaluator input row."""

    id: str
    text: str
    history: str = ""


@dataclass(frozen=True)
class SearchResult:
    """One retrieved knowledge fragment with traceable source metadata."""

    source: str
    text: str
    score: float


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    questions_path: Path
    output_path: Path
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    timeout_seconds: float = 45.0
    max_retries: int = 2
    top_k: int = 4

    @classmethod
    def from_environment(
        cls, questions: str | None = None, output: str | None = None
    ) -> "Settings":
        configured_data_dir = os.getenv("DATA_DIR", "").strip()
        data_dir = Path(configured_data_dir or ".").expanduser().resolve()
        questions_path = _resolve_input_path(data_dir, questions)
        # For an explicit local CSV, keep knowledge discovery inside its folder.
        # On the evaluation platform DATA_DIR remains the authoritative boundary.
        if questions and not configured_data_dir:
            data_dir = questions_path.parent
        output_path = Path(output or os.getenv("OUTPUT_PATH", "submission.csv"))
        if not output_path.is_absolute():
            output_path = Path.cwd() / output_path

        return cls(
            data_dir=data_dir,
            questions_path=questions_path,
            output_path=output_path.resolve(),
            llm_base_url=_first_env("LLM_BASE_URL", "OPENAI_BASE_URL").rstrip("/"),
            llm_api_key=_first_env("LLM_API_KEY", "OPENAI_API_KEY"),
            llm_model=_first_env("LLM_MODEL", "OPENAI_MODEL"),
            timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "45")),
            max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
            top_k=int(os.getenv("TOP_K", "4")),
        )


def _first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _resolve_input_path(data_dir: Path, explicit: str | None) -> Path:
    configured = explicit or os.getenv("QUESTIONS_PATH", "")
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = data_dir / candidate
        return candidate.resolve()

    for filename in ("questions.csv", "demo_questions.csv"):
        candidate = data_dir / filename
        if candidate.is_file():
            return candidate.resolve()
    return (data_dir / "questions.csv").resolve()


def load_questions(path: Path) -> list[Question]:
    """Load and normalize questions, preserving identifiers as strings."""

    if not path.is_file():
        raise FileNotFoundError(f"Questions file not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "id" not in reader.fieldnames:
            raise ValueError("questions.csv must contain an 'id' column")
        question_column = next(
            (column for column in QUESTION_COLUMNS if column in reader.fieldnames), None
        )
        if not question_column:
            options = ", ".join(QUESTION_COLUMNS)
            raise ValueError(f"questions.csv needs one text column: {options}")
        history_column = next(
            (column for column in HISTORY_COLUMNS if column in reader.fieldnames), None
        )

        rows: list[Question] = []
        seen_ids: set[str] = set()
        for line_number, row in enumerate(reader, start=2):
            question_id = (row.get("id") or "").strip()
            if not question_id:
                raise ValueError(f"Empty id at CSV line {line_number}")
            if question_id in seen_ids:
                raise ValueError(f"Duplicate id in questions.csv: {question_id}")
            seen_ids.add(question_id)
            rows.append(
                Question(
                    id=question_id,
                    text=(row.get(question_column) or "").strip(),
                    history=(row.get(history_column) or "").strip()
                    if history_column
                    else "",
                )
            )
    return rows


def _flatten_json(value: object) -> Iterable[str]:
    if isinstance(value, str):
        if value.strip():
            yield value.strip()
    elif isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and key.strip():
                yield key.strip()
            yield from _flatten_json(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _flatten_json(nested)
    elif value is not None:
        yield str(value)


def _read_knowledge_file(path: Path) -> str:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return "\n".join(" | ".join(row) for row in csv.reader(handle))
    if path.suffix.lower() == ".json":
        with path.open("r", encoding="utf-8-sig") as handle:
            return "\n".join(_flatten_json(json.load(handle)))
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _chunk_text(text: str, max_chars: int = 1800) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            chunks.append(paragraph)
            continue
        for start in range(0, len(paragraph), max_chars):
            chunks.append(paragraph[start : start + max_chars].strip())
    return chunks


def load_knowledge(data_dir: Path, questions_path: Path) -> list[SearchResult]:
    """Load supported knowledge files into independently searchable chunks."""

    excluded_names = {
        questions_path.name,
        "submission.csv",
        "demo_output.csv",
        "control_questions.csv",
    }
    chunks: list[SearchResult] = []
    if not data_dir.is_dir():
        return chunks

    for path in sorted(data_dir.rglob("*")):
        if (
            not path.is_file()
            or path.name in excluded_names
            or path.suffix.lower() not in SUPPORTED_KNOWLEDGE_SUFFIXES
            or ".git" in path.parts
        ):
            continue
        try:
            text = _read_knowledge_file(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            LOGGER.warning("Skipping unreadable knowledge file %s: %s", path, exc)
            continue
        source = str(path.relative_to(data_dir))
        chunks.extend(SearchResult(source, part, 0.0) for part in _chunk_text(text))
    return chunks


def _tokens(text: str) -> Counter[str]:
    return Counter(token.lower() for token in TOKEN_RE.findall(text))


def retrieve(
    question: str, knowledge: Sequence[SearchResult], top_k: int = 4
) -> list[SearchResult]:
    """Return lexical-overlap results. Role 2 can replace this implementation."""

    query_tokens = _tokens(question)
    if not query_tokens:
        return []

    ranked: list[SearchResult] = []
    for chunk in knowledge:
        document_tokens = _tokens(chunk.text)
        overlap = sum(
            min(count, document_tokens.get(token, 0))
            for token, count in query_tokens.items()
        )
        if overlap:
            score = overlap / max(sum(query_tokens.values()), 1)
            ranked.append(SearchResult(chunk.source, chunk.text, score))
    ranked.sort(key=lambda result: (-result.score, result.source))
    return ranked[: max(top_k, 0)]


def build_messages(
    question: Question, results: Sequence[SearchResult]
) -> list[dict[str, str]]:
    """Build the prompt boundary that role 3 can tune independently."""

    system_prompt = (
        "Ты консультант по продукту Energytool. Отвечай на русском языке, "
        "используя только предоставленную базу знаний. Не выдумывай факты. "
        "Если данных недостаточно, прямо скажи об этом и задай один конкретный "
        "уточняющий вопрос. Дай ясный практичный ответ не длиннее 150 слов."
    )
    context = "\n\n".join(
        f"[Источник: {result.source}]\n{result.text}" for result in results
    )
    user_parts = []
    if question.history:
        user_parts.append(f"История диалога:\n{question.history}")
    user_parts.append(f"Вопрос пользователя:\n{question.text}")
    user_parts.append(f"База знаний:\n{context or '[релевантные данные не найдены]'}")
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def _chat_completions_url(base_url: str) -> str:
    if base_url.endswith("/chat/completions"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    return f"{base_url}/v1/chat/completions"


def generate_answer(
    question: Question, results: Sequence[SearchResult], settings: Settings
) -> str:
    """Generate an answer with an OpenAI-compatible chat-completions endpoint."""

    if not question.text:
        return "Пожалуйста, сформулируйте вопрос, чтобы я мог помочь."
    if not results:
        return (
            "В базе знаний недостаточно данных для точного ответа. "
            "Уточните, пожалуйста, о какой функции или сценарии Energytool идёт речь?"
        )
    if not (settings.llm_base_url and settings.llm_model):
        excerpt = results[0].text.replace("\n", " ").strip()[:700]
        return f"По данным базы знаний: {excerpt}"

    payload = json.dumps(
        {
            "model": settings.llm_model,
            "messages": build_messages(question, results),
            "temperature": 0.1,
            "max_tokens": 500,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    last_error: Exception | None = None
    for attempt in range(settings.max_retries + 1):
        request = urllib.request.Request(
            _chat_completions_url(settings.llm_base_url),
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=settings.timeout_seconds
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
            answer = body["choices"][0]["message"]["content"].strip()
            if answer:
                return answer[:10_000]
            raise ValueError("LLM returned an empty answer")
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError) as exc:
            last_error = exc
            if attempt < settings.max_retries:
                time.sleep(2**attempt)

    LOGGER.error("LLM request failed after retries: %s", last_error)
    excerpt = results[0].text.replace("\n", " ").strip()[:700]
    return f"Не удалось получить ответ модели. По данным базы знаний: {excerpt}"


def write_submission(path: Path, rows: Sequence[tuple[str, str]]) -> None:
    """Write the exact evaluator schema with standards-compliant CSV escaping."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("id", "answer"))
        for question_id, answer in rows:
            normalized = answer.strip()[:10_000]
            if not normalized:
                normalized = "Не удалось сформировать ответ. Уточните, пожалуйста, вопрос."
            writer.writerow((question_id, normalized))


def run(settings: Settings) -> int:
    questions = load_questions(settings.questions_path)
    knowledge = load_knowledge(settings.data_dir, settings.questions_path)
    LOGGER.info(
        "Loaded %d questions and %d knowledge chunks", len(questions), len(knowledge)
    )
    answers: list[tuple[str, str]] = []
    for index, question in enumerate(questions, start=1):
        results = retrieve(question.text, knowledge, settings.top_k)
        answer = generate_answer(question, results, settings)
        answers.append((question.id, answer))
        LOGGER.info("Processed %d/%d (id=%s)", index, len(questions), question.id)
    write_submission(settings.output_path, answers)
    LOGGER.info("Wrote %s", settings.output_path)
    return len(answers)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Energytool stage-1 baseline")
    parser.add_argument("--questions", help="Input CSV path (default: DATA_DIR/questions.csv)")
    parser.add_argument("--output", help="Output CSV path (default: submission.csv)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        settings = Settings.from_environment(args.questions, args.output)
        run(settings)
    except (FileNotFoundError, ValueError, OSError) as exc:
        LOGGER.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

