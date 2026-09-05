"""Фоновый сервис пула cookies: держит наборы разблокированными."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from loguru import logger

from cookie_pool import COOKIES_DIR, load_config, maintain, pool_size

PID_PATH = COOKIES_DIR / "service.pid"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    # На Windows os.kill(pid, 0) = CTRL_C_EVENT и убивает процесс.
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        exit_code = wintypes.DWORD()
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        return bool(ok) and exit_code.value == 259
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def service_running() -> bool:
    if not PID_PATH.exists():
        return False
    try:
        pid = int(PID_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if _pid_alive(pid):
        return True
    PID_PATH.unlink(missing_ok=True)
    return False


def _write_pid() -> None:
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()), encoding="utf-8")


def run_loop() -> None:
    from log_setup import setup_file_log

    setup_file_log("cookies.log")
    cfg = load_config()
    pause = max(30, int(cfg.get("cookie_unblock_pause") or 60))
    _write_pid()
    logger.info(f"Сервис пула cookies, размер {pool_size(cfg)}, пауза {pause} сек.")
    try:
        while True:
            try:
                maintain(cfg)
            except Exception as err:
                logger.error(f"Ошибка цикла пула: {err}")
            time.sleep(pause)
    finally:
        if PID_PATH.exists() and PID_PATH.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID_PATH.unlink(missing_ok=True)


def start_background() -> bool:
    if service_running():
        logger.info("Сервис пула cookies уже запущен")
        return False
    thread = threading.Thread(target=run_loop, name="cookie-service", daemon=True)
    thread.start()
    return True


if __name__ == "__main__":
    if service_running():
        raise SystemExit("Сервис пула уже запущен")
    run_loop()
