"""Хеширование паролей и непрозрачных токенов серверных сессий."""

import hashlib
import secrets
from pwdlib import PasswordHash

passwords = PasswordHash.recommended()
# Проверка фиктивного хеша уменьшает разницу во времени входа для существующего
# и неизвестного email. Это дополнение к ограничению попыток, а не абсолютная защита.
DUMMY_HASH = passwords.hash(secrets.token_urlsafe(32))


def digest(token):
    """Хеш случайного токена для поиска в БД; для паролей используется Argon2, не SHA-256."""
    return hashlib.sha256(token.encode()).hexdigest()
