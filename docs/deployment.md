# Production deployment одного Telegram worker

Ця схема призначена для Linux-сервера, де вже працюють інші Docker-застосунки. Бот
використовує Telegram long polling, тому **не слухає жодного вхідного порту**, не потребує
домену, HTTPS, Nginx/Traefik і не займає `80`, `443`, `5432` чи інший порт хоста.

## Що ізольовано

- Compose project має окрему назву `family-finance`;
- створюється лише внутрішня Docker-мережа `family-finance_default`;
- production Compose не публікує `ports` і не підключається до мереж іншого застосунку;
- worker обмежений одним CPU, 512 MiB RAM і 128 процесами;
- контейнер працює як UID `10001`, з read-only root filesystem і без Linux capabilities;
- локальна база з `docker-compose.yml` у production не запускається — використовується Neon;
- одночасно повинен працювати рівно один `bot` container. Не застосовуйте `--scale bot=2`.

## 1. Попередня перевірка сервера

Увійдіть як поточний адміністратор і зафіксуйте стан іншого застосунку:

```bash
sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
sudo ss -lntup
sudo docker network ls
sudo docker compose version
df -h
free -h
```

Не видаляйте чужі containers, volumes або networks і не запускайте глобальні команди
`docker compose down`, `docker system prune` чи `docker network prune`. Усі подальші Compose
команди виконуються з явними `--project-name` і `--file`.

Якщо Docker Compose v2 ще не встановлено, встановіть Docker Engine і Compose plugin за
офіційною інструкцією для дистрибутива. Увімкніть запуск Docker після reboot:

```bash
sudo systemctl enable --now docker
```

## 2. Окремий host-користувач і каталоги

```bash
sudo adduser --disabled-password --gecos '' financebot
sudo install -d -o financebot -g financebot -m 0750 /opt/family-finance
sudo install -d -o financebot -g financebot -m 0700 /var/backups/family-finance
```

Не додавайте `financebot` до групи `docker`: членство у ній практично дає root-доступ до
хоста. Репозиторієм володіє `financebot`, а Docker-команди запускає адміністратор через
`sudo`.

Скопіюйте код одним зі способів:

```bash
sudo -u financebot git clone <REPOSITORY_URL> /opt/family-finance/app
# або скопіюйте release-архів у /opt/family-finance/app і встановіть власника:
sudo chown -R financebot:financebot /opt/family-finance/app
```

Для приватного Git-репозиторію використовуйте read-only deploy key користувача
`financebot`, а не особистий SSH-ключ адміністратора.

## 3. Окремі Neon development і production

У Neon project створіть дві незалежні branch (наприклад, `development` і `production`) або
два окремі projects. Для найбільшої ізоляції та окремих квот краще два projects; для MVP
достатньо двох branches.

1. Не використовуйте production branch у локальних тестах.
2. Для production створіть окрему database/role, якщо це дозволяє обрана Neon-конфігурація.
3. Скопіюйте production connection string з Neon Connect.
4. Для worker і Alembic використовуйте **direct connection**, якщо вона доступна. Рядок має
   містити TLS, наприклад `sslmode=require`.
5. Development URL зберігайте лише в локальному `.env`; production URL — лише на сервері.

Застосунок приймає обидва формати:

```text
postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require
postgresql+psycopg://USER:PASSWORD@HOST/DATABASE?sslmode=require
```

Перед першою реальною міграцією до production корисно створити manual Neon branch від
production як точку швидкого відновлення. Це доповнює, але не замінює зовнішній dump.

## 4. Production secrets

Створіть файл поза Git-історією:

```bash
sudo -u financebot cp /opt/family-finance/app/.env.example \
  /opt/family-finance/.env.production
sudo chmod 0600 /opt/family-finance/.env.production
sudo -u financebot nano /opt/family-finance/.env.production
```

Мінімальний production-вміст:

```dotenv
TELEGRAM_BOT_TOKEN=...
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-6-luna
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/DATABASE?sslmode=require
ALLOWED_CHAT_IDS=-1001234567890
APP_ENV=production
LOG_LEVEL=INFO
TELEGRAM_TIMEOUT_SECONDS=20
OPENAI_TIMEOUT_SECONDS=20
OPENAI_MAX_RETRIES=2
DATABASE_CONNECT_TIMEOUT_SECONDS=10
MAX_MESSAGE_LENGTH=1000
AI_REQUESTS_PER_MINUTE=10
STORE_ORIGINAL_TEXT=true
WORKER_HEARTBEAT_FILE=/tmp/family-finance-worker.heartbeat
WORKER_HEARTBEAT_INTERVAL_SECONDS=10
```

Не задавайте `TEST_DATABASE_URL` у production. Перевірте права без друку вмісту:

```bash
sudo stat -c '%a %U:%G %n' /opt/family-finance/.env.production
```

Очікується `600 financebot:financebot`. Не передавайте секрети параметрами командного
рядка, у shell history, Git, issue або логах.

## 5. Firewall і порти

Нові inbound-правила не потрібні. Не додавайте секцію `ports:` до
`compose.production.yml`. Серверу потрібні тільки вихідні з'єднання та DNS:

- HTTPS `443/tcp` до Telegram API та OpenAI API;
- PostgreSQL TLS, звичайно `5432/tcp`, до Neon hostname;
- DNS (`53`) і коректний системний час.

Якщо outbound firewall уже обмежений, додайте лише ці виходи відповідно до політики
сервера. Не змінюйте існуючі Nginx/Traefik/Caddy, certificates або мережу іншого застосунку.

## 6. Перше розгортання

Задамо коротку функцію лише для читабельності команд цієї сесії:

```bash
cd /opt/family-finance/app
export APP_ENV_FILE=/opt/family-finance/.env.production
export COMPOSE_PROJECT_NAME=family-finance
sudo --preserve-env=APP_ENV_FILE,COMPOSE_PROJECT_NAME \
  docker compose -f compose.production.yml config --quiet
```

Перевірте, що у зведеній конфігурації немає опублікованих портів:

```bash
sudo --preserve-env=APP_ENV_FILE,COMPOSE_PROJECT_NAME \
  docker compose -f compose.production.yml config | grep -n 'ports:' || true
```

Порядок production startup:

```bash
# 1. Побудувати immutable application image локально на сервері.
sudo --preserve-env=APP_ENV_FILE,COMPOSE_PROJECT_NAME \
  docker compose -f compose.production.yml build

# 2. Застосувати Alembic migrations; при помилці наступні кроки не виконувати.
sudo --preserve-env=APP_ENV_FILE,COMPOSE_PROJECT_NAME \
  docker compose -f compose.production.yml run --rm migrate

# 3. Запустити рівно один worker.
sudo --preserve-env=APP_ENV_FILE,COMPOSE_PROJECT_NAME \
  docker compose -f compose.production.yml up -d --no-deps bot
```

Сам worker перед Telegram перевіряє `SELECT 1` у БД. Scheduler реєструється в startup
lifecycle polling і стартує лише після успішного запуску Telegram dispatcher. Якщо БД
недоступна, процес завершується, а `restart: unless-stopped` повторює запуск.

## 7. Перевірка після запуску

```bash
sudo docker compose --project-name family-finance \
  --file /opt/family-finance/app/compose.production.yml ps
sudo docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' \
  family-finance-bot-1
sudo docker compose --project-name family-finance \
  --file /opt/family-finance/app/compose.production.yml logs --tail=100 bot
```

Через 40–90 секунд стан має бути `running healthy`. У JSON-логах мають з'явитися
`database_connection_verified` і `report_scheduler_started`, без секретів. Надішліть у
дозволену Telegram-групу тестову операцію, підтвердьте її та перевірте `/last`.

Переконайтеся, що інший застосунок не змінився:

```bash
sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
sudo ss -lntup
```

## 8. Restart і graceful shutdown

Compose використовує `restart: unless-stopped`; Docker daemon автоматично відновить worker
після падіння або reboot. `init: true`, `SIGTERM` і `stop_grace_period: 30s` дають aiogram
час завершити polling, scheduler і DB pool.

Безпечний тест restart:

```bash
sudo docker restart family-finance-bot-1
sleep 10
sudo docker inspect --format '{{.RestartCount}} {{.State.Status}}' family-finance-bot-1
```

Не запускайте другу копію з тим самим Telegram token: long polling між двома workers
конфліктуватиме. DB constraints захищають транзакції та report slots від повторів, але
single-worker залишається обов'язковою production-топологією MVP.

## 9. Логи

Застосунок пише структуровані JSON-логи лише у stdout/stderr. Docker `local` logging driver
централізує їх для цього Compose project і обмежує диск до п'яти файлів приблизно по 10 MiB.

```bash
sudo docker compose --project-name family-finance \
  --file /opt/family-finance/app/compose.production.yml logs -f --tail=200 bot
```

Для кількох серверів підключіть на рівні Docker/host уже прийнятий у вас агент збору
логів (Loki/Promtail, Vector, Fluent Bit тощо). Не додавайте log shipper у цей Compose без
узгодження з існуючою observability-схемою сервера.

## 10. Backup і перевірка restore

Скрипт створює PostgreSQL custom-format dump і відразу перевіряє, що `pg_restore` читає
архів:

```bash
cd /opt/family-finance/app
sudo ./scripts/backup_neon.sh \
  /opt/family-finance/.env.production /var/backups/family-finance
```

Скрипт використовує офіційний `postgres:17-alpine` image і не відкриває порт. Перший запуск
завантажить image. Major-версія `pg_dump` не повинна бути нижчою за server major: перевірте Neon
командою `SHOW server_version;` і, якщо production уже на PostgreSQL 18, передайте третім
аргументом `postgres:18-alpine`. Зберігайте ще одну зашифровану копію поза цим сервером.
Retention для MVP:
7 щоденних, 4 тижневих і 6 місячних копій; автоматичне видалення додавайте лише після
перевірки off-site копії та restore rehearsal.

Приклад root cron для щоденного dump о 02:15 UTC:

```cron
15 2 * * * cd /opt/family-finance/app && ./scripts/backup_neon.sh /opt/family-finance/.env.production /var/backups/family-finance >>/var/log/family-finance-backup.log 2>&1
```

Не перевіряйте відновлення у production database. Створіть тимчасову Neon branch/database,
отримайте її URL і виконайте з адміністративної машини:

```bash
docker run --rm --env RESTORE_DATABASE_URL \
  --mount type=bind,src=/var/backups/family-finance,dst=/backup,readonly \
  postgres:17-alpine \
  sh -c 'pg_restore --clean --if-exists --no-owner --no-acl --dbname="$RESTORE_DATABASE_URL" /backup/finance-YYYYMMDDTHHMMSSZ.dump'
```

Тут `RESTORE_DATABASE_URL` має бути стандартним libpq URL `postgresql://...`, без
SQLAlchemy-суфікса `+psycopg`.

Після перевірки таблиць видаліть лише тимчасову restore branch через Neon console.

## 11. Оновлення і rollback

Для звичайного оновлення використовуйте deployment-скрипт. Він перевіряє чистий Git
working tree, виконує `pull --ff-only`, backup, Compose validation, build, зупинку worker,
міграції, запуск і очікує стану `healthy`:

```bash
/opt/family-finance/app/scripts/deploy_production.sh
```

За замовчуванням скрипт використовує:

```text
APP_ENV_FILE=/opt/family-finance/.env.production
COMPOSE_PROJECT_NAME=family-finance
DEPLOY_USER=financebot
BACKUP_DIR=/var/backups/family-finance
```

Шляхи можна змінити environment variables перед запуском. Скрипт запитує sudo-пароль
на початку. Якщо migration завершується помилкою, worker залишається зупиненим, щоб старий
код не працював із потенційно несумісною схемою.

Ручний еквівалент процесу:

```bash
sudo -u financebot git -C /opt/family-finance/app pull --ff-only
cd /opt/family-finance/app
export APP_ENV_FILE=/opt/family-finance/.env.production
export COMPOSE_PROJECT_NAME=family-finance
sudo --preserve-env=APP_ENV_FILE,COMPOSE_PROJECT_NAME docker compose -f compose.production.yml build
sudo --preserve-env=APP_ENV_FILE,COMPOSE_PROJECT_NAME docker compose -f compose.production.yml run --rm migrate
sudo --preserve-env=APP_ENV_FILE,COMPOSE_PROJECT_NAME docker compose -f compose.production.yml up -d --no-deps bot
```

Не робіть автоматичний Alembic downgrade під час rollback коду: спочатку перевірте, чи
міграція сумісна назад і чи не втрачає дані. Для аварійного відновлення використовуйте
перевірений dump/Neon restore branch та зафіксовану версію коду.

## 12. Короткий troubleshooting

- `migrate` failed: не запускайте bot; дивіться `docker compose ... logs migrate`, перевірте
  direct Neon URL, TLS і outbound `5432`.
- `bot` restarting: дивіться `docker inspect` і JSON-логи; найчастіше помилковий token,
  allowlist або DB URL.
- `unhealthy`, але process running: asyncio loop не оновлює heartbeat понад 45 секунд;
  збережіть логи й перезапустіть тільки цей container.
- Telegram `Conflict`: десь працює друга копія бота з тим самим token; знайдіть і зупиніть її.
- Закінчується диск: перевірте `docker system df` і backup retention. Не запускайте prune,
  доки не визначите власника кожного ресурсу.

Офіційні довідки: [Neon branches](https://neon.com/docs/manage/branches),
[Neon connection strings](https://neon.com/docs/connect/connect-from-any-app),
[Docker restart policies](https://docs.docker.com/engine/containers/start-containers-automatically/),
[Docker logging](https://docs.docker.com/engine/logging/configure/).
