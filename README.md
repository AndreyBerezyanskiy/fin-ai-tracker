# Family Finance Tracker

Telegram-first трекер спільного сімейного бюджету. На цьому етапі репозиторій містить
базовий Python-проєкт, конфігурацію, порожній Telegram-бот і каркас міграцій.

## Вимоги

- Python 3.12+
- PostgreSQL (для локальної розробки можна використати Docker Compose)

## Локальний запуск

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp .env.example .env
```

Заповніть `.env` реальними секретами. Для Neon рекомендований SQLAlchemy URL формату
`postgresql+psycopg://user:password@host/database?sslmode=require`.

Запуск бота через Telegram long polling:

```bash
family-finance-bot
# або
python -m main
```

Поки що бот не має команд чи обробників, але після запуску приймає Telegram updates.

## Перевірки

```bash
pytest
ruff check .
ruff format --check .
```

Автоматичне форматування:

```bash
ruff format .
```

## Міграції

```bash
alembic revision --autogenerate -m "опис зміни"
alembic upgrade head
alembic downgrade -1
```

## Docker

Після створення `.env` бот використовує базу з `DATABASE_URL`. Для Neon локальний
PostgreSQL не запускається:

```bash
docker compose up --build bot
```

Опціональний PostgreSQL-контейнер призначений лише для локальної розробки та
інтеграційних тестів. Щоб запустити його разом із ботом, вкажіть у `.env`
`DATABASE_URL=postgresql+psycopg://finance:local-development-only@db:5432/finance` і
активуйте профіль:

```bash
docker compose --profile local-db up --build
```

## Структура

```text
src/
  bot/                 # Telegram handlers, keyboards, middlewares
  application/         # прикладні сервіси та сценарії
  domain/              # доменні моделі та enum-и
  infrastructure/      # PostgreSQL, OpenAI, repositories
  config.py            # типізована конфігурація середовища
  main.py              # точка входу long polling
tests/
migrations/
```

Секрети читаються лише зі змінних середовища або локального `.env`, який виключено з Git.
