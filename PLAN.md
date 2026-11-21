# План и статус реализации HH API Help Bot

Документ фиксирует целевое состояние проекта, принятые решения и фактический статус
этапов. Каждый завершённый этап соответствует отдельному коммиту в git. Исходные правила
разработки находятся в `AGENTS.md`, архитектурные детали — в `docs/architecture.md`.

## 1. Целевой результат

Бот MAX должен:

1. принимать сообщения через long polling без webhook и белого IP;
2. сохранять пользователей, чаты, диалоги, сообщения и результаты обработки в PostgreSQL;
3. индексировать подготовленную документацию HH.ru через LlamaIndex в PostgreSQL/pgvector;
4. отвечать через Yandex Assistant (`OpenAILike`) только по найденному контексту;
5. не терять контекст после рестарта и не отвечать повторно на дубликат update;
6. запускаться через `docker compose`;
7. иметь README с полным описанием `.env` и требованием tool calling к модели;
8. не содержать в git секретов, `venv`, `node_modules`, кэшей и временных файлов.

## 2. Итоговая архитектура

```text
MAX API  ◄── long polling ──  max-bot (TypeScript)
                                  │ HTTP + Idempotency-Key
                                  ▼
                            backend (FastAPI)
                             ├── PostgreSQL + pgvector
                             ├── LlamaIndex + PGVectorStore
                             └── Yandex Assistant (OpenAILike)
```

| Область | Решение |
|---|---|
| MAX | TypeScript, `@maxhub/max-bot-api` 0.3.1, Node >= 20.19, ESM |
| Транспорт MAX | `bot.start({ mode: "polling" })`, CA Минцифры, без отключения TLS |
| Backend | Python 3.12+, FastAPI, Pydantic |
| ORM и миграции | SQLAlchemy 2.x, Alembic |
| БД | PostgreSQL 17 + pgvector |
| RAG | LlamaIndex, `PGVectorStore`, Markdown-чанкинг по разделам и эндпоинтам |
| LLM | Yandex Cloud через `OpenAILike`, модель с tool calling |
| Инструмент RAG | `search_hh_docs` с endpoint-aware retrieval |
| Идемпотентность | `processed_updates.update_key` и подтверждение доставки |
| Развёртывание | `docker compose`, healthcheck и отдельные образы backend/max-bot |

Границы ответственности:

- `max-bot` знает токен MAX, принимает update, форматирует и отправляет ответ, но не
  работает с PostgreSQL и не вызывает LLM напрямую;
- `backend` владеет схемой БД, историей диалогов, RAG-индексом и вызовом модели;
- PostgreSQL хранит операционное состояние и векторный индекс, а исходная документация
  остаётся в репозитории.

## 3. Зачем проекту база данных

MAX не является хранилищем состояния бота. Метод `GET /chats` отсутствует, сессии SDK по
умолчанию живут в памяти, long polling может повторно доставить update, а обработчик может
упасть между получением сообщения и отправкой ответа. Поэтому PostgreSQL хранит:

| Таблица | Назначение |
|---|---|
| `max_users` | Пользователи MAX и время последней активности |
| `max_chats` | Диалоги, группы и каналы, где установлен бот |
| `conversations` | Активный контекст пары «чат + пользователь» |
| `messages` | Входящие и исходящие сообщения, статусы и ошибки |
| `processed_updates` | Идемпотентность update и сохранённый ответ для replay |
| `llm_runs` | Параметры вызова модели, источники, latency и ошибки |
| `feedback` | Оценки и комментарии пользователей |
| `knowledge_versions` | Версии индекса, checksum, модель и размерность эмбеддингов |

Векторные чанки хранятся через `pgvector` и LlamaIndex `PGVectorStore` в таблицах версий
индекса. Бизнес-схема управляется SQLAlchemy и Alembic; таблицы не создаются вручную в
коде приложения.

`processed_updates` хранит не только ключ, но и `reply_text`, `reply_format` и
`reply_attachments`. Это позволяет безопасно повторить ответ транспорту, не вызывая модель
заново, и не отправить сообщение дважды после подтверждения доставки.

## 4. Статус этапов

### Этап 0. Структура и правила проекта

Статус: выполнено.

- [x] Зафиксированы `AGENTS.md`, архитектура и план.
- [x] Настроены `.gitignore` и `.dockerignore`.
- [x] Создан `.env.example` без секретов.
- [x] Подготовлены `requirements.txt`, Dockerfile и каркас README.

Коммит: `b35f065 chore: bootstrap repository structure and project rules`

### Этап 1. Backend, конфигурация и healthcheck

Статус: выполнено.

- [x] Создан FastAPI-каркас и структурное логирование.
- [x] Реализована `load_env()` и валидация обязательных переменных.
- [x] Добавлены настройки PostgreSQL, Yandex, RAG и внутреннего API.
- [x] Добавлены unit-тесты конфигурации.

Коммит: `aa32d67 feat(backend): add FastAPI skeleton and environment configuration`

### Этап 2. Схема PostgreSQL и Alembic

Статус: выполнено.

- [x] Описаны SQLAlchemy-модели пользователей, чатов, диалогов и сообщений.
- [x] Добавлены идемпотентность, `llm_runs`, `feedback` и `knowledge_versions`.
- [x] Настроены Alembic и миграции `0001`–`0004`.
- [x] Добавлены partial unique indexes для активного диалога и активной версии знаний.
- [x] Добавлены репозитории сессий и операций с диалогами.

Коммит: `73a9e63 feat(db): add SQLAlchemy models and Alembic migrations`

### Этап 3. Индексация документации и retrieval

Статус: выполнено.

- [x] Подготовленный `doc_preparing/output/openapi.md` копируется в `backend/data/hh_api.md`.
- [x] Чанкинг идёт по заголовкам, metadata содержит `source`, `section`, `endpoint` и строки.
- [x] `knowledge_versions` хранит checksum, модель, размерность, число чанков и версию.
- [x] Новая версия индекса создаётся в отдельной таблице и активируется после smoke-retrieval.
- [x] Предыдущая активная версия помечается `superseded` только после успешной сборки.
- [x] Реализованы embeddings Yandex и `PGVectorStore`.

Коммит: `6d5e9b1 feat(rag): add versioned HH API indexing into pgvector`

### Этап 4. Yandex Assistant и RAG-агент

Статус: выполнено.

- [x] `OpenAILike` настроен на Yandex Cloud через переменные `.env`.
- [x] Агент вызывает инструмент `search_hh_docs`.
- [x] Endpoint-aware retrieval выбирает подходящий раздел и ищет top-k внутри него.
- [x] `initial_tool_choice` гарантирует вызов поиска до ответа модели.
- [x] Системные правила имеют приоритет над пользовательским вводом и стилевым промптом.
- [x] Пустой retrieval и ошибка модели превращаются в понятный ответ пользователю.
- [x] Smoke-проверка подтвердила tool calling и работу embeddings.

Коммит: `e91240d feat(llm): add Yandex OpenAILike client and tool-calling RAG agent`

### Этап 5. Оркестратор и внутренний API

Статус: выполнено.

- [x] `POST /internal/v1/updates` защищён `INTERNAL_API_TOKEN`.
- [x] Update валидируется через Pydantic и обрабатывается идемпотентно.
- [x] Сохраняются пользователь, чат, диалог, входящее и исходящее сообщения.
- [x] Повторный update возвращает сохранённый ответ без вызова модели.
- [x] Реализованы ack `/delivered` и очистка контекста `/actions/clear`.
- [x] События `bot_started`, `bot_added`, `bot_removed`, `dialog_cleared` и
  `dialog_removed` обрабатываются без лишнего вызова LLM.

Коммит: `cf368d2 feat(orchestrator): add idempotent update processing and conversation API`

### Этап 6. MAX-транспорт

Статус: выполнено.

- [x] Бот работает через long polling, webhook-код отсутствует.
- [x] CA Минцифры подключён через `NODE_EXTRA_CA_CERTS`; TLS не отключается.
- [x] Нормализуются все обязательные типы update и строится идемпотентный ключ.
- [x] Реализованы HTTP-клиент backend с таймаутом и ограниченным retry.
- [x] Обрабатываются `bot.catch()`, `SIGTERM` и `stopPolling()`.
- [x] Добавлены `/healthz`, тесты нормализации и клиента backend.

Коммит: `9695edc feat(max-bot): add long-polling MAX transport`

### Этап 7. Доставка, форматирование и команды

Статус: выполнено.

- [x] Ответы режутся по code points на части не более 4000 символов.
- [x] Учитываются абзацы, пробелы, кириллица и emoji.
- [x] Многострочный код извлекается в файл-вложение с корректным именем и MIME-типом.
- [x] Маркированные и нумерованные списки разворачиваются в допустимый для MAX текст.
- [x] Транспорт подтверждает доставку после успешной отправки.
- [x] Реализованы `/start`, `/help` и `/clear`.
- [x] Добавлены тесты форматирования и разбиения.

Коммит: `9695edc feat(max-bot): add long-polling MAX transport`

### Этап 8. Docker Compose и документация

Статус: выполнено.

- [x] `docker compose` запускает `db`, `backend` и `max-bot`.
- [x] Используется PostgreSQL 17 с pgvector и volume для данных.
- [x] Настроены healthcheck и `depends_on: service_healthy`.
- [x] Секреты передаются через `.env`, без дублирования в compose.
- [x] README описывает `.env`, запуск, индексацию, ограничения MAX и tool calling.
- [x] Миграции `0004` безопасны на чистой БД; проверен полный `upgrade head`.

Коммит: `a36f536 feat(deploy): add docker compose setup and environment documentation`

### Этап 9. Финальная стабилизация и документация

Статус: в работе.

- [x] Устранён риск падения `alembic upgrade head` на чистой БД.
- [x] Проверены миграции и pgvector на отдельной пустой базе.
- [x] Актуализирован план под фактическое состояние проекта.
- [ ] Добавить итоговые результаты проверок и демонстрационный сценарий в README.
- [ ] Проверить `git status --ignored`, health сервисов и отсутствие секретов.

Коммиты:

`fix(db): make index version migrations safe on fresh database`

`docs: finalize project documentation and demo instructions`

## 5. Проверки и критерии готовности

| Проверка | Статус | Как проверялось |
|---|---|---|
| Загрузка `.env` через `load_env()` | Пройдено | unit-тесты и запуск backend |
| Отсутствие секретов в git | Пройдено | `git status --ignored`, `.gitignore` |
| Миграции на чистой БД | Пройдено | `alembic upgrade head` на отдельной БД pgvector |
| Идемпотентность update | Пройдено | unit-тесты и replay через внутренний API |
| Сохранение контекста после рестарта | Пройдено | сообщения и диалоги в PostgreSQL |
| Ответ по документации | Пройдено | RAG smoke: `GET /vacancies`, источники в `llm_runs` |
| Tool calling модели | Пройдено | `backend.scripts.smoke_llm`: `tool_calling=ok` |
| Длинный ответ > 4000 символов | Пройдено | unit-тесты TypeScript |
| Код доставлен файлом без потери строк | Пройдено | форматирование и smoke-ответ с вложениями |
| Кириллица и emoji | Пройдено | unit-тесты TypeScript |
| Отсутствие webhook | Пройдено | review и поиск по коду |
| TLS без отключения проверки | Пройдено | CA в образе, `NODE_EXTRA_CA_CERTS` |
| Docker Compose | Пройдено | `docker compose ps`, health всех сервисов |

Команды повторной проверки:

```bash
TEST_DATABASE_URL='postgresql+asyncpg://...' .venv/bin/pytest backend/tests -q
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend/app backend/scripts backend/tests
cd max-bot && npm test && npm run typecheck && npm run lint && npm run build
docker compose ps
curl -s http://127.0.0.1:8000/health
docker compose exec backend alembic -c /app/backend/alembic.ini current
```

## 6. Ограничения и дальнейшие работы

- Модель `MODEL_NAME` должна поддерживать tool calling; при смене модели нужно повторить
  `python -m backend.scripts.smoke_llm`.
- В MVP нет клавиатур обратной связи и промышленной политики удаления истории. Таблица
  `feedback` и обработчик callback подготовлены для расширения.
- `downgrade` миграции `0004` нельзя применять к базе, где уже есть несколько версий одного
  checksum: старый уникальный constraint их запрещает. Для production откат нужно делать
  через новую миграцию, а не разрушать данные.
- Индекс нужно перестраивать после изменения `doc_preparing/output/openapi.md`
  командой `python -m backend.scripts.sync_knowledge`.
- Backend опубликован только на `127.0.0.1`; внешний доступ должен идти через защищённый
  reverse proxy и не должен обходить `INTERNAL_API_TOKEN`.
- Для промышленной эксплуатации нужны ротация логов, мониторинг ошибок модели и лимиты
  хранения персональных данных.

## 7. Порядок коммитов

```text
b35f065 chore: bootstrap repository structure and project rules
aa32d67 feat(backend): add FastAPI skeleton and environment configuration
73a9e63 feat(db): add SQLAlchemy models and Alembic migrations
6d5e9b1 feat(rag): add versioned HH API indexing into pgvector
e91240d feat(llm): add Yandex OpenAILike client and tool-calling RAG agent
cf368d2 feat(orchestrator): add idempotent update processing and conversation API
9695edc feat(max-bot): add long-polling MAX transport
5afe6d2 docs(agents): add MAX messenger integration skill
a36f536 feat(deploy): add docker compose setup and environment documentation
fix(db): make index version migrations safe on fresh database
docs: finalize project documentation and demo instructions
```

Каждый коммит должен оставаться собираемым и не смешивать независимые этапы.
