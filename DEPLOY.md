# Деплой на сервер (Docker)

## Что нужно на сервере

- Linux (Ubuntu/Debian и т.п.)
- Docker Engine 24+ и Docker Compose v2
- Открытый порт **8765** (или свой, см. ниже)

## 1. Загрузить проект на сервер

```bash
# с вашего ПК (git)
git clone <url-репозитория> parser1
cd parser1

# или scp архивом
scp -r parser1 user@your-server:/opt/parser1
```

## 2. Конфиг

На сервере положите рабочий `config.toml` в корень проекта:

```bash
cp config.toml.example config.toml
nano config.toml   # прокси, cookies_api_key, proxy_change_url
```

Проверьте `web_port = 8765` (порт внутри контейнера).

## 3. Сборка и запуск

```bash
cd /opt/parser1
docker compose up -d --build
```

Проверка:

```bash
docker compose ps
docker compose logs -f parser
curl -I http://127.0.0.1:8765/
```

Откройте в браузере: `http://IP-СЕРВЕРА:8765`

## 4. Обновление после изменений кода

```bash
git pull          # если через git
docker compose up -d --build
```

## 5. Данные

Docker volumes (сохраняются между перезапусками):

- `parser-storage` — cookies, аккаунты, подписки, объявления
- `parser-logs` — логи парсера и cookie-сервиса

Посмотреть логи:

```bash
docker compose logs -f --tail=100 parser
```

## 6. Другой внешний порт

В `.env` рядом с `docker-compose.yml`:

```env
WEB_PORT=8080
```

```bash
docker compose up -d
```

## 7. HTTPS через Nginx (рекомендуется для продакшена)

Пример `/etc/nginx/sites-available/parser1`:

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

        # SSE — лента объявлений
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400s;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/parser1 /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

SSL: `certbot --nginx -d signal.example.com`

## 8. Firewall

```bash
# UFW — только nginx снаружи, порт 8765 закрыт
sudo ufw allow 80
sudo ufw allow 443

# или напрямую без nginx
sudo ufw allow 8765/tcp
```

## Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `WEB_HOST` | `0.0.0.0` | Адрес bind HTTP-сервера |
| `WEB_OPEN_BROWSER` | `0` | Не открывать браузер в контейнере |
| `SKIP_VPN_BYPASS` | `1` | Не настраивать Windows-маршруты VPN |

## Локальный запуск без Docker

Как раньше:

```bash
python parser.py
# http://127.0.0.1:8765
```
