# Архитектура HH API Help Bot

Документ фиксирует целевую архитектуру, модель данных и ключевые технические решения. Он
дополняет `AGENTS.md` и `PLAN.md`; при изменении контрактов или схемы БД его нужно обновлять
в том же коммите.

## 1. Обзор системы

```text
┌──────────────┐       long polling        ┌────────────────────┐
│   MAX API    │ ◄──────────────────────── │      max-bot       │
│platform-api2 │                           │ TypeScript / Node  │
└──────────────┘                           └─────────┬──────────┘
                                                     │ HTTP JSON
                                                     │ internal API
                                           ┌─────────▼──────────┐
                                           │      backend       │
                                           │ Python / FastAPI   │
                                           │ orchestrator + RAG │
                                           └──────┬───────┬─────┘
                                                  │       │
                                     SQLAlchemy   │       │ OpenAILike
                                                  ▼       ▼
                                          ┌──────────┐ ┌──────────────┐
                                          │PostgreSQL│ │ Yandex Cloud │
                                          │+pgvector │ │ Assistant    │
                                          └──────────┘ └──────────────┘
```

`max-bot` — транспортный адаптер. Он не хранит бизнес-состояние и не вызывает LLM.
`backend` — единственный владелец состояния, RAG и LLM. PostgreSQL является общей точкой
истины для диалогов и индекса. Это позволяет перезапускать любой компонент без потери
контекста и тестировать backend независимо от MAX.

## 2. Потоки

### 2.1. Обычное сообщение

1. MAX доставляет `message_created` через `GET /updates`.
2. `max-bot` проверяет, что сообщение не от бота и содержит текст.
3. `max-bot` строит `Idempotency-Key` и отправляет нормализованный update в
   `POST /internal/v1/updates`.
4. Backend в одной транзакции делает upsert пользователя и чата, находит активный
   `conversation`, сохраняет входящее `message` и резервирует `processed_updates`.
5. Если ключ уже обработан, backend возвращает сохранённые текст, формат и вложения ответа
   и не вызывает LLM.
6. Orchestrator собирает последние сообщения диалога, вызывает RAG-агента и сохраняет
   `llm_run` с найденными источниками.
7. Backend сохраняет исходящее `message` со статусом `pending` и возвращает `reply`.
8. `max-bot` отправляет текст ответа в MAX, разбивая его на части до 4000 символов.
9. После успешной отправки всех частей `max-bot` вызывает
   `POST /internal/v1/updates/{update_key}/delivered`.
10. Backend помечает update и сообщения как доставленные.

Многострочные примеры кода не отправляются fenced-блоком: `max-bot` извлекает их из ответа,
загружает через `POST /uploads?type=file` и прикрепляет файлом. В тексте остаётся краткое
описание примера и имя файла. Это нужно потому, что MAX схлопывает переводы строк внутри
кодовых блоков.

Если шаг 8 завершился ошибкой, `max-bot` логирует её и повторяет ограниченное число раз.
Backend при повторном запросе с тем же ключом возвращает сохранённый ответ, поэтому модель
не вызывается повторно. Если ответ уже был доставлен, backend возвращает `reply: null`.

### 2.2. Жизненный цикл чата

- `bot_started` — upsert пользователя и чата, создать активный диалог, отправить приветствие.
- `bot_added` — upsert чата и пользователя-инициатора, создать диалог.
- `bot_removed` — пометить чат `removed`, закрыть активный диалог.
- `dialog_cleared` — закрыть текущий диалог с причиной `cleared`, следующий запрос создаст
  новый.
- `dialog_removed` — закрыть диалог и пометить его `removed`.
- `bot_stopped` — пометить, что пользователь остановил бота; новые сообщения обрабатывать
  только после `bot_started`.

Список чатов не запрашивается через MAX: источниками являются эти события. Если событие
потеряно, чат будет восстановлен при следующем `message_created` или `bot_started`.

### 2.3. Callback-кнопки

`message_callback` нормализуется в update с `callback_id` и `payload`. Backend обрабатывает
действие:

- `clear` — закрывает диалог;
- `feedback:positive` / `feedback:negative` — сохраняет оценку в `feedback`;
- `help` — возвращает справку без вызова LLM.

Ответ на callback отправляется через `POST /answers`; частота не должна превышать 2 ответа/с
на чат. Если callback устарел или сообщение удалено, ошибка логируется и не роняет polling.

### 2.4. Обновление базы знаний

1. `doc_preparing/output/openapi.md` пересобирается скриптом подготовки.
2. `sync_knowledge.py` считает SHA-256 и сравнивает с активной `knowledge_versions`.
3. При изменении файл копируется в `backend/data/hh_api.md`.
4. Документ разбивается на чанки по разделам и эндпоинтам; `section` и `endpoint`
   включаются в embedding-текст, чтобы различать похожие методы.
5. LlamaIndex строит векторы в новой версии индекса.
6. Выполняется smoke-retrieval на наборе контрольных вопросов.
7. Новая версия становится активной, старая помечается `superseded`.

`--force` создаёт новую версию даже при неизменном SHA-256. Поэтому уникальность
`knowledge_versions` ограничена только одним активным индексом, а не парой
`source_sha256 + embedding_model`.

Индексация не должна происходить при старте API: это отдельная команда, чтобы не замедлять
запуск и не блокировать обработку сообщений.

## 3. Модель данных

### 3.1. `max_users`

| Поле | Тип | Ограничения | Назначение |
|---|---|---|---|
| `id` | bigint PK | identity | Внутренний id |
| `max_user_id` | bigint | unique, not null | `user_id` из MAX |
| `first_name` | varchar(255) | nullable | Имя |
| `last_name` | varchar(255) | nullable | Фамилия |
| `username` | varchar(255) | nullable | Username MAX |
| `is_bot` | boolean | not null default false | Признак бота |
| `locale` | varchar(16) | nullable | Локаль пользователя |
| `first_seen_at` | timestamptz | not null | Первое событие |
| `last_seen_at` | timestamptz | not null | Последняя активность |

Индекс: `unique(max_user_id)`.

### 3.2. `max_chats`

| Поле | Тип | Ограничения | Назначение |
|---|---|---|---|
| `id` | bigint PK | identity | Внутренний id |
| `max_chat_id` | bigint | unique, not null | `chat_id` из MAX |
| `chat_type` | varchar(16) | not null | `dialog`, `chat`, `channel` |
| `title` | varchar(200) | nullable | Название |
| `status` | varchar(16) | not null default `active` | `active`, `removed`, `stopped` |
| `added_by_user_id` | bigint FK → `max_users.id` | nullable | Кто добавил бота |
| `first_seen_at` | timestamptz | not null | Первое событие |
| `last_seen_at` | timestamptz | not null | Последняя активность |
| `removed_at` | timestamptz | nullable | Когда бот удалён |

Индекс: `unique(max_chat_id)`, индекс по `status`.

### 3.3. `conversations`

Один активный диалог на пару «чат + пользователь». Групповой чат может иметь несколько
независимых диалогов, если бот отвечает конкретным пользователям.

| Поле | Тип | Ограничения | Назначение |
|---|---|---|---|
| `id` | bigint PK | identity | Внутренний id |
| `chat_id` | bigint FK → `max_chats.id` | not null | Чат |
| `user_id` | bigint FK → `max_users.id` | not null | Пользователь |
| `status` | varchar(16) | not null default `active` | `active`, `closed`, `removed` |
| `close_reason` | varchar(32) | nullable | `cleared`, `removed`, `command` |
| `summary` | text | nullable | Краткое резюме для длинных диалогов |
| `started_at` | timestamptz | not null | Начало |
| `last_activity_at` | timestamptz | not null | Последняя активность |
| `closed_at` | timestamptz | nullable | Закрытие |

Ограничения:

- `unique(chat_id, user_id) where status = 'active'` — partial unique index;
- индекс по `last_activity_at`.

### 3.4. `messages`

| Поле | Тип | Ограничения | Назначение |
|---|---|---|---|
| `id` | bigint PK | identity | Внутренний id |
| `conversation_id` | bigint FK → `conversations.id` | not null | Диалог |
| `chat_id` | bigint FK → `max_chats.id` | not null | Чат для быстрых выборок |
| `user_id` | bigint FK → `max_users.id` | nullable | Автор (null для бота) |
| `direction` | varchar(8) | not null | `inbound`, `outbound` |
| `max_message_id` | varchar(128) | nullable | `mid` из MAX |
| `update_key` | varchar(255) | nullable | Связь с обработанным update |
| `text` | text | not null | Текст без усечения, если политика хранения разрешает |
| `text_length` | integer | not null | Длина в code points |
| `status` | varchar(16) | not null | `received`, `pending`, `sent`, `delivered`, `failed` |
| `error_code` | varchar(64) | nullable | Код ошибки доставки |
| `created_at` | timestamptz | not null | Создание |
| `sent_at` | timestamptz | nullable | Отправка в MAX |
| `delivered_at` | timestamptz | nullable | Подтверждение доставки |

Ограничения:

- индекс `(conversation_id, created_at)`;
- уникальный индекс `(conversation_id, max_message_id)` при `max_message_id is not null`,
  чтобы не сохранять одно и то же сообщение дважды.

### 3.5. `processed_updates`

| Поле | Тип | Ограничения | Назначение |
|---|---|---|---|
| `id` | bigint PK | identity | Внутренний id |
| `update_key` | varchar(255) | unique, not null | Идемпотентный ключ |
| `update_type` | varchar(32) | not null | Тип события MAX |
| `update_timestamp` | bigint | not null | Время события |
| `conversation_id` | bigint FK | nullable | Диалог |
| `inbound_message_id` | bigint FK → `messages.id` | nullable | Входящее сообщение |
| `outbound_message_id` | bigint FK → `messages.id` | nullable | Ответ |
| `status` | varchar(16) | not null | `processing`, `replied`, `delivered`, `failed`, `ignored` |
| `reply_text` | text | nullable | Кэш ответа для повторного запроса |
| `reply_format` | varchar(16) | nullable | Формат кэшированного ответа |
| `reply_attachments` | jsonb | not null default `[]` | Файлы-вложения, которые нужно повторить при replay |
| `error_code` | varchar(64) | nullable | Ошибка обработки |
| `created_at` | timestamptz | not null | Получение |
| `completed_at` | timestamptz | nullable | Завершение |

`update_key` — основной механизм защиты от дублей. Вставка выполняется с обработкой
конфликта: если ключ уже есть, backend возвращает сохранённый результат.

### 3.6. `llm_runs`

| Поле | Тип | Ограничения | Назначение |
|---|---|---|---|
| `id` | bigint PK | identity | Внутренний id |
| `conversation_id` | bigint FK | not null | Диалог |
| `update_key` | varchar(255) | not null | Связь с запросом |
| `model_name` | varchar(255) | not null | Модель |
| `prompt_version` | varchar(64) | not null | Версия промпта |
| `knowledge_version_id` | bigint FK | nullable | Версия знаний |
| `retrieved_chunks` | jsonb | not null default `[]` | Источники и scores |
| `request_tokens` | integer | nullable | Токены запроса |
| `response_tokens` | integer | nullable | Токены ответа |
| `latency_ms` | integer | nullable | Задержка |
| `status` | varchar(16) | not null | `success`, `error` |
| `error_code` | varchar(64) | nullable | Код ошибки |
| `created_at` | timestamptz | not null | Время |

Индекс: `(conversation_id, created_at)`, `update_key`.

### 3.7. `feedback`

| Поле | Тип | Ограничения | Назначение |
|---|---|---|---|
| `id` | bigint PK | identity | Внутренний id |
| `conversation_id` | bigint FK | not null | Диалог |
| `message_id` | bigint FK → `messages.id` | nullable | Оценённое сообщение |
| `user_id` | bigint FK | not null | Пользователь |
| `rating` | smallint | nullable | `1`/`-1` или `1..5` |
| `comment` | text | nullable | Комментарий |
| `created_at` | timestamptz | not null | Время |

### 3.8. `knowledge_versions`

| Поле | Тип | Ограничения | Назначение |
|---|---|---|---|
| `id` | bigint PK | identity | Внутренний id |
| `source_path` | varchar(512) | not null | Исходный файл |
| `source_sha256` | varchar(64) | not null | Checksum |
| `embedding_model` | varchar(255) | not null | Модель эмбеддингов |
| `embedding_dim` | integer | not null | Размерность |
| `chunk_count` | integer | not null | Количество чанков |
| `table_name` | varchar(128) | not null | Таблица/коллекция pgvector |
| `status` | varchar(16) | not null | `building`, `active`, `superseded`, `failed` |
| `created_at` | timestamptz | not null | Начало индексации |
| `activated_at` | timestamptz | nullable | Активация |

Ограничение: только одна запись со `status = 'active'` (partial unique index). Пара
`source_sha256 + embedding_model` индексируется, но не уникальна: принудительная
переиндексация того же файла создаёт новую версию.

## 4. RAG-контракт

Инструмент `search_hh_docs` принимает один аргумент:

```json
{
  "query": "как получить список вакансий"
}
```

Внутри инструмента выполняется endpoint-aware retrieval:

1. Из pgvector загружается каталог уникальных пар `section + endpoint`.
2. Модель структурированным вызовом выбирает номер подходящего эндпоинта из каталога
   или `-1`, если вопрос не про один метод.
3. Top-k ищется только среди чанков выбранного `section`; без выбора выполняется обычный
   векторный поиск по всему индексу.
4. Найденные чанки возвращаются в модель как текст с заголовком раздела и эндпоинта.

В `llm_runs.retrieved_chunks` сохраняются объекты:

```json
{
  "text": "фрагмент документации",
  "source": "hh_api.md",
  "section": "Вакансии",
  "endpoint": "GET /vacancies",
  "line_start": 1200,
  "line_end": 1280,
  "score": 0.87
}
```

Metadata обязательна, потому что ответ пользователю должен содержать ссылку на раздел
документации, а разработчик должен понимать, откуда взялся ответ.

Промпт:

- загружается из `prompts/initial.md` при старте;
- дополняется правилами проекта и инструкцией «отвечай только по контексту»;
- версия промпта считается по SHA-256 и записывается в `llm_runs`;
- не содержит ключей и внутренних URL с токенами.

## 5. Внутренний HTTP-контракт

### `POST /internal/v1/updates`

Заголовки: `Authorization: Bearer <INTERNAL_API_TOKEN>`,
`Idempotency-Key: <update_key>`.

Тело — нормализованный update:

```json
{
  "update_type": "message_created",
  "update_timestamp": 1760000000000,
  "user_locale": "ru",
  "chat": { "id": 123, "type": "dialog", "title": null },
  "user": { "id": 456, "first_name": "Иван", "last_name": null, "username": null, "is_bot": false },
  "message": { "id": "mid-1", "text": "Как получить список вакансий?", "timestamp": 1760000000000 },
  "callback": null
}
```

Ответ:

```json
{
  "reply": {
    "text": "Краткий ответ...\n\nИсточник: ...",
    "format": "markdown",
    "attachments": [
      { "type": "file", "filename": "example.py", "mime_type": "text/x-python", "content": "..." }
    ]
  },
  "already_delivered": false
}
```

Повторный запрос с тем же ключом:

- если ответ ещё не доставлен, возвращается тот же `reply`;
- если доставка подтверждена, возвращается `{"reply": null, "already_delivered": true}`.

### `POST /internal/v1/updates/{update_key}/delivered`

Помечает update и связанное исходящее сообщение доставленными. Идемпотентен.

### `POST /internal/v1/actions/clear`

Закрывает активный диалог по `chat_id` и `user_id`, возвращает число закрытых диалогов:
`{"closed": 1}`.

### `GET /health`

Проверяет доступность процесса, БД и активной версии индекса. Не проверяет Yandex API при
каждом вызове, чтобы не платить за лишние запросы; добавляется отдельный `/ready` для
проверки конфигурации модели.

## 6. Ограничения MAX, влияющие на дизайн

| Ограничение | Последствие для архитектуры |
|---|---|
| Нет `GET /chats` | Чаты создаются из событий, а не из запроса списка |
| Нет встроенного хранения сессий | Контекст хранится в PostgreSQL |
| Long polling и webhook взаимоисключающие | В проекте только polling |
| 4000 символов на сообщение | Разбиение в `max-bot`, хранение полного текста в БД |
| Переводы строк в code block схлопываются | Примеры кода доставляются файлами |
| Нет списков, таблиц и `hr` | Prompt и post-processing преобразуют формат |
| Нет `send_at` | Отложенные сообщения не входят в MVP |
| CA Минцифры | CA в образе, проверка TLS не отключается |
| Повторная доставка update | `processed_updates` + ack доставки |
| 30 rps ко всему API | Ограничение частоты отправки и батчинг, если появятся рассылки |
| 2 rps на `POST /answers` | Ограничение callback-ответов |

## 7. Безопасность

- `INTERNAL_API_TOKEN` защищает backend от вызовов извне Docker-сети; сравнение токена
  выполняется constant-time.
- Токен MAX и ключ Yandex не передаются в backend без необходимости. Backend не знает
  `MAX_BOT_API_KEY`.
- В логах маскируются `Authorization`, ключи, токены и персональные данные.
- Пользовательский текст не интерполируется в системный промпт как инструкция; он передаётся
  отдельным сообщением с явной ролью `user`.
- Ответы модели не выполняются как код.
- Для внешних ссылок в ответе используется allowlist доменов документации HH.ru.
- PostgreSQL не публикует порт наружу в production-профиле compose.

## 8. Наблюдаемость

Минимальные метрики:

- количество полученных update по типам;
- доля дубликатов;
- latency backend и LLM;
- количество пустых retrieval;
- ошибки Yandex API и MAX API;
- размер очереди/задержка обработки;
- количество активных диалогов.

Логи — JSON с `request_id`, `update_key`, `conversation_id`, `chat_id`, `event` и
`duration_ms`. Текст сообщений по умолчанию не логируется.

## 9. Что не входит в MVP

- Webhooks и публичный HTTPS-домен.
- Мини-приложение MAX и `window.WebApp`.
- Отправка файлов, изображений и голосовых сообщений.
- Отложенные рассылки.
- Мультиязычные ответы и автоматический перевод.
- Полноценная админ-панель.
- Обучение собственной модели или fine-tuning.

Эти возможности можно добавить после этапа 9, не меняя базовую архитектуру.
