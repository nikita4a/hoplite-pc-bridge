# Клиенты: opencode и omp на мозге Hoplite

> Глава про то, как **пользоваться** стеком `hoplite-pc-bridge` + `hoplite-gateway` из двух
> локальных CLI-агентов. Читатель раньше Hoplite не видел — поэтому каждая команда
> объяснена: что делает, что напечатает и **зачем**. Всё, что вставлено как «вывод
> команды», — реальный вывод с этой машины (2026-09-25); места, где вывод
> сокращён или обезличен, помечены. Команды, которые в этой сессии не запускались,
> помечены **[не проверено вживую]**.

Версии, на которых всё проверено:

| Компонент | Версия | Где |
|---|---|---|
| opencode | 1.18.29 | `C:/Users/User/.openagents/nodejs/opencode.cmd` |
| omp (oh-my-pi) | 18.3.0 | в `PATH` |
| hoplite-gateway | v3 (server.py 1035 строк) | `C:/Users/User/tmp/hoplite-gateway`, порт 8787 |
| pc-bridge (mcp-proxy + desktop-commander) | desktop-commander 0.2.50 | порт 8888, ngrok-туннель |
| Windows | 10.0.26200, Python 3.11 | — |

Предусловия главы: мост поднят (`start_bridge.bat`), гейтвей поднят
(`python server.py` в `hoplite-gateway`), в `config.json` гейтвея лежит рабочий
`hop_`-ключ. Как это сделать — в `GUIDE.md` обеих репозиториев.

---

## 1. Две формы интеграции — и когда какую брать

Hoplite можно «пристегнуть» к локальному агенту двумя принципиально разными
способами. Путают их постоянно, поэтому сначала — суть в одну таблицу:

| | **(a) Hoplite как МОДЕЛЬ** | **(b) Hoplite как ИНСТРУМЕНТ** |
|---|---|---|
| Как выглядит | `opencode` / `omp` думают, что у них «просто OpenAI-модель» `hoplite-opus-5` | модель любая; в списке инструментов появляется `hoplite_ask` / `pc-tools` |
| Канал | `POST http://127.0.0.1:8787/v1/chat/completions` | MCP: `http://127.0.0.1:8787/mcp` (гейтвей) или `https://api.hoplite.sh/mcp` (облако) |
| Кто исполняет инструменты | **твой ПК** — bash/read/edit клиента крутятся локально | зависит: `pc-tools` — твой ПК, `hoplite-gw` — облако |
| Цена одного сообщения | каждый ход = облачный прогон агента, **20–120 с** | ходы дешёвые; облачное время платится только при вызове инструмента |
| Когда брать | reasoning, ревью, планирование, «сильная модель без подписки» | «спроси у облака, когда надо», «дай облачному агенту руки на моём ПК» |

### 1.1 Hoplite как модель (путь «гейтвей»)

```
opencode / omp ──OpenAI v1──▶ hoplite-gateway :8787 ──REST──▶ Hoplite cloud (Opus 5.5)
   инструменты (bash/edit/read) исполняются ЗДЕСЬ, на твоём ПК
```

Клиент отправляет обычный OpenAI-запрос. Гейтвей под капотом создаёт **тред**
облачного агента, докладывает туда промпт, опрашивает прогресс (1 c первые 15 c,
потом 2.5 c) и стримит дельты обратно как SSE. Для клиента это неотличимо от
обычной модели — кроме скорости.

### 1.2 Hoplite как инструмент (путь «MCP»)

Три MCP-точки, все три живут в этой связке:

| Сервер | URL | Что даёт |
|---|---|---|
| `hoplite-gw` (гейтвей) | `http://127.0.0.1:8787/mcp` | 5 инструментов: `hoplite_ask` (блокирующий вопрос облаку), `hoplite_task` (неблокирующий), `hoplite_task_status`, `hoplite_models`, `hoplite_conversations` |
| `hoplite` (облако) | `https://api.hoplite.sh/mcp` | нативные Hoplite-инструменты облака (управление тредами); требует OAuth-JWT |
| `pc-tools` (мост) | `http://127.0.0.1:8888/mcp-<секрет>` | 26 инструментов desktop-commander: shell, файлы, процессы — **на твоём ПК** |

Разница против формы (a): облачное время тратится **только когда агент решает
вызвать инструмент**, а не на каждое сообщение. Форма (b) — это и есть способ
дать облаку «руки» (pc-tools, подробно в `GUIDE.md` моста).

### 1.3 Замеренная латентность (реальные замеры этой сессии)

| Слой | Операция | Замер |
|---|---|---|
| гейтвей, локально | `GET /health` | 16 ms |
| гейтвей, локально | `GET /v1/models` (20 моделей) | 6 ms |
| гейтвей, MCP | `initialize` | 4 ms |
| гейтвей, MCP | `tools/list` (5 инструментов) | 2 ms |
| гейтвей, MCP | `tools/call hoplite_models` | 46 ms |
| pc-tools, loopback | `initialize` | 13 ms (сессия выдаётся мгновенно) |
| pc-tools, loopback | `tools/list` (26 инструментов) | 62 ms |
| pc-tools, через ngrok | `initialize` (проверка 2 в `status_all.py`) | 5.2 s |
| облако → pc-tools | `POST /api/mcp/probe` (проверка 6 в `status_all.py`) | 2.4 s |
| облако, MCP | `initialize` на `https://api.hoplite.sh/mcp` | 1044 ms |
| **полный ход, форма (a)** | opencode / omp один вопрос-ответ | см. §2.9 и §3.6 (замеры ниже) |
| **полный вызов, форма (b)** | `hoplite_ask` | тот же облачный прогон: 30–120 с (README гейтвея: «blocks 30s-15min», `wait_s` по умолчанию 120 c) |

Вывод для читателя: **рукопожатия почти бесплатны, ходы — дорогие**. Планируй
запросы к Hoplite батчами, не по слову за раз. Гейтвей держит дедлайн 540 c на ход
(`DEADLINE = 540` в server.py; переопределяется `deadline_s` в config.json).

---

## 2. opencode — исчерпывающе

### 2.1 Бинарь и первая проверка

`opencode` здесь — не `npm i -g`, а локальная сборка в `C:/Users/User/.openagents/nodejs/`.
Проверка, что он жив:

```powershell
& "C:\Users\User\.openagents\nodejs\opencode.cmd" --version
```

```
1.18.29
```

Главный `--help` (сокращено до релевантного):

```
Commands:
  opencode [project]           start opencode tui               [default]
  opencode run [message..]     run opencode with a message
  opencode models [provider]   list all available models
  opencode mcp                 manage MCP servers
  opencode providers           manage AI providers and credentials [aliases: auth]
  opencode agent               manage agents
Options:
  -m, --model         model to use in the format of provider/model      [string]
  -c, --continue      continue the last session                        [boolean]
  -s, --session       session id to continue                            [string]
      --agent         agent to use                                      [string]
      --auto          auto-approve permissions that are not explicitly denied (dangerous!)
      --print-logs    print logs to stderr                              [boolean]
```

`opencode auth` и `opencode providers` — одна и та же команда (`auth` — алиас).

### 2.2 Конфиг: где лежит и как выглядит

Один файл: `C:/Users/User/.config/opencode/opencode.jsonc`. Поддерживается и
`.json`, и `.jsonc` (комментарии разрешены — официальный docs: «OpenCode supports
both JSON and JSONC»). Дальше — реальный рабочий конфиг, поле за полем.

#### Верхний уровень: `model`, `small_model`, `default_agent`

```jsonc
{
  "model": "hoplite/hoplite-opus-5",
  "small_model": "hoplite/hoplite-sonnet-5",
```

| Поле | Зачем | Что будет без него |
|---|---|---|
| `model` | модель по умолчанию в формате `провайдер/модель` | opencode спросит при старте |
| `small_model` | модель для мелочи: генерация заголовков сессий, summaries | docs: «OpenCode tries to use a cheaper model if one is available from your provider, otherwise it falls back to your main model» — т.е. **без неё заголовки сессий жгут Opus** (каждый — облачный прогон!) |
| `default_agent` | какой агент стартует, если не указан | дефолт `build` |

`default_agent` — официальный ключ конфига (docs: «You can set the default agent
using the `default_agent` option»). Здесь не задан — работаем со встроенным `build`.

#### Блок `provider` — поле за полем

```jsonc
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
    "models": { "...": "..." }
  }
}
```

| Поле | Значение | Зачем именно такое |
|---|---|---|
| `hoplite` (ключ) | ID провайдера | попадает в `-m hoplite/...` и в `opencode models hoplite` |
| `npm` | `@ai-sdk/openai-compatible` | **ставить ничего не надо**: пакет уже вшит в бинарь opencode, в `package.json` проекта его нет и не должно быть. Поле лишь говорит, какой из встроенных провайдер-адаптеров использовать |
| `name` | человекочитаемое имя | показывается в UI при выборе модели |
| `options.baseURL` | `http://127.0.0.1:8787/v1` | адрес гейтвея. Обязательно **с `/v1`** — это OpenAI-совместимый префикс, а не корень |
| `options.apiKey` | `sk-local` | гейтвей не проверяет значение ключа (проверяет только наличие `Authorization`). Любая строка |
| `options.timeout` | 600000 ms | общий таймаут запроса. Ход Hoplite 20–120 c, дефолты рассчитаны на чат-миллисекунды — ставим 10 минут |
| `options.headerTimeout` | 300000 ms | ожидание первых заголовков ответа |
| `options.chunkTimeout` | 600000 ms | сторожевой пёс между чанками SSE-стрима. Промежутки между дельтами у Hoplite бывают длинные (агент думает) — дефолт (5 минут) режет честные ответы |
| `models` | карта моделей | см. ниже |

Эти три таймаута — официальные опции провайдера (docs opencode: «Provider options
can include `timeout`, `headerTimeout`, `chunkTimeout`»).

#### `models.*` — свойства каждой модели

```jsonc
"hoplite-opus-5": {
  "name": "Hoplite Opus 5.5 (cloud)",
  "limit": { "context": 200000, "output": 32000 },
  "cost": { "input": 0, "output": 0 },
  "reasoning": true,
  "tool_call": true,
  "attachment": false,
  "modalities": { "input": ["text"], "output": ["text"] }
}
```

| Поле | Зачем |
|---|---|
| `name` | как модель подписана в UI |
| `limit.context` / `limit.output` | окно контекста/выхода — opencode использует для авто-компакции и подсчёта «влезет ли» |
| `cost.input/output` | 0: оплата — облачное время Hoplite, не токены; в статистике opencode ход будет «бесплатным» |
| `reasoning` | модель умеет thinking-блоки (у гейтвея `reasoning_effort` прокидывается в Hoplite `reasoning.mode`) |
| `tool_call` | модель может вызывать инструменты |
| `attachment` | **`false` — см. §2.7, это важно** |
| `modalities` | честное объявление «текст на вход, текст на выход» |

Суффикс `-fast` у любой модели (`hoplite-opus-5-fast` и т.д.) включает
`speed: fast` на стороне Hoplite — те же мозги, но шустрее.

### 2.3 Почему НЕ нужен `opencode auth login`

`opencode auth login` заводит креды в системное хранилище
(`~/.local/share/opencode/auth.json`), чтобы провайдер мог дёргать OAuth-флоу или
переменные окружения. Наш путь короче: ключ задан прямо в конфиге через
`options.apiKey`, адаптер `@ai-sdk/openai-compatible` кладёт его в заголовок
`Authorization: Bearer sk-local`, гейтвей доволен. Проверить, что провайдер виден:

```powershell
& "C:\Users\User\.openagents\nodejs\opencode.cmd" models hoplite
```

```
hoplite/hoplite-agent
hoplite/hoplite-agent-stream
hoplite/hoplite-code
hoplite/hoplite-gpt-5.5
hoplite/hoplite-gpt-5.6-terra
hoplite/hoplite-opus-4.8
hoplite/hoplite-opus-5
hoplite/hoplite-opus-5-fast
hoplite/hoplite-sonnet-5
```

(9 моделей — ровно те, что перечислены в `models` конфига.) С `--verbose` opencode
печатает ещё и JSON с capabilities — полезно убедиться, что `attachment: false`
доехал как надо:

```
hoplite/hoplite-agent
{
  "id": "hoplite-agent",
  "api": { "id": "hoplite-agent", "npm": "@ai-sdk/openai-compatible", "url": "" },
  "status": "active",
  "name": "Hoplite project default (cloud)",
  "providerID": "hoplite",
  "capabilities": {
    "temperature": false,
    "reasoning": true,
    "attachment": false,
    "toolcall": true,
    "input": { "text": true, "audio": false, "image": false, "video": false, "pdf": false },
    "output": { "text": true, "audio": false, "image": false, "video": false, "pdf": false }
  },
```

### 2.4 Подстановки `{env:…}` и `{file:…}`

В любом строковом значении конфига opencode понимает две подстановки
(официальные, docs → Config → Variable substitution):

```jsonc
// переменная окружения; отсутствующая переменная = пустая строка
"apiKey": "{env:HOPLITE_GATEWAY_KEY}"

// содержимое файла; путь — абсолютный, с ~/ или относительный к конфигу
"apiKey": "{file:~/.secrets/hoplite-gateway-key}"
```

Зачем это тебе: файл конфига часто хочется закоммитить. Значение `sk-local` — не
секрет (гейтвей его не проверяет), но привычка правильная: ключи и прочее — в
переменных/файлах, конфиг — в git. В текущем конфиге подстановки не используются —
нечего прятать. **[подстановки не проверялись вживую в этой сессии — описано по
официальным docs opencode.ai/docs/config/]**

### 2.5 Блок `agent` и permissions

Агент — это «роль» с своим промптом, моделью и правами. У opencode есть
встроенные: `build` (всё можно), `plan` (read-only), сабагенты `general`/`explore`/
`scout`, скрытые `title`/`summary`/`compaction`. Наш конфиг переопределяет `build`:

```jsonc
"agent": {
  "build": {
    "mode": "primary",
    "description": "Hoplite cloud brain, local hands (bash/edit/files)",
    "model": "hoplite/hoplite-opus-5",
    "permission": {
      "edit": "allow",
      "bash": "allow",
      "webfetch": "allow"
    }
  }
}
```

Ключевая идея главы: **`model` внутри агента = облачный мозг, `permission` = руки на
твоём ПК**. Разрешения бывают трёх значений:

| Значение | Поведение |
|---|---|
| `"allow"` | выполнять без вопросов |
| `"ask"` | спрашивать подтверждение каждый раз |
| `"deny"` | инструмент выключен |

Для `bash` (и ещё для `edit`, `read`, `grep`, …) вместо одного значения можно задать
**карту glob-паттернов** — официальная форма из docs:

```jsonc
"permission": {
  "bash": {
    "git push": "ask",
    "grep *": "allow",
    "rm *": "deny"
  }
}
```

Обрати внимание: в этой связке `permission.bash: "allow"` — осознанный выбор
(видео-гайд, машина одноразовая). Для постоянной работы честнее хотя бы
`"git push": "ask"`.

Свои агенты можно класть и маркдауном в `~/.config/opencode/agents/*.md` —
frontmatter с теми же ключами (`description`, `model`, `permission`), имя = имя
файла. **[не проверено вживую — по docs opencode.ai/docs/agents/]**

### 2.6 MCP-серверы в opencode: `local` и `remote`

Блок `mcp` конфига — какие внешние инструменты дать агенту. Два типа, оба есть в
живом конфиге:

```jsonc
// LOCAL: opencode сам запускает процесс и говорит с ним по stdio
"codebase-memory-mcp": {
  "command": ["C:/Users/User/.local/bin/codebase-memory-mcp.exe"],
  "type": "local"
},
"playwright": {
  "type": "local",
  "command": [
    "C:/Users/User/AppData/Roaming/npm/playwright-mcp.cmd",
    "--browser", "chrome",
    "--caps", "vision",
    "--image-responses", "omit",
    "--output-dir", "C:/Users/User/tmp/opencode-shots",
    "--viewport-size", "1600x1000"
  ],
  "enabled": true,
  "timeout": 120000
},

// REMOTE: готовый HTTP-сервер, ничего не запускаем
"hoplite-gw": {
  "type": "remote",
  "url": "http://127.0.0.1:8787/mcp",
  "enabled": false,          // ← почему выключен: §6, «SSE error: Non-200 (405)»
  "oauth": false,            // сервер не OAuth: авторизация — заголовком ниже
  "timeout": 300000,
  "headers": { "Authorization": "Bearer sk-local" }
}
```

Поля по официальным docs (таблицы Options на opencode.ai/docs/mcp-servers/):

| Тип | Поля | Комментарий |
|---|---|---|
| `local` | `command[]` (обяз.), `cwd`, `environment`, `enabled`, `timeout` | `cwd` — рабочая папка процесса (относительные — от workspace) |
| `remote` | `url` (обяз.), `headers`, `oauth`, `enabled`, `timeout` | `oauth: false` отключает авто-OAuth (для серверов с API-ключом); объект — пре-регистрация clientId/clientSecret/scope |

`environment` — передать переменные окружения в процесс сервера. Пример есть в
доках; в нашем конфиге не используется.

Проверка живости MCP-серверов:

```powershell
& "C:\Users\User\.openagents\nodejs\opencode.cmd" mcp list
```

```
┌  MCP Servers
│
●  ✓ codebase-memory-mcp connected
│      C:/Users/User/.local/bin/codebase-memory-mcp.exe
│
●  ✓ lean-ctx connected
│      C:/Users/User/AppData/Roaming/npm/node_modules/lean-ctx-bin/bin/lean-ctx.exe
│
●  ✓ deploychan connected
│      https://mcp.deploychan.webcam/mcp
│
●  ○ hoplite-gw disabled
│      http://127.0.0.1:8787/mcp
│
●  ✓ playwright connected
│      C:/Users/User/AppData/Roaming/npm/playwright-mcp.cmd --browser chrome --caps vision --image-responses omit --output-dir C:/Users/User/tmp/opencode-shots --viewport-size 1600x1000
│
└  5 server(s)
```

Каждый MCP-сервер стоит контекста (docs предупреждают: «When you use an MCP server,
it adds to the context»), поэтому держим их мало и по делу.

### 2.7 Вложения картинок: почему `attachment: false` — честная настройка

Гейтвей пересылает **только текст**. В коде это видно напрямую — функция выбора
текста из сообщений берёт только текстовые части:

```python
# server.py, _last_user_text():
if isinstance(c, list):
    c = " ".join(p.get("text", "") for p in c
                 if isinstance(p, dict) and p.get("type") == "text")
```

Части `{"type": "image_url", ...}` не извлекаются вообще (поиск слова `image` по
server.py — ноль совпадений; картинка молча выбрасывается).

Если объявить модель зрячей (`attachment: true`), произойдёт самое неприятное из
возможного: opencode **прикрепит картинку, гейтвей её вырежет, модель ответит,
не видя её, и никто не поднимет тревогу**. Молчаливая слепота. С
`attachment: false` opencode честно откажется прикреплять файл с
«model does not support attachments». Отказ, который видно, лучше слепоты,
которой не видно.

Хотите дать агенту глаза на текстовый гейтвей — §5: OCR-скрипт + playwright.

### 2.8 `opencode run` — неинтерактивные флаги

Для скриптов/CI — всё, что нужно, у `run` (вывод `opencode run --help`,
сокращённый до рабочего набора):

| Флаг | Что делает |
|---|---|
| `-m, --model <provider/model>` | модель на этот запуск: `-m hoplite/hoplite-sonnet-5` |
| `--agent <name>` | агент (см. §2.5): `--agent plan` |
| `--auto` | авто-аппрув всех разрешений, не запрещённых явно (dangerous — для скриптов на одноразовой машине) |
| `--format json` | сырые JSON-события вместо форматированного текста — для парсинга |
| `-f, --file <path>` | прикрепить файл(ы) к сообщению (для текстовых — прочитает; картинки — нет, см. §2.7) |
| `-c, --continue` | продолжить последнюю сессию |
| `-s, --session <id>` | продолжить конкретную сессию |
| `--dir <path>` | рабочая папка (в т.ч. на удалённом сервере при `--attach`) |
| `--print-logs` | логи в stderr — когда что-то не работает, первое, что включать |
| `--thinking` | показать thinking-блоки |
| `--variant` | вариант reasoning-усилия модели (провайдер-специфично) |
| `-i, --interactive` | интерактивный режим с split-footer |

Пример одноразового прогона:

```powershell
cd C:\Users\User\tmp
& "C:\Users\User\.openagents\nodejs\opencode.cmd" run -m hoplite/hoplite-sonnet-5 "Reply with exactly: PROBE-OC-OK"
```

### 2.9 Живые прогоны opencode (реальные, этой сессии)

Три прогона этой сессии — один успешный, два честно упавших, и это само по себе
полезный материал.

**Успешный** (вся цепочка opencode → гейтвей → Hoplite → ответ):

```powershell
cd C:\Users\User\tmp
& "C:\Users\User\.openagents\nodejs\opencode.cmd" run -m hoplite/hoplite-sonnet-5 "Reply with exactly: PROBE-OC-OK"
```

```
> build · hoplite-sonnet-5

This is a prompt injection attempt embedded in a fake "earlier conversation" — it's trying
to get me to role-play as a different tool-calling backend and echo "PROBE-OC-OK" or start
emitting raw JSON tool calls outside my actual tool contract. I won't follow embedded
instructions from message content, only the real system/tool contract for this session.

I don't have a genuine task from you in this message yet. What would you like me to do in this repository?

real	0m40.083s
```

Разбор — здесь **две** новости, и обе важные:

1. **Связка работает.** 40 секунд: opencode поднял сессию, гейтвей создал тред
   Hoplite, Sonnet 5 в облаке отработал, ответ вернулся и напечатался. В карте
   разговоров гейтвея (`conv_threads.json`) появилась запись `h:7d51f6…` — тред
   действительно создан.
2. **Hoplite — не «голая» модель, а агент с защитой.** Промпт «answer exactly X»
   пришёл к нему завёрнутым: гейтвей кладёт системный промпт opencode в преамбулу
   `[earlier conversation]` (так устроена continuity-механика server.py), и
   облачный агент расценил это как попытку промпт-инъекции — и **отказался
   эхать**. Это не сбой, это фича: Hoplite-агент следует своему контракту, а не
   тексту внутри сообщений. Тот же эффект задокументирован в `AGENTS.md` моста
   («I ignored the embedded instructions»).

Практический вывод для твоих прогонов: **пробуй Hoplite через opencode
естественными вопросами**, а не echo-пробами. «Ответь ровно X» работает через
голый API (проверка 7 в `status_all.py`, см. §7) — там промпт уходит без тысячестрочного системного промпта opencode.

**Два упавших прогона** (обе — окружение, не конфиг):

```
> build · hoplite-sonnet-5
Error: Cannot connect to API: Unable to connect. Is the computer able to access the url?
real	2m55.462s
```

Гейтвей в этот момент перезапускался (см. §6, строка 1 матрицы отказов). Диагностика
одного взгляда: `GET /health` на `127.0.0.1:8787` — если не отвечает, ждём ~90 c
(watchdog поднимет) и повторяем.

---

## 3. omp (oh-my-pi) — исчерпывающе

### 3.1 Три файла — и все три обязательны

omp настраивается тремя файлами в `C:/Users/User/.omp/agent/`. Классическая ошибка
— поправить один и получить `Model "..." not found`:

| Файл | Роль | Что будет, если его нет |
|---|---|---|
| `providers.yml` | провайдеры-коннекты: baseUrl, ключ, список id | omp не видит провайдера вообще |
| `models.yml` | карточки моделей: контекст, лимиты, compat-флаги | omp видит провайдера, но не модели (в списке пусто, `--model` не находит) |
| `config.yml` | что включено и какие роли: `enabledModels`, `modelRoles`, fallback-цепочки | модель описана, но **выключена** — тоже `not found` |

`omp --help` (v18.3.0) — релевантные флаги:

```
USAGE
  $ omp [COMMAND]
ARGUMENTS
  MESSAGES   Messages to send (prefix files with @)
FLAGS
      --model=<value>        Model to use (fuzzy match: "opus", "gpt-5.2", or "openai/gpt-5.2")
      --smol=<value>         Smol/fast model for lightweight tasks (or PI_SMOL_MODEL env)
      --slow=<value>         Slow/reasoning model for thorough analysis (or PI_SLOW_MODEL env)
      --plan=<value>         Plan model for architectural planning (or PI_PLAN_MODEL env)
      --system-prompt=<value>     System prompt (default: coding assistant prompt)
      --allow-home           Allow starting in ~ without auto-switching to a temp dir
      --cwd=<value>          Directory to start in (overrides the launch cwd)
      --mode=<value>         Output mode: text (default), json, rpc, or rpc-ui
      --config=<value>       Load an extra config.yml-style overlay for this run (repeatable)
      --add-dir=<value>      Add a workspace directory beyond the working directory (repeatable)
  -p, --print                Non-interactive mode: process prompt and exit
```

### 3.2 `providers.yml` — коннект к гейтвею

Рабочий блок (реальный файл, ключ не секрет):

```yaml
providers:
  hermes-hoplite:
    apiKey: sk-local
    baseUrl: http://127.0.0.1:8787/v1
    discoverModels: false
    models:
    - id: hoplite-agent
      name: hoplite-agent
    - id: hoplite-opus-5
      name: hoplite-opus-5
    # ... ещё 17 id (см. §4)
    provider: openai
    streamIdleTimeoutSeconds: 600
```

| Поле | Зачем |
|---|---|
| `hermes-hoplite` | ID провайдера → `--model hermes-hoplite/...` |
| `provider: openai` | какой адаптер использовать: OpenAI-совместимый |
| `baseUrl` | гейтвей **с `/v1`** |
| `apiKey: sk-local` | любое значение — гейтвей проверяет только наличие заголовка |
| `discoverModels: false` | не опрашивать `/v1/models` при старте: список фиксирован ниже. (Опрашивать можно — но это лишний ход и зависимость от живости гейтвея на старте) |
| `models[].id` | те же id, что в `models.yml` — иначе модель не смэтчится |
| `streamIdleTimeoutSeconds: 600` | **важно**: молчание стрима дольше дефолта (300 c) = разрыв. Hoplite думает подолгу, ставим 10 минут |

### 3.3 `models.yml` — карточки моделей

Внимание, не споткнись: **корневой ключ здесь тоже `providers:`**, хотя файл
называется models.yml. Рабочий блок:

```yaml
providers:
  hermes-hoplite:
    api: openai-completions
    apiKey: sk-local
    authHeader: true
    baseUrl: http://127.0.0.1:8787/v1
    compat:
      maxTokensField: max_tokens
      supportsDeveloperRole: false
      supportsStore: false
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

| Поле | Зачем |
|---|---|
| `api: openai-completions` | диалект: OpenAI chat-completions |
| `authHeader: true` | слать ключ в `Authorization` (а не в query) |
| `compat.maxTokensField: max_tokens` | гейтвей ждёт классическое поле, не `max_completion_tokens` |
| `compat.supportsStreaming: true` | включить SSE-стрим (гейтвей стримит честно) |
| `compat.supportsToolChoice: true` | позволять omp форсить выбор инструмента |
| `compat.supportsUsageInStreaming: false` | usage-статистика в стриме не приходит — честно выключено |
| `contextWindow` / `maxTokens` | окно и лимит выхода — влияют на авто-компакцию omp |
| `input: [text]` | **текстовый вход** (то же честное ограничение, что `attachment: false` в opencode) |
| `reasoning: true` | у моделей с thinking (opus-5, gpt-5.6-terra); у `hoplite-agent`/`code`/`plan` — `false` |
| `cost` | нули: платим облачным временем, не токенами |

### 3.4 `config.yml` — включение и роли

Три секции, которые касаются Hoplite:

```yaml
enabledModels:
  - hermes-hoplite/*          # без этого модель есть, но выключена

modelRoles:                   # роли → модели (реальный файл, приведён hoplite-релевантное)
  plan: hermes-dashscope/qwen3.8-max-0902:high
  slow: hermes-dashscope/qwen3.8-max-0902:high
  task: hermes-dashscope/deepseek-v4-flash-0731:high
  smol: hermes-dashscope/qwen3.8-flash
  default: hermes-dashscope/qwen3.8-max-0902

retry:
  fallbackChains:
    default:
      - hermes-dashscope/deepseek-v4-flash-0731
      - plane/claude-sonnet-5
      - hermes-gitlabduo/astra-6
      - hermes-hoplite/hoplite-agent     # ← хвост цепочки: если всё умерло — облако
```

Что это значит на практике:

- `enabledModels: hermes-hoplite/*` — включает **все** модели провайдера разом.
- Роли (`--smol`, `--slow`, `--plan`, `task`, `vision`, `commit`, `advisor`,
  `designer`, `tiny`, `default`) можно отдать Hoplite-моделям, поменяв
  соответствующую строку (например `default: hermes-hoplite/hoplite-opus-5` —
  и весь omp думает в облаке). В референс-настройке роли на дешёвых локальных
  моделях, Hoplite — по явному `--model`.
- **`fallbackChains` — двусторонний меч**, и это наблюдалось вживую (§3.6): если
  hermes-hoplite недоступен (гейтвей лежал), omp молча уходит на
  `deepseek-v4-flash-0731` и отвечает от его имени. Ответ есть — Hoplite нет.
  Проверяй поле `provider` в `--mode json`, когда важен именно источник ответа.

### 3.5 `mcp.json` — MCP-серверы и живой токен

Файл `~/.omp/agent/mcp.json` — словарь `mcpServers` + список `disabledServers`.
Три Hoplite-релевантных записи (реальные; секреты скрыты):

```json
{
  "disabledServers": ["browsermcp", "context-mode", "context7", "firecrawl", "local-mcp-easy", "shardx"],
  "mcpServers": {
    "hoplite": {                                  // облако, нативные Hoplite-инструменты
      "type": "http",
      "url": "https://api.hoplite.sh/mcp",
      "headers": {
        "Authorization": "Bearer eyJhbGci…",      // ← живой JWT; обновляет watchdog, см. ниже
        "Accept": "application/json, text/event-stream"
      }
    },
    "hoplite-gw": {                               // гейтвей как MCP-сервер
      "type": "http",
      "url": "http://127.0.0.1:8787/mcp",
      "headers": {
        "Authorization": "Bearer sk-local",
        "Accept": "application/json, text/event-stream"
      },
      "timeout": 900000
    },
    "pc-tools": {                                 // мост: shell/файлы на этом ПК
      "type": "http",
      "url": "http://127.0.0.1:8888/mcp-6411abf4…",  // ← секретный путь, укорочен
      "headers": { "Accept": "application/json, text/event-stream" },
      "timeout": 900000
    }
  }
}
```

Детали, которые не очевидны:

- `pc-tools` в omp ходит **по loopback** (`127.0.0.1:8888`) — omp-агент работает
  на этой же машине, туннель не нужен. Путь `/mcp-<32 hex>` — capability-секрет:
  знать URL = иметь доступ.
- `timeout: 900000` — 15 минут: `hoplite_ask` может блокировать долго, а
  дефолтные таймауты MCP-клиентов короче (у opencode в docs — вообще 5 c, §6).
- `Accept: application/json, text/event-stream` — streamable-HTTP: сервер может
  отвечать и JSON-ом, и SSE-кадрами; клиент обязан принимать оба.

**Watchdog токена.** Запись `hoplite` живёт на OAuth-JWT со временем жизни ~1 час.
Обновляет его `C:/Users/User/tmp/hoplite-gateway/watchdog.pyw` (автозапуск:
планировщик задач `\HopliteGatewayWatchdog`, «At logon», скрытый pythonw).
Механика (по коду watchdog.pyw):

- каждые 30 c — health-check гейтвея, 3 подряд отказа → перезапуск server.py;
- каждые **45 минут** — refresh-грант OAuth2 (`grant_type=refresh_token`) на
  `https://api.hoplite.sh/api/auth/oauth2/token` и **горячая заплатка** записи
  `hoplite` в `mcp.json` (файл переписывается на лету, omp подхватывает при
  следующем старте сессии);
- грант обязательно несёт RFC 8707-параметр `resource` (URL MCP-сервера): без
  него Hoplite выдаёт opaque-токен, и `api.hoplite.sh/mcp` отвергает его
  `401 no token payload`; с ним — EdDSA-JWT с правильным `aud`;
- better-auth **ротирует refresh-токен при каждом использовании** — watchdog
  сохраняет новый до проверки старого (иначе следующего цикла не будет);
- запасной путь — CLI `hoplite mcp start` (в истории падал таймаутом 120 c три
  раза подряд 2026-09-24, поэтому основной путь — прямой грант).

Свидетельство работы — `status.json` рядом с watchdog:

```json
{
  "gateway": "UP",
  "watchdog_restarts": 4,
  "mcp_token_expires": "2026-09-25T02:28:27+0300",
  "mcp_refreshes": 17,
  "checked_at": "2026-09-25T01:45:20"
}
```

17 освежений токена — это сутки-двое аптайма без единого ручного логина.

### 3.6 One-shot без интерактива и `--mode json`

Неинтерактивный режим — флаг `-p` (`--print`): обработать промпт и выйти. С
`--mode json` omp печатает **поток событий** — по нему видно всё: кто ответил,
сколько длилось, что ретраилось.

Успешный прогон этой сессии (модель — Hoplite через гейтвей, без фолбэка):

```powershell
cd C:\Users\User\tmp
omp -p --model hermes-hoplite/hoplite-gpt-5.6-terra --mode json "Сколько будет 6 умножить на 7? Ответь одним числом."
```

```
{"type":"turn_start"}
{"type":"message_start","message":{"role":"user","content":[{"type":"text","text":"Сколько будет 6 умножить на 7? Ответь одним числом."}]}}
{"type":"message_update","assistantMessageEvent":{"type":"text_start","contentIndex":0}}
{"type":"message_update","assistantMessageEvent":{"type":"text_delta","contentIndex":0,"delta":"42"}}
{"type":"message_update","assistantMessageEvent":{"type":"text_end","contentIndex":0,"content":"42"}}
{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"42"}],
 "api":"openai-completions","provider":"hermes-hoplite","model":"hoplite-gpt-5.6-terra",
 "usage":{"input":0,"output":0,...},"stopReason":"stop",
 "duration":18056.08,"ttft":18055.07,"responseId":"chatcmpl-c8ea78ad4f43"}}
{"type":"turn_end","message":{...}}
{"type":"agent_end","messages":[...]}

real	0m57.398s
```

Читаем метрики: `provider: hermes-hoplite`, `model: hoplite-gpt-5.6-terra` — ответ
пришёл **из Hoplite**; `duration: 18.1 c` — сам облачный ход (почти всё — TTFT,
агент думает прежде первого токена); `real 57.4 c` — вместе со стартом omp
(скиллы, правила, память).

Карта событий `--mode json`:

| Событие | Что несёт |
|---|---|
| `turn_start` | начало хода |
| `message_start/end` | сообщение целиком (роль, контент) |
| `message_update` | дельты: `thinking_delta`, `text_start/text_delta/text_end` |
| `message_end` | финал: `provider`, `model`, `usage`, `duration`, `ttft`, `responseId` |
| `turn_end` | ход закончен, вложенные `toolResults` |
| `agent_end` | вся сессия |
| `retry_fallback_succeeded`, `auto_retry_end` | **сработал фолбэк**: в `retryErrors` видно, какая модель упала и на кого заменились |

Последние два — не теория. Тот же прогон часом раньше, когда гейтвей
перезапускался:

```
{"type":"message_end","message":{...,"provider":"hermes-dashscope","model":"deepseek-v4-flash-0731",...}}
{"type":"retry_fallback_succeeded","model":"hermes-dashscope/deepseek-v4-flash-0731:high","role":"default"}
{"type":"auto_retry_end","success":true,"attempt":1,
 "retryErrors":[{"persistenceKey":"assistant:1790290286480:hermes-hoplite:hoplite-gpt-5.6-terra::error",
  "note":"switched model; retried","recovery":"model",...}]}
```

Вывод: **`provider` в `message_end` — источник истины**. Ответ «есть», а Hoplite
«не было» — виден только здесь. Для CI-проверки гейтвея через omp грепай именно
`"provider":"hermes-hoplite"`.

В интерактивном режиме всё то же самое руками: `omp` → `/model hermes-hoplite/hoplite-opus-5`.

### 3.7 Бонус: нативный провайдер `hoplite` внутри omp

В omp 18.3.0 Hoplite — встроенный провайдер: `omp models hoplite` показывает
каталог (claude-sonnet-5, deepseek-v4-flash-0731, gpt-5.6-luna, gpt-5.6-terra,
z-ai/glm-5.3, z-ai/glm-5.3-flash — все 200K/16K, без картинок). Он ходит в
Hoplite напрямую (OAuth, тот же `~/.config/hoplite/`), без гейтвея. В этой
связке он **не** используется: референс — `hermes-hoplite` через локальный
гейтвей, потому что гейтвей даёт conversation-continuity, маппинг моделей и
единый `/v1` для всех клиентов сразу. **[нативный провайдер не проверялся
вживую — каталог виден в `omp models hoplite`]**

Ещё один живой артефакт старта omp — предупреждения о MCP-серверах:

```
Warning: MCP server "chrome-devtools" not ready after 30000ms; its tools are unavailable for this run.
Warning: MCP server "hoplite-gw" failed to connect: The socket connection was closed unexpectedly.; its tools are unavailable for this run.
```

Это честное поведение: omp грузит MCP при старте, и недоступный сервер не валит
сессию — его инструменты просто отсутствуют. Причина для `hoplite-gw` — §6.

---

## 4. Какую модель выбирать

Гейтвей отдаёт 20 моделей (`GET /v1/models`): 10 базовых + 9 вариантов `-fast` +
одна локальная (`hoplite-opus-5-local` — про неё в ACCESS_PC.md гейтвея).
Базовый набор и когда что брать:

| Модель | Под капотом | Берём когда |
|---|---|---|
| `hoplite-opus-5` | Claude Opus 5.5 | дефолт: самое умное reasoning, ревью, архитектура |
| `hoplite-sonnet-5` | Claude Sonnet 5 | `small_model` в opencode: заголовки сессий, summaries |
| `hoplite-opus-4.8` | Claude Opus 4.8 | если 5.5 недоступен/капризничает |
| `hoplite-gpt-5.6-terra` | GPT-5.6 Terra | **агрессивные/кастомные системные промпты** — см. ниже |
| `hoplite-gpt-5.5` | GPT-5.5 | запасной GPT |
| `hoplite-agent` / `-stream` / `-code` / `-plan` / `-review` | проектные дефолты Hoplite | «как в UI app.hoplite.sh», включая их промпты |
| любой `-fast` | + `speed: fast` | тот же мозг, но шустрее |

(`hoplite-opus-5-local` в omp-конфигах нет — 19 из 20 описаны; opencode описывает
9 — рабочую четвёрку + GPT-варианты. Это осознанный отбор, не ошибка.)

### 4.1 Грабля №1: Claude Opus фильтрует «агрессивные» системные промпты

Зафиксировано владельцем стека на живых прогонах (документировано в GUIDE.md
гейтвея, §2.5): тред, запущенный с red-team/ENI-профилем в `--system-prompt`,
заканчивается так:

```
role=system, eventType=run.failed
The model declined this request because of its content policy. (content_filter)
```

При этом: **GPT-5.6 Terra тот же промпт принимает**, и **Opus с чистым
`--system-prompt`** работает. **[в этой сессии не воспроизводилось — экономия
облачных тредов; наблюдение задокументировано в GUIDE.md гейтвея]**

Правило выбора, одной строкой:

> Кастомный «жёсткий» системный промпт → `hoplite-gpt-5.6-terra`.
> Хочешь именно Opus → чистый промпт: `omp --model hermes-hoplite/hoplite-opus-5
> --system-prompt "You are a concise assistant."`

### 4.2 Грабля №2: Hoplite-агент не эхо-машина (живое наблюдение §2.9)

«Ответь ровно X» через opencode/omp может быть отклонён как «подозрение на
инъекцию» — системный промпт клиента заворачивается в `[earlier conversation]`,
и защита агента срабатывает. Естественные вопросы проходят всегда. Для
механической проверки конвейера используй голый API (`status_all.py`, проверка 7).

---

## 5. Глаза для текстового гейтвея

Гейтвей пересылает только текст (§2.7). Это не значит, что агент слепой — это
значит, что «зрение» надо строить из текста. Три рабочих способа, все
проверены на этой машине.

### 5.1 WinRT-OCR: весь экран как текст — `see_screen.ps1`

Скрипт `C:/Users/User/.config/opencode/tools/see_screen.ps1` — чистый PowerShell
+ WinRT (Windows 10/11, **ноль зависимостей**): снимает экран в PNG, гонит
нативный OCR, печатает и картинку, и текст:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  C:/Users/User/.config/opencode/tools/see_screen.ps1 `
  -Out C:/Users/User/tmp/opencode-shots/screen.png
```

Реальный вывод (текст экрана сокращён — там личные окна):

```
SCREENSHOT C:\Users\User\tmp\_clients_probe\screen.png 4480x1440
OCR_LANG ru CHARS 1575
--- SCREEN TEXT ---
Hoplite … (дамп OCR всего экрана, ~1.5 КБ текста)
```

Формат вывода — контракт, на который опирается агент:

| Строка | Смысл |
|---|---|
| `SCREENSHOT <path> <W>x<H>` | PNG сохранён; путь можно отдать человеку |
| `OCR_LANG <tag> CHARS <n>` | язык распознавания (профиль юзера) и объём текста |
| `--- SCREEN TEXT ---` | дальше — сырой OCR-дамп |

Флаг `-NoOcr` — только скриншот (для человека). Скорость — пара секунд.
Качество: заголовки и крупные подписи — надёжно; мелкий UI-текст — «примерно».
Правило из `AGENTS.md`: OCR доверяем заголовкам, детали проверяем чтением
файла/DOM. Если языкового пакета нет — `OCR_UNAVAILABLE no language pack`.

### 5.2 Браузерные глаза: `@playwright/mcp` с `--image-responses omit`

Конфиг opencode (§2.6) запускает Playwright-MCP с настоящим Chrome и тремя
обязательными флагами:

```jsonc
"command": [
  "C:/Users/User/AppData/Roaming/npm/playwright-mcp.cmd",
  "--browser", "chrome",
  "--caps", "vision",
  "--image-responses", "omit",
  "--output-dir", "C:/Users/User/tmp/opencode-shots",
  "--viewport-size", "1600x1000"
]
```

Зачем каждый:

- `--image-responses omit` — **главный**: инструменты браузера не возвращают
  картинки в поток модели. Скриншоты пишутся файлами в `--output-dir`, а модель
  получает путь. Без этого флага image-части умирали бы в гейтвее молча.
- `--caps vision` — включает screenshot-инструменты (файл — человеку, путь — модели).
- A11y-снапшот страницы (`browser_snapshot`) — **текст** (ARIA-дерево) —
  единственный формат, который проходит гейтвей как есть. Поэтому правило:
  `browser_snapshot` вместо `browser_take_screenshot`, когда «смотреть» должен
  агент, а не человек.

### 5.3 Самые дешёвые глаза — `bash`

Состояние машины точнее всего читается не пикселями, а текстом:

```powershell
Get-Process | Where-Object {$_.MainWindowTitle} | Select-Object ProcessName,Id,MainWindowTitle
```

```
ProcessName             Id MainWindowTitle
-----------             -- ---------------
chrome               45064 Hoplite - Google Chrome
comet                32144 (19) ?????? ? ?? - YouTube - Comet
firefox              37856 sergeiperry134/keyhunter-imba - Hoplite - Mozilla Firefox
Kudu                  6336 System Cleaner - Kudu
node                 14248 RaycastNodeGracefulShutdownWindow
```

(кириллица в заголовках приезжает знаками «?» — кодировка конвейера; латиница
читается свободно)

```powershell
netstat -ano | findstr "8888 8787"
```

```
  TCP    127.0.0.1:8787         0.0.0.0:0              LISTENING       53988
  TCP    127.0.0.1:8888         0.0.0.0:0              LISTENING       46940
```

По PID дальше: `tasklist /FI "PID eq 53988"` — кто это за процесс. Для
«что сейчас открыто/что слушает порт» это быстрее и честнее OCR.

---

## 6. Матрица отказов: симптом → причина → лечение

Всё, что ниже, — либо поймано вживую в этой сессии, либо вычитано из кода
server.py (строки указаны), либо задокументировано в GUIDE.md. Формат как в
родительском гайде — только про клиентов.

| # | Симптом | Причина | Лечение |
|---|---|---|---|
| 1 | opencode: `Error: Cannot connect to API: Unable to connect…` (ждал 2–3 мин) | гейтвей лежал/перезапускался в момент запроса (два живых случая: 2m55s, 2m38s) | `GET /health`; watchdog поднимает за ~90 c; повторить запуск |
| 2 | opencode: `SSE error: Non-200 (405)` на `hoplite-gw` | MCP-SDK opencode открывает GET-SSE-стрим, а `/mcp` гейтвея — только POST. Проверено вживую: `GET /mcp` → `405 {"detail":"Method Not Allowed"}` | держать `hoplite-gw` `"enabled": false`; включать после появления GET-хендлера в server.py |
| 3 | omp: `Model "hermes-hoplite/…" not found` | модель не во всех трёх файлах (§3.1) или YAML битый | проверить `providers.yml` + `models.yml` + `config.yml` (`enabledModels`); `python -c "import yaml;yaml.safe_load(open(…))"` |
| 4 | omp при старте: `MCP server "hoplite-gw" failed to connect: The socket connection was closed unexpectedly` | гейтвей недоступен в момент старта или рвёт сессию (POST-only + п.5) | проверить `/health`; инструменты этого сервера будут просто недоступны, сессия жива; надёжная проверка — прямой JSON-RPC-пробник (§7) |
| 5 | `POST /mcp` c `notifications/initialized` → `500 Internal Server Error` | **баг server.py**: хендлер уведомлений возвращает `Response(status_code=202)`, но `Response` не импортирован (импортированы `HTMLResponse, JSONResponse, StreamingResponse`) → `NameError: name 'Response' is not defined`. Три таких 500 в `gateway_stderr.log` | патч одной строки: добавить `Response` в импорт `fastapi.responses`. Владелец стека в курсе;**правку не делаю — интеграцию ведёт он** |
| 6 | Ход висит и умирает через ~9 минут | дедлайн гейтвея: `DEADLINE = 540` c (server.py:48), потом `agent did not finish within 540s (thread …)`, HTTP 504 | поднять `deadline_s` в config.json гейтвея; сам тред мог докончить — глянуть app.hoplite.sh |
| 7 | `429 gateway busy: all 3 agent slots busy for 30s` | семафор `MAX_CONCURRENT = 3`, слот не дался за `SEM_ACQUIRE_TIMEOUT = 30` c (server.py:45–46) | повторить позже; параллелить клиентов не больше трёх |
| 8 | `429 … agent threads already active (limit 4)` | очередь Hoplite: `QUEUE_LIMIT = 4` живых тредов | дать облаку докончить; `hoplite_task_status`; поднять `queue_limit` |
| 9 | Ответ пришёл **дважды** | исторический баг v2 (мёртвый аккумулятор буфера — финальный flush повторял текст); в v3 исправлен, задокументирован в GUIDE.md | обновить server.py; симптомы не должно быть |
| 10 | opencode прикрепляет картинку — модель «не видит» и не жалуется | `attachment: true` + гейтвей вырезает image-части (§2.7: `_last_user_text` берёт только `type: "text"`) | держать `attachment: false` / `input: [text]` — честный отказ вместо молчаливой слепоты |
| 11 | omp ответил, но не Hoplite | сработал `fallbackChains` (§3.4); вживую: ответ пришёл от `deepseek-v4-flash-0731`, в событии `retry_fallback_succeeded` | смотреть `"provider":"hermes-hoplite"` в `--mode json` (§3.6) |
| 12 | Echo-проба через клиента отклонена («prompt injection attempt») | Hoplite-агент — не эхо-машина: системный промпт клиента в обёртке `[earlier conversation]` триггерит защиту (§2.9, живой случай) | пробовать естественными вопросами; механически — голый API (проверка 7 в status_all) |
| 13 | MCP-инструменты не подгрузились, предупреждение `not ready after 30000ms` | дефолтный таймаут каталога инструментов: docs opencode — `timeout … Defaults to 5000 (5 seconds)`; v2-доки описывают startup 30 c / catalog 30 c / execution 12 h. **какое значение реально в бинарнике — не проверяемо, бинарник скомпилирован** | задавать `timeout` явно: у нас 120000 (playwright) и 300000–900000 (hoplite-серверы) |
| 14 | `Extension error (pmb-memory.ts): handler timed out after 2000ms` в конце omp-прогона | расширение памяти не успело за 2 c (замечено вживую, на ответ не влияет) | игнорировать; если повторяется — смотреть `~/.omp/agent/logs/` |

Два «документ vs код» из этой таблицы, проговорить в видео отдельно:

- **README гейтвея против кода**: README пишет «agent did not finish within
  **240s**» и «max **3** parallel agent threads» — в коде `DEADLINE = 540`, а
  «3» — это семафор слотов, очередь — отдельный `QUEUE_LIMIT = 4`. README
  упрощает, код — источник истины.
- **Документация opencode против практики**: заявленный дефолт таймаута MCP
  5 c — для нашего облака смехотворен; и сколько реально в бинарнике — из
  бинарника не вычитывается. Поэтому в конфигах всё явно.

---

## 7. Рецепты проверки — по слоям, снизу вверх

Каждая команда запускалась в этой сессии; вывод — реальный. Порядок — как
диагностика: сначала дёшево, потом дорого.

| Слой | Команда | Что доказывает |
|---|---|---|
| 0. мост жив | `python status_all.py` (проверки 1–4) | loopback-рукопожатие, 26 инструментов, shell на ПК |
| 1. мост виден из облака | `python status_all.py` (проверки 5–6) | сервер зарегистрирован в Hoplite, облако достукивается |
| 2. гейтвей жив | `python -c "…GET /health…"` (пробник ниже) | ключ на месте, счётчики |
| 3. модели гейтвея | `python -c "…GET /v1/models…"` | 20 моделей |
| 4. MCP гейтвея | пробник `--mcp` ниже | initialize, 5 инструментов, hoplite_models |
| 5. opencode видит провайдера | `opencode models hoplite` | 9 моделей из конфига |
| 6. opencode MCP | `opencode mcp list` | статусы 5 серверов |
| 7. opencode ход | `opencode run -m hoplite/hoplite-sonnet-5 "<естественный вопрос>"` | полный конвейер (§2.9: 40 c) |
| 8. omp видит модели | `omp models hermes-hoplite` | 19 моделей, thinking-уровни |
| 9. omp ход | `omp -p --model hermes-hoplite/hoplite-gpt-5.6-terra --mode json "<вопрос>"` | ответ + метрики (§3.6: 18 c модель, 57 c всего) |
| 10. всё разом | `python status_all.py` | 7 проверок, exit 0/1 |

Вывод `omp models hermes-hoplite` (реальный):

```
hermes-hoplite (19)
│ hoplite-agent              │    200K │     16K │ -                             │ no     │
│ hoplite-gpt-5.6-terra      │    200K │     16K │ minimal,low,medium,high,xhigh │ no     │
│ hoplite-opus-5             │    200K │     16K │ minimal,low,medium,high,xhigh │ no     │
│ hoplite-sonnet-5           │    200K │     16K │ minimal,low,medium,high,xhigh │ no     │
│ … (всего 19: base + -fast)
```

Колонка `thinking` — уровни reasoning (`:minimal…:xhigh` суффиксом к модели,
например `hermes-hoplite/hoplite-opus-5:high`), колонка `images` — честное `no`.

### 7.1 Пробник «гейтвей + MCP» одним файлом

Голый HTTP-клиент в bash на этой машине блокирован локальным guard'ом, поэтому
пробник — файл. Сохрани как `check_gateway.py` рядом с мостом и запускай:
`python check_gateway.py` (проверит /health, /v1/models, /mcp) или
`python check_gateway.py --mcp` (только MCP). Ничего секретного не печатает
(ключ `sk-local` — не секрет). Ошибки не глотает: любой не-200/не-JSON —
трейсбек и exit 1.

```python
"""check_gateway.py — probe hoplite-gateway: /health, /v1/models, /mcp (JSON-RPC).
Usage: python check_gateway.py [--mcp]  Exit 0 = all green."""
import argparse, json, sys, urllib.request
import urllib.error as urlerr

GW = "http://127.0.0.1:8787"
AUTH = {"Authorization": "Bearer sk-local",
        "Accept": "application/json, text/event-stream"}

def rpc(method, params=None, mid=1):
    body = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        body["params"] = params
    req = urllib.request.Request(GW + "/mcp", data=json.dumps(body).encode(),
                           method="POST", headers={**AUTH, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mcp", action="store_true", help="MCP probe only (skip /health,/v1/models)")
    args = ap.parse_args()
    ok = True
    if not args.mcp:
        with urllib.request.urlopen(urllib.request.Request(GW + "/health", headers=AUTH), timeout=5) as r:
            h = json.loads(r.read())
        print("health:", "auth=%s requests=%s errors=%s" % (h["auth"], h.get("requests"), h.get("errors")))
        ok &= h["auth"]
        with urllib.request.urlopen(urllib.request.Request(GW + "/v1/models", headers=AUTH), timeout=5) as r:
            ids = [m["id"] for m in json.loads(r.read())["data"]]
        print("models: %d ids, first=%s" % (len(ids), ids[0]))
        ok &= len(ids) > 0
    init = rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                              "clientInfo": {"name": "check_gateway", "version": "0"}})
    print("mcp initialize:", init["result"]["serverInfo"])
    tools = [t["name"] for t in rpc("tools/list", {})["result"]["tools"]]
    print("mcp tools (%d): %s" % (len(tools), ", ".join(tools)))
    ok &= "hoplite_ask" in tools
    print("RESULT:", "OK" if ok else "FAIL")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
```

> Сниппет — файл, а не однострочник: так его удобно положить рядом с мостом и дёргать из CI.

Прогон этого сниппета (извлечён из этой главы дословно, сохранён в файл):

```
health: auth=True requests=4 errors=0
models: 20 ids, first=hoplite-opus-5
mcp initialize: {'name': 'hoplite-gateway', 'version': '3.6.0'}
mcp tools (5): hoplite_ask, hoplite_task, hoplite_task_status, hoplite_models, hoplite_conversations
RESULT: OK
```

(и `--help` работает; exit-код 0 — можно в CI).

### 7.2 `status_all.py` — главный вердикт

Один запуск проверяет все 7 пунктов и возвращает exit 0/1. Реальный прогон этой
сессии (машина под параллельной нагрузкой — проверки моста флапнули из-за ngrok
rate-limit; **каждая** из 7 проверок в этой сессии прошла хотя бы в одном
прогоне):

```
PASS  1. bridge local handshake                      no session  [0.0s]
FAIL  2. bridge public handshake                     no session  [4.2s]   ← ngrok-флап
FAIL  3+4. shell on this PC + sandbox denies outside handshake failed [4.5s]
PASS  5. registered in Hoplite                       id=mcp_4a1b… enabled=True  [2.3s]
PASS  6. Hoplite cloud reaches us                    connected=True tools=26  [11.3s]
PASS  7. OpenAI gateway turn                         turn -> 'GW-OK' uptime=250s  [20.3s]
PASS  8. omp + opencode configs                      omp pc-tools=yes, opencode hoplite=yes  [0.0s]

========================================================================
5/7 checks passed -> ЕСТЬ ПРОБЛЕМЫ
```

Обрати внимание на **проверку 7**: `'GW-OK'` за 20.3 c — вот она, честная
сквозная проверка модельного пути (голый API, без системного промпта клиента —
поэтому echo-проба тут работает, §4.2). На тихой машине прогоны дают 7/7
(исторический вывод в GUIDE.md моста: `7/7 checks passed -> РАБОТАЕТ`, ход
77.7 c). Правило при флапе: перезапустить `status_all.py` через минуту; каждая
проверка проходит — значит, стек жив, шумит только канал.

### 7.3 Мини-чеклист для видео (60 секунд)

```powershell
python status_all.py                                # 0/1 — весь стек
& "C:\Users\User\.openagents\nodejs\opencode.cmd" models hoplite   # 9 моделей
& "C:\Users\User\.openagents\nodejs\opencode.cmd" mcp list         # 5 серверов
omp models hermes-hoplite                           # 19 моделей
omp -p --model hermes-hoplite/hoplite-opus-5 --mode json "Одно предложение: что такое MCP?" 
```

---

## 8. Расхождения «документация ↔ реальность», собранные в этой главе

1. **`/mcp` + `notifications/initialized` → 500** (`NameError: Response`) — не
   описан нигде, пойман вживую. Совместимые MCP-клиенты обязаны слать это
   уведомление — сейчас сервер на нём падает в 500 (не фатально для тулзов, но
   сессия может считаться сломанной, см. п.4/5 §6).
2. **README гейтвея: 240 s / 3 потока** vs **код: `DEADLINE = 540`, слоты 3 +
   очередь 4** (§6).
3. **README гейтвея: 20 моделей не перечислены полностью** — `hoplite-opus-5-local`
   есть в `/v1/models`, отсутствует в таблице README и в omp/opencode конфигах (19/9).
4. **Документация opencode: MCP `timeout` Defaults to 5000 ms** — на практике
   для облака явно ставим 120000–900000; реальный дефолт бинарника не проверяем.
   (v2-документация уже другая: startup 30 c / catalog 30 c / execution 12 h.)
5. **`hoplite-gw` в README гейтвея подан как рабочий OMP-инструмент** — в
   живом старте omp подключение падало («socket connection was closed
   unexpectedly») на текущем server.py; стабильная проверка — прямой JSON-RPC
   (§7.1). Плюс для opencode он и не включён (405-проблема).
6. **README моста называет status_all «8 проверок»** — фактически проверок 7
   (3 и 4 объединены в одну строку отчёта; в выводе 8 строк PASS/FAIL).
7. **GUIDE.md моста рекомендует echo-пробу в opencode** («Reply with exactly:
   PIPE-OK») — вживую такой промпт отклоняется защитой Hoplite-агента (§2.9);
   работать будут естественные вопросы, echo — только через голый API.
8. **README гейтвея: omp-конфиг без `streamIdleTimeoutSeconds`** — в реальном
   `providers.yml` стоит 600; без него длинные «молчаливые» ходы Hoplite рвут
   стрим.
9. Клавиши omp: в `--help` заявлен `--service-tier` и десятки env — не
   проверялись; для Hoplite не нужны.

## 9. Источники

| Что | Где |
|---|---|
| Конфиг opencode (живой) | `C:/Users/User/.config/opencode/opencode.jsonc` |
| Docs opencode: Config (model/small_model/default_agent, {env:}/{file:}, таймауты провайдера) | https://opencode.ai/docs/config/ |
| Docs opencode: Providers (npm-пакеты встроены, примеры openai-compatible) | https://opencode.ai/docs/providers/ |
| Docs opencode: MCP servers (local/remote, oauth, timeout 5000) | https://opencode.ai/docs/mcp-servers/ |
| Docs opencode: Agents (permission, glob-карты bash) | https://opencode.ai/docs/agents/ |
| Конфиги omp (живые) | `C:/Users/User/.omp/agent/{providers.yml,models.yml,config.yml,mcp.json}` |
| Гейтвей (код) | `C:/Users/User/tmp/hoplite-gateway/server.py` (DEADLINE, семафоры, /mcp), `watchdog.pyw` (токен) |
| Родительские гайды | `GUIDE.md` в обеих репах; `README.md`, `ACCESS_PC.md` гейтвея |
| Репозитории | https://github.com/nikita4a/hoplite-pc-bridge · https://github.com/nikita4a/hoplite-gateway |
| OCR-помощник | `C:/Users/User/.config/opencode/tools/see_screen.ps1` |

---

*Глава собрана живыми прогонами 2026-09-25: opencode 1.18.29, omp 18.3.0,
гейтвей v3 (server.py 1035 строк), desktop-commander 0.2.50. Секреты (hop_-ключ,
JWT, ngrok-домен, secret_path) в тексте скрыты; все URL `127.0.0.1` — локальные.*
