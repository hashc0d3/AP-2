"""Web Push: хранение подписок и отправка уведомлений."""

from __future__ import annotations

import base64
import json
import os
import threading
from pathlib import Path

from loguru import logger

SUBS_PATH = Path("storage") / "push_subscriptions.json"
VAPID_PRIVATE_PATH = Path("storage") / "vapid_private.pem"
VAPID_PUBLIC_PATH = Path("storage") / "vapid_public.key"
_lock = threading.RLock()


def _normalize_pem(raw: str) -> str:
    text = raw.strip().strip('"').strip("'")
    text = text.replace("\\n", "\n")
    if "BEGIN PRIVATE KEY" not in text:
        return text
    if not text.endswith("\n"):
        text += "\n"
    return text


def _load_vapid() -> object | None:
    try:
        from py_vapid import Vapid02
    except ImportError:
        logger.warning("py-vapid не установлен")
        return None

    pem = ""
    if VAPID_PRIVATE_PATH.is_file():
        pem = VAPID_PRIVATE_PATH.read_text(encoding="utf-8")
    elif os.environ.get("VAPID_PRIVATE_KEY", "").strip():
        pem = os.environ.get("VAPID_PRIVATE_KEY", "")

    pem = _normalize_pem(pem)
    if not pem or "BEGIN PRIVATE KEY" not in pem:
        return None

    try:
        vapid = Vapid02()
        vapid.from_pem(pem.encode("utf-8"))
        return vapid
    except Exception as err:
        logger.warning(f"VAPID private key invalid: {err}")
        return None


def _public_key_b64u(vapid: object) -> str | None:
    from cryptography.hazmat.primitives import serialization

    pub_raw = vapid.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(pub_raw).decode("ascii").rstrip("=")


def vapid_public_key() -> str | None:
    vapid = _load_vapid()
    if vapid:
        try:
            return _public_key_b64u(vapid)
        except Exception as err:
            logger.warning(f"VAPID public key error: {err}")
    env_pub = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
    if env_pub:
        return env_pub
    if VAPID_PUBLIC_PATH.is_file():
        stored = VAPID_PUBLIC_PATH.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    return None


def is_configured() -> bool:
    return _load_vapid() is not None and bool(vapid_public_key())


def _vapid_claims() -> dict[str, str]:
    contact = os.environ.get("VAPID_CONTACT", "mailto:admin@peterparser.ru").strip()
    return {"sub": contact}


def _load() -> list[dict]:
    if not SUBS_PATH.exists():
        return []
    try:
        data = json.loads(SUBS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _save(subs: list[dict]) -> None:
    SUBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUBS_PATH.write_text(json.dumps(subs, ensure_ascii=False, indent=2), encoding="utf-8")


def subscribe(subscription: dict) -> int:
    endpoint = str(subscription.get("endpoint") or "").strip()
    keys = subscription.get("keys")
    if not endpoint or not isinstance(keys, dict):
        raise ValueError("Некорректная подписка push")
    if not str(keys.get("p256dh") or "").strip() or not str(keys.get("auth") or "").strip():
        raise ValueError("Некорректные ключи подписки")

    entry = {
        "endpoint": endpoint,
        "keys": {
            "p256dh": str(keys["p256dh"]),
            "auth": str(keys["auth"]),
        },
    }
    with _lock:
        subs = [item for item in _load() if item.get("endpoint") != endpoint]
        subs.append(entry)
        _save(subs)
        logger.info(f"Push: подписка сохранена, всего {len(subs)}")
        return len(subs)


def unsubscribe(endpoint: str) -> int:
    endpoint = endpoint.strip()
    if not endpoint:
        raise ValueError("Нет endpoint")
    with _lock:
        subs = [item for item in _load() if item.get("endpoint") != endpoint]
        _save(subs)
        return len(subs)


def subscription_count() -> int:
    with _lock:
        return len(_load())


def _format_payload(ads: list[dict]) -> str:
    first = ads[0] if ads else {}
    title = str(first.get("title") or "").strip() or "Новое объявление"
    body = title if len(ads) == 1 else f"{len(ads)} новых объявлений · {title}"
    return json.dumps({"title": "Сигнал", "body": body, "url": "/"}, ensure_ascii=False)


def _send_all(payload: str) -> None:
    vapid = _load_vapid()
    if not vapid:
        logger.warning("Push: VAPID не настроен — уведомление не отправлено")
        return

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning("pywebpush не установлен — push не отправлен")
        return

    with _lock:
        subs = list(_load())
    if not subs:
        logger.debug("Push: нет подписчиков — уведомление не отправлено")
        return

    dead: list[str] = []
    sent = 0
    for sub in subs:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=vapid,
                vapid_claims=_vapid_claims(),
            )
            sent += 1
        except WebPushException as err:
            status = getattr(getattr(err, "response", None), "status_code", None)
            if status in {404, 410}:
                endpoint = sub.get("endpoint")
                if endpoint:
                    dead.append(str(endpoint))
            logger.warning(f"Push → {status or 'err'}: {err}")
        except Exception as err:
            logger.warning(f"Push error: {err}")

    if dead:
        with _lock:
            subs = [item for item in _load() if item.get("endpoint") not in dead]
            _save(subs)
        logger.info(f"Push: удалено мёртвых подписок {len(dead)}")

    if sent:
        logger.info(f"Push: отправлено {sent} уведомлений")
    elif subs:
        logger.warning(f"Push: не удалось отправить ни одному из {len(subs)} подписчиков")


def notify_new_ads(ads: list[dict]) -> None:
    if not ads or not is_configured():
        return
    payload = _format_payload(ads)
    threading.Thread(target=_send_all, args=(payload,), name="web-push", daemon=True).start()


def send_test_push() -> bool:
    if not is_configured():
        return False
    payload = json.dumps(
        {"title": "Сигнал", "body": "Уведомления включены", "url": "/"},
        ensure_ascii=False,
    )
    _send_all(payload)
    return subscription_count() > 0
