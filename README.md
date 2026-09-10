# Сигнал — мониторинг объявлений Avito

Веб-приложение, которое непрерывно опрашивает Avito и показывает новые
объявления в реальном времени: лента обновляется сама, есть фильтры по
категории, региону, моделям iPhone и продавцам, push-уведомления на телефон
и кнопка «Позвонить» через привязанную сессию Avito.

## Как это работает

Приложение — один процесс Python, который поднимает три вещи:

- **веб-интерфейс** на `127.0.0.1:8765` — статика из `static/` плюс JSON-API;
- **цикл мониторинга** — опрашивает Avito, но только после того, как в
  интерфейсе нажали «Начать поиск»;
- **сервис пула cookies** — держит наборы мобильных cookies рабочими, пока
  идёт опрос.

Avito отдаёт выдачу только «мобильному» клиенту, поэтому запросы идут через
мобильный прокси со сменой IP (можно два канала — при бане IP опрос сразу
уходит на соседний) и через пул покупных cookies: сгоревший набор уходит на
разблокировку, а опрос продолжается на остальных.

Подробнее — [docs/architecture.md](docs/architecture.md).

## Быстрый старт

Нужен Python 3.11+ и (для сборки интерфейса) Node.js 20+.

```bash
python -m venv .venv
.venv\Scripts\activate         # Windows
# source .venv/bin/activate    # Linux/macOS
pip install -r requirements.txt
```

Скопируйте оба файла настроек и заполните секреты:

```bash
copy config.toml.example config.toml   # Windows
copy .env.example .env
# cp config.toml.example config.toml   # Linux/macOS
# cp .env.example .env
```

| Файл          | Что хранит                                       | В git |
| ------------- | ------------------------------------------------ | ----- |
| `.env`        | прокси, ключ сервиса cookies, логин/пароль входа | нет   |
| `config.toml` | фильтры, интервалы опроса, размер пула           | нет   |

Минимум для запуска — `PROXY_STRING`, `COOKIES_API_KEY`, `APP_LOGIN` и
`APP_PASSWORD` в `.env`. Все настройки описаны в
[docs/configuration.md](docs/configuration.md).

Запуск:

```bash
python -m avito_monitor
```

Откройте <http://127.0.0.1:8765>, войдите под `APP_LOGIN`/`APP_PASSWORD` и
нажмите «Начать поиск».

## Docker

```bash
cp config.toml.example config.toml
cp .env.example .env    # заполнить секреты
docker compose up -d --build
```

Контейнер публикует порт только на `127.0.0.1`: наружу приложение выходит
через nginx с TLS. Полная процедура, включая домен, сертификат и обновление,
— в [docs/deployment.md](docs/deployment.md).

## Структура проекта

```
avito_monitor/        Приложение (Python)
├── avito/            Всё, что знает про Avito: ссылки, разбор выдачи, фильтры
├── cookies/          Пул мобильных cookies и его обслуживание
├── monitor/          Цикл опроса, темп опроса, память о показанном
├── net/              HTTP-клиент и мобильный прокси
├── web/              Сервер, API, лента, push, прокси картинок
├── data/             Справочники регионов, категорий и моделей iPhone
└── tools/            Служебные скрипты (VAPID-ключи, разбор логов)

web/                  Исходники интерфейса (TypeScript + Vite + Tailwind)
static/               Собранный интерфейс — его и раздаёт сервер
tests/                Автотесты (pytest + Playwright)
docker/               Entrypoint контейнера
deploy/nginx/         Пример конфига nginx
docs/                 Документация
```

## Документация

| Документ                                       | О чём                                              |
| ---------------------------------------------- | -------------------------------------------------- |
| [architecture.md](docs/architecture.md)        | Из чего состоит приложение и как идут данные        |
| [configuration.md](docs/configuration.md)      | Все настройки `config.toml` и `.env`                |
| [api.md](docs/api.md)                          | HTTP-API: эндпоинты, форматы, коды ошибок           |
| [deployment.md](docs/deployment.md)            | Установка на сервер, nginx, HTTPS, обновление       |
| [operations.md](docs/operations.md)            | Эксплуатация: логи, cookies, прокси, диагностика    |
| [development.md](docs/development.md)          | Разработка: тесты, линтеры, сборка интерфейса       |

## Тесты и проверки

```bash
python -m pytest              # автотесты (включая UI в headless-браузере)
python -m ruff check .        # линтер
cd web && npm run check       # типы, линтер и формат интерфейса
```

Подробнее — [docs/development.md](docs/development.md).
