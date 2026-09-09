"""Защита PID-файла сервиса пула cookies."""

from __future__ import annotations

import os

from avito_monitor.cookies import service


def test_stale_self_pid_is_ignored() -> None:
    """После docker restart файл часто содержит PID 1 нового контейнера."""
    service.PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    service.PID_PATH.write_text(str(os.getpid()), encoding="utf-8")

    assert service.service_running() is False
    assert not service.PID_PATH.exists()


def test_foreign_live_pid_is_respected() -> None:
    parent = os.getppid()
    if parent <= 0:
        return
    service.PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    service.PID_PATH.write_text(str(parent), encoding="utf-8")

    assert service.service_running() is True
    assert service.PID_PATH.exists()


def test_claimed_pid_counts_as_running() -> None:
    service._claim_pid()
    try:
        assert service.service_running() is True
    finally:
        service._release_pid()
    assert service.service_running() is False
