"""Web Push: уведомления о новых объявлениях при закрытой вкладке.

Для отправки нужна пара VAPID-ключей — она создаётся один раз командой
``python -m avito_monitor.tools.generate_vapid`` и лежит в
``storage/vapid_private.pem``. Публичный ключ отдаётся браузеру, приватным
подписываются запросы к push-сервису (Google, Apple и т.п.).

Приватный ключ держим в файле, а не в ``.env``: PEM многострочный, и при
передаче через переменные окружения Docker ломает переводы строк.
"""

from __future__ import annotations

import base64
import json
import os
import threading
from typing import Any

from loguru import logger

from avito_monitor.paths import PUSH_SUBSCRIPTIONS_PATH, VAPID_PRIVATE_PATH, VAPID_PUBLIC_PATH

NOTIFICATION_TITLE = "Сигнал"
DEFAULT_CONTACT = "mailto:admin@example.com"

# Push-сервис отвечает так, когда подписка больше не существует.
_DEAD_SUBSCRIPTION_STATUSES = {404, 410}
_PEM_MARKER = "BEGIN PRIVATE KEY"

_lock = threading.RLock()


# ── VAPID-ключи ────────────────────────────────────────────────────────────


def _normalize_pem(raw: str) -> str:
    """Привести PEM к виду, который понимает ``cryptography``."""
    text = raw.strip().strip('"').strip("'").replace("\\n", "\n")
    if _PEM_MARKER not in text:
        return text
    return text if text.endswith("\n") else text + "\n"


def _load_private_key(pem: str):
    from cryptography.hazmat.primitives import serialization

    return serialization.load_pem_private_key(pem.encode("utf-8"), password=None)


def _private_pem() -> str | None:
    """Приватный ключ: из файла, иначе из ``.env``. ``None`` — ключа нет."""
    if VAPID_PRIVATE_PATH.is_file():
        raw = VAPID_PRIVATE_PATH.read_text(encoding="utf-8")
    else:
        raw = os.environ.get("VAPID_PRIVATE_KEY", "")
    pem = _normalize_pem(raw)
    if not pem or _PEM_MARKER not in pem:
        return None
    try:
        _load_private_key(pem)
    except Exception as err:
        logger.warning(f"VAPID: приватный ключ не читается — {err}")
        return None
    return pem


def _signing_key():
    """Объект ``Vapid02`` для подписи запросов; ``None`` — ключа нет."""
    pem = _private_pem()
    if not pem:
        return None
    try:
        from py_vapid import Vapid02

        vapid = Vapid02()
        vapid.private_key = _load_private_key(pem)
        return vapid
    except Exception as err:
        logger.warning(f"VAPID: не удалось подготовить ключ подписи — {err}")
        return None


def vapid_public_key() -> str | None:
    """Публичный ключ в base64url — его браузер передаёт push-сервису."""
    pem = _private_pem()
    if pem:
        try:
            from cryptography.hazmat.primitives import serialization

            raw = (
                _load_private_key(pem)
                .public_key()
                .public_bytes(
                    encoding=serialization.Encoding.X962,
                    format=serialization.PublicFormat.UncompressedPoint,
                )
            )
            return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        except Exception as err:
            logger.warning(f"VAPID: не удалось вычислить публичный ключ — {err}")

    from_env = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
    if from_env:
        return from_env
    if VAPID_PUBLIC_PATH.is_file():
        return VAPID_PUBLIC_PATH.read_text(encoding="utf-8").strip() or None
    return None


def is_configured() -> bool:
    """Готов ли сервер отправлять push."""
    return _signing_key() is not None and bool(vapid_public_key())


def _claims() -> dict[str, str]:
    """Контакт администратора — его требует спецификация VAPID."""
    return {"sub": os.environ.get("VAPID_CONTACT", DEFAULT_CONTACT).strip() or DEFAULT_CONTACT}


# ── Подписки ───────────────────────────────────────────────────────────────


def _read() -> list[dict]:
    if not PUSH_SUBSCRIPTIONS_PATH.exists():
        return []
    try:
        data = json.loads(PUSH_SUBSCRIPTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _write(subscriptions: list[dict]) -> None:
    PUSH_SUBSCRIPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PUSH_SUBSCRIPTIONS_PATH.write_text(
        json.dumps(subscriptions, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def subscribe(subscription: dict[str, Any]) -> int:
    """Сохранить подписку браузера. Возвращает общее число подписок.

    :raises ValueError: браузер прислал неполную подписку.
    """
    endpoint = str(subscription.get("endpoint") or "").strip()
    keys = subscription.get("keys")
    if not endpoint or not isinstance(keys, dict):
        raise ValueError("Некорректная подписка push")
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not p256dh or not auth:
        raise ValueError("Некорректные ключи подписки")

    with _lock:
        # Один endpoint — одна запись: браузер пересоздаёт подписку при
        # смене VAPID-ключа, и старая запись стала бы мусором.
        subscriptions = [item for item in _read() if item.get("endpoint") != endpoint]
        subscriptions.append({"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}})
        _write(subscriptions)
        logger.info(f"Push: подписка сохранена, всего {len(subscriptions)}")
        return len(subscriptions)


def unsubscribe(endpoint: str) -> int:
    """Удалить подписку. Возвращает общее число оставшихся.

    :raises ValueError: не передан endpoint.
    """
    endpoint = (endpoint or "").strip()
    if not endpoint:
        raise ValueError("Нет endpoint")
    with _lock:
        subscriptions = [item for item in _read() if item.get("endpoint") != endpoint]
        _write(subscriptions)
        return len(subscriptions)


def subscription_count() -> int:
    with _lock:
        return len(_read())


# ── Отправка ───────────────────────────────────────────────────────────────


def _ad_line(ad: dict) -> str:
    title = str(ad.get("title") or "").strip() or "Новое объявление"
    price = str(ad.get("price") or "").strip()
    return f"{title} · {price}" if price and price not in {"—", "-"} else title


def _payload(ads: list[dict]) -> str:
    """Текст уведомления: одно объявление или сводка по нескольким."""
    line = _ad_line(ads[0]) if ads else "Новое объявление"
    body = line if len(ads) == 1 else f"{len(ads)} новых объявлений · {line}"
    return json.dumps({"title": NOTIFICATION_TITLE, "body": body, "url": "/"}, ensure_ascii=False)


def _send_all(payload: str) -> None:
    """Отправить уведомление всем подписчикам, отсеяв мёртвые подписки."""
    vapid = _signing_key()
    if not vapid:
        logger.warning("Push: VAPID не настроен — уведомление не отправлено")
        return
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning("Push: pywebpush не установлен — уведомление не отправлено")
        return

    with _lock:
        subscriptions = list(_read())
    if not subscriptions:
        logger.debug("Push: нет подписчиков")
        return

    dead: list[str] = []
    sent = 0
    for subscription in subscriptions:
        try:
            webpush(
                subscription_info=subscription,
                data=payload,
                vapid_private_key=vapid,
                vapid_claims=_claims(),
            )
            sent += 1
        except WebPushException as err:
            status = getattr(getattr(err, "response", None), "status_code", None)
            if status in _DEAD_SUBSCRIPTION_STATUSES:
                endpoint = subscription.get("endpoint")
                if endpoint:
                    dead.append(str(endpoint))
            logger.warning(f"Push → {status or 'ошибка'}: {err}")
        except Exception as err:
            logger.warning(f"Push: непредвиденная ошибка отправки — {err}")

    if dead:
        with _lock:
            _write([item for item in _read() if item.get("endpoint") not in dead])
        logger.info(f"Push: удалено мёртвых подписок {len(dead)}")

    if sent:
        logger.info(f"Push: отправлено {sent} уведомлений")
    else:
        logger.warning(f"Push: ни одному из {len(subscriptions)} подписчиков не доставлено")


def notify_new_ads(ads: list[dict]) -> None:
    """Уведомить о новых объявлениях, не задерживая цикл опроса."""
    if not ads or not is_configured():
        return
    threading.Thread(
        target=_send_all,
        args=(_payload(ads),),
        name="web-push",
        daemon=True,
    ).start()


def send_test_push() -> bool:
    """Отправить проверочное уведомление при включении тумблера."""
    if not is_configured():
        return False
    payload = json.dumps(
        {"title": NOTIFICATION_TITLE, "body": "Уведомления включены", "url": "/"},
        ensure_ascii=False,
    )
    _send_all(payload)
    return subscription_count() > 0
