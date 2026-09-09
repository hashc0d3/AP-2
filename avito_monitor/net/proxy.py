"""Мобильный прокси: проверка готовности, смена IP и обход VPN.

Мобильный прокси — основной способ обойти лимиты Avito: по команде он меняет
внешний IP. Смена занимает несколько секунд, поэтому мы не ждём фиксированную
паузу, а опрашиваем туннель до готовности.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time

import requests
from loguru import logger

# Домены операторов мобильных прокси, чей трафик должен идти мимо VPN.
PROXY_HOSTS = ("mproxy.site", "fproxy.site", "bproxy.site", "gproxy.site")

# Лёгкая страница Avito: важен сам факт ответа, а не содержимое.
PROBE_URL = "https://www.avito.ru/robots.txt"
PROBE_TIMEOUT = 3.0
CHANGE_IP_TIMEOUT = 20.0
_POLL_STEP = 0.3


def proxy_is_live(proxy_string: str, timeout: float = PROBE_TIMEOUT) -> bool:
    """Поднялся ли туннель.

    Любой HTTP-код считаем успехом: важно, что прокси уже отвечает, а как
    Avito отнесётся к новому IP — выяснится в рабочем цикле.
    """
    if not proxy_string:
        return True
    proxy_url = f"http://{proxy_string}"
    try:
        requests.head(PROBE_URL, proxies={"http": proxy_url, "https": proxy_url}, timeout=timeout)
    except requests.RequestException:
        return False
    return True


def change_ip(change_url: str, proxy_string: str = "", wait_max: float = 12.0) -> None:
    """Сменить внешний IP мобильного прокси и дождаться туннеля.

    :raises RuntimeError: сервис смены IP не ответил.
    """
    if not change_url:
        raise RuntimeError("Не настроен PROXY_CHANGE_URL в .env")

    started = time.time()
    logger.info(f"Меняю IP: {change_url.split('?')[0]}")
    try:
        response = requests.get(change_url, params={"format": "json"}, timeout=CHANGE_IP_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as err:
        raise RuntimeError(f"Смена IP не прошла: {err}") from err

    logger.info(f"Новый IP: {_reported_ip(response) or 'ок'}")

    if wait_max <= 0:
        return

    deadline = started + wait_max
    while time.time() < deadline:
        if proxy_is_live(proxy_string):
            logger.info(f"Прокси готов через {time.time() - started:.1f} с")
            return
        time.sleep(_POLL_STEP)
    logger.warning(f"Прокси не ответил за {wait_max:.0f} с после смены IP")


def _reported_ip(response: requests.Response) -> str:
    """Новый адрес из ответа сервиса, если он его вернул."""
    content_type = response.headers.get("content-type", "")
    if "json" not in content_type and not response.text.strip().startswith("{"):
        return ""
    try:
        payload = response.json()
    except ValueError:
        return ""
    return str(payload.get("new_ip") or "") if isinstance(payload, dict) else ""


# ── Обход VPN на Windows ───────────────────────────────────────────────────


def _wifi_gateway() -> str | None:
    """Шлюз обычного подключения, минуя интерфейсы VPN."""
    script = (
        "Get-NetRoute -DestinationPrefix '0.0.0.0/0' | "
        "Where-Object { $_.NextHop -ne '0.0.0.0' -and "
        "$_.InterfaceAlias -notmatch 'Amnezia|WireGuard|TAP|OpenVPN' } | "
        "Sort-Object RouteMetric | Select-Object -First 1 -ExpandProperty NextHop"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return (result.stdout or "").strip() or None


def ensure_proxy_bypasses_vpn() -> None:
    """Пустить трафик к мобильному прокси мимо VPN (только Windows).

    VPN нужен, чтобы открывался сайт оператора прокси, но сам прокси через
    VPN работает нестабильно. Поэтому для его адресов прописываем маршрут
    через обычный шлюз. В Docker и на Linux шаг пропускается — там VPN нет,
    и ``SKIP_VPN_BYPASS=1`` выставлен в образе.
    """
    if os.name != "nt" or os.environ.get("SKIP_VPN_BYPASS") == "1":
        return

    gateway = _wifi_gateway()
    if not gateway:
        logger.warning("Не нашёл Wi-Fi шлюз, split-tunnel для прокси не настроен")
        return

    for host in PROXY_HOSTS:
        try:
            ip = socket.gethostbyname(host)
        except OSError as err:
            logger.warning(f"Не резолвится {host}: {err}")
            continue
        added = subprocess.run(
            ["route", "add", ip, "mask", "255.255.255.255", gateway],
            capture_output=True,
            text=True,
            encoding="cp866",
            errors="replace",
            check=False,
        )
        if added.returncode == 0:
            logger.info(f"Маршрут мимо VPN: {host} ({ip}) → {gateway}")
        else:
            reason = added.stdout.strip() or added.stderr.strip()
            logger.debug(f"Маршрут {ip} уже есть или нет прав: {reason}")
