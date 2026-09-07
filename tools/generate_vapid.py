#!/usr/bin/env python3
"""Сгенерировать VAPID-ключи для Web Push."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization

STORAGE = Path("storage")
PRIVATE_PATH = STORAGE / "vapid_private.pem"
PUBLIC_PATH = STORAGE / "vapid_public.key"


def main() -> int:
    try:
        from py_vapid import Vapid02
    except ImportError:
        print("Установите: pip install py-vapid pywebpush", file=sys.stderr)
        return 1

    vapid = Vapid02()
    vapid.generate_keys()

    pub_raw = vapid.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public = base64.urlsafe_b64encode(pub_raw).decode("ascii").rstrip("=")
    private_pem = vapid.private_pem().decode("utf-8")

    STORAGE.mkdir(parents=True, exist_ok=True)
    PRIVATE_PATH.write_text(private_pem, encoding="utf-8")
    PUBLIC_PATH.write_text(public, encoding="utf-8")

    print("Сохранено:")
    print(f"  {PRIVATE_PATH}")
    print(f"  {PUBLIC_PATH}")
    print()
    print("В .env достаточно (приватный ключ — в файле storage/vapid_private.pem):")
    print(f"VAPID_PUBLIC_KEY={public}")
    print("VAPID_CONTACT=mailto:admin@peterparser.ru")
    print()
    print("Удалите VAPID_PRIVATE_KEY из .env, если был — он ломается при \\n в docker.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
