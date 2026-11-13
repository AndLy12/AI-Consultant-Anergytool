"""Локальные административные команды; публичная регистрация не выдаёт роль сотрудника."""
import argparse
from sqlalchemy import select, update, delete
from backend.db import make_engine, users, sessions, now


def main(argv=None):
    """Меняем роль, удаляем истёкшие сессии или восстанавливаем прерванные запросы."""
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["grant-staff", "revoke-staff", "purge-expired-sessions", "recover-processing"])
    parser.add_argument("--email")
    args = parser.parse_args(argv)
    engine = make_engine()
    try:
        with engine.begin() as connection:
            if args.command in {"grant-staff", "revoke-staff"}:
                # Права проверяются по users при каждом запросе: отзыв действует
                # и для уже открытых сессий, без удаления истории и аккаунта.
                email = (args.email or "").strip().lower()
                row = connection.execute(select(users.c.id).where(users.c.email == email)).first()
                if not row:
                    raise SystemExit("Register the account first")
                enabled = args.command == "grant-staff"
                connection.execute(update(users).where(users.c.id == row.id).values(is_staff=enabled))
                print("Staff role granted" if enabled else "Staff role revoked")
            elif args.command == "purge-expired-sessions":
                # Удаляем только уже недействующие токены. Действующие входы,
                # аккаунты, диалоги и заявки остаются на месте; операция повторяема.
                count = connection.execute(delete(sessions).where(sessions.c.expires_at <= now())).rowcount
                print(f"Removed {count} expired sessions")
            else:
                # Ручное восстановление запускают только при остановленном backend:
                # иначе можно ошибочно пометить реально выполняющуюся генерацию как сбой.
                from backend.db import messages
                count = connection.execute(update(messages).where(messages.c.status == "processing").values(status="failed")).rowcount
                print(f"Recovered {count} interrupted requests. Run only while backend is stopped.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
