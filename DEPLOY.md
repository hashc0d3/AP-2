# Деплой на сервер (Docker)

## Что нужно на сервере

- Linux (Ubuntu/Debian и т.п.)
- Docker Engine 24+ и Docker Compose v2
- Порты **80** и **443** (nginx). Порт **8765** снаружи **не открывать**

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

Локально на сервере: `curl http://127.0.0.1:8765/`. Снаружи — только через домен (nginx).

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

### peterparser.ru (пошагово)

**DNS:** A-запись `peterparser.ru` (и при необходимости `www`) → IP сервера.

**1. Приложение** (Docker слушает только localhost — снаружи заходит nginx):

```bash
cd /opt/AP-2   # или каталог проекта
git pull origin AV-5
cp config.toml.example config.toml   # если ещё нет
cp .env.example .env                 # заполнить секреты
```

В `.env` оставьте `WEB_PORT=8765`. Запуск:

```bash
docker compose up -d --build
curl -I http://127.0.0.1:8765/
```

**2. Nginx** (Debian/Ubuntu):

```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx

sudo cp deploy/nginx/peterparser.ru.conf /etc/nginx/sites-available/peterparser.ru
sudo ln -sf /etc/nginx/sites-available/peterparser.ru /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default   # если мешает default-сайт
sudo nginx -t && sudo systemctl reload nginx
```

**3. SSL (Let's Encrypt):**

```bash
sudo certbot --nginx -d peterparser.ru -d www.peterparser.ru
```

Certbot сам добавит `listen 443 ssl` и редирект с HTTP. Проверка продления:

```bash
sudo certbot renew --dry-run
```

**4. Firewall:**

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw status
```

Порт **8765** наружу не открывайте — доступ только через nginx на 443.

**5. Только домен (без IP:8765):**

В `.env` на сервере:

```env
ALLOWED_HOST=peterparser.ru
```

Docker слушает только localhost (см. `docker-compose.yml`: `127.0.0.1:8765`). Пересборка:

```bash
docker compose up -d --build
ss -tlnp | grep 8765   # должно быть 127.0.0.1:8765, не 0.0.0.0
```

Firewall (если 8765 был открыт):

```bash
sudo ufw delete allow 8765/tcp 2>/dev/null; sudo ufw status
```

Nginx-конфиг: `deploy/nginx/peterparser.ru.conf` → `/opt/infra-proxy/conf.d/40-peterparser.ru.conf`

Откройте: **https://peterparser.ru**

**6. Web Push (фоновые уведомления на телефоне):**

```bash
pip install pywebpush py-vapid
python tools/generate_vapid.py
```

Скопируйте ключи в `.env`, пересоберите контейнер:

```bash
docker compose up -d --build
```

В меню включите «Push о новых объявлениях». На **iPhone** добавьте сайт **«На экран Домой»** (Safari → Поделиться) — иначе push только при открытой вкладке.

---

### Общий шаблон nginx

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
        proxy_set_header X-Forwarded-Proto $scheme;
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
