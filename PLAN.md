# План реализации HH API Help Bot

Документ описывает порядок работ, границы этапов и критерии готовности. Каждый этап
завершается отдельным git-коммитом. Пока проект не имеет первого коммита, поэтому этап 0
фиксирует базовую структуру и правила разработки, а последующие этапы не смешивают
инфраструктуру, БД, RAG и MAX-транспорт в одном изменении.

## 1. Целевой результат

Готовый проект должен:

1. Принимать сообщения из MAX через long polling без webhook и белого IP.
2. Сохранять пользователей, чаты, диалоги, сообщения и результаты обработки в PostgreSQL.
3. Индексировать подготовленную документацию HH.ru через LlamaIndex в PostgreSQL/pgvector.
4. Отвечать через Yandex Assistant (OpenAILike) только по найденному контексту.
5. Не терять контекст диалога после рестарта и не отвечать повторно на дубликат update.
6. Запускаться одной командой `docker compose up --build`.
7. Иметь README с полным описанием `.env`, включая требование tool calling к модели.
8. Не содержать в git секретов, `venv`, `node_modules`, кэшей и временных файлов.

## 2. Принятые технические решения

| Область | Решение | Причина |
|---|---|---|
| MAX | TypeScript, `@maxhub/max-bot-api` 0.3.1, Node >= 20.19, ESM | Официальный клиент, поддержка long polling и ESM |
| MAX transport | `bot.start({ mode: 'polling', options: {...} })` | Нет белого IP; webhooks требуют HTTPS на порту 443 |
| Backend | Python 3.12+, FastAPI, Pydantic | Явный HTTP-контракт, удобная валидация и тесты |
| ORM | SQLAlchemy 2.x + Alembic | Требование проекта; миграции и типизированные модели |
| БД | PostgreSQL 17 + pgvector | Операционные данные и векторный индекс в одном хранилище |
| RAG | LlamaIndex, `PGVectorStore`, Markdown-чанкинг по разделам | Требование проекта; ссылки на разделы документации |
| LLM | Yandex Cloud через `OpenAILike` | OpenAI-совместимый endpoint Yandex Assistant |
| Tool calling | LlamaIndex-агент с инструментом `search_hh_docs` | Модель должна уметь вызывать инструменты, а не имитировать их |
| Идемпотентность | `processed_updates.update_key` + ack доставки | Защита от повторных update и повторных ответов |
| Развёртывание | `docker compose`, отдельные Dockerfile для backend и max-bot | Изоляция Python и Node-зависимостей |

## 3. Текущее состояние

На момент составления плана в репозитории уже есть:

- `AGENTS.md` с правилами архитектуры и ограничениями;
- `prompts/initial.md` с начальным системным промптом;
- `doc_preparing/` со скриптом подготовки OpenAPI в Markdown;
- `doc_preparing/output/openapi.md` — подготовленный источник знаний;
- `.env` с локальными значениями (не должен попасть в git);
- пустые заготовки `requirements.txt`, `Dockerfile`, `docker-compose.yml`, `.gitignore`.

Ещё не созданы приложение, схема БД, индексатор, MAX-модуль, README и `.env.example`.

## 4. Этапы работ

### Этап 0. Гигиена репозитория и документация

**Цель:** зафиксировать структуру проекта и исключить попадание секретов и артефактов в git.

Задачи:

- [ ] Проверить актуальность `AGENTS.md`, `PLAN.md` и `docs/architecture.md`.
- [ ] Заполнить `.gitignore`: `.env`, `venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`,
  `node_modules/`, `dist/`, `coverage/`, логи, временные файлы IDE и ОС.
- [ ] Создать `.env.example` со всеми переменными без секретов.
- [ ] Добавить `.dockerignore` для backend и max-bot.
- [ ] Создать каркас README: назначение, схема компонентов, быстрый старт, ссылки на план.
- [ ] Удалить или переиспользовать пустые корневые `Dockerfile` и `docker-compose.yml`.
- [ ] Сделать первый git-коммит только с безопасными файлами.

Критерии готовности:

- `git status` не показывает `.env`, `venv/` и `node_modules/`;
- `.env.example` содержит имена всех переменных без значений секретов;
- README объясняет, что проект пока находится в разработке.

Коммит: `chore: bootstrap repository structure and project rules`

### Этап 1. Каркас Python-бэкенда и конфигурация

**Цель:** получить запускаемый backend без RAG и без бизнес-логики.

Задачи:

- [ ] Создать `backend/pyproject.toml` или `backend/requirements.txt` для локальной разработки;
- [ ] Заполнить корневой `requirements.txt` всеми Python-зависимостями;
- [ ] Создать `backend/app/main.py` с FastAPI и `GET /health`;
- [ ] Реализовать `backend/app/config.py`:
  - `load_env()` с загрузкой `.env`;
  - Pydantic Settings с обязательными и необязательными полями;
  - понятную ошибку при отсутствии секрета без вывода самого значения;
- [ ] Добавить структурное логирование и настройку уровня `LOG_LEVEL`;
- [ ] Добавить базовый `Dockerfile` backend;
- [ ] Написать тест конфигурации и healthcheck.

Критерии готовности:

- backend запускается локально и отвечает `{"status": "ok"}`;
- отсутствие обязательной переменной приводит к понятной ошибке;
- тест проверяет загрузку переменных через `load_env()`.

Коммит: `feat(backend): add FastAPI skeleton and environment configuration`

### Этап 2. Схема PostgreSQL и миграции

**Цель:** создать устойчивое хранилище состояния диалогов и идемпотентности.

Задачи:

- [ ] Поднять PostgreSQL с расширением `vector` в `docker-compose.yml`.
- [ ] Настроить SQLAlchemy 2.x async engine, `async_sessionmaker` и зависимости FastAPI.
- [ ] Настроить Alembic и первую миграцию.
- [ ] Создать таблицы:
  - `max_users`;
  - `max_chats`;
  - `conversations`;
  - `messages`;
  - `processed_updates`;
  - `llm_runs`;
  - `feedback`;
  - `knowledge_versions`.
- [ ] Добавить внешние ключи, уникальные индексы и partial unique index для одного
  активного диалога на пару `chat_id + user_id`.
- [ ] Реализовать репозитории для upsert пользователя/чата и создания/закрытия диалога.
- [ ] Добавить интеграционный тест миграций на чистой БД.

Критерии готовности:

- `alembic upgrade head` работает на пустой PostgreSQL;
- повторный upsert пользователя/чата не создаёт дубликаты;
- после рестарта backend данные и активный диалог сохраняются;
- `processed_updates` гарантирует уникальность `update_key`.

Коммит: `feat(db): add SQLAlchemy models and initial Alembic migration`

### Этап 3. Подготовка и индексация базы знаний

**Цель:** получить воспроизводимый RAG-индекс документации HH API.

Задачи:

- [ ] Скопировать `doc_preparing/output/openapi.md` в `backend/data/hh_api.md`.
- [ ] Добавить скрипт `backend/scripts/sync_knowledge.py`, который:
  - проверяет наличие источника;
  - считает SHA-256;
  - сравнивает с активной `knowledge_versions`;
  - копирует файл в `backend/data/`;
  - разбивает документ по заголовкам и сохраняет metadata;
  - строит векторный индекс в отдельной таблице/коллекции версии;
  - выполняет smoke-retrieval;
  - атомарно переключает активную версию;
  - помечает старую версию `superseded`.
- [ ] Настроить embeddings через OpenAI-совместимый endpoint Yandex.
- [ ] Зафиксировать `EMBEDDING_MODEL_NAME` и размерность вектора в конфигурации.
- [ ] Добавить тест чанкинга на небольшом fixture: заголовки, код, metadata, отсутствие
  разрезанного блока кода без необходимости.

Критерии готовности:

- повторный запуск с тем же файлом не переиндексирует корпус;
- изменённый файл создаёт новую версию и не ломает активный индекс до успешной проверки;
- запрос про конкретный эндпоинт возвращает чанк с корректным `section` и `source`;
- в БД есть ровно одна активная версия знания.

Коммит: `feat(rag): add versioned HH API indexing into pgvector`

### Этап 4. Интеграция Yandex Assistant и RAG-агент

**Цель:** научить backend отвечать на вопрос по документации с использованием tool calling.

Задачи:

- [ ] Создать `backend/app/llm/client.py` с `OpenAILike`:
  - `api_base` из `YANDEX_CLOUD_BASE_URL`;
  - ключ из `YANDEX_CLOUD_API_KEY`;
  - folder id из `YANDEX_CLOUD_FOLDER`;
  - модель из `MODEL_NAME`;
  - таймауты и ограничение retry.
- [ ] Проверить tool calling реальным smoke-запросом; при неподдерживаемой модели остановить
  этап и заменить модель.
- [ ] Реализовать инструмент `search_hh_docs(query, section=None, endpoint=None)` поверх
  активной версии `PGVectorStore`.
- [ ] Собрать системный промпт из `prompts/initial.md` и правил проекта.
- [ ] Собрать LlamaIndex-агента с одним инструментом поиска.
- [ ] Ограничить top-k и размер контекста; сохранять найденные источники.
- [ ] Добавить fallback-ответ при пустом retrieval и при ошибке модели.
- [ ] Написать тесты промпта, citations и fallback-сценариев с mock LLM.

Критерии готовности:

- на тестовый вопрос об эндпоинте HH API агент вызывает `search_hh_docs`;
- ответ содержит корректные параметры и ссылку на раздел документации;
- при отсутствии контекста бот не выдумывает ответ;
- ошибка Yandex API не приводит к необработанному исключению.

Коммит: `feat(llm): add Yandex OpenAILike client and tool-calling RAG agent`

### Этап 5. Оркестратор и внутренний API backend

**Цель:** связать БД, RAG и HTTP-контракт с MAX-транспортом.

Задачи:

- [ ] Реализовать `POST /internal/v1/updates` с проверкой `INTERNAL_API_TOKEN`.
- [ ] Валидировать update через Pydantic-схемы.
- [ ] В одной транзакции:
  - upsert пользователя и чата;
  - найти или создать активный диалог;
  - сохранить входящее сообщение;
  - вставить `processed_updates` с `update_key`.
- [ ] При повторном `update_key` возвращать сохранённый результат, не вызывая модель.
- [ ] Собирать контекст из последних N сообщений; для MVP использовать лимит по количеству
  и токенам, без обязательного summary.
- [ ] Вызывать RAG-агента и сохранять `llm_runs` и исходящее сообщение.
- [ ] Реализовать `POST /internal/v1/updates/{update_key}/delivered` для подтверждения
  отправки в MAX.
- [ ] Реализовать `POST /internal/v1/actions/clear` для `dialog_cleared` и команды очистки.
- [ ] Обрабатывать `bot_started`, `bot_added`, `bot_removed` и `dialog_removed` без вызова LLM.
- [ ] Добавить таймауты, retry только для временных ошибок и структурные логи без секретов.

Критерии готовности:

- повторный POST с тем же `Idempotency-Key` не создаёт второе сообщение и второй `llm_run`;
- после рестарта backend новый запрос видит предыдущие сообщения диалога;
- `dialog_cleared` закрывает старый диалог и создаёт новый при следующем сообщении;
- ack доставки меняет статус исходящего сообщения;
- API возвращает 4xx на неверный токен и 5xx только на внутренние сбои.

Коммит: `feat(orchestrator): add idempotent update processing and conversation API`

### Этап 6. MAX-бот на TypeScript

**Цель:** получить long-polling транспорт, который принимает события MAX и вызывает backend.

Задачи:

- [ ] Создать `max-bot/package.json` с ESM, Node >= 20.19 и `@maxhub/max-bot-api` 0.3.1.
- [ ] Добавить TypeScript strict, ESLint и тестовый runner.
- [ ] Загрузить конфигурацию через `dotenv`/`loadEnv()` и валидировать обязательные поля.
- [ ] Добавить корневой CA Минцифры в `max-bot/certs/` и проверить fingerprint.
- [ ] В Dockerfile установить CA в trust store и задать `NODE_EXTRA_CA_CERTS`.
- [ ] Реализовать `bot.start({ mode: 'polling', options: { allowedUpdates: [...] } })`.
- [ ] Подписаться на события:
  `message_created`, `message_callback`, `bot_started`, `bot_added`, `bot_removed`,
  `dialog_cleared`, `dialog_removed`, `bot_stopped`.
- [ ] Нормализовать update в контракт backend и строить `Idempotency-Key`.
- [ ] Реализовать HTTP-клиент backend с таймаутом и ограниченным retry.
- [ ] Игнорировать сообщения от ботов и пустые сообщения.
- [ ] Обрабатывать `bot.catch()`, `SIGTERM`, `stopPolling()`.
- [ ] Добавить минимальный HTTP `/healthz` для docker healthcheck, не являющийся webhook.
- [ ] Написать тесты нормализации, ключа идемпотентности и клиента backend.

Критерии готовности:

- бот подключается к MAX без отключения TLS;
- приходит `POST` в backend на тестовое сообщение;
- в коде нет webhook-подписок и `bot.start({ mode: 'webhook' })`;
- ошибка одного обработчика не останавливает polling;
- корректное завершение по `SIGTERM`.

Коммит: `feat(max-bot): add long-polling MAX transport and backend client`

### Этап 7. Доставка ответа, форматирование и команды

**Цель:** довести пользовательский сценарий до качественного ответа в MAX.

Задачи:

- [ ] Отправлять текстовый ответ частями не более 4000 символов, считая code points.
- [ ] Разбивать по абзацам, затем по предложениям/пробелам; не разрезать emoji и surrogate
  pairs.
- [ ] Извлекать многострочные примеры кода из ответа и отправлять каждый отдельным
  файлом-вложением с корректным расширением и MIME-типом; в тексте оставлять ссылку на файл
  и краткое пояснение.
- [ ] Не полагаться на fenced-блоки для многострочного кода: MAX схлопывает переводы строк
  внутри блоков, поэтому пример должен оставаться воспроизводимым в файле.
- [ ] Преобразовать markdown-списки и таблицы в допустимый для MAX текст.
- [ ] Экранировать пользовательский текст перед вставкой в markdown.
- [ ] Отправлять `typing` action на время обработки, если это не замедляет ответ.
- [ ] После успешной отправки вызывать ack доставки backend.
- [ ] Реализовать команды `/start`, `/help`, `/clear`.
- [ ] Добавить обработку callback-кнопок `clear` и `feedback`, если обратная связь включена.
- [ ] Добавить тесты разбиения: 4000 символов, кириллица, emoji, незакрытый code fence.

Критерии готовности:

- длинный ответ приходит несколькими сообщениями без потери текста;
- многострочный пример кода приходит отдельным файлом и открывается без искажений;
- кириллица и emoji не ломаются на границе;
- `/clear` закрывает контекст;
- повторный update не отправляет ответ второй раз;
- ответы не содержат markdown-таблиц и списков, которые MAX не отображает.

Коммит: `feat(max-bot): add safe message splitting and user commands`

### Этап 8. Docker Compose и документация `.env`

**Цель:** обеспечить воспроизводимый запуск всего проекта.

Задачи:

- [ ] Создать `docker-compose.yml` с сервисами `db`, `backend`, `max-bot`.
- [ ] Использовать образ PostgreSQL с pgvector и volume для данных.
- [ ] Добавить healthcheck для БД, backend и max-bot.
- [ ] Настроить `depends_on` с `condition: service_healthy`.
- [ ] Пробросить переменные из `.env` без дублирования секретов в compose.
- [ ] Описать в README:
  - назначение каждого поля `.env`;
  - пример заполнения;
  - требование, чтобы `MODEL_NAME` поддерживал tool calling;
  - команды запуска, миграций, индексации и тестов;
  - ограничения MAX: 4000 символов, отсутствие списков/таблиц, long polling, CA Минцифры.
- [ ] Добавить инструкцию по обновлению базы знаний.
- [ ] Проверить запуск на чистом клоне с `.env.example`.

Критерии готовности:

- `docker compose up --build` поднимает все сервисы;
- backend ждёт готовности PostgreSQL;
- max-bot ждёт готовности backend;
- после `docker compose down` и повторного запуска история диалогов сохраняется;
- README позволяет новому разработчику повторить запуск без чтения исходников.

Коммит: `feat(deploy): add docker compose setup and environment documentation`

### Этап 9. Тестирование, проверка ограничений и приёмка

**Цель:** доказать работоспособность и отсутствие нарушений требований.

Задачи:

- [ ] Unit-тесты Python: конфигурация, чанкинг, промпт, идемпотентность, репозитории,
  fallback-ответы.
- [ ] Integration-тесты: миграции, PostgreSQL, pgvector, API с mock LLM.
- [ ] Contract-тесты max-bot ↔ backend на фиксированных JSON-примерах.
- [ ] E2E-тест с реальным MAX-токеном:
  - `/start`;
  - вопрос про эндпоинт HH API;
  - ответ с источником;
  - `/clear` и новый диалог.
- [ ] Проверить повторный update: ответ не дублируется.
- [ ] Проверить рестарт max-bot и backend: контекст сохраняется.
- [ ] Проверить TLS: нет `NODE_TLS_REJECT_UNAUTHORIZED`, `verify=False`, `curl -k`.
- [ ] Проверить лимит 4000 символов и отсутствие неподдерживаемой разметки.
- [ ] Проверить, что webhook-код и секреты не попали в git.
- [ ] Зафиксировать результаты тестов в README или `docs/testing.md`.

Критерии готовности:

- все автоматические тесты проходят;
- E2E-сценарий подтверждён на реальном боте;
- duplicate/restart/TLS/formatting проверены и описаны;
- в `git status` нет лишних файлов.

Коммит: `test: add integration and end-to-end coverage for bot flow`

### Этап 10. Финальная документация и демонстрация

**Цель:** подготовить проект к сдаче.

Задачи:

- [ ] Обновить README актуальной архитектурой и скриншотами/примерами ответов.
- [ ] Добавить раздел troubleshooting: CA, Yandex API, пустой retrieval, лимиты MAX.
- [ ] Описать политику хранения сообщений и персональных данных.
- [ ] Проверить, что все этапы отражены в git-истории.
- [ ] Подготовить демонстрационный сценарий на 3–5 вопросов.
- [ ] Провести финальный аудит: `requirements.txt`, `.gitignore`, `.env.example`,
  docker compose, отсутствие webhook.

Критерии готовности:

- README полностью описывает запуск и ограничения;
- история git показывает последовательные этапы;
- проект воспроизводимо запускается с нуля;
- демонстрационный сценарий проходит без ручных правок БД.

Коммит: `docs: finalize project documentation and demo instructions`

## 5. Матрица проверок

| Проверка | Где проверяется | Обязательность |
|---|---|---|
| Загрузка `.env` через `load_env()` | unit + запуск backend | Да |
| Отсутствие секретов в git | `git status`, `.gitignore` | Да |
| Миграции на чистой БД | integration | Да |
| Идемпотентность update | unit + integration + E2E | Да |
| Сохранение контекста после рестарта | integration + E2E | Да |
| Ответ по документации с источником | integration + E2E | Да |
| Tool calling модели | smoke + integration | Да |
| Длинный ответ > 4000 символов | unit + E2E | Да |
| Многострочный код доставлен файлом без потери строк | unit + E2E | Да |
| Кириллица и emoji | unit | Да |
| Отсутствие webhook | code review + grep | Да |
| TLS без отключения проверки | E2E | Да |
| Docker Compose с нуля | manual | Да |

## 6. Риски и меры

| Риск | Мера |
|---|---|
| Модель Yandex не поддерживает tool calling | Проверить smoke-запросом на этапе 4 и заменить модель |
| Не задана embedding-модель | Добавить `EMBEDDING_MODEL_NAME` в `.env.example` и выбрать поддерживаемую модель до этапа 3 |
| Большой OpenAPI-файл даёт плохой retrieval | Чанкинг по заголовкам, metadata эндпоинтов, top-k, тестовые вопросы |
| MAX повторно доставляет update | `processed_updates` + ack доставки |
| max-bot отправляет ответ дважды | Backend возвращает `reply: null`, если доставка уже подтверждена |
| Ответ длиннее 4000 символов | Разбиение по code points с тестами |
| TLS-ошибка CA Минцифры | CA в образе и `NODE_EXTRA_CA_CERTS`, без отключения проверки |
| Утечка персональных данных в логах | Не логировать текст сообщений по умолчанию, маскировать токены |
| Нет первого коммита и история этапов потеряется | Этап 0 и последующие коммиты строго по одному этапу |

## 7. Открытые вопросы

1. Какая именно embedding-модель Yandex будет использоваться и какова её размерность?
2. Поддерживает ли выбранная `MODEL_NAME` tool calling в текущем OpenAI-совместимом endpoint?
3. Нужны ли кнопки обратной связи в MVP или достаточно текстового `/help` и `/clear`?
4. Сколько сообщений контекста хранить и через сколько дней удалять историю?
5. Нужен ли публичный доступ к backend или только внутренняя Docker-сеть?

Ответы на вопросы 1–2 нужно получить до этапа 3–4, иначе индексация и tool calling будут
заблокированы. Вопросы 3–5 можно уточнять параллельно с этапами 6–8.

## 8. Порядок коммитов

```text
chore: bootstrap repository structure and project rules
feat(backend): add FastAPI skeleton and environment configuration
feat(db): add SQLAlchemy models and initial Alembic migration
feat(rag): add versioned HH API indexing into pgvector
feat(llm): add Yandex OpenAILike client and tool-calling RAG agent
feat(orchestrator): add idempotent update processing and conversation API
feat(max-bot): add long-polling MAX transport and backend client
feat(max-bot): add safe message splitting and user commands
feat(deploy): add docker compose setup and environment documentation
test: add integration and end-to-end coverage for bot flow
docs: finalize project documentation and demo instructions
```

Каждый коммит должен быть самостоятельно собираемым: не добавлять код, который ссылается на
ещё не созданные модули, и не переносить миграции БД в коммиты с UI-логикой.
