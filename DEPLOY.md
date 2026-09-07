# Деплой на сервер (Docker)

## Что нужно на сервере

- Linux (Ubuntu/Debian и т.п.)
- Docker Engine 24+ и Docker Compose v2
- Открытый порт **8765** (или свой, см. `.env`)

## 1. Загрузить проект

```bash
git clone <url-репозитория> parser1
cd parser1
```

## 2. Конфигурация

```bash
cp config.toml.example config.toml
cp .env.example .env
nano .env          # прокси, ключ ресурса, логин/пароль
nano config.toml   # фильтры и интервалы (по необходимости)
```

Секреты храните только в `.env`, не коммитьте его в git.

## 3. Сборка и запуск

```bash
docker compose up -d --build
```

Проверка:

```bash
docker compose ps
docker compose logs -f parser
curl -I http://127.0.0.1:8765/
```

Откройте в браузере: `http://IP-СЕРВЕРА:8765`

## 4. Обновление

```bash
git pull
docker compose up -d --build
```

## 5. Данные

Docker volumes (сохраняются между перезапусками):

- `parser-storage` — cookies, сессии, объявления
- `parser-logs` — логи парсера и cookie-сервиса

```bash
docker compose logs -f --tail=100 parser
```

## 6. Другой внешний порт

В `.env`:

```env
WEB_PORT=8080
```

```bash
docker compose up -d
```

## 7. HTTPS через Nginx

```nginx
server {
    listen 80;
    server_name signal.example.com;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400s;
    }
}
```

SSL: `certbot --nginx -d signal.example.com`

## 8. Firewall

```bash
sudo ufw allow 80
sudo ufw allow 443
# или напрямую:
sudo ufw allow 8765/tcp
```

## Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `PROXY_STRING` | — | HTTP-прокси |
| `COOKIES_API_KEY` | — | Ключ сервиса cookies |
| `APP_LOGIN` | `admin` | Логин веб-интерфейса |
| `APP_PASSWORD` | `change_me` | Пароль |
| `WEB_PORT` | `8765` | Порт |
| `WEB_HOST` | `0.0.0.0` | Адрес bind |
| `WEB_OPEN_BROWSER` | `0` | Не открывать браузер в контейнере |
| `SKIP_VPN_BYPASS` | `1` | Не настраивать Windows-маршруты VPN |

## Локальный запуск без Docker

```bash
pip install -r requirements.txt
cp config.toml.example config.toml
cp .env.example .env
python parser.py
```
