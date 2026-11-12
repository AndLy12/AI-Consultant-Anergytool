"""Проверка пути от входного CSV до результата с подменой только сетевого вызова."""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import subprocess
import unittest
import urllib.error
from unittest.mock import patch

import solution
import test_acceptance


class ModelTransport:
    """Подменяем urlopen, сохраняя реальную сборку запроса и обработку ответа."""

    url = "https://model.invalid"

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def urlopen(self, request, timeout):
        self.requests.append({
            "path": request.selector,
            "authorization": request.get_header("Authorization"),
            "content_type": request.get_header("Content-type"),
            "body": json.loads(request.data),
        })
        index = min(len(self.requests) - 1, len(self.responses) - 1)
        status, payload, delay = self.responses[index]
        if delay > timeout:
            raise TimeoutError("Simulated model timeout")
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url, status, "Simulated failure", {}, io.BytesIO(b"")
            )
        encoded = payload if isinstance(payload, bytes) else json.dumps(
            payload, ensure_ascii=False
        ).encode("utf-8")
        return io.BytesIO(encoded)


def completion(answer):
    return {"choices": [{"message": {"content": answer}}]}


# Переиспользуем подготовку данных, не наследуя тесты: иначе они запустятся повторно.
class HttpIntegrationTest(unittest.TestCase):
    setUp = test_acceptance.CliContractTest.setUp
    write_questions = test_acceptance.CliContractTest.write_questions
    read_result = test_acceptance.CliContractTest.read_result

    def model(self, responses, retries=0, timeout=2):
        server = ModelTransport(responses)
        self.transport = server
        self.env.update({
            "LLM_BASE_URL": server.url, "LLM_MODEL": "test-model",
            "LLM_API_KEY": "test-only-placeholder", "LLM_MAX_RETRIES": str(retries),
            "LLM_TIMEOUT_SECONDS": str(timeout),
        })
        return server

    def invoke(self, *arguments, expected_code=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        handler = logging.StreamHandler(stderr)
        solution.LOGGER.addHandler(handler)
        try:
            with patch.dict(os.environ, self.env, clear=True), patch(
                "solution.urllib.request.urlopen", side_effect=self.transport.urlopen
            ), patch("solution.time.sleep"), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = solution.main(list(arguments))
        finally:
            solution.LOGGER.removeHandler(handler)
        self.assertEqual(code, expected_code, stdout.getvalue() + stderr.getvalue())
        return subprocess.CompletedProcess([], code, stdout.getvalue(), stderr.getvalue())

    def test_request_contains_history_context_model_and_authorization(self):
        self.write_questions(
            [("001", "Как настроить уведомления?", "Ранее выбрали объект Склад")],
            ("id", "question", "history"),
        )
        server = self.model([(200, completion("Готовый ответ"), 0)])
        result = self.invoke()
        self.assertEqual(self.read_result(["001"])[0]["answer"], "Готовый ответ")
        self.assertEqual(len(server.requests), 1)
        request = server.requests[0]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual(request["authorization"], "Bearer test-only-placeholder")
        self.assertEqual(request["content_type"], "application/json")
        self.assertEqual(request["body"]["model"], "test-model")
        messages = request["body"]["messages"]
        self.assertEqual(messages[0]["role"], "system")
        user_content = "\n".join(m["content"] for m in messages if m["role"] == "user")
        for expected in ("Как настроить уведомления?", "Склад", "карточке объекта", "knowledge.md"):
            self.assertIn(expected, user_content)
        self.assertNotIn("test-only-placeholder", result.stdout + result.stderr)

    def test_api_base_url_variants(self):
        server = self.model([(200, completion("Ответ"), 0)])
        for suffix in ("/v1", "/v1/", "/v1/chat/completions"):
            with self.subTest(suffix=suffix):
                self.env["LLM_BASE_URL"] = server.url + suffix
                self.invoke()
                self.assertEqual(server.requests[-1]["path"], "/v1/chat/completions")

    def test_retry_after_server_error_recovers(self):
        server = self.model([
            (503, {"error": "temporarily unavailable"}, 0),
            (200, completion("Ответ после повтора"), 0),
        ], retries=1)
        self.invoke()
        self.assertEqual(len(server.requests), 2)
        self.assertEqual(self.read_result(["001"])[0]["answer"], "Ответ после повтора")

    def test_retry_budget_is_bounded_and_fallback_is_non_empty(self):
        server = self.model([(503, {"error": "unavailable"}, 0)], retries=1)
        self.invoke()
        self.assertEqual(len(server.requests), 2)
        self.assertIn("карточке объекта", self.read_result(["001"])[0]["answer"])

    def test_timeout_produces_fallback(self):
        self.model([(200, completion("Слишком поздно"), 0.3)], timeout=0.05)
        self.invoke()
        self.assertIn("карточке объекта", self.read_result(["001"])[0]["answer"])

    def test_bad_api_responses_do_not_abort_batch(self):
        server = self.model([(200, completion("unused"), 0)])
        bad_responses = (
            (401, {"error": "unauthorized"}, 0),
            (429, {"error": "rate limited"}, 0),
            (200, b"not json", 0),
            (200, {}, 0),
            (200, completion("   "), 0),
            (200, {"choices": []}, 0),
            (200, {"choices": [{"message": {"content": None}}]}, 0),
            (200, {"choices": None}, 0),
            (200, completion(["unexpected", "list"]), 0),
            (200, [], 0),
        )
        for response in bad_responses:
            with self.subTest(response=response):
                server.responses = [response]
                self.invoke()
                self.assertIn("карточке объекта", self.read_result(["001"])[0]["answer"])

    def test_one_failed_request_does_not_lose_remaining_questions(self):
        self.write_questions([("001", "Уведомления"), ("002", "Уведомления")])
        server = self.model([
            (503, {"error": "unavailable"}, 0),
            (200, completion("Второй ответ"), 0),
        ])
        self.invoke()
        rows = self.read_result(["001", "002"])
        self.assertIn("карточке объекта", rows[0]["answer"])
        self.assertEqual(rows[1]["answer"], "Второй ответ")
        self.assertEqual(len(server.requests), 2)

    def test_answer_limit_and_csv_escaping(self):
        answer = 'Кириллица, "кавычки"\n' + "я" * 12_000
        self.model([(200, completion(answer), 0)])
        self.invoke()
        self.assertEqual(self.read_result(["001"])[0]["answer"], answer[:10_000])

    def test_no_context_or_empty_question_does_not_call_model(self):
        server = self.model([(200, completion("Не должен использоваться"), 0)])
        self.write_questions([("001", "Погода на Марсе"), ("002", "")])
        self.invoke()
        self.read_result(["001", "002"])
        self.assertEqual(server.requests, [])
