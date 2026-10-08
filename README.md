# Family Finance Tracker

Telegram-first трекер спільного сімейного бюджету. Бот працює через long polling,
приймає повідомлення і автоматично створює household та members.

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

Перед додаванням бота до фінансової групи:

1. Додайте Telegram chat ID групи до `ALLOWED_CHAT_IDS` (через кому для кількох груп).
2. У @BotFather виконайте `/setprivacy` і вимкніть Privacy Mode для бота, щоб на
   наступному етапі він міг отримувати звичайні повідомлення групи.
3. Застосуйте міграції командою `alembic upgrade head`.

Доступні команди: `/start`, `/help`, `/today`, `/month`, `/budget`, `/set_budget`,
`/set_category_budget`, `/last`, `/undo`, `/stats`,
`/categories`, `/category_add`, `/category_rename`, `/category_hide`, `/category_show`,
`/settings`, `/menu`. `/menu` відкриває BotFather-подібну inline-навігацію для останніх
операцій, статистики, категорій і налаштувань; текстові команди залишаються доступними.
Звичайні повідомлення дозволених користувачів розпізнаються через OpenAI Structured Outputs,
зберігаються як pending і підтверджуються або скасовуються inline-кнопками.
Якщо в тексті вказано учасника сім’ї, операція прив’язується до нього; інакше — до автора
Telegram-повідомлення. `/stats`, `/stats today` і `/stats YYYY-MM` показують підтверджені
доходи та витрати за категоріями. `/set_budget 2000` встановлює загальний бюджет поточного
місяця, `/set_category_budget groceries 600` — категорійний ліміт, а `/budget` показує
витрати, залишок, використаний відсоток і стан усіх категорійних лімітів.
Результат додатково перевіряється застосунком; AI не записує дані в базу й не виконує
фінансових розрахунків. За замовчуванням використовується `gpt-6-luna` з низьким рівнем
reasoning; модель можна змінити через `OPENAI_MODEL`.

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
# Створити або оновити чисту базу до актуальної схеми однією командою:
alembic upgrade head

# Команди для подальшої розробки міграцій:
alembic revision --autogenerate -m "опис зміни"
alembic downgrade -1
```

Міграція також створює стартові системні категорії витрат. Усі timestamps зберігаються
як PostgreSQL `TIMESTAMP WITH TIME ZONE`; застосунок встановлює UTC для кожного з'єднання.

Інтеграційні CRUD-тести потребують окремої порожньої/одноразової PostgreSQL-бази. Вони
застосовують міграції перед тестами та відкочують їх після завершення:

```bash
TEST_DATABASE_URL=postgresql+psycopg://finance:password@localhost:5432/finance_test \
  pytest -m integration
```

Без `TEST_DATABASE_URL` інтеграційні тести безпечно пропускаються.

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
