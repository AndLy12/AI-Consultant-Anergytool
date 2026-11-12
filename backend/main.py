"""Совместимый импорт для run.py и IDE; реализация API находится в backend.app."""

from backend.app import create_app

__all__ = ["create_app"]
