"""Фоновое обслуживание пула cookies.

Сервис держит наборы разблокированными, пока цикл опроса работает. Обычно
запускается в потоке вместе с парсером; отдельным процессом
(``python -m avito_monitor.cookies.service``) — если нужно обслуживать пул,
не запуская мониторинг.

PID-файл защищает от двух обслуживающих процессов одновременно: они мешали
бы друг другу, выкупая наборы сверх нужного количества.
"""

from __future__ import annotations

import os
import threading
import time

from loguru import logger

from avito_monitor.config import Settings, load_settings
from avito_monitor.cookies import pool
from avito_monitor.paths import COOKIES_DIR

PID_PATH = COOKIES_DIR / "service.pid"

_WINDOWS_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# True, когда этот процесс сам взял обслуживание. Нужно отличать свой
# живой поток от leftover PID в Docker: контейнер почти всегда PID 1,
# а файл лежит на томе и переживает restart.
_owned_by_this_process = False


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс с таким PID."""
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _pid_alive_windows(pid: int) -> bool:
    """Проверка через WinAPI.

    ``os.kill(pid, 0)`` на Windows означает ``CTRL_C_EVENT`` и убивает
    процесс, поэтому вместо него открываем процесс и смотрим код выхода.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    exit_code = wintypes.DWORD()
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
    kernel32.CloseHandle(handle)
    return bool(ok) and exit_code.value == _WINDOWS_STILL_ACTIVE


def service_running() -> bool:
    """Обслуживает ли пул кто-то ещё. Устаревший PID-файл удаляется."""
    if _owned_by_this_process:
        return True
    if not PID_PATH.exists():
        return False
    try:
        pid = int(PID_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if _pid_alive(pid) and pid != os.getpid():
        return True
    # Свой PID в файле, но поток мы не запускали — это leftover после
    # docker restart: новый контейнер снова PID 1, старый файл врёт.
    PID_PATH.unlink(missing_ok=True)
    return False


def _claim_pid() -> None:
    global _owned_by_this_process
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    _owned_by_this_process = True


def _release_pid() -> None:
    """Убрать свой PID-файл, не тронув чужой."""
    global _owned_by_this_process
    try:
        if PID_PATH.exists() and PID_PATH.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID_PATH.unlink(missing_ok=True)
    except OSError:
        pass
    _owned_by_this_process = False


def run_loop(settings: Settings | None = None) -> None:
    """Обслуживать пул до остановки процесса."""
    from avito_monitor.logging_setup import setup_file_log

    setup_file_log("cookies.log")
    settings = settings or load_settings()
    pause = settings.cookie_unblock_pause
    _claim_pid()
    logger.info(f"Сервис пула cookies: размер {pool.pool_size(settings)}, пауза {pause} с")
    try:
        while True:
            try:
                pool.maintain(settings)
            except Exception as err:
                # Сеть и сервис могут отваливаться — сервис обслуживания
                # должен переживать это и пробовать снова.
                logger.error(f"Ошибка цикла пула: {err}")
            time.sleep(pause)
    finally:
        _release_pid()


def start_background(settings: Settings | None = None) -> bool:
    """Запустить обслуживание в фоновом потоке.

    ``False`` — пул уже обслуживает другой процесс.
    """
    if service_running():
        logger.info("Сервис пула cookies уже запущен")
        return False
    thread = threading.Thread(
        target=run_loop,
        args=(settings,),
        name="cookie-service",
        daemon=True,
    )
    thread.start()
    return True


def main() -> None:
    if service_running():
        raise SystemExit("Сервис пула уже запущен")
    run_loop()


if __name__ == "__main__":
    main()
