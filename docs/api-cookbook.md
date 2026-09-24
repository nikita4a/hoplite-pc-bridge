# Глава 3. Hoplite REST API — поваренная книга

> Это третья глава руководства по стеку `hoplite-pc-bridge` + `hoplite-gateway`.
> Первая (bridge) и вторая (cookies) — в `GUIDE.md` и `docs/cookies.md`.
> Всё, что ниже, получено живыми запросами к `https://api.hoplite.sh` 2026-09-24 (UTC).
> Инструмент главы: `tools/hoplite_api.py` — только стандартная библиотека Python 3.11, ноль зависимостей.

---

## 3.0 Зачем вам REST API, если есть UI

Hoplite — это облачный агент с сандбоксами, тредами и MCP-интеграциями. Веб-интерфейс на
`app.hoplite.sh` — просто тонкий клиент: каждая кнопка в нём дёргает JSON-эндпоинт на
`api.hoplite.sh`. Если вы умеете говорить с этим API напрямую, вы можете:

- зарегистрировать свой MCP-сервер (например, наш `pc-tools` — bridge на ваш ПК) **скриптом**, без ручного заполнения диалога;
- проверить здоровье этого сервера **изнутри Hoplite** (эндпоинт `probe` сам ходит в ваш туннель и говорит, видит ли он инструменты);
- читать треды, статусы, сообщения, модельный каталог и расход кредитов — всё то, на чём стоит `hoplite-gateway`;
- построить собственную автоматизацию: cron-проверка, дашборд, бот.

Вся глава — один файл `tools/hoplite_api.py` и живые ответы сервера. Ничего не выдумано: каждый
блок вывода ниже — реальный ответ, который вы получите теми же командами (ID тредов/серверов —
наши, они не секретны; секреты — только ключ `hop_...` и capability-путь MCP, они нигде не напечатаны).

---

## 3.1 Аутентификация: два мира — API-ключ и сессия браузера

**API-ключ** (`hop_...`) создаётся в UI Hoplite (Settings → API keys). Ключ передаётся двумя заголовками:

```
X-Api-Key: hop_...
Origin: https://app.hoplite.sh
```

`Origin` обязателен: часть эндпоинтов проверяет его как CSRF-подобную защиту. Ключ хранится в
`hoplite-gateway/config.json` (`api_key`) — наш CLI читает его оттуда и никогда не печатает.

**Сессия браузера** — cookie-набор `app.hoplite.sh` (better-auth). Добывается через `docs/cookies.md` (глава 2).

Ключевой факт, который половина интеграций узнаёт слишком поздно — **матрица доступа**
(проверена живыми запросами, каждая клетка — реальный статус HTTP):

| Действие | X-Api-Key | Сессия (cookies) |
|---|---|---|
| `GET /api/projects`, `/api/threads`, `/api/threads/{id}/messages`, `/api/model-providers`, `/api/mcp/servers`, `/api/mcp/catalog`, `/api/usage` | ✅ 200 | — |
| `POST /api/threads` (создать тред) | ✅ 200 | — |
| `POST /api/mcp/servers` (зарегистрировать MCP) | ✅ 200 | — |
| `POST /api/mcp/probe` (проверить чужой MCP) | ✅ 200 | — |
| `POST /api/threads/{id}/messages` (дописать сообщение) | ❌ **401 `invalid_api_key`** | ✅ 201 (живая сессия) |
| `PATCH` / `DELETE /api/mcp/servers/{id}` | ❌ **401 `invalid_api_key`** | только живая сессия |
| `GET /api/auth/session` | ❌ 401 `auth_required` | ✅ |

Вывод: **API-ключ — «read + create», сессия — «write»**. Именно поэтому `hoplite-gateway`
держит в конфиге и ключ, и cookies: ключ создаёт треды, сессия досылает сообщения.
Сессия живёт ограниченное время — протухшие cookie дают `auth_required` (см. §3.7).

Два разных сообщения об ошибке — два разных мира:

- `invalid_api_key` = «этот эндпоинт в принципе не принимает ключ» (или ключ битый);
- `auth_required` = «сюда нужна сессия, а твои cookie не распознаны».

---

## 3.2 Инструмент: `tools/hoplite_api.py`

Стандартная библиотека, никаких `pip install`. Все команды печатают pretty-JSON и завершаются
с кодом ≠ 0 при HTTP ≥ 400 — ошибки не глотаются. Ключ читается из `--config` (по умолчанию
конфиг шлюза), секреты не выводятся никогда.

```
$ python tools/hoplite_api.py --help
usage: hoplite_api.py [-h] [--config CONFIG]
                      {session,projects,threads,thread,messages,models,mcp-servers,mcp-catalog,mcp-create,mcp-patch,mcp-delete,mcp-probe,ask,append,stop,raw}
                      ...

Minimal stdlib CLI for the Hoplite REST API (api.hoplite.sh).

positional arguments:
  {session,projects,threads,thread,messages,models,mcp-catalog,mcp-create,mcp-patch,mcp-delete,mcp-probe,ask,append,stop,raw}
    session             GET /api/auth/session — who am I
    projects            GET /api/projects — list projects
    threads             GET /api/threads?limit=N — list threads
    thread              GET /api/threads/{id} — thread detail
    messages            GET /api/threads/{id}/messages — message list
    models              GET /api/model-providers — available models
    mcp-servers         GET /api/mcp/servers — registered MCP servers
    mcp-catalog         GET /api/mcp/catalog — MCP catalog
    mcp-create          POST /api/mcp/servers — register an MCP server
    mcp-patch           PATCH /api/mcp/servers/{id}
    mcp-delete          DELETE /api/mcp/servers/{id}
    mcp-probe           POST /api/mcp/probe — health-check an MCP endpoint
    ...
```

`raw` — аварийный люк для любого эндпоинта: `python tools/hoplite_api.py raw GET /api/usage`.

Сессионные эндпоинты проверяем сразу — это заодно демо матрицы:

```
$ python tools/hoplite_api.py session
ERROR: GET /api/auth/session -> 401: {"ok": false, "error": "auth_required"}

$ python tools/hoplite_api.py raw GET /api/auth/get-session
HTTP 200
null
```

`get-session` при ключе возвращает `200 null` — «сессии нет, но и ошибки нет». Это дешёвый способ
проверить, жив ли API, не тратя квоту.

---

## 3.3 Читаем базу: проекты, треды, модели

### Проекты

```
$ python tools/hoplite_api.py projects
{
  "ok": true,
  "projects": [
    {
      "id": "proj_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
      "orgId": "org_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
      "name": "<your-account>/<your-repo>",
      "description": null,
      "defaultBranch": "main",
      "previewPort": 3000,
      "setupScript": null,
      "runScript": null,
      "framework": "none",
      "instructions": null,
      "defaultModel": null,
      "reasoningEffort": null,
      "agentSpeed": null,
      "prebuildsEnabled": false,
      "prReviewAutofixDefault": null,
      "sandboxSpec": null,
      "createdByUserId": "4785a90c-a378-498a-8fbc-8e76e8494280",
      "version": 0,
      "createdAt": "2026-09-23T20:36:15.239Z",
      "repos": [
        { "repoFullName": "<your-account>/<your-repo>", "repositoryId": "repo_1322015550" }
      ]
    }
  ]
}
```

**Зачем**: `projectId` обязателен для создания треда. Проект = репозиторий + настройки песочницы
(`setupScript`, `sandboxSpec`, `defaultModel`). `framework: none` — Hoplite умеет сам детектить стек.

### Треды

```
$ python tools/hoplite_api.py threads --limit 3
{
  "ok": true,
  "threads": [ { ... см. ниже ... } ],
  "hasMore": true,
  "nextCursor": "eyJpZCI6InRocl81YzY2NGRkZmQ5MWM0...",   # курсорная пагинация
  "statusCounts": { "ready": 30, "failed": 4 },
  "totalCount": 34
}
```

Ветка + агрегаты в одном ответе: `statusCounts` — сколько тредов в каком статусе, не вытягивая
все страницы. Один элемент `threads` (обрезан до смысловых полей):

```json
{
  "id": "thr_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "incarnationId": "86848e81-75b8-4214-a91b-f610c68f90d6",
  "orgId": "org_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "projectId": "proj_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "title": "Reply with exactly PROBE OK",
  "modelId": null,
  "status": "ready",
  "initializationState": "completed",
  "initializationAttemptCount": 1,
  "initializationErrorCode": null,
  "metadata": { "credentialPolicy": "non_subscription" },
  "executionTarget": null,
  "version": 11,
  "createdAt": "2026-09-24T22:31:42.005Z",
  "updatedAt": "2026-09-24T22:32:05.430Z",
  "participantUserIds": [ "4785a90c-a378-498a-8fbc-8e76e8494280" ]
}
```

Расшифровка нетривиальных полей (наблюдение + фронтенд-бандл `app.hoplite.sh/assets/src-*.js`):

| Поле | Что значит |
|---|---|
| `status` | Жизненный цикл: `queued → running → (waiting / blocked) → ready / failed / archived`. Терминальные: `ready`, `failed`, `archived`. |
| `initializationState` | Отдельная машина состояний песочницы: `preparing → ready` (в UI `ready`→`accepted`, `failed`→`error`). `initializationPhase`/`AttemptCount`/`ErrorCode` — диагностика сбоев сандбокса. |
| `modelId` | Модель, закреплённая за тредом. `null` = дефолт проекта. |
| `executionTarget` | `null` = облачная песочница. Объект `{kind:"local", bindingId, hostId, ...}` = **локальное исполнение** через Hoplite Desktop (см. `hoplite-gateway/ACCESS_PC.md`). |
| `incarnationId` | «Поколение» треда — меняется при retry/recovery, сам `id` стабилен. |
| `metadata.credentialPolicy` | `non_subscription` = платим кредитами BYOK, не подпиской. |

Обратите внимание на заголовок треда: `Reply with exactly PROBE OK` — Hoplite **сам
сгенерировал заголовок из промпта**, мы его не задавали.

### Модели

```
$ python tools/hoplite_api.py models
{
  "ok": true,
  "modelConfigurationRevision": "5d1e84c5-...",
  "providers": [
    { "defaultModelId": "claude-fable-5",   "label": "Anthropic",  "provider": "anthropic" },
    { "defaultModelId": "gpt-6-sol",        "label": "OpenAI",    "provider": "openai" },
    { "defaultModelId": "moonshotai/kimi-k3","label": "OpenRouter","provider": "openrouter" }
  ],
  "runConfig": {
    "defaultModelId": "gpt-6-sol",
    "defaultReasoning": "medium",
    "defaultSpeed": "standard",
    "modelFallbackIds": [ "claude-fable-5-1", "...", "grok-build-0.1" ],
    "reasoningOptions": [ "low", "medium", "high", "xhigh", "max" ],
    "speedOptions": [ "standard", "fast" ]
  },
  "models": [ ... ]
}
```

`models` — полный каталог с ценами. Сводка (цены: `inputMicrosPerMTok / 1e6` = $ за миллион токенов):

| Модель | Провайдер | Контекст | Reasoning | Fast | Images | $/M in | $/M out |
|---|---|---|---|---|---|---|---|
| claude-fable-5-1 | Anthropic | 1M | low…max | – | ✅ | 11.0 | 55.0 |
| claude-opus-5-5 | Anthropic | 1M | low…max | – | ✅ | 4.4 | 22.0 |
| claude-fable-5 | Anthropic | 200K | low…xhigh | – | ✅ | 11.0 | 55.0 |
| claude-sonnet-5 | Anthropic | 1M | none…max | – | ✅ | 3.3 | 16.5 |
| gpt-6-astra | OpenAI | 272K | low…max | ✅ | ✅ | 11.0 | 55.0 |
| gpt-6-sol / -1m | OpenAI | 272K / 1M | none…max | ✅ | ✅ | 2.2 / 4.4 | 11.0 / 16.5 |
| gpt-5.6-terra / -1m | OpenAI | 200K / 1M | none…max | ✅ | ✅ | 2.2 / 4.4 | 13.2 / 19.8 |
| gpt-6-luna / -1m | OpenAI | 272K / 1M | none…max | ✅ | ✅ | 0.11 / 0.22 | 0.55 / 0.83 |
| grok-4.6 | xAI | 500K | low…xhigh | – | ✅ | 2.2 | 6.6 |
| grok-4.3 | xAI | 1M | low…xhigh | – | ✅ | 1.375 | 2.75 |
| grok-build-0.1 | xAI | 256K | — | – | ✅ | 1.1 | 2.2 |
| meta/muse-spark-1.3 | OpenRouter | 1M | minimal…xhigh | – | ✅ | 1.375 | 4.675 |
| moonshotai/kimi-k3 | OpenRouter | 1M | low, high, max | – | – | 3.3 | 16.5 |
| z-ai/glm-5.3 | OpenRouter | 1M | low, high, max | – | – | 1.54 | 4.84 |
| z-ai/glm-5.3-flash | OpenRouter | 1M | low, high, max | – | ✅ | 0.165 | 0.55 |
| deepseek/deepseek-v4-flash-0731 | OpenRouter | 1M | none, low, high, max | – | – | 0.242 | 0.726 |

Нюанс про reasoning: в zod-схемах фронта enum широчайший — `off|none|minimal|low|medium|high|xhigh|max`,
но `runConfig.reasoningOptions` отдаёт `low…max`, а каждая модель дополнительно режет список
своим `capabilities.reasoningLevels` (у Kimi/GLM нет `medium`!). Отправляйте уровень, который
есть в `reasoningLevels` конкретной модели — иначе 400.

---

## 3.4 Жизненный цикл треда: создать → поллить → прочитать

Создание — `POST /api/threads`. Минимальное тело, которое сервер реально принимает:

```json
{ "projectId": "proj_...", "prompt": "Reply with exactly: PROBE-OK" }
```

Опционально: `title`, `model` (id из каталога), `reasoning: {"mode": "..."}`, `speed` (`standard|fast`),
`executionTarget` (локальный запуск — глава ACCESS_PC в hoplite-gateway), `repos`.

CLI-обёртка `ask` создаёт тред и поллит `GET /api/threads/{id}` до терминального статуса:

```
$ python tools/hoplite_api.py ask --prompt "Reply with exactly: PROBE-OK" --wait 300
thread: thr_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx  status: queued
  status=running
  status=ready
final status: ready
```

**Важно про деньги**: каждый тред поднимает облачную песочницу и жжёт кредиты — не устраивайте
фабрику тредов в цикле. Для смоук-тестов используйте однозначные промпты (`Reply with exactly: ...`)
и читайте уже готовые треды через GET. Тред выше — `thr_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` —
мы создали ровно этой командой; дальше читаем его, не создавая новых.

Сообщения — `GET /api/threads/{id}/messages`. Вывод реального треда (полный, не сокращённый):

```
$ python tools/hoplite_api.py messages thr_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
{
  "count": 4,
  "messages": [
    {
      "id": "msg_53f6b51c04dd4ffb8f6833b0a94d7beb",
      "runId": "run_e5b1bf10af6941209e8afe770d124cfe",
      "role": "user",
      "content": "Reply with exactly: PROBE-OK",
      "createdAt": "2026-09-24T22:31:42.029Z",
      "author": { "id": "4785a90c-...", "name": "<your-account>", "image": "https://avatars.githubusercontent.com/..." }
    },
    {
      "id": "evt_run_e5b1..._workspace_setup_1_started",
      "role": "tool",
      "content": "",
      "kind": "tool",
      "toolCallId": "workspace-setup:thr_...:1",
      "toolName": "workspace_setup",
      "metadata": {
        "input": { "stage": "running_setup" },
        "description": "Preparing the workspace in the background",
        "eventType": "tool.started"
      }
    },
    {
      "id": "evt_run_e5b1..._workspace_setup_1_completed",
      "role": "tool",
      "content": "No setup command is configured in the project, .hoplite/settings.json, or .hoplite/setup.sh; no setup command was run.",
      "toolName": "workspace_setup",
      "metadata": {
        "input": { "stage": "completed" },
        "output": { "summary": "No setup command is configured..." },
        "eventType": "tool.completed"
      }
    },
    {
      "id": "msg_run_e5b1..._final_s0",
      "role": "assistant",
      "content": "PROBE-OK",
      "metadata": { "estimatedContextTokens": 47 },
      "createdAt": "2026-09-24T22:32:05.416Z",
      "author": null
    }
  ]
}
```

Вся кухня видна как на ладони:

- `role: "user"` — ваш промпт;
- `role: "tool"` с `eventType: tool.started/completed` — **события песочницы** (workspace_setup
  всегда первый: Hoplite клонирует репо проекта и запускает setup-скрипт). Это не сообщения агента,
  это телеметрия, перемешанная с диалогом — фильтруйте по `role`;
- `role: "assistant"` с id `..._final_s0` — финальный ответ ран #0. Агент ответил ровно `PROBE-OK`
  через 23 секунды после промпта.

Дописать сообщение в существующий тред: `POST /api/threads/{id}/messages` c `{"content": "..."}` —
но, см. матрицу §3.1, **только с сессией**:

```
$ python tools/hoplite_api.py append thr_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx --text "hello?"
ERROR: config.json has no `cookies` — POST /messages rejects X-Api-Key (401 invalid_api_key).
```

```
$ python tools/hoplite_api.py raw POST /api/threads/thr_.../messages --body "{\"content\":\"demo\"}"
HTTP 401
{ "ok": false, "error": "invalid_api_key" }
```

Именно на этом спотыкаются самописные интеграции: создал тред ключом — а ответить второй репликой
уже не может. Решение — держать свежие cookies (глава 2, `docs/cookies.md`) и досылать ими.
Так работает `hoplite-gateway`: ключ создаёт треды, сессия досылает follow-up'ы.

---

## 3.5 MCP-менеджмент через API (главное для pc-bridge)

### Что уже зарегистрировано

```
$ python tools/hoplite_api.py mcp-servers
{
  "ok": true,
  "servers": [
    {
      "id": "mcp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
      "name": "pc-tools",
      "config": {
        "url": "https://<your-domain>.ngrok-free.dev/mcp-<32-hex-capability>",
        "transport": "http",
        "description": "Shell + filesystem on the operator PC (desktop-commander)"
      },
      "enabled": true,
      "version": 0,
      "createdAt": "2026-09-23T23:59:14.825Z"
    }
  ]
}
```

(URL заменён на плейсхолдер: реальный домен ngrok + capability-путь — это секреты, глава 1
объясняет, почему.) Это и есть наш `pc-tools` — bridge с ПК. `projectId: null` = сервер
организационный, виден во всех проектах.

### probe — проверка изнутри Hoplite

`POST /api/mcp/probe` с телом `{"url": "...", "transport": "http"}` — Hoplite **сам** подключается
к вашему MCP-серверу и отчитывается. Это лучший smoke-тест всей цепочки
ngrok → mcp-proxy → desktop-commander:

```
$ python tools/hoplite_api.py mcp-probe --url https://mcp.linear.app/mcp
{
  "ok": true,
  "probe": {
    "connected": false,
    "requiresAuthentication": true,
    "requiresOAuth": true,
    "supportsDynamicRegistration": true,
    "transport": "http",
    "serverName": null,
    "toolCount": null
  }
}
```

Linear без OAuth не пускает — ожидаемо. А вот наш мост:

```
$ python tools/hoplite_api.py mcp-probe --url https://<your-domain>.ngrok-free.dev/mcp-<32-hex>
{
  "ok": true,
  "probe": {
    "connected": true,
    "requiresAuthentication": false,
    "requiresOAuth": false,
    "supportsDynamicRegistration": false,
    "transport": "http",
    "serverName": "desktop-commander",
    "toolCount": 26,
    "instructions": null
  }
}
```

**`connected: true`, `serverName: "desktop-commander"`, `toolCount: 26`** — 26 инструментов с
вашего ПК (shell, файлы, процессы) доступны облачному агенту Hoplite. Один запрос заменил
весь ручной чеклист «открыл UI → открыл настройки MCP → нажал Test connection».

### Регистрация нового сервера

```
$ python tools/hoplite_api.py mcp-create --name cookbook-demo \
      --url https://mcp.linear.app/mcp --description "temporary server for the api-cookbook chapter"
{
  "ok": true,
  "server": {
    "id": "mcp_35a182872a9f41b5933de43742e7f87f",
    "name": "cookbook-demo",
    "config": {
      "url": "https://mcp.linear.app/mcp",
      "transport": "http",
      "description": "temporary server for the api-cookbook chapter"
    },
    "enabled": true,
    "version": 0
  }
}
```

Тело: `{name, enabled, config: {transport: "http"|"sse", url, description, headers?, needsAuth?}}`.
Хедеры (например `Authorization: Bearer ...`) кладутся в `config.headers` — потом Hoplite сам
слабит их при подключении.

Сервер не пускает приватные адреса — проверено живым запросом:

```
$ python tools/hoplite_api.py mcp-create --name cookbook-demo-bad --url https://localhost/mcp
ERROR: POST /api/mcp/servers -> 400: {"ok": false, "error": "invalid_mcp_server"}
```

Вот почему мосту нужен ngrok: `127.0.0.1:8888` Hoplite не увидит ни создать, ни пробить.
Только публичный https.

### Изменение и удаление — только с живой сессией

```
$ python tools/hoplite_api.py mcp-patch mcp_35a182872a9f41b5933de43742e7f87f --enabled false
ERROR: PATCH /api/mcp/servers/mcp_... -> 401: {"ok": false, "error": "invalid_api_key"}

$ python tools/hoplite_api.py mcp-delete mcp_35a182872a9f41b5933de43742e7f87f
ERROR: DELETE /api/mcp/servers/mcp_... -> 401: {"ok": false, "error": "invalid_api_key"}
```

Асимметрия из §3.1 в полный рост: создать ключом можно, а вычистить за собой — только из
браузерной сессии (UI → Settings → MCP → Delete). Для автоматики «создал-попробовал-удалил»
нужен cookie-флоу из главы 2. Протухшая сессия здесь отвечает `auth_required` — обновите cookies.

### Каталог готовых MCP

`GET /api/mcp/catalog` — витрина готовых интеграций (Intercom, Miro, Zapier, Context7, Linear,
Sentry...), каждая с доменом, иконкой и типом (`mcp`/`openapi`):

```
$ python tools/hoplite_api.py mcp-catalog --limit 1800
{
  "ok": true,
  "entries": [
    {
      "domain": "context7.com",
      "name": "Context7",
      "description": "Context7 fetches up-to-date code examples and documentation right into your LLM's context...",
      "kinds": [ "mcp" ],
      "url": "https://integrations.sh/context7.com"
    },
    ...
  ]
}
```

---

## 3.6 Деньги и прочие GET-ы

```
$ python tools/hoplite_api.py raw GET /api/usage
HTTP 200
{
  "ok": true,
  "usage": {
    "orgId": "org_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "totalCostMicros": 1752562,
    "inputTokens": 536037,
    "outputTokens": 4424,
    "events": 42
  }
}
```

`totalCostMicros` — микродоллары: 1752562 = $1.75 израсходовано на 34 треда. Стоимость
одного probe-треда ≈ $0.01–0.03 (посмотрите `inputTokens` до/после). Для мониторинга
фермы/гейтвея это готовая метрика — `hoplite-gateway` так и считает бюджет.

Ошибка тоже информативна — эндпоинт PR-статуса на треде без PR:

```
$ python tools/hoplite_api.py raw GET /api/threads/thr_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.../pr/status
HTTP 404
{ "ok": false, "error": "pull_request_not_found" }
```

---

## 3.7 Шпаргалка ошибок

| HTTP | `error` | Что это значит | Что делать |
|---|---|---|---|
| 401 | `auth_required` | Нужна браузерная сессия, cookie не распознаны/протухли | Обновить cookies (глава 2), ключ тут бессилен |
| 401 | `invalid_api_key` | Эндпоинт не принимает ключ вообще (write-операции) | Перейти на cookie-сессию или делать через UI |
| 400 | `invalid_mcp_server` | Схема тела битая или URL приватный/не https | Публичный https, поля `name/config/transport` |
| 404 | `pull_request_not_found` | PR-эндпоинт на треде без PR | Норма, если PR не создавали |

## 3.8 Итог главы

Вы умеете: аутентифицироваться двумя способами и знаете, где какой работает; читать проекты,
треды, сообщения, модели и деньги; создавать тред с промптом и поллить его до `ready`;
регистрировать MCP-сервер и пробить его изнутри Hoplite (`probe` — главный smoke-тест
`hoplite-pc-bridge`); читать ошибки как подсказки. Всё — одним stdlib-файлом
`tools/hoplite_api.py` без зависимостей.

Куда дальше: `hoplite-gateway/GUIDE.md` — как на этом API стоит OpenAI-совместимый прокси;
`docs/cookies.md` — как добывать и обновлять сессию для write-операций.
