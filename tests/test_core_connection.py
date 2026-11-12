"""Проверка фабрики и диагностики без API модели и доступа к базе знаний."""

import contextlib
import io
import json
import os
import types
import unittest
from importlib.machinery import ModuleSpec
from unittest.mock import patch

from backend.check_core import main
from backend.core import load_core
from core_contract import CoreResponse, CoreUnavailable


class ConnectionTest(unittest.TestCase):
    def invoke(self, arguments=()):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(arguments)
        return code, json.loads(output.getvalue())

    def plugin(self, factory):
        # Регистрируем тестовый модуль точно так же, как импортируется модуль Данька.
        module = types.ModuleType("test_consultant_plugin")
        module.__spec__ = ModuleSpec(module.__name__, loader=None)
        if factory is not None:
            module.create_core = factory
        return module

    def test_stub_is_explicit_and_validated(self):
        with patch.dict(os.environ, {"CORE_MODE": "stub"}):
            code, report = self.invoke(["--message", "Тестовый запрос"])
        self.assertEqual(code, 0)
        self.assertEqual(report["mode"], "stub")
        self.assertGreater(report["answer_length"], 0)

    def test_real_factory_is_loaded_once(self):
        calls = []
        class Core:
            def respond(self, request):
                return CoreResponse(answer="Тестовый ответ")
        def factory():
            calls.append(1)
            return Core()
        module = self.plugin(factory)
        with patch.dict(os.environ, {"CORE_MODE": "real", "CORE_MODULE": module.__name__}), patch.dict("sys.modules", {module.__name__: module}):
            core = load_core()
            self.assertEqual(core.respond(None).answer, "Тестовый ответ")
            self.assertEqual(core.respond(None).answer, "Тестовый ответ")
        self.assertEqual(len(calls), 1)

    def test_missing_module_is_not_ready(self):
        with patch.dict(os.environ, {"CORE_MODE": "real"}), patch("backend.core.importlib.util.find_spec", return_value=None):
            code, report = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(report["reason"], "core_module_missing")

    def test_incompatible_modules_are_rejected(self):
        for factory in (None, lambda: object()):
            module = self.plugin(factory)
            with self.subTest(factory=factory), patch.dict(os.environ, {"CORE_MODE":"real", "CORE_MODULE":module.__name__}), patch.dict("sys.modules", {module.__name__:module}):
                with self.assertRaises(CoreUnavailable):
                    load_core()

    def test_invalid_mode_does_not_silently_load_real_core(self):
        with patch.dict(os.environ, {"CORE_MODE": "stbu"}):
            self.assertEqual(self.invoke()[0], 2)

    def test_diagnostic_does_not_disclose_upstream_exception(self):
        def failing():
            raise ValueError("secret-token-must-not-leak")
        module = self.plugin(failing)
        with patch.dict(os.environ, {"CORE_MODE":"real", "CORE_MODULE":module.__name__}), patch.dict("sys.modules", {module.__name__:module}):
            code, report = self.invoke()
        self.assertEqual(code, 2)
        self.assertNotIn("secret-token", json.dumps(report))

    def test_invalid_result_is_rejected(self):
        class Core:
            def respond(self, request):
                return {"answer": " "}
        module = self.plugin(Core)
        with patch.dict(os.environ, {"CORE_MODE":"real", "CORE_MODULE":module.__name__}), patch.dict("sys.modules", {module.__name__:module}):
            self.assertEqual(self.invoke(["--message", "Тест"])[0], 2)
