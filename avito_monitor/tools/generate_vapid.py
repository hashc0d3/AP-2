"""Создать пару VAPID-ключей для Web Push.

Запуск: ``python -m avito_monitor.tools.generate_vapid``

Ключи кладутся в ``storage/``. Приватный ключ нужен серверу для подписи
уведомлений, публичный — браузеру при подписке. Повторный запуск
перезаписывает пару: старые подписки после этого станут недействительными,
и браузеры подпишутся заново автоматически.
"""

from __future__ import annotations

import base64
import sys

from avito_monitor.paths import STORAGE_DIR, VAPID_PRIVATE_PATH, VAPID_PUBLIC_PATH


def main() -> int:
    try:
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid02
    except ImportError:
        print("Установите зависимости: pip install -r requirements.txt", file=sys.stderr)
        return 1

    if VAPID_PRIVATE_PATH.exists():
        answer = input(f"{VAPID_PRIVATE_PATH} уже есть. Перезаписать? [y/N] ").strip().lower()
        if answer not in {"y", "yes", "д", "да"}:
            print("Отменено.")
            return 0

    vapid = Vapid02()
    vapid.generate_keys()

    raw_public = vapid.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_key = base64.urlsafe_b64encode(raw_public).decode("ascii").rstrip("=")

    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    VAPID_PRIVATE_PATH.write_text(vapid.private_pem().decode("utf-8"), encoding="utf-8")
    VAPID_PUBLIC_PATH.write_text(public_key, encoding="utf-8")

    print("Ключи сохранены:")
    print(f"  {VAPID_PRIVATE_PATH}  (приватный, не коммитить)")
    print(f"  {VAPID_PUBLIC_PATH}  (публичный)")
    print()
    print("Сервер подхватит ключи сам. В .env можно указать только контакт:")
    print("  VAPID_CONTACT=mailto:admin@example.com")
    return 0


if __name__ == "__main__":
    sys.exit(main())
