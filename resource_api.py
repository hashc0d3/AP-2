"""Баланс сервисного ресурса (cookies API)."""

from __future__ import annotations

import requests
from loguru import logger

from settings import load_config

BALANCE_URL = "https://spfa.pro/api/balance/"


def _api_key() -> str:
    key = (load_config().get("cookies_api_key") or "").strip()
    if not key:
        raise ValueError("Не настроен COOKIES_API_KEY в .env")
    return key


def fetch_balance() -> dict:
    try:
        response = requests.post(
            BALANCE_URL,
            json={"api_key": _api_key()},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=45,
        )
    except requests.RequestException as err:
        raise RuntimeError(f"Не удалось связаться с ресурсом: {err}") from err
    if response.status_code == 429:
        raise RuntimeError("Лимит запросов — подождите минуту")
    try:
        payload = response.json()
    except ValueError as err:
        raise RuntimeError(f"Ресурс вернул не JSON: {response.text[:240]}") from err
    if not isinstance(payload, dict):
        raise RuntimeError("Ресурс вернул неожиданный ответ")
    if not response.ok:
        message = payload.get("message") or payload.get("error") or response.text[:240]
        raise RuntimeError(f"Ошибка ресурса {response.status_code}: {message}")
    if not payload.get("success"):
        raise RuntimeError(payload.get("message") or "Не удалось получить баланс")
    balance = payload.get("balance")
    if balance is None:
        raise RuntimeError("Ресурс не вернул баланс")
    logger.debug(f"Баланс ресурса: {balance}")
    return {"success": True, "balance": float(balance)}
