"""Совместимость нового backend с неизменённым async-протоколом Данька."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.legacy_core import AsyncConsultantAdapter
from core.contracts import Message
from core_contract import CoreRequest, CoreUnavailable, Turn


class AsyncCore:
    def __init__(self, status="answered"):
        self.calls, self.loops = [], []
        self.status = status

    async def answer(self, history):
        self.calls.append(history)
        self.loops.append(asyncio.get_running_loop())
        return {"answer": "Ответ тестового ядра", "status": self.status,
                "sources": [{"document_id":"d1", "chunk_id":"c1", "title":"Источник",
                             "quote":"Цитата", "section":"Раздел", "page":2}]}


class AsyncAdapterTest(unittest.TestCase):
    def adapter(self, core, **options):
        adapter = AsyncConsultantAdapter(core, **options)
        self.addCleanup(adapter.close)
        return adapter

    def test_history_sources_and_same_event_loop(self):
        core = AsyncCore()
        adapter = self.adapter(core)
        request = CoreRequest(message="Текущая реплика", history=[Turn(role="user",content="Ранее")])
        result = adapter.respond(request)
        adapter.respond(request)
        self.assertEqual(core.calls[0], (Message("user","Ранее"),Message("user","Текущая реплика")))
        self.assertIs(core.loops[0], core.loops[1])
        self.assertEqual(result.sources[0].excerpt, "Цитата")
        self.assertEqual(result.sources[0].page, 2)

    def test_status_mapping_and_rejection_of_stub(self):
        for status, action in [("answered","answer"),("clarification_needed","clarify"),
                               ("insufficient_data","clarify"),("out_of_scope","out_of_scope")]:
            self.assertEqual(self.adapter(AsyncCore(status)).respond(CoreRequest(message="Вопрос")).action,action)
        with self.assertRaises(CoreUnavailable):
            self.adapter(AsyncCore("stub")).respond(CoreRequest(message="Вопрос"))

    def test_async_timeout(self):
        class SlowCore:
            async def answer(self, history):
                await asyncio.sleep(10)
        with self.assertRaises(CoreUnavailable):
            self.adapter(SlowCore(),timeout=0.01).respond(CoreRequest(message="Вопрос"))

    def test_answered_without_sources_is_rejected(self):
        class BadCore:
            async def answer(self, history):
                return {"answer":"Необоснованный факт", "status":"answered", "sources":[]}
        with self.assertRaises(CoreUnavailable):
            self.adapter(BadCore()).respond(CoreRequest(message="Вопрос"))

    def test_http_backend_calls_async_core_and_saves_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = "sqlite:///" + str(Path(tmp)/"integration.db")
            with patch.dict(os.environ,{"DATABASE_URL":url}):
                command.upgrade(Config("alembic.ini"), "head")
            core = AsyncCore()
            with TestClient(create_app(url,self.adapter(core))) as client:
                registration = client.post("/api/auth/register",json={"email":"async@example.test","password":"Test-password-123"})
                client.headers["X-CSRF-Token"] = registration.json()["csrf_token"]
                dialog = client.post("/api/dialogs").json()["id"]
                for text in ["Первый вопрос","Второй вопрос"]:
                    response = client.post(f"/api/dialogs/{dialog}/messages",json={"request_id":str(uuid4()),"content":text})
                    self.assertEqual(response.status_code,200,response.text)
                history = client.get(f"/api/dialogs/{dialog}").json()["messages"]
                self.assertEqual(len(history),4)
                self.assertEqual(history[-1]["sources"][0]["section"],"Раздел")
                self.assertEqual([m.role for m in core.calls[-1]],["user","assistant","user"])
