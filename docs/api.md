# HTTP API

Интерфейс общается с сервером по JSON. Таблица маршрутов —
`avito_monitor/web/routes.py`; потоковые эндпоинты и статика обслуживаются
в `avito_monitor/web/server.py`.

## Соглашения

- Тело запроса и ответа — JSON в UTF-8. BOM в теле допускается: редакторы
  cookies иногда его добавляют.
- Аутентификация — cookie сессии `session`, которую выдаёт
  `POST /api/auth/login`. Cookie помечена `HttpOnly` и `SameSite=Lax`.
- Ошибки приходят в виде `{"error": "текст", "code": "…"}`. Поле `code`
  присутствует не всегда.

| Код | Когда                                                              |
| --- | ------------------------------------------------------------------ |
| 400 | некорректный запрос: битый JSON, отсутствует обязательное поле      |
| 403 | нужен вход (`{"code": "auth_required"}`)                            |
| 404 | нет такого эндпоинта                                                |
| 413 | тело запроса больше 1 МБ                                            |
| 502 | внешний сервис (cookies, Avito) недоступен                          |
| 503 | Web Push не настроен на сервере                                     |
| 500 | ошибка на нашей стороне; подробности — в логе                        |

Открыты без входа: `GET /api/auth/status`, `GET /api/status`,
`GET /api/categories`, `GET /api/regions`, `GET /api/push/vapid`,
`POST /api/auth/login`, `POST /api/auth/logout`. Остальное требует сессии.

## Вход

### `GET /api/auth/status`

```json
{ "logged_in": true, "username": "admin", "active": true }
```

### `POST /api/auth/login`

```json
{ "login": "admin", "password": "…" }
```

Отвечает тем же телом, что и `/api/auth/status`, и ставит cookie сессии.
Неверная пара — `400`. Принимается и ключ `username` вместо `login`.

### `POST /api/auth/logout`

Гасит текущую сессию и удаляет cookie.

## Поиск

### `GET /api/status`

Состояние поиска. Это же тело возвращают `POST /api/search` и
`POST /api/search/stop` (с добавленным `"ok": true`).

```json
{
  "generation": 3,
  "running": true,
  "query": "iphone 15",
  "region": { "slug": "sankt-peterburg", "name": "Санкт-Петербург" },
  "category": { "id": "apple", "name": "Смартфоны Apple" },
  "web_url": "https://www.avito.ru/…",
  "api_url": "https://m.avito.ru/api/…",
  "error": "",
  "seller_skip": ["премиум"],
  "search_mode": "query",
  "iphone_models": ["15-pro", "15-pro-max"],
  "iphone_models_in_url": true,
  "auth": { "logged_in": true, "username": "admin", "active": true }
}
```

`generation` растёт при каждом старте и остановке — по нему цикл опроса
понимает, что условия сменились. `iphone_models` равно `null`, если фильтр
моделей выключен.

### `POST /api/search`

Запускает поиск и очищает ленту.

| Поле            | Тип              | Описание                                              |
| --------------- | ---------------- | ----------------------------------------------------- |
| `mode`          | `"query"`/`"url"`| как искать; по умолчанию `query`                       |
| `query`         | строка           | поисковая фраза; обязательна для категории `all`       |
| `url`           | строка           | готовая ссылка Avito (для `mode=url`)                  |
| `region`        | строка           | slug региона; принимается и как `slug`                 |
| `category`      | строка           | id категории; `all` — все категории по тексту запроса  |
| `seller_skip`   | список строк     | чёрный список продавцов; не список — считается пустым   |
| `iphone_models` | список строк     | id моделей; `null` — не менять текущий фильтр           |

В режиме `url` ссылка берётся как есть: фильтры и регион переносятся в
JSON API локально. Выдача API — `presentationType=serp` и `sort=date`,
как 6 сентября. Если в ссылке уже задан
фильтр моделей, повторно фильтровать по названию не нужно — сервер это
определяет сам и сообщает флагом `iphone_models_in_url`.

Ошибки: `400` — не разобрать ссылку или неизвестный регион/категория;
`502` — сервис cookies не отдал адрес API.

### `POST /api/search/stop`

Останавливает поиск. Повторный вызов ничего не меняет.

### `GET /api/categories`

```json
[{ "id": "apple", "name": "Смартфоны Apple" }]
```

### `GET /api/regions?q=каз`

Подсказки для поля выбора региона; без `q` — начало общего списка.

```json
[{ "slug": "respublika_tatarstan_kazan", "name": "Казань" }]
```

## Лента

### `GET /api/ads`

Текущая лента, свежие впереди. Набор полей совпадает с типом `Ad` в
`web/src/types.ts` — менять его нужно с двух сторон.

```json
[
  {
    "id": 1234567890,
    "title": "iPhone 15 Pro 256GB",
    "price": "85 000 ₽",
    "address": "Санкт-Петербург, Невский пр-т",
    "url": "https://www.avito.ru/…",
    "images": ["https://…"],
    "can_call": true,
    "can_message": true,
    "seller": "Иван",
    "published": "сегодня в 14:32",
    "ts": 1757331120,
    "description": "…"
  }
]
```

`ts` — время публикации (Unix), `published` — оно же, отформатированное в
часовом поясе региона поиска.

### `GET /events`

Поток Server-Sent Events. Каждое сообщение — `data:` с JSON-массивом новых
объявлений в том же формате, что и `/api/ads`. Поток держится открытым,
поэтому за ним не должно быть буферизующего прокси (в nginx —
`proxy_buffering off`).

### `POST /api/reset`

Очищает ленту.

```json
{ "ok": true, "cleared": 42 }
```

### `GET /img?url=…`

Прокси картинок Avito: напрямую из браузера они не загружаются, потому что
Avito проверяет `Referer`. Отдаёт изображение, а не JSON.

## Чёрный список продавцов

### `GET /api/seller-blacklist`

```json
{ "sellers": ["премиум", "avito.ru/user/abc"] }
```

### `POST /api/seller-blacklist`

```json
{ "sellers": ["премиум"] }
```

Список заменяется целиком и действует со следующего цикла опроса. Отсутствие
поля `sellers` или не список — `400`. Значения из `config.toml` действуют
всегда и этим вызовом не затираются.

## Сессия Avito

Нужна для кнопки «Позвонить»: номер телефона Avito отдаёт только
авторизованному пользователю.

### `GET /api/avito/session`

```json
{ "connected": true, "logged_in": true, "label": "+7 921 …", "saved_at": 1757331120.0 }
```

### `POST /api/avito/session/import`

Тело — cookies, экспортированные расширением Cookie-Editor: либо массив
объектов `{"name": …, "value": …}`, либо объект `{"имя": "значение"}`.
Отвечает состоянием сессии с полем `label`.

### `POST /api/avito/session/clear`

Удаляет привязку.

### `POST /api/avito/phone`

```json
{ "ad_id": 1234567890 }
```

Успех — `{"ok": true, "phone": "+7…"}`. Отказ — `{"ok": false, "error": …,
"code": …}`, где `code` принимает значения `no_session`, `bad_id`,
`blocked`, `auth_required`, `unavailable`. Отсутствие `ad_id` — `400`.

## Push-уведомления

### `GET /api/push/vapid`

```json
{ "configured": true, "publicKey": "BEl…" }
```

### `POST /api/push/subscribe`

Тело — объект подписки из `PushManager.subscribe()`. Ответ:
`{"ok": true, "subscriptions": 3}`. Если Web Push на сервере не настроен —
`503`.

Параметр `?test=1` отправляет проверочное уведомление. Он нужен только при
включении тумблера: обычное переподписывание браузера не должно будить
пользователя.

### `POST /api/push/unsubscribe`

```json
{ "endpoint": "https://fcm.googleapis.com/…" }
```

## Баланс сервиса cookies

### `GET /api/resource/balance`

```json
{ "success": true, "balance": 1234.5 }
```

Недоступность сервиса — `502`.
