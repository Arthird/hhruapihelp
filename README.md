# HH API Help Bot

Чат-бот для MAX Messenger, который отвечает разработчикам на вопросы об API HeadHunter
(HH.ru) по подготовленной документации. Ответы строятся через RAG: LlamaIndex находит
разделы документации, Yandex Assistant формулирует ответ, а MAX-транспорт доставляет его
пользователю.

Проект состоит из двух модулей:

- `max-bot/` — TypeScript-бот MAX, работает через **long polling**, нормализует update,
  вызывает внутренний API backend и отправляет ответ пользователю.
- `backend/` — Python/FastAPI: оркестратор диалогов, PostgreSQL/SQLAlchemy, RAG-индексация
  и интеграция с Yandex Assistant через `OpenAILike`.

## Возможности

- ответы только по найденным фрагментам документации HH.ru со ссылкой на раздел;
- сохранение пользователей, чатов, диалогов, сообщений и истории в PostgreSQL;
- идемпотентная обработка update: повторная доставка не вызывает модель и не отправляет
  ответ второй раз;
- хранение версий базы знаний: checksum, модель эмбеддингов, размерность, время активации;
- long polling MAX без webhook и белого IP;
- отправка длинных ответов частями до 4000 символов и многострочного кода отдельным файлом;
- команды `/start`, `/help`, `/clear`.

## Архитектура

```text
MAX API  ◄── long polling ──  max-bot (TypeScript)
                                  │ HTTP + Idempotency-Key
                                  ▼
                            backend (FastAPI)
                             ├── PostgreSQL + pgvector
                             ├── LlamaIndex + PGVectorStore
                             └── Yandex Assistant (OpenAILike)
```

Подробнее: `docs/architecture.md`, план этапов и критерии готовности: `PLAN.md`.

## Быстрый запуск

1. Скопируйте пример окружения и заполните секреты:

   ```bash
   cp .env.example .env
   ```

2. Соберите и запустите сервисы:

   ```bash
   docker compose up --build -d
   ```

3. Примените миграции (backend делает это автоматически при старте, но команда полезна для
   ручной проверки):

   ```bash
   docker compose exec backend alembic -c /app/backend/alembic.ini upgrade head
   ```

4. Проверьте модель и эмбеддинги (модель должна поддерживать tool calling):

   ```bash
   docker compose exec backend python -m backend.scripts.smoke_llm
   ```

5. Постройте индекс документации:

   ```bash
   docker compose exec backend python -m backend.scripts.sync_knowledge
   ```

6. Проверьте состояние:

   ```bash
   curl -s http://127.0.0.1:8000/health
   docker compose ps
   docker compose logs -f max-bot
   ```

`max-bot` при старте сам подключается к MAX через long polling. Сообщения из MAX начнут
обрабатываться после того, как backend станет healthy и будет построен индекс знаний.

## Переменные `.env`

Все секреты и настройки передаются через `.env` в корне проекта. Файл `.env` не коммитится;
в git хранится только `.env.example`. Python загружает значения через `load_env()` из
`backend/app/config.py`, TypeScript — через `dotenv` в `max-bot/src/config.ts`.

Выбранная модель `MODEL_NAME` **обязана поддерживать tool calling**: RAG-агент вызывает
инструмент `search_hh_docs`, и без этой возможности ответы будут неполными. Проверка
выполняется командой `python -m backend.scripts.smoke_llm`.

| Переменная | Обязательна | Назначение | Пример |
|---|---:|---|---|
| `MAX_BOT_API_KEY` | да | Токен бота MAX из бизнес-кабинета. | `1234567890:AA...` |
| `MAX_API_BASE_URL` | да | Базовый URL MAX Bot API. Разрешён только актуальный `platform-api2`. | `https://platform-api2.max.ru` |
| `MAX_ROOT_CA_PATH` | да | Путь к корневому CA Минцифры внутри контейнера `max-bot`. | `/app/certs/russian_trusted_root_ca.pem` |
| `BACKEND_BASE_URL` | да | Адрес backend для `max-bot`; в compose переопределяется на `http://backend:8000`. | `http://backend:8000` |
| `INTERNAL_API_TOKEN` | да | Длинный случайный токен для запросов `max-bot → backend`. | `change-me-long-random-value` |
| `BACKEND_TIMEOUT_SECONDS` | нет | Таймаут вызова backend, секунды. | `60` |
| `POSTGRES_DB` | да | Имя базы данных. | `hh_api_help` |
| `POSTGRES_USER` | да | Пользователь PostgreSQL. | `hh_api_help` |
| `POSTGRES_PASSWORD` | да | Пароль PostgreSQL. | `change-me` |
| `POSTGRES_HOST` | да | Хост PostgreSQL; в compose переопределяется на `db`. | `db` |
| `POSTGRES_PORT` | да | Порт PostgreSQL. | `5432` |
| `DATABASE_URL` | да* | Async SQLAlchemy URL. В compose собирается из `POSTGRES_*`. | `postgresql+asyncpg://hh_api_help:change-me@db:5432/hh_api_help` |
| `YANDEX_CLOUD_API_KEY` | да | API-ключ Yandex Cloud с доступом к Assistant и embeddings. | `AQVN...` |
| `YANDEX_CLOUD_BASE_URL` | да | OpenAI-совместимый endpoint Yandex Assistant. | `https://ai.api.cloud.yandex.net/v1` |
| `YANDEX_CLOUD_FOLDER` | да | Идентификатор каталога Yandex Cloud. | `b1g...` |
| `MODEL_NAME` | да | URI модели в формате Yandex, поддерживающей tool calling. | `gpt://b1g.../yandexgpt/latest` |
| `EMBEDDING_MODEL_NAME` | да | Модель эмбеддингов для документов. Для запросов автоматически используется `text-search-query`. | `text-search-doc` |
| `EMBEDDING_DIM` | да | Размерность эмбеддингов. Для `text-search-doc/query` — `256`. | `256` |
| `EMBEDDING_API_BASE` | нет | OpenAI-совместимый endpoint, если используется не Yandex-модель эмбеддингов. | `https://ai.api.cloud.yandex.net/v1` |
| `KNOWLEDGE_PATH` | нет | Путь к рабочей копии документации для индексации. | `backend/data/hh_api.md` |
| `KNOWLEDGE_SOURCE_PATH` | нет | Исходный подготовленный файл, который копируется перед индексацией. | `doc_preparing/output/openapi.md` |
| `PROMPT_PATH` | нет | Начальный системный промпт. | `prompts/initial.md` |
| `RAG_TOP_K` | нет | Сколько чанков передавать модели. | `5` |
| `RAG_CHUNK_SIZE` | нет | Максимальный размер чанка в символах. | `1200` |
| `RAG_CHUNK_OVERLAP` | нет | Перекрытие соседних чанков. | `150` |
| `PGVECTOR_ENABLED` | нет | Создавать ли таблицы pgvector автоматически. | `true` |
| `LOG_LEVEL` | нет | Уровень структурированных логов. | `INFO` |

\* `DATABASE_URL` можно не задавать, если заполнены `POSTGRES_*`; код соберёт URL сам.
Если `DATABASE_URL` задан, он имеет приоритет.

Не логируйте секреты и не передавайте их в аргументах команд. `docker compose config`
показывает раскрытые значения `.env`, поэтому не публикуйте его вывод.

## Индексация документации

Индексатор использует подготовленный файл `doc_preparing/output/openapi.md` и перед
индексацией копирует его в `backend/data/hh_api.md`. Это делает backend независимым от
скрипта подготовки документации.

```bash
docker compose exec backend python -m backend.scripts.sync_knowledge
```

Повторный запуск с тем же checksum и той же моделью эмбеддингов ничего не перестраивает.
Принудительная переиндексация создаёт новую версию даже для неизменённого файла:

```bash
docker compose exec backend python -m backend.scripts.sync_knowledge --force
```

Индексатор создаёт новую версию знаний, проверяет retrieval smoke-запросом, затем
переключает активную версию. Старая версия остаётся в БД со статусом `superseded`.

Первичная индексация 2547 чанков занимает несколько минут: embeddings вызываются
последовательно с ограничением около 8 запросов/с и retry на `RESOURCE_EXHAUSTED`.
Прогресс виден в логах контейнера; backend остаётся доступным и продолжает отвечать по
текущей активной версии, пока новая версия строится.

Retrieval двухступенчатый. Сначала агент выбирает точный эндпоинт из каталога разделов,
затем ищет top-k чанков только в выбранном разделе. Значения `section` и `endpoint`
включаются в embedding-текст чанка, поэтому поиск различает похожие методы вроде
`GET /vacancies` и `GET /vacancies/{vacancy_id}`.

## Внутренний API

`max-bot` вызывает backend с заголовками:

```http
POST /internal/v1/updates
Authorization: Bearer <INTERNAL_API_TOKEN>
Idempotency-Key: message_created:<timestamp>:<message_id>
```

Backend возвращает готовый ответ, но сам в MAX ничего не отправляет:

```json
{
  "reply": {
    "text": "Краткий ответ...",
    "format": "markdown",
    "attachments": []
  },
  "already_delivered": false
}
```

После успешной отправки транспорт подтверждает доставку:

```http
POST /internal/v1/updates/<url-encoded-key>/delivered
```

Повторный update с тем же ключом возвращает сохранённый ответ, включая `attachments`,
пока доставка не подтверждена, и `reply: null` после подтверждения. Модель повторно не
вызывается.

## Ограничения MAX и принятые меры

- **Long polling, не webhook.** У проекта нет белого IP и HTTPS-домена на порту 443,
  поэтому используется `bot.start({ mode: "polling" })`. Webhook-код отсутствует.
- **CA Минцифры.** `platform-api2.max.ru` не доверяется стандартным хранилищам Node.
  Корневой сертификат лежит в `max-bot/certs/russian_trusted_root_ca.pem` и подключается
  через `NODE_EXTRA_CA_CERTS`. Проверка TLS не отключается.
- **4000 символов.** Текст режется по code points, сначала по переводу строки, затем по
  пробелу. Пробелы между частями не теряются.
- **Нет markdown-таблиц и списков.** Промпт требует обычные абзацы; backend дополнительно
  разворачивает маркированные и нумерованные пункты в обычные строки.
- **Многострочный код.** MAX схлопывает переводы строк в блоках кода, поэтому backend
  выносит его в файл-вложение, а транспорт загружает файл в MAX.
- **Нет `GET /chats`.** Чаты сохраняются из update `bot_added`, `bot_started` и помечаются
  удалёнными при `bot_removed`, `dialog_removed`.
- **Callback-кнопки.** Ответ на callback отправляется через `POST /answers`; лимит — не более
  2 ответов в секунду на чат. В текущем MVP клавиатуры не создаются, обработчик готов к
  добавлению feedback-кнопок.

## Разработка и проверки

Backend:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend/app backend/scripts backend/tests
.venv/bin/pytest backend/tests
```

Интеграционные тесты требуют PostgreSQL с pgvector и переменную `TEST_DATABASE_URL`.
Тесты, которым нужна БД, пропускаются, если переменная не задана.

TypeScript:

```bash
cd max-bot
npm ci
npm test
npm run typecheck
npm run lint
npm run build
```

Проверка конфигурации Docker Compose:

```bash
docker compose config --quiet
```

## Структура репозитория

```text
backend/          FastAPI, SQLAlchemy, Alembic, RAG и скрипты
max-bot/          TypeScript-бот MAX и сертификат CA
prompts/          системный промпт
doc_preparing/    подготовка OpenAPI HH.ru в Markdown
docs/             архитектура
docker-compose.yml
requirements.txt
.env.example
```

## Политика хранения данных

В PostgreSQL сохраняются идентификаторы MAX, имена/username, тексты диалогов, источники
ответов, latency и ошибки обработки — в объёме, необходимом для контекста и диагностики.
Секреты, токены и полные служебные заголовки в БД не сохраняются. Для промышленной
эксплуатации нужно добавить задачу очистки истории по сроку хранения и удаление данных по
запросу пользователя.
