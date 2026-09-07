#!/bin/sh
set -eu

mkdir -p /app/storage/cookies /app/logs

if [ ! -f /app/config.toml ]; then
  echo "ERROR: /app/config.toml не найден." >&2
  echo "Скопируйте config.toml.example и создайте .env с секретами." >&2
  exit 1
fi

if [ ! -f /app/.env ] && [ -z "${COOKIES_API_KEY:-}" ]; then
  echo "WARNING: .env не найден — убедитесь, что секреты переданы через env_file." >&2
fi

exec "$@"
