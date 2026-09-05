"""Умный анализ цен через SPFA batch_lookup."""

from __future__ import annotations

import time

import requests
from loguru import logger

from parser import load_config

SPFA_BATCH_LOOKUP_URL = "https://spfa.pro/api/batch_lookup/"
SPFA_BATCH_STATUS_URL = "https://spfa.pro/api/batch_lookup/{task_id}/"
SPFA_BALANCE_URL = "https://spfa.pro/api/balance/"
_MAX_QUERIES = 5


def _api_key() -> str:
    key = (load_config().get("cookies_api_key") or "").strip()
    if not key:
        raise ValueError("Не настроен cookies_api_key в config.toml")
    return key


def _request_json(method: str, url: str, **kwargs) -> dict:
    try:
        response = requests.request(method, url, timeout=45, **kwargs)
    except requests.RequestException as err:
        raise RuntimeError(f"Не удалось связаться с SPFA: {err}") from err
    if response.status_code == 429:
        raise RuntimeError("Лимит SPFA: подождите минуту (анализ цен)")
    try:
        payload = response.json()
    except ValueError as err:
        raise RuntimeError(f"SPFA вернул не JSON: {response.text[:240]}") from err
    if not isinstance(payload, dict):
        raise RuntimeError("SPFA вернул неожиданный ответ")
    if not response.ok:
        message = payload.get("message") or payload.get("error") or response.text[:240]
        raise RuntimeError(f"SPFA {response.status_code}: {message}")
    return payload


def start_batch_lookup(queries: list[str], region: str = "all") -> dict:
    cleaned = [str(item).strip() for item in queries if str(item).strip()]
    if not cleaned:
        raise ValueError("Укажите объявления для анализа")
    if len(cleaned) > _MAX_QUERIES:
        raise ValueError(f"Максимум {_MAX_QUERIES} объявлений за запрос")
    payload = _request_json(
        "POST",
        SPFA_BATCH_LOOKUP_URL,
        json={"api_key": _api_key(), "region": region or "all", "queries": cleaned},
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    if not payload.get("success") or not payload.get("task_id"):
        raise RuntimeError(f"SPFA не создал задание: {payload}")
    logger.info(f"SPFA batch_lookup: task {payload['task_id']} ({len(cleaned)} шт.)")
    return payload


def batch_lookup_status(task_id: str) -> dict:
    task_id = str(task_id or "").strip()
    if not task_id:
        raise ValueError("Укажите task_id")
    payload = _request_json(
        "GET",
        SPFA_BATCH_STATUS_URL.format(task_id=task_id),
        headers={"Accept": "application/json"},
    )
    return payload


def wait_batch_lookup(task_id: str, timeout: float = 90.0, pause: float = 1.5) -> dict:
    deadline = time.time() + max(5.0, timeout)
    while time.time() < deadline:
        payload = batch_lookup_status(task_id)
        status = str(payload.get("status") or "").upper()
        if status == "SUCCESS":
            return payload
        if status in {"FAILED", "ERROR", "CANCELLED"}:
            raise RuntimeError(payload.get("message") or f"Анализ цен завершился со статусом {status}")
        time.sleep(pause)
    raise RuntimeError("Таймаут ожидания анализа цен")


def fetch_balance() -> dict:
    payload = _request_json(
        "POST",
        SPFA_BALANCE_URL,
        json={"api_key": _api_key()},
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    if not payload.get("success"):
        raise RuntimeError(payload.get("message") or "SPFA не вернул баланс")
    balance = payload.get("balance")
    if balance is None:
        raise RuntimeError("SPFA не вернул баланс")
    return {"success": True, "balance": float(balance)}
