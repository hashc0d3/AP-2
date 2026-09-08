# Установка на сервер

Схема простая: приложение в Docker слушает только `127.0.0.1`, а наружу его
выводит nginx с сертификатом Let's Encrypt. Порт `8765` в интернет не
открывается никогда.

```
интернет ──443──▶ nginx (хост) ──▶ 127.0.0.1:8765 ──▶ контейнер avito-monitor
```

## Что нужно на сервере

- Linux (Ubuntu/Debian и подобные)
- Docker Engine 24+ и Docker Compose v2
- открытые порты 80 и 443
- домен с A-записью на IP сервера

## 1. Проект и настройки

```bash
git clone <url-репозитория> avito-monitor
cd avito-monitor

cp config.toml.example config.toml
cp .env.example .env
nano .env          # прокси, ключ сервиса cookies, логин/пароль
nano config.toml   # фильтры и интервалы, если нужно
```

В `.env` для сервера:

```env
WEB_PORT=8765
ALLOWED_HOST=monitor.example.com
```

`ALLOWED_HOST` закрывает вход по IP в обход nginx и TLS. Полный список
переменных — [configuration.md](configuration.md).

`.env` в git не попадает. Если файла нет и секреты не переданы через
`env_file`, контейнер предупредит об этом при старте; отсутствие
`config.toml` — ошибка запуска.

## 2. Запуск

```bash
docker compose up -d --build
```

Проверка:

```bash
docker compose ps
docker compose logs -f monitor
curl -I http://127.0.0.1:8765/
```

Контейнер публикует порт как `127.0.0.1:${WEB_PORT}:8765`, поэтому снаружи
он не отвечает — это ожидаемо. Убедиться, что порт действительно локальный:

```bash
ss -tlnp | grep 8765   # должно быть 127.0.0.1:8765, а не 0.0.0.0:8765
```

## 3. Nginx и HTTPS

```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx

sudo cp deploy/nginx/peterparser.ru.conf /etc/nginx/sites-available/monitor.conf
sudo nano /etc/nginx/sites-available/monitor.conf    # подставить свой домен
sudo ln -sf /etc/nginx/sites-available/monitor.conf /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default          # если мешает
sudo nginx -t && sudo systemctl reload nginx

sudo certbot --nginx -d monitor.example.com
sudo certbot renew --dry-run
```

Certbot сам добавит `listen 443 ssl` и редирект с HTTP.

Минимальный конфиг, если делать вручную:

```nginx
server {
    listen 80;
    server_name monitor.example.com;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Лента объявлений приходит потоком (SSE): буферизация её ломает.
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400s;
    }
}
```

`proxy_buffering off` и большой `proxy_read_timeout` обязательны: без них
поток `/events` рвётся и лента перестаёт обновляться сама.

## 4. Firewall

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw delete allow 8765/tcp 2>/dev/null   # если когда-то открывали
sudo ufw status
```

## 5. Push-уведомления

Ключи VAPID нужно создать один раз:

```bash
docker compose exec monitor python -m avito_monitor.tools.generate_vapid
```

Ключи сохраняются в `storage/` (том `parser-storage`) и переживают
пересборку образа. Если удобнее держать их в окружении — скопируйте вывод в
`.env` (`VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`) и перезапустите контейнер.

Дальше в интерфейсе: меню пользователя → «Push о новых объявлениях». На
iPhone сайт нужно добавить «На экран Домой» (Safari → Поделиться) — иначе
push приходят только при открытой вкладке. Требуется iOS 16.4+.

Push работают только по HTTPS, поэтому сначала нужен шаг 3.

## 6. Обновление

```bash
git pull
docker compose up -d --build
```

Данные лежат в томах и переживают пересборку. Логи после обновления:

```bash
docker compose logs -f --tail=100 monitor
```

## 7. Данные

| Том              | Что внутри                                            |
| ---------------- | ----------------------------------------------------- |
| `parser-storage` | cookies, лента, сессии, подписки push, ключи VAPID     |
| `parser-logs`    | `parser.log`, `cookies.log`, `cookie_lifecycle.log`    |

Имена томов исторические и специально не переименованы: новые имена создали
бы пустые тома, а вместе с ними потерялись бы купленные cookies и ключи
push.

Резервная копия:

```bash
docker run --rm -v parser-storage:/data -v "$PWD":/backup alpine \
  tar czf /backup/storage-$(date +%F).tar.gz -C /data .
```

`config.toml` подключён к контейнеру только для чтения, поэтому правится на
хосте — изменения подхватываются перезапуском:

```bash
docker compose restart monitor
```

## Другой внешний порт

```env
WEB_PORT=8080
```

```bash
docker compose up -d
```

Внутри контейнера порт всегда `8765`; меняется только сторона хоста.

## Запуск без Docker

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.toml.example config.toml
cp .env.example .env      # заполнить
python -m avito_monitor
```

Так удобно проверять настройки и смотреть логи в консоли, но для постоянной
работы лучше Docker: он сам поднимает приложение после перезагрузки сервера
(`restart: unless-stopped`) и следит за здоровьем через healthcheck.
