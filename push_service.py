"""Web Push: хранение подписок и отправка уведомлений."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from loguru import logger

SUBS_PATH = Path("storage") / "push_subscriptions.json"
_lock = threading.RLock()


def _vapid_keys() -> tuple[str, str] | None:
    pub = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
    priv = os.environ.get("VAPID_PRIVATE_KEY", "").strip().replace("\\n", "\n")
    if pub and priv:
        return pub, priv
    return None


def vapid_public_key() -> str | None:
    keys = _vapid_keys()
    return keys[0] if keys else None


def is_configured() -> bool:
    return vapid_public_key() is not None


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
    keys = _vapid_keys()
    if not keys:
        return

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning("pywebpush не установлен — push не отправлен")
        return

    _, private_key = keys
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
                vapid_private_key=private_key,
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
