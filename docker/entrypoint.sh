#!/bin/sh
set -eu

mkdir -p /app/storage/cookies /app/logs

if [ ! -f /app/config.toml ]; then
  echo "ERROR: /app/config.toml не найден." >&2
  echo "Скопируйте config.toml на сервер или смонтируйте volume:" >&2
  echo "  ./config.toml:/app/config.toml:ro" >&2
  exit 1
fi

exec "$@"
