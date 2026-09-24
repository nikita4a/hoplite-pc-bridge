# hoplite-pc-bridge

**Облачный агент Hoplite получает руки на твоём ПК.** Один скрипт поднимает локальный
MCP-сервер с shell+файлами и публикует его через ngrok — URL вставляется в
`Hoplite → Add MCP server`, и облачный агент (Opus 5.5 / GPT-5.x) начинает читать
файлы, запускать команды и смотреть процессы на этой машине.

> **Полный гайд от А до Я** — установка, аккаунты, ngrok, регистрация в Hoplite через UI и
> через API, три уровня проверки, безопасность, все грабли и сценарий видео: **[GUIDE.md](GUIDE.md)**

```
Hoplite cloud agent (sandbox в облаке)
   │  HTTPS, MCP streamable-HTTP
   ▼
https://<your-domain>.ngrok-free.dev/<secret-path>   ← публичный URL (capability URL)
   │  ngrok agent (исходящее соединение, входящих портов не нужно)
   ▼
mcp-proxy  127.0.0.1:8888   ← слушает ТОЛЬКО loopback
   │  stdio
   ▼
@wonderwhy-er/desktop-commander   ← shell + файлы, исполняется на этом ПК
```

Обратное направление (Hoplite как **мозг** для локального opencode/OMP) живёт в
[`nikita4a/hoplite-gateway`](https://github.com/nikita4a/hoplite-gateway). Эти два моста
независимы и могут работать одновременно.

---

## Требования

| Что | Проверено |
|---|---|
| Python ≥ 3.10 (stdlib only, зависимостей нет) | 3.11.9 |
| Node.js ≥ 18 | v22.14.0 |
| `ngrok` **≥ 3.20.0** | 3.39.11 |
| npm `mcp-proxy` | 6.7.18 |
| npm `@wonderwhy-er/desktop-commander` | 0.2.50 |

```powershell
winget install Ngrok.Ngrok          # затем ОБЯЗАТЕЛЬНО: ngrok update
npm i -g mcp-proxy @wonderwhy-er/desktop-commander
```

> `winget` ставит ngrok 3.3.1, а аккаунт требует ≥ 3.20.0 → `ERR_NGROK_121`.
> `ngrok update` лечит за ~40 с. `bridge.py` сам находит бинарь в
> `%LOCALAPPDATA%\Microsoft\WinGet\Packages\Ngrok.Ngrok_*\ngrok.exe`, PATH не нужен.

---

## Установка

```powershell
cd hoplite-pc-bridge
copy config.example.json config.json
```

1. **`ngrok.yml`** — скачай agent-конфиг с <https://dashboard.ngrok.com/get-started/setup>
   и положи рядом как `ngrok.yml`. Из него берутся только **authtoken** и **домен**;
   сам файл ngrok'у не отдаётся (см. «Почему не `ngrok start`»).
2. **`config.json`** — впиши папки в `desktop_commander.allowedDirectories`.
   Пустой список = весь диск, `bridge.py` в таком случае отказывается стартовать.
3. Запуск: `start_bridge.bat` (или `python bridge.py --verify`).

Статический домен не обязателен: без него ngrok выдаст случайный URL, а
`public_url` определится через локальный API ngrok (`127.0.0.1:4040`). Но со
статическим доменом URL не меняется между перезапусками — в Hoplite его можно
вписать один раз.

---

## Подключение в Hoplite

`app.hoplite.sh → проект → MCP servers → Add MCP server`:

| Поле | Значение |
|---|---|
| **Server key** | `pc-tools` (любое короткое имя) |
| **Connection** | `http` |
| **URL** | `https://<domain>.ngrok-free.dev/<secret_path>` — целиком, как напечатал `bridge.py` |
| **Description** | `Shell + filesystem on the operator PC` |
| **Authentication** | `None` — секретом является сам путь URL |
| **Advanced** | не нужно |

После сохранения **обязательно начни новый тред**: в уже идущих список инструментов
не обновляется.

Тот же URL работает в любом MCP-клиенте (Claude Desktop, opencode, Cline, OMP):
транспорт — streamable HTTP, SSE-лег не поднят намеренно.

---

## Проверка

```powershell
python verify_bridge.py
```

Прогоняет реальное рукопожатие через **публичный** URL и четыре проверки:

```
[verify] initialize OK -> desktop-commander 0.2.50 (session …)
[verify] tools/list OK -> 26 tools: get_config, read_file, write_file, start_process, …
[prove] list_directory C:/Users/User/tmp -> OK (… chars of listing)
[prove] get_config OK -> allowedDirectories=[…] blockedCommands=0 telemetry=False
[prove] boundary read_file C:/Windows/win.ini -> DENIED (folder sandbox holds for file ops)
[prove] start_process shell -> OK, executed on this PC
VERDICT: OK — public MCP endpoint is usable
```

`python bridge.py --verify` делает то же при старте. Exit code 0/1 — можно в CI или cron.

---

## Модель безопасности

Честно, без приукрашивания:

- **mcp-proxy слушает только `127.0.0.1`.** Единственный вход — туннель ngrok.
- **Секрет = путь URL** (`/mcp-<32 hex>`). Кто знает URL, тот имеет доступ.
  Ротация: поменяй `secret_path` в `config.json` и перезапусти, затем обнови URL в Hoplite.
- **`allowedDirectories` ограничивает ТОЛЬКО файловые операции.** Команды в
  `start_process` / `interact_with_process` видят весь диск. Это ограничение
  desktop-commander, а не моста — не считай папки песочницей для shell.
- **`blockedCommands`** в этой поставке **не задаётся**: ключ живёт в общем
  `~/.claude-server-commander/config.json` и принадлежит всем твоим MCP-клиентам.
  Пустой список = shell без ограничений. Чтобы ввести блок-лист, добавь ключ в
  `config.json → desktop_commander`; штатный список пакета приведён в
  `config.example.json`.
- **Общий конфиг не перезаписывается.** `bridge.py` мёржит в
  `~/.claude-server-commander/config.json` только свои ключи, сохраняя `clientId`,
  `usageStats` и лимиты, и один раз делает `config.json.bak-<timestamp>` рядом.
  Откат: скопируй `.bak` обратно. Сервер следит за каталогом и подхватывает изменения
  на лету.
- **`telemetryEnabled: false`** — desktop-commander по умолчанию шлёт телеметрию.
- **Не держи туннель поднятым зря.** `stop_bridge.bat` убивает и proxy, и ngrok.
- **`config.json`, `ngrok.yml`, `status.json`, `*.log` в git не попадают** —
  `.gitignore` построен как deny-all allowlist.

---

## Эксплуатация

| Действие | Команда |
|---|---|
| старт | `start_bridge.bat` или `python bridge.py` |
| старт + полная проверка | `python bridge.py --verify` |
| только URL (и выйти) | `python bridge.py --print-url` |
| проверка живого моста | `python verify_bridge.py` |
| стоп | `stop_bridge.bat` или `python bridge.py --stop` (Ctrl+C в окне тоже) |
| состояние | `status.json` (pid, URL, порт, общие папки) |
| логи | `bridge.log` (proxy + MCP), `ngrok.log` (туннель, JSON) |

Инструменты, которые получает агент: `read_file`, `read_multiple_files`, `write_file`,
`edit_block`, `create_directory`, `list_directory`, `move_file`, `get_file_info`,
`start_search` / `get_more_search_results` / `stop_search` / `list_searches`,
`start_process`, `interact_with_process`, `read_process_output`, `force_terminate`,
`list_processes`, `list_sessions`, `kill_process`, `get_config`, `set_config_value`,
`get_usage_stats`, `get_recent_tool_calls`, `write_pdf`, `get_prompts`.

---

## Грабли (все найдены empirical, все обойдены)

| Симптом | Причина | Решение |
|---|---|---|
| `ERR_NGROK_121 … version "3.3.1" is too old` | winget ставит старый агент | `ngrok update` (→ 3.39.x) |
| `unknown version '3'. valid versions are: [1 2]` | агент-конфиг с дашборда новее агента | не отдавай yml ngrok'у — `bridge.py` сам тянет из него токен и домен и запускает `ngrok http <port> --domain … --authtoken …` |
| `authentication failed` | нет/чужой authtoken | `ngrok.authtoken` в `config.json`, или `NGROK_AUTHTOKEN` в env, или yml |
| домен занят | статический домен зарезервирован другим аккаунтом/туннелем | освободи в дашборде или убери `public_url`/`domain` |
| `port 8888 is already in use` | прошлый proxy не умер | `python bridge.py --stop`, затем старт |
| `tunnel not answering` | proxy не поднялся | `bridge.log` |
| `get_config` показывает `allowedDirectories: []` | desktop-commander читает `~/.claude-server-commander/config.json`, а **не** `config.json` в cwd (README пакета врёт) | `bridge.py` пишет туда сам; проверь `verify_bridge.py` |
| вывод команд тонет в ошибках `Import-PowerShellDataFile` | `defaultShell: powershell.exe` грузит профиль юзера | в `config.json` стоит `cmd.exe` |
| `start_process` → `invalid_type … timeout_ms Required` | поле обязательное | всегда передавай `timeout_ms` |
| агент жалуется на контекст | `list_directory` по `C:/Users/User/tmp` возвращает ~190 КБ | проси узкие пути, а не корень |

---

## Почему не `ngrok start <endpoint>`

Именованные endpoints из yml требуют, чтобы версия агента совпадала с версией
формата конфига. Скачанный с дашборда `version: "3"` не читается агентом 3.3.1, а
обновлённый агент читает и его, и `version: "2"`. Поэтому `bridge.py` трактует yml
как **данные** (регулярками достаёт `authtoken` и домен) и всегда запускает одну и
ту же форму `ngrok http <port> --domain <d> --authtoken <t> --log stdout
--log-format json`. Один путь запуска, работающий на любом агенте, без YAML-зависимости.

---

## Файлы

| Файл | Назначение |
|---|---|
| `bridge.py` | весь мост: конфиг, merge dc-конфига, proxy, ngrok, MCP-клиент для проверки |
| `verify_bridge.py` | проверка живого моста через публичный URL (рукопожатие + 4 доказательства) |
| `config.json` | **твои секреты и пути — в git не попадает** |
| `config.example.json` | шаблон со штатным `blockedCommands` пакета |
| `ngrok.yml` | agent-конфиг ngrok (токен + домен) — **в git не попадает** |
| `start_bridge.bat` / `stop_bridge.bat` | запуск/остановка одним кликом |
| `status.json`, `bridge.log`, `ngrok.log` | состояние и логи — **в git не попадают** |
