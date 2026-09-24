# Hoplite + твой ПК: полный гайд от А до Я

**Что получится:** облачный агент Hoplite (Claude Opus 5.5 / GPT-5.6) получает настоящие
инструменты на твоём компьютере — читает и пишет файлы, запускает команды, смотрит процессы.
Плюс обратная связка: Hoplite как «мозг» для локальных `opencode` и `omp`.

Гайд написан под видеосъёмку: каждый шаг — что делать, что увидишь на экране, и **зачем** это
нужно (это можно читать в кадре).

---

## Часть 0. Зачем это вообще нужно

Облачный агент Hoplite работает в своей песочнице: `/tmp/hoplite/workspace`, Linux, gvisor,
1 vCPU / 16 GB. Он клонирует твой репозиторий, ставит зависимости, гоняет тесты и возвращает
PR. Это отлично — но **твоего компьютера он не видит**. Дословный ответ агента на вопрос
«можешь создать файл у меня на ПК»:

> «Нет — ни видеть, ни создавать файлы на твоём ПК я не могу. Я нахожусь в
> `/tmp/hoplite/workspace` на Linux, путей вроде `/c` или `/mnt/c` не существует —
> твои диски сюда не примонтированы».

И это правда. Есть ровно два способа это обойти, и они решают **разные** задачи:

| Связка | Направление | Что даёт | Репозиторий |
|---|---|---|---|
| **PC Bridge** (этот) | облако → твой ПК | агент Hoplite получает shell и файлы на твоей машине | `hoplite-pc-bridge` |
| **Gateway** | твой ПК → облако | `opencode` / `omp` используют Opus 5.5 как модель | `hoplite-gateway` |

```
ЧАСТЬ 1 — облако получает руки на твоём ПК
──────────────────────────────────────────
Hoplite cloud agent  (песочница в облаке)
   │  MCP over HTTPS
   ▼
https://<твой-домен>.ngrok-free.dev/<секретный-путь>
   │  ngrok (исходящий туннель — входящие порты открывать НЕ нужно)
   ▼
mcp-proxy  127.0.0.1:8888     ← слушает только loopback
   │  stdio
   ▼
desktop-commander             ← shell + файлы, исполняется на твоём ПК


ЧАСТЬ 2 — твой ПК получает мозг из облака
──────────────────────────────────────────
opencode / omp  (локально, инструменты исполняются у тебя)
   │  OpenAI-совместимый API
   ▼
hoplite-gateway  127.0.0.1:8787
   │  REST (X-Api-Key)
   ▼
Hoplite cloud agent  (Claude Opus 5.5)
```

---

## Часть 1. PC Bridge — облачный агент с руками на твоём ПК

### 1.1. Аккаунты, которые понадобятся

| Аккаунт | Зачем | Где взять |
|---|---|---|
| **Hoplite** | сам облачный агент | <https://app.hoplite.sh> → Sign Up |
| **Hoplite API key** | регистрация MCP-сервера через API (можно и без него, через UI) | app.hoplite.sh → Settings → API Keys → `hop_...` |
| **ngrok** | публичный URL до твоей машины | <https://dashboard.ngrok.com> → бесплатный план |
| **GitHub** (необязательно) | publication | <https://github.com> |

**Почему ngrok, а не Cloudflare Tunnel / localtunnel:**

- `cloudflared` quick tunnel даёт **случайный** URL каждый запуск → в Hoplite пришлось бы
  перерегистрировать сервер постоянно. У ngrok на бесплатном плане есть **один статический
  домен** — URL не меняется, зарегистрировал один раз и забыл.
- `localtunnel` показывает страницу-заглушку с паролем (твой публичный IP) → MCP-клиент
  спотыкается на HTML вместо JSON-RPC.
- ngrok бесплатно: 1 статический домен, 1 туннель онлайн, 20 запросов/мин. Для агента хватает.

### 1.2. Софт

```powershell
# Node.js >= 18 и Python >= 3.10 уже должны стоять. Проверка:
node --version      # v22.x
python --version    # Python 3.11.x

# два npm-пакета (оба — готовые MCP-серверы, ничего писать не нужно)
npm i -g mcp-proxy @wonderwhy-er/desktop-commander

# ngrok
winget install Ngrok.Ngrok
```

> ⚠️ **Грабли №1, обязательные:** `winget` ставит ngrok **3.3.1**, а аккаунт требует
> **≥ 3.20.0**. Без обновления получишь `ERR_NGROK_121: Your ngrok-agent version "3.3.1"
> is too old`. Лечится одной командой:
>
> ```powershell
> ngrok update        # 3.3.1 -> 3.39.x, ~40 секунд
> ngrok --version
> ```

Что за пакеты и зачем каждый:

| Пакет | Роль |
|---|---|
| `@wonderwhy-er/desktop-commander` | MCP-сервер с 26 инструментами: shell, файлы, процессы, поиск. Работает по **stdio** (не умеет в HTTP). |
| `mcp-proxy` | мост stdio ↔ HTTP. Превращает desktop-commander в сетевой MCP-сервер, добавляет авторизацию и TLS-терминацию не нужны (TLS закрывает ngrok). |

### 1.3. Скачиваем репозиторий

```powershell
cd C:\Users\<ты>\tmp
git clone https://github.com/nikita4a/hoplite-pc-bridge.git
cd hoplite-pc-bridge
copy config.example.json config.json
```

### 1.4. ngrok: статический домен и токен

1. dashboard.ngrok.com → **Domains** → New Domain → получишь
   `что-то-там.ngrok-free.dev` (бесплатно, один на аккаунт).
2. dashboard.ngrok.com → **Getting Started** → скачай agent-конфиг → сохрани в папку репы
   как **`ngrok.yml`**.

Внутри `ngrok.yml` будет примерно так:

```yaml
version: "3"
agent:
    authtoken: 2yHd...твой_токен...
endpoints:
    - name: llm-gateway
      url: "https://твой-домен.ngrok-free.dev"
      upstream:
        url: "http://127.0.0.1:8888"
```

> ⚠️ **Грабли №2:** этот файл — формата `version: "3"`, а агент 3.3.1 умеет только `1` и `2`
> и падает с `unknown version '3'`. **Решение уже вшито в `bridge.py`:** он не отдаёт yml
> ngrok'у, а вытаскивает из него регулярками только `authtoken` и домен, и всегда запускает
> одну и ту же команду `ngrok http 8888 --domain ... --authtoken ...`. Формат конфига
> перестаёт иметь значение.

`ngrok.yml` в git **не попадает** (`.gitignore` — deny-all allowlist).

### 1.5. `config.json` — единственная настройка

```json
{
  "port": 8888,
  "secret_path": "",
  "api_key": "",
  "ngrok": {
    "config_path": "ngrok.yml",
    "endpoint_name": "llm-gateway",
    "public_url": "https://твой-домен.ngrok-free.dev"
  },
  "desktop_commander": {
    "defaultShell": "cmd.exe",
    "telemetryEnabled": false,
    "allowedDirectories": [
      "C:/Users/твой-юзер/tmp",
      "C:/Users/твой-юзер/Desktop"
    ]
  }
}
```

Что здесь важно и почему:

| Поле | Зачем |
|---|---|
| `secret_path` | **Это и есть пароль.** Пустой → `bridge.py` сгенерирует `mcp-<32 hex>` при первом запуске и сохранит. Публичный URL становится capability-URL: кто знает путь, тот имеет доступ. Поэтому Hoplite-диалог можно оставить на `Authentication: None`. |
| `defaultShell` | **Ставь `cmd.exe`, не `powershell.exe`.** PowerShell грузит профиль юзера, и если там сломанный модуль (у меня — `Terminal-Icons`), вывод каждой команды тонет в простыне ошибок `Import-PowerShellDataFile`. Облачный агент в этом теряется. |
| `allowedDirectories` | Какие папки видят **файловые** инструменты. Пустой список = весь диск, поэтому `bridge.py` **отказывается стартовать** с пустым значением. |
| `telemetryEnabled` | desktop-commander по умолчанию шлёт телеметрию. Выключаем. |
| `api_key` | Необязательно. Если заполнить — mcp-proxy дополнительно потребует заголовок `X-API-Key`. Имеет смысл, только если твой MCP-клиент умеет слать свои заголовки. |

> ⚠️ **Грабли №3 (важная для понимания):** `allowedDirectories` ограничивает **только файловые
> инструменты**. Команды через `start_process` видят весь диск — это ограничение самого
> desktop-commander, а не моста. Не считай список папок песочницей для shell.

### 1.6. Запуск

```powershell
start_bridge.bat
# или с полной самопроверкой:
python bridge.py --verify
```

Что увидишь в консоли (это же готовый кадр для видео):

```
[dc-config] allowedDirectories: [] -> ['C:/Users/.../tmp', 'C:/Users/.../Desktop']
[dc-config] telemetryEnabled: True -> False
[dc-config] merged into C:\Users\...\.claude-server-commander\config.json (one-time .bak kept alongside)

==============================================================================
  Hoplite -> Add MCP server
==============================================================================
  Server key    : pc-tools
  Connection    : http
  URL           : https://твой-домен.ngrok-free.dev/mcp-1a2b3c4d...
  Description   : Shell + filesystem on the operator PC (desktop-commander)
  Authentication: None  (the URL path is the secret)
==============================================================================
  Shared folders (file ops only — terminal commands are NOT sandboxed):
    - C:/Users/.../tmp
    - C:/Users/.../Desktop
  Blocked commands: 0 in config.json (UNRESTRICTED shell)
  ⚠ anyone with this URL has that access. Stop the bridge when done.
==============================================================================
  After saving in Hoplite: start a NEW thread — existing ones keep the old tool list.
  tunnel READY
```

Скрипт делает пять вещей, которые иначе пришлось бы делать руками:

1. **Пишет конфиг desktop-commander туда, где он реально лежит.** README пакета утверждает,
   что конфиг — это `config.json` в рабочей директории сервера. Это неправда: в
   `dist/config.js` путь зашит как `~/.claude-server-commander/config.json`. Файл **общий**
   для всех твоих MCP-клиентов (Claude Desktop, Cline, …) и хранит `clientId` + статистику
   вызовов. Поэтому `bridge.py` его **мёржит** (трогает только свои ключи), а не
   перезаписывает, и один раз кладёт рядом `config.json.bak-<timestamp>`. Сервер следит за
   каталогом и подхватывает изменения на лету.
2. Поднимает `mcp-proxy` на `127.0.0.1:8888` — **только loopback**, единственные ворота
   наружу это ngrok. SSE-лег отключён (`--server stream`), чтобы не оставить второй
   неаутентифицированный эндпоинт.
3. Поднимает ngrok и определяет публичный URL.
4. Ждёт, пока эндпоинт реально ответит на MCP-рукопожатие (а не просто откроет порт).
5. Пишет `status.json` — по нему работают `verify_bridge.py`, `status_all.py` и `--stop`.

Остановка: `stop_bridge.bat` (или Ctrl+C в окне) — убивает и proxy, и ngrok.

### 1.7. Регистрация в Hoplite

#### Способ А — через UI (то, что показано в диалоге)

`app.hoplite.sh` → проект → **MCP servers** → **Add server**:

| Поле | Значение |
|---|---|
| **Server key** | `pc-tools` |
| **Connection** | `http` |
| **URL** | целиком, как напечатал `bridge.py` (со `/<секретный-путь>`) |
| **Description** | `Shell + filesystem on the operator PC` |
| **Authentication** | `None` — секретом является сам путь URL |
| **Advanced** | не трогать |

После сохранения — **обязательно новый тред**. В уже идущих список инструментов не
обновляется.

#### Способ Б — через API (без браузера, одной командой)

Нужен `hop_`-ключ. Схема тела подобрана реверсом фронтенд-бандла
(`app.hoplite.sh/assets/src-D_ttX4zR.js`):

```bash
curl -X POST https://api.hoplite.sh/api/mcp/servers \
  -H "X-Api-Key: hop_ТВОЙ_КЛЮЧ" \
  -H "Content-Type: application/json" \
  -H "Origin: https://app.hoplite.sh" \
  -d '{
    "name": "pc-tools",
    "enabled": true,
    "config": {
      "transport": "http",
      "url": "https://твой-домен.ngrok-free.dev/mcp-1a2b3c4d...",
      "description": "Shell + filesystem on the operator PC (desktop-commander)"
    }
  }'
```

Ответ `201`:

```json
{"ok":true,"server":{"id":"mcp_4a1b...","orgId":"org_...","projectId":null,
 "name":"pc-tools","config":{"url":"https://...","transport":"http","description":"..."},
 "enabled":true}}
```

Три неочевидных факта об этом эндпоинте:

- **Тело вложенное.** Плоское `{"name":..., "transport":..., "url":...}` даёт
  `400 invalid_mcp_server`. Нужен именно `{name, enabled, config:{...}}`.
- **`projectId: null` = уровень организации.** Сервер виден во всех проектах аккаунта,
  а не в одном.
- **Валидатор URL строгий:** только `https` и только **публичный** хост. Loopback,
  link-local, приватные и внутренние адреса запрещены сообщением
  `MCP url must not target a loopback, link-local, private, or internal host`.
  Именно поэтому туннель обязателен — `http://127.0.0.1:8888` зарегистрировать нельзя.

Прочие роуты: `GET /api/mcp/servers` (список), `PATCH /api/mcp/servers/{id}` (правка),
`DELETE /api/mcp/servers/{id}`, `POST /api/mcp/probe` (проверка связи),
`GET /api/mcp/catalog` (каталог готовых интеграций).

### 1.8. Проверка — три уровня

**Уровень 1. Hoplite видит твой сервер** (их же эндпоинт, самая честная проверка):

```bash
curl -X POST https://api.hoplite.sh/api/mcp/probe \
  -H "X-Api-Key: hop_ТВОЙ_КЛЮЧ" -H "Content-Type: application/json" \
  -d '{"url":"https://твой-домен.ngrok-free.dev/mcp-1a2b3c4d..."}'
```

Реальный ответ:

```json
{"ok":true,"probe":{"connected":true,"requiresAuthentication":false,
 "requiresOAuth":false,"supportsDynamicRegistration":false,"transport":"http",
 "serverName":"desktop-commander","toolCount":26,"instructions":null}}
```

**Уровень 2. Инструменты реально исполняются на ПК:**

```powershell
python verify_bridge.py
```

```
[verify] initialize OK -> desktop-commander 0.2.50 (session ...)
[verify] tools/list OK -> 26 tools: get_config, read_file, write_file, start_process, ...
[prove] list_directory C:/Users/.../tmp -> OK (198768 chars of listing)
[prove] get_config OK -> allowedDirectories=[...] blockedCommands=0 telemetry=False
[prove] boundary read_file C:/Windows/win.ini -> DENIED (folder sandbox holds for file ops)
[prove] start_process shell -> OK, executed on this PC
VERDICT: OK — public MCP endpoint is usable
```

Обрати внимание на строку `boundary ... DENIED`: проверка **намеренно** пытается прочитать
файл за пределами разрешённых папок и ожидает отказ. Если когда-нибудь станет `ALLOWED` —
значит `allowedDirectories` не применился, и весь диск открыт.

**Уровень 3. Всё сразу, включая гейтвей и конфиги клиентов:**

```powershell
python status_all.py
```

```
PASS  1. bridge local handshake                      session 95a069db  [0.0s]
PASS  2. bridge public handshake                     session 7a18a13a  [2.5s]
PASS  3+4. shell on this PC + sandbox denies outside shell=OK sandbox_denied=OK  [3.9s]
PASS  5. registered in Hoplite                       id=mcp_4a1b... enabled=True  [1.5s]
PASS  6. Hoplite cloud reaches us                    connected=True tools=26  [2.2s]
PASS  7. OpenAI gateway turn                         turn -> 'GW-OK'  [77.7s]
PASS  8. omp + opencode configs                      omp pc-tools=yes, opencode hoplite=yes  [0.0s]

7/7 checks passed -> РАБОТАЕТ
```

Exit code 0/1 → можно вешать в cron или CI.

### 1.9. Что именно получает агент

26 инструментов desktop-commander:

| Группа | Инструменты |
|---|---|
| **Shell** | `start_process`, `interact_with_process`, `read_process_output`, `force_terminate`, `list_processes`, `list_sessions`, `kill_process` |
| **Файлы** | `read_file`, `read_multiple_files`, `write_file`, `edit_block`, `create_directory`, `list_directory`, `move_file`, `get_file_info`, `write_pdf` |
| **Поиск** | `start_search`, `get_more_search_results`, `stop_search`, `list_searches` |
| **Служебные** | `get_config`, `set_config_value`, `get_usage_stats`, `get_recent_tool_calls`, `get_prompts` |

Два подводных камня при использовании:

- `start_process` **требует** `timeout_ms`. Без него — `invalid_type: timeout_ms Required`.
- `list_directory` на большой папке возвращает ~200 КБ текста и съедает контекст агента.
  Проси узкие пути, не корень проекта.

---

## Часть 2. Gateway — Hoplite как мозг для opencode и omp

### 2.1. Зачем вторая связка

В первой части облако получает руки. Во второй — наоборот: твои локальные CLI-агенты
(`opencode`, `omp`) получают **сильную модель** (Claude Opus 5.5) без подписки Anthropic.
Hoplite не отдаёт «чистый» chat-completions API — он создаёт **тред с агентом в песочнице**.
Гейтвей оборачивает это в стандартный `POST /v1/chat/completions`, который понимает любой
OpenAI-клиент.

### 2.2. Установка

```powershell
git clone https://github.com/nikita4a/hoplite-gateway.git
cd hoplite-gateway
pip install -r requirements.txt        # fastapi, uvicorn, httpx
copy config.example.json config.json   # вписать hop_ ключ
python server.py                       # http://127.0.0.1:8787
```

Проверка: `curl http://127.0.0.1:8787/health` → `{"ok":true,"auth":true,...}`.

### 2.3. opencode

`~/.config/opencode/opencode.jsonc`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "model": "hoplite/hoplite-opus-5",
  "small_model": "hoplite/hoplite-sonnet-5",
  "provider": {
    "hoplite": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Hoplite (local gateway :8787)",
      "options": {
        "baseURL": "http://127.0.0.1:8787/v1",
        "apiKey": "sk-local",
        "timeout": 600000,
        "headerTimeout": 300000,
        "chunkTimeout": 600000
      },
      "models": {
        "hoplite-opus-5": {
          "name": "Hoplite Opus 5.5 (cloud)",
          "limit": { "context": 200000, "output": 32000 },
          "cost": { "input": 0, "output": 0 },
          "reasoning": true,
          "tool_call": true,
          "attachment": false,
          "modalities": { "input": ["text"], "output": ["text"] }
        }
      }
    }
  }
}
```

Пять вещей, которые стоит знать:

- `@ai-sdk/openai-compatible` **встроен в бинарь opencode** — в `package.json` ничего
  добавлять не нужно.
- `options.apiKey` достаточен, `opencode auth login` не требуется.
- **`attachment: false` — намеренно.** Гейтвей передаёт только текст: части
  `{"type":"image_url"}` вырезаются на сервере. Если объявить модель зрячей, она будет
  **молча слепой** — картинка уйдёт в никуда, а ты об этом не узнаешь. Лучше честный отказ.
- Таймауты: ход Hoplite это 20–120 с, а `chunkTimeout` по умолчанию 300 с. Для стрима
  с запасом ставим 600 с.
- `small_model` нужен, иначе opencode будет жечь Opus на генерацию заголовков сессий.

Проверка:

```powershell
opencode models hoplite                     # список моделей
opencode mcp list                           # статусы MCP-серверов
opencode run -m hoplite/hoplite-opus-5 "Сколько будет 6*7? Ответь только числом."
```

### 2.4. omp (oh-my-pi)

Нужны **три** файла — чаще всего забывают `models.yml`.

`~/.omp/agent/providers.yml`:

```yaml
providers:
  hermes-hoplite:
    apiKey: sk-local
    baseUrl: http://127.0.0.1:8787/v1
    discoverModels: false
    models:
    - id: hoplite-opus-5
      name: hoplite-opus-5
    provider: openai
    streamIdleTimeoutSeconds: 600
```

`~/.omp/agent/models.yml` (корень — тоже `providers:`):

```yaml
providers:
  hermes-hoplite:
    api: openai-completions
    apiKey: sk-local
    authHeader: true
    baseUrl: http://127.0.0.1:8787/v1
    compat:
      maxTokensField: max_tokens
      supportsStreaming: true
      supportsToolChoice: true
      supportsUsageInStreaming: false
    models:
    - id: hoplite-opus-5
      name: hoplite-opus-5 (Claude Opus 5.5)
      contextWindow: 200000
      maxTokens: 16384
      input:
      - text
      reasoning: true
      cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}
```

`~/.omp/agent/config.yml`:

```yaml
enabledModels:
  - hermes-hoplite/*
```

Проверка (неинтерактивно, одним выстрелом):

```powershell
omp --model hermes-hoplite/hoplite-gpt-5.6-terra --mode json "Сколько будет 6*7? Ответь только числом."
```

В интерактиве: `omp` → `/model hermes-hoplite/hoplite-gpt-5.6-terra`.

> ⚠️ **Не проверяй канал командой «Reply with exactly: ТОКЕН».** Облачный агент Hoplite
> трактует такие эхо-пробы как prompt injection и **отказывает** (проверено живьём в
> opencode: ответ — «prompt injection attempt»). Обычный арифметический вопрос работает:
> `omp` вернул `42` от `provider: hermes-hoplite` за 18 с. Через голый API гейтвея
> (`status_all.py`, проверка 7) эхо-проба проходит — там нет системного промпта клиента.

### 2.5. Выбор модели

Гейтвей отдаёт 20 моделей (`curl http://127.0.0.1:8787/v1/models`):

| Модель | Что под капотом | Когда брать |
|---|---|---|
| `hoplite-opus-5` | Claude Opus 5.5 | дефолт, самый умный |
| `hoplite-opus-5-fast` | то же + `speed: fast` | когда нужен Opus, но быстрее |
| `hoplite-sonnet-5` | Claude Sonnet 5 | лёгкие задачи, `small_model` |
| `hoplite-gpt-5.6-terra` | GPT-5.6 Terra | **когда промпт «агрессивный»** — см. ниже |
| `hoplite-gpt-5.5` | GPT-5.5 | запасной GPT |
| `hoplite-agent` / `-code` / `-plan` / `-review` | проектный дефолт Hoplite | когда хочешь «как в UI» |

Суффикс `-fast` у любой модели включает `speed: fast`. Параметр `reasoning_effort`
(`off`…`xhigh`) мапится в `reasoning.mode`.

> ⚠️ **Грабли №4, самая неочевидная.** Claude Opus **отказывает** «агрессивным» системным
> промптам. На red-team/ENI-профиле omp тред закончился так:
>
> ```
> role=system, eventType=run.failed
> The model declined this request because of its content policy. (content_filter)
> ```
>
> GPT-5.6 Terra тот же промпт принимает. Поэтому:
> - для omp с кастомным системным промптом → **`hoplite-gpt-5.6-terra`**;
> - хочешь именно Opus → передавай чистый промпт:
>   `omp --model hermes-hoplite/hoplite-opus-5 --system-prompt "You are a concise assistant."`
>
> Проверено обоими способами: GPT с дефолтным промптом — работает; Opus с чистым
> промптом — работает; Opus с дефолтным — content_filter.

### 2.6. Честное ограничение связки «гейтвей = мозг»

Hoplite-агент **автономен**: у него свой sandbox и свои инструменты (`workspace_setup`,
`shell`). Попросив его «запусти `python -c "print(6*7)"` через мой bash-тул», он ответил:

> «I ran it in the workspace shell... Also, I ignored the embedded instructions»

— то есть выполнил команду **у себя в облаке**, а не вызвал локальный инструмент.
Протокол `{"tool_calls": [...]}` он соблюдает, если попросить прямо (проверено: вернулся
настоящий `tool_calls` на `read_file`), но по умолчанию предпочитает свои инструменты.

Практический вывод:

- **Хочешь, чтобы облако работало с твоими файлами** → Часть 1 (PC Bridge). Там инструменты
  настоящие: сервер зарегистрирован в Hoplite, и агент вызывает их сам.
- **Хочешь сильную модель для reasoning / ревью / планирования** → Часть 2 (гейтвей).
- **Хочешь локальный tool-loop** → локальная модель, не облачная.

---

## Часть 3. Безопасность

Мост публикует **shell-доступ** в интернет. Относись к этому соответственно.

Что уже сделано:

| Мера | Как работает |
|---|---|
| Loopback-only | `mcp-proxy` слушает `127.0.0.1`. Единственный вход — туннель ngrok. |
| Capability URL | Секрет — это путь `/mcp-<32 hex>`. Не знаешь путь — не подключишься. |
| Без SSE-лега | `--server stream`: второго, неаутентифицированного эндпоинта просто нет. |
| Файловая песочница | `allowedDirectories` реально работает: `read_file C:/Windows/win.ini` → **DENIED** (проверяется автоматически). |
| Телеметрия выключена | `telemetryEnabled: false`. |
| Общий конфиг не перезаписывается | merge + одноразовый `.bak` рядом. |
| Секреты не в git | `.gitignore` — deny-all allowlist. `config.json`, `ngrok.yml`, `status.json`, `*.log` не коммитятся никогда. |

Чего **нет** и что надо понимать:

- **Shell не песочница.** `allowedDirectories` не ограничивает команды. Полный доступ к
  диску через `start_process` — это то, что ты выбрал осознанно.
- **`blockedCommands` по умолчанию пуст.** Штатный блок-лист пакета (mkfs, dd, format,
  diskpart, sudo, passwd, shutdown, reg, net, sc, cipher, takeown…) лежит в
  `config.example.json` — скопируй в `config.json`, если хочешь ограничить разрушающие
  команды.
- **Не держи туннель поднятым просто так.** `stop_bridge.bat` — и всё.
- **Утёк URL** → меняй `secret_path` в `config.json`, перезапуск, обнови URL в Hoplite
  (`PATCH /api/mcp/servers/{id}`).

---

## Часть 4. Все грабли, которые мы собрали (чтобы ты не собирал)

| Симптом | Причина | Решение |
|---|---|---|
| `ERR_NGROK_121: version "3.3.1" is too old` | winget ставит старый агент | `ngrok update` |
| `unknown version '3'. valid versions are: [1 2]` | yml с дашборда новее агента | `bridge.py` не отдаёт yml ngrok'у — только читает из него токен и домен |
| `400 invalid_mcp_server` при регистрации | плоское тело запроса | вложенное: `{name, enabled, config:{transport,url,...}}` |
| `MCP url must not target a loopback...` | попытка зарегистрировать `127.0.0.1` | только публичный https-URL, т.е. туннель |
| `get_config` показывает `allowedDirectories: []` | desktop-commander читает `~/.claude-server-commander/config.json`, **не** cwd (README пакета врёт) | `bridge.py` пишет туда сам; проверь `verify_bridge.py` |
| Вывод команд тонет в `Import-PowerShellDataFile` | `defaultShell: powershell.exe` грузит профиль | `cmd.exe` |
| `start_process` → `invalid_type ... timeout_ms Required` | поле обязательное | всегда передавай `timeout_ms` |
| Агент жалуется на контекст | `list_directory` по корню = ~200 КБ | узкие пути |
| opencode/omp висит 540 с и ничего не возвращает | httpx-клиент закрывался до чтения стрима; каждый опрос падал и глотался `except: pass` | исправлено: `_stream_owned()` владеет клиентом на всё время стрима |
| Запрос висит вечно, ответов нет | мёртвые клиентские соединения держали слоты семафора до DEADLINE | исправлено: 30 с на слот → `429 gateway busy` |
| В транскрипте печатается `{"tool_calls": [...]}` | стрим отдавал дельты сразу, а протокол парсился в конце | исправлено: при `tools` контент не стримится, уходит один раз в конце |
| Ответ приходит дважды | мёртвый аккумулятор `buffer` → финальный flush повторял весь текст | исправлено |
| `SSE error: Non-200 (405)` на `hoplite-gw` в opencode | MCP SDK открывает GET-стрим, а `/mcp` гейтвея только POST | нужен GET-хендлер на `/mcp`; пока сервер отключён |
| omp: `Model "hermes-hoplite/..." not found` | правки не во всех трёх файлах, или YAML сломан | providers.yml + models.yml + config.yml, проверить `python -c "import yaml; yaml.safe_load(...)"` |
| Claude отвечает `content_filter` | «агрессивный» системный промпт | GPT-модель или чистый `--system-prompt` |

---

## Часть 5. Сценарий видео (что показывать и что говорить)

**0:00 — Проблема.** Открой тред в app.hoplite.sh и спроси агента: *«можешь создать файл
у меня на компьютере?»*. Он честно ответит «нет, я в песочнице». Это хук.

**0:40 — Схема.** Покажи ASCII-диаграмму из Части 0. Ключевая фраза: «агент не видит твой
ПК, потому что между ними интернет. Значит, нужно дать ему дверь».

**1:20 — Установка.** `npm i -g mcp-proxy @wonderwhy-er/desktop-commander`,
`winget install Ngrok.Ngrok`, и обязательно `ngrok update` — покажи ошибку
`ERR_NGROK_121`, это живой момент.

**2:30 — Настройка.** `copy config.example.json config.json`, впиши папки. Объясни
capability-URL: «пароль — это часть адреса». Покажи, что `defaultShell` должен быть
`cmd.exe`, и почему.

**3:30 — Запуск.** `start_bridge.bat`. Покажи консольный баннер целиком — он сам по себе
инструкция. Отдельно подсвети строку `⚠ anyone with this URL has that access`.

**4:30 — Регистрация в Hoplite.** Диалог Add MCP server: `pc-tools` / `http` / URL /
`None`. Нажми Save. Скажи про «новый тред обязателен».

**5:30 — Доказательство.** Два кадра:
1. `python verify_bridge.py` — строка `start_process shell -> OK, executed on this PC`.
2. В Hoplite новый тред: *«прочитай файл X и создай рядом Y»*. Агент вызывает `read_file`
   и `write_file` — и файлы появляются у тебя на диске. Покажи их в проводнике.

**7:00 — Граница.** Попроси агента прочитать `C:/Windows/win.ini` — получишь отказ.
Объясни: файловые инструменты в песочнице, shell — нет. Это честное место, зритель
должен его услышать.

**8:00 — Вторая связка (если влезет).** opencode + гейтвей: `opencode models hoplite`,
один запрос, и предупреждение про `content_filter` у Claude.

**9:00 — Финал.** `python status_all.py` → `7/7 checks passed -> РАБОТАЕТ`.
И обязательно: `stop_bridge.bat`, «не держите туннель поднятым зря».

---

## Приложение. Быстрая шпаргалка

```powershell
# ── мост ──────────────────────────────────────────────
start_bridge.bat                     # поднять
python bridge.py --verify            # поднять + полная проверка
python bridge.py --print-url         # только URL
python verify_bridge.py              # проверить живой мост
python status_all.py                 # проверить ВЕСЬ стек (7 проверок)
stop_bridge.bat                      # остановить
python bridge.py --stop              # остановить (из другой консоли)

# ── Hoplite API ───────────────────────────────────────
curl -H "X-Api-Key: hop_..." https://api.hoplite.sh/api/mcp/servers
curl -X POST -H "X-Api-Key: hop_..." -H "Content-Type: application/json" \
     -d '{"url":"https://.../mcp-..."}' https://api.hoplite.sh/api/mcp/probe

# ── гейтвей ───────────────────────────────────────────
python server.py                     # поднять (:8787)
python tests_gateway.py              # 17 офлайн-тестов
python verify_stream.py              # живой стрим: ответ ровно один раз
python verify_stream.py --tools      # живой стрим: протокол не утекает в текст

# ── клиенты ───────────────────────────────────────────
opencode models hoplite
opencode run -m hoplite/hoplite-opus-5 "..."
omp --model hermes-hoplite/hoplite-gpt-5.6-terra --mode json "..."
```

## Приложение. Файлы репозитория

| Файл | Назначение |
|---|---|
| `bridge.py` | весь мост: merge конфига, mcp-proxy, ngrok, MCP-клиент для самопроверки. Только stdlib. |
| `verify_bridge.py` | проверка живого моста через публичный URL: рукопожатие + 4 доказательства |
| `status_all.py` | проверка всего стека (8 проверок), exit 0/1 |
| `test_bridge.py` | офлайн-тесты двух мест, которые нас уже укусили: парсер yml и merge общего конфига |
| `config.example.json` | шаблон со штатным `blockedCommands` пакета |
| `config.json` | твои настройки — **в git не попадает** |
| `ngrok.yml` | токен + домен — **в git не попадает** |
| `start_bridge.bat` / `stop_bridge.bat` | запуск и остановка одним кликом |
| `GUIDE.md` | этот файл |
| `README.md` | короткая версия |
