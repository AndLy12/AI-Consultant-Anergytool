"""Локальные административные команды; публичная регистрация не выдаёт роль сотрудника."""
import argparse
from sqlalchemy import select, update
from backend.db import make_engine, users


def main():
    """Назначаем роль существующему аккаунту или восстанавливаем зависшие запросы."""
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["grant-staff", "recover-processing"])
    parser.add_argument("--email")
    args = parser.parse_args()
    engine = make_engine()
    with engine.begin() as connection:
        if args.command == "grant-staff":
            # Назначение доступно оператору через CLI, а не через пользовательский API.
            email = (args.email or "").strip().lower()
            row = connection.execute(select(users.c.id).where(users.c.email == email)).first()
            if not row:
                raise SystemExit("Register the staff account first")
            connection.execute(update(users).where(users.c.id == row.id).values(is_staff=True))
            print("Staff role granted")
        else:
            # Ручное восстановление запускают только при остановленном backend:
            # иначе можно ошибочно пометить реально выполняющуюся генерацию как сбой.
            from backend.db import messages
            count = connection.execute(update(messages).where(messages.c.status == "processing").values(status="failed")).rowcount
            print(f"Recovered {count} interrupted requests. Run only while backend is stopped.")
    engine.dispose()


if __name__ == "__main__":
    main()
