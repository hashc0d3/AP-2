# Сигнал — мониторинг объявлений Avito

Веб-приложение для мониторинга новых объявлений Avito в реальном времени: лента обновляется автоматически, есть фильтры, привязка Avito для кнопки «Позвонить», push-уведомления и Docker-деплой.

## Структура проекта

```
parser1/
├── parser.py              # Точка входа
├── settings.py            # Загрузка config.toml + .env
├── config.toml.example    # Настройки парсера (без секретов)
├── .env.example           # Секреты и доступы
├── web/                   # Исходники фронтенда (TypeScript + Vite)
├── static/                # Собранный фронтенд (для локального запуска)
├── docker/                # Docker entrypoint
├── tests/                 # Автотесты
└── tools/                 # Утилиты для диагностики
```

## Быстрый старт (локально)

### 1. Зависимости

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS
pip install -r requirements.txt
```

### 2. Конфигурация

```bash
copy config.toml.example config.toml   # Windows
# cp config.toml.example config.toml   # Linux/macOS

copy .env.example .env
# Заполните .env: прокси, ключ ресурса, логин/пароль
```

| Файл | Что хранить |
|------|-------------|
| `.env` | Прокси, ключ API, логин/пароль, порт |
| `config.toml` | Фильтры, интервалы опроса, URL поиска |

### 3. Запуск

```bash
python parser.py
```

Откройте http://127.0.0.1:8765

### 4. Сборка фронтенда (при изменении UI)

```bash
cd web
npm ci
npm run build
```

## Docker

Подробнее — в [DEPLOY.md](DEPLOY.md).

```bash
cp config.toml.example config.toml
cp .env.example .env
# заполните .env

docker compose up -d --build
```

## Тесты

```bash
python tests/test_iphone_filter.py
python tests/test_parser_speed.py
```

## Переменные окружения (.env)

| Переменная | Описание |
|---|---|
| `PROXY_STRING` | HTTP-прокси `user:pass@host:port` |
| `SOCKS5_PROXY` | SOCKS5-прокси (опционально) |
| `PROXY_CHANGE_URL` | URL смены IP мобильного прокси |
| `COOKIES_API_KEY` | Ключ сервиса cookies |
| `APP_LOGIN` | Логин веб-интерфейса |
| `APP_PASSWORD` | Пароль веб-интерфейса |
| `WEB_PORT` | Порт (по умолчанию 8765) |
| `WEB_HOST` | Адрес bind (0.0.0.0 для сервера) |
| `WEB_OPEN_BROWSER` | Открывать браузер при старте (1/0) |
| `SKIP_VPN_BYPASS` | Не настраивать Windows-маршруты VPN (1/0) |

## Push-уведомления (фоновые)

1. На сервере сгенерируйте VAPID-ключи: `python tools/generate_vapid.py` → добавьте в `.env`
2. Откройте **https://peterparser.ru**, войдите, меню → «Push о новых объявлениях»
3. **Android Chrome** — push приходят при свёрнутой вкладке
4. **iPhone** — добавьте сайт «На экран Домой» (Safari → Поделиться), iOS 16.4+

Формат: заголовок **«Сигнал»**, текст — название объявления (или «N новых объявлений · …»).

## Привязка Avito (кнопка «Позвонить»)

1. На Android установите Kiwi Browser и расширение Cookie-Editor
2. Откройте сайт парсера в Kiwi
3. Войдите на `m.avito.ru`
4. Экспортируйте cookies (JSON) и вставьте в меню «Привязка Avito»

## Диагностика

```bash
python tools/bench_speed.py --log logs/parser.log
```
