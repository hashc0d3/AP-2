#!/usr/bin/env python3
"""Сгенерировать VAPID-ключи для Web Push."""

from __future__ import annotations

import base64
import sys

from cryptography.hazmat.primitives import serialization


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
    private = vapid.private_pem().decode("utf-8").replace("\n", "\\n")

    print("Добавьте в .env:\n")
    print(f"VAPID_PUBLIC_KEY={public}")
    print(f"VAPID_PRIVATE_KEY={private}")
    print("VAPID_CONTACT=mailto:admin@peterparser.ru")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
