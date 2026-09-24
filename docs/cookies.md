# Глава: Cookies Hoplite — второй мир авторизации

> Для кого: ты уже запустил `hoplite-pc-bridge` и `hoplite-gateway`, всё работает, но хочешь
> понять, **почему** одно место требует `X-Api-Key: hop_...`, а другое — browser-cookies.
> Это самая недокументированная часть стека; здесь всё проверено живыми запросами
> к `https://api.hoplite.sh` 25.09.2026. Выводы команд ниже — настоящие, скопированы из терминала.

---

## 1. Проблема: у Hoplite ДВА мира авторизации

Hoplite различает два способа обратиться к своему API:

| Мир | Что передаём | Что это даёт |
|---|---|---|
| **API-мир** | заголовок `X-Api-Key: hop_...` (+ `Origin: https://app.hoplite.sh`) | создание/чтение тредов, проектов, MCP-серверов |
| **Cookie-мир** | cookie `__Secure-better-auth.session_token=...` (+ `Origin`) | всё то же **плюс** запись сообщений в тред и mirror браузера |

Почему два мира: фронтенд `app.hoplite.sh` ходит в API с сессионными cookies (better-auth),
а программный доступ — по ключу. Большинство эндпоинтов принимает оба варианта. Но **не все**:
запись сообщений сервер отдаёт только cookie-миру.

### 1.1 Матрица, проверенная живыми запросами (2026-09-25)

Ключ и cookies в этом стенде принадлежат **разным аккаунтам** (об этом — раздел 6),
поэтому матрица получилась нагляднее любого учебника:

| Эндпоинт | `X-Api-Key` | session-cookie |
|---|---|---|
| `GET /api/projects` | **200** (аккаунт ключа: `<your-account>/<your-repo>`) | **200** (аккаунт cookies: `SomeAccount/xznunukb`) |
| `GET /api/threads` | **200** | **200** |
| `GET /api/auth/session` | **401** `auth_required` | **200** (полная сессия) |
| `GET /api/mcp/servers` | **200** (видит `pc-tools`) | **200** (пусто у SomeAccount) |
| `GET /api/mcp/catalog` | **200** | **200** |
| `POST /api/threads/{id}/messages` | **401** `invalid_api_key` — тред СВОЕГО org тоже | **201** |

Реальный вывод «ножниц». Проб-скрипты ниже — scratch-файлы вне репо (весь их вывод вставлен целиком);
легко повторить из любой среды: POST `/api/threads/{id}/messages` с одним заголовком `X-Api-Key`.

```
$ python tmp/probe_apikey_append.py      # POST messages, только X-Api-Key
HTTP 401
{"ok":false,"error":"invalid_api_key"}
```

…и тем же ключом в тред СВОЕЙ организации:

```
$ python tmp/probe_apikey_append_own.py  # POST messages, свой тред, только X-Api-Key
HTTP 401
{"ok":false,"error":"invalid_api_key"}
```

Вывод: `invalid_api_key` здесь **не про ключ** — ключ живой (GET выше прошёл 200).
Это «ножницы» доступов: append по API-ключу запрещён всегда, даже на своих тредах.
Имя ошибки вводит в заблуждение — это первая ловушка, о которой надо знать.

А вот cookie-мир тот же запрос принимает:

```
$ python tools/hoplite_session.py append thr_ded1ef56ba4f4d55b3ed3dc288d918db \
      "Reply with exactly: COOKIE-APPEND-PROBE-OK" --config ../hoplite-gateway/cookies.json
APPEND OK (201) — message msg_c700df88080440f0ac6e78b77d34ea8d, run run_3ef5e94ec3ec49a2a95b0e7189f8addb
```

И агент реально ответил (проверка чтением, read-only GET):

```
$ (read-back GET /api/threads/thr_ded1.../messages)
assistant | msg_run_8b3b... | COOKIE-APPEND-OK          <- более ранний probe
     user | msg_c700df88... | Reply with exactly: COOKIE-APPEND-PROBE-OK
assistant | msg_run_3ef5... | COOKIE-APPEND-PROBE-OK    <- ответ на наш append
http 200 | total 7
```

Именно поэтому `hoplite-gateway` устроен так: треды создаёт ключом, сообщения дописывает
cookies (файл `server.py`, `_append_message`, ~L237, комментарий автора: *«Proven auth matrix
(2026-09): hop_ API key can create/read threads but POST /messages rejects it (401
invalid_api_key); session cookies + Origin header work (201)»*). Наша сегодняшняя матрица
независимо это подтверждает.

---

## 2. Анатомия cookies app.hoplite.sh

Открой DevTools → Application → Cookies → `https://app.hoplite.sh`. Вот кто там живёт:

| Cookie | Тип | Нужен для API | Что делает |
|---|---|---|---|
| `__Secure-better-auth.session_token` | httpOnly, Secure | **ДА, единственный** | сессия better-auth: единственный паспорт cookie-мира. Без него append невозможен |
| `_iidt` | обычный | нет | антибот-метка (Incapsula-класс), встречается не всегда; шлюзу не нужна |
| `hoplite.shell_hint` | обычный | нет | UI-преференс фронтенда (какой shell-хинт показывать); на API не влияет |

Три факта, которые экономят часы:

1. **httpOnly**: `document.cookie` в Console НЕ покажет `session_token`. Только панель
   Application или копирование запроса из вкладки Network (Copy as curl).
2. **Не декодируй значение.** В токене встречаются `%2F`, `%3D` — это штатная
   percent-encoded форма. Сервер принимает и encoded, и «голую». Менять ничего не надо.
3. Нужен ровно **один** cookie. `Origin: https://app.hoplite.sh` (и по вкусу `Referer`)
   в заголовках — обязателен: без него сервер отвечает `403`-классом csrf-ошибок
   (better-auth требует origin для cookie-запросов).

Почему passport именно один: сессии лучше-auth идентифицируются единственным
`session_token`, всё остальное — обвес браузера. Шлюзы (`hoplite-gateway`,
`tools/hoplite_session.py`) осознанно шлют только его.

---

## 3. Как достать свежие cookies (ручная процедура)

Сессия живёт ограниченно: в нашем стенде `expires 2026-10-01T22:28:38.997Z` — т.е.
~7 дней от выпуска. Потом `check` скажет DEAD и надо повторить:

1. Открой `https://app.hoplite.sh`, войди в нужный аккаунт.
2. `F12` → вкладка **Application** → слева **Cookies** → выбери домен `https://app.hoplite.sh`.
3. Строка `__Secure-better-auth.session_token` → двойной клик по Value → `Ctrl+C`.
4. (опц.) там же скопируй `_iidt`, если он есть.
5. Положи в конфиг шлюза:

   ```json
   {
     "cookies": {
       "__Secure-better-auth.session_token": "<вставь значение целиком, с %2F как есть>"
     }
   }
   ```


6. Проверь:

```
$ python tools/hoplite_session.py check
ALIVE — session valid
  user : SomeAccount <user@example.com> (id KWavu4WN...)
  org  : dsada (id org_6d46..., role owner)
  sess : id Hkrxj2SL..., expires 2026-10-01T22:28:38.997Z
```

**Почему** именно так: DevTools-панель — единственное место, где httpOnly-токен виден
целиком; JSON-структура `{"cookies": {...}}` — формат, который читает и `hoplite-gateway`
(`server.py` L97: `SESSION_COOKIES = CONFIG.get("cookies", {})`), и наш CLI.

Альтернативный путь: вкладка **Network** → любой запрос к `api.hoplite.sh` → правый клик →
Copy → Copy as curl (bash) → в скопированном найди заголовок `-H 'Cookie: ...'`.

**Важно про аккаунт**: cookies должны быть от того же аккаунта, что и `api_key`, иначе
получишь два несвязанных мира (см. раздел 6 — мы на этом прожились и превратили в фичу).

---

## 4. Инструмент: `tools/hoplite_session.py`

Stdlib-only CLI (Python 3.11+, без зависимостей) для рутинных вопросов про cookie-мир.
Никогда не печатает значения cookies — только email/org/id/статусы.

```
$ python tools/hoplite_session.py --help
usage: hoplite_session.py [-h] [--config CONFIG] [--json]
                          {check,whoami,append,refresh-hint} ...

    check               жива ли сессия; печатает email/org
    whoami              user + org + список проектов
    append              дописать сообщение в тред (cookie-auth)
    refresh-hint        пошаговая инструкция экспорта свежих cookies

Exit codes: 0 ok, 1 session dead, 2 api/network error, 3 config missing.
```

Конфиг ищется так: флаг `--config` → env `HOPLITE_COOKIES_FILE` → дефолт
`../hoplite-gateway/config.json` относительно тулзы. Формат — плоский JSON с cookies
или с обёрткой `{"cookies": {...}}` (понимает оба).

### 4.1 `check` — liveness

```
$ python tools/hoplite_session.py check --config ../hoplite-gateway/cookies.json
ALIVE — session valid
  user : SomeAccount <user@example.com> (id KWavu4WN...)
  org  : dsada (id org_6d46..., role owner)
  sess : id Hkrxj2SL..., expires 2026-10-01T22:28:38.997Z
```

Мёртвая сессия (старая копия в config.json — см. раздел 5) падает громко и с кодом 1:

```
$ python tools/hoplite_session.py check      # default: config.json шлюза
DEAD: http 401 — сессия не распознана сервером: cookie истёк, ротирован браузером,
или аккаунт вышел / отозвал сессию (error=auth_required)
exit=1
```

**Почему** `GET /api/auth/session` как проба: это самый дешёвый запрос, который
отвечает 200+тело только для валидной сессии и 401 `auth_required` для всего остального
(мы проверили: X-Api-Key на нём тоже 401). Не тратит треды и sandbox-время.

### 4.2 `whoami` — идентичность + проекты

```
$ python tools/hoplite_session.py whoami --config ../hoplite-gateway/cookies.json
ALIVE — session valid
  ...
  projects (1):
    - proj_36f9a3e... SomeAccount/xznunukb  (org org_6d46..., branch main)
```

**Почему** полезно: мгновенно показывает, **чужой ли** ты аккаунт видишь (см. раздел 6).

### 4.3 `append` — proof-of-work cookie-мира

```
$ python tools/hoplite_session.py append <thread_id> "текст" --config <cfg>
APPEND OK (201) — message msg_c700df88080440f0ac6e78b77d34ea8d, run run_3ef5...
```

### 4.4 `--json` — для скриптов

```
$ python tools/hoplite_session.py --json check --config ../hoplite-gateway/cookies.json
{
  "alive": true,
  "api": "/api/auth/session",
  "http": 200,
  "user": {
    "id": "KWavu4WNR8vsROMbf1DkCqCPLQ4CDxeu",
    "name": "SomeAccount",
    "email": "user@example.com"
  },
  "org": {
    "id": "org_6d467811cc224b44b289e28d78cad9b6",
    "name": "dsada",
    "slug": "dsada",
    "member_role": "owner"
  },
  "session": {
    "id": "Hkrxj2SLqx4zz7xvTDgbA6i7ag2Q6jUC",
    "expires_at": "2026-10-01T22:28:38.997Z"
  }
}
```

### 4.5 `refresh-hint` — шпаргалка раздела 3 прямо в терминале

```
$ python tools/hoplite_session.py refresh-hint
1. Открой https://app.hoplite.sh и войди в нужный аккаунт ...
...
7. Проверка: python tools/hoplite_session.py check
```

---

## 5. Где живут cookies и почему ровно там (археология)

Текущее состояние стенда:

| Файл | cookies в нём | статус |
|---|---|---|
| `hoplite-gateway/cookies.json` (LEGACY) | есть, **живые** (expires 2026-10-01) | источник для `--config` |
| `hoplite-gateway/config.json` | копия, **мёртвая** (401 auth_required) | не трогать руками; обновляется через UI шлюза |

**Почему** так вышло —.git-археология `hoplite-gateway`:

- Коммит `cc90d11` (v3, 23.09 00:16): `_save_config` писал `config.json` **без merge** —
  любое `POST /admin/config` (hot-change ключа через UI) стирало из файла объект
  `cookies` со всеми остальными ключами.
- Коммит `2d1997b` (24.09 01:24): починка (test-first), формулировка автора коммита:
  *«_save_config rewrote config.json with only 4 keys, silently deleting cookies
  (which are the ONLY way to append messages)…»*. Теперь перед записью читается текущий
  файл и делается `update` — выживает всё.
- Текущий код: `server.py` L84-93 — merge-запись; L77-79 — LEGACY-миграция `cookies.json`;
  L97 — `SESSION_COOKIES` из config.

Практический вывод: **живой источник cookies сегодня — legacy `cookies.json`**, шлюз
читает `config.json`. Если обновляешь cookies — обновляй `config.json` (UI или руками),
`cookies.json` остаётся запасным слепком. Наш CLI читает любой из них через `--config`.

Обновление живых cookies в config.json шлюза — POST `/admin/config` с телом
`{"cookies": {...}}` (merge-safe с v3.8, коммит 2d1997b) либо правка файла + рестарт шлюза.
Рестарт/остановку живого шлюза делай только ты — из этой инструкции сервисы не трогаются.

---

## 6. Ловушка двух аккаунтов (мы в неё попали — и задокументировали)

Симптом: append «не работает», хотя ключ валидный и cookies свежие.

Причина в стенде: `api_key` из `config.json` принадлежит аккаунту **A**
(`<your-account>/<your-repo>`, org_df5ab…), а cookies — аккаунту **B**
(`SomeAccount`, org_6d46…). Треды A недоступны B и наоборот:

```
GET /api/projects  [api-key] -> проект <your-account>/<your-repo>   (аккаунт A)
GET /api/projects  [cookies] -> проект SomeAccount/xznunukb      (аккаунт B)
GET /api/mcp/servers [api-key] -> pc-tools (mcp_461bbe..., enabled)  (зарегистрирован на A!)
GET /api/mcp/servers [cookies] -> []                                  (у B пусто)
```

401 на append при этом выглядит как `invalid_api_key` — и вводит в заблуждение (раздел 1.1):
ключ живой, но append запрещён **всем** ключам; а «чужие» треды и вовсе 404.

Диагноз за 10 секунд:

```
$ python tools/hoplite_session.py whoami --config <cookies-файл>
$ # сравни org/project с тем, что видит ключ:
$ python -c "import json;print(json.load(open('../hoplite-gateway/config.json'))['api_base'])"
$ # и GET /api/projects с X-Api-Key (см. матрицу 1.1)
```

Правило: **один аккаунт — весь стек**. Cookies должны быть от аккаунта, выпустившего
`api_key`. Иначе два мира не пересекутся: шлюз будет создавать треды в A, а дописывать
пытаться в B (fallback с 401 в stderr — ищи в логах строку
`[gw] session cookie expired — refresh cookies in config.json`).

---

## 7. Безопасность (минимум, обязательный)

- Cookies = **полный доступ к аккаунту** (append, org-действия). В публичный репозиторий
  не попадают никогда: `.gitignore` обеих реп уже исключает `config.json`/`cookies.json`;
  новые тулзы его не пишут.
- В логах/доках — только обрезки: `Hkrxj2SL...`, `msg_c700df88...`. Полные значения
  токенов не печатаются даже «для отладки».
- Ротация: при любом подозрении (утёк лог, shared скрин) — Sign out в web UI
  (убивает сессию на сервере) → новый экспорт по разделу 3 → обновить config шлюза.
- `session_token` httpOnly + `__Secure-` префикс: по голому HTTP не передаётся,
  из JS не читается. Это серверная гарантия — пользуемся ею, не обходя.

---

## 8. Чек-лист главы

- [ ] `check` на живых cookies → `ALIVE` + твой email (раздел 4.1)
- [ ] `whoami` показывает org/project, совпадающий с миром ключа (раздел 6)
- [ ] append probe `Reply with exactly: PROBE-OK` → `APPEND OK (201)` (раздел 4.3)
- [ ] read-back подтверждает ответ агента (раздел 1.1)
- [ ] мёртвый cookies-файл → exit 1 с внятной причиной, не загадочный 500 (раздел 4.1)

Каждый пункт этого чек-листа прогнан сегодня живьём; выводы — в тексте.
