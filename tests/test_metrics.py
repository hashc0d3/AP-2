"""Замеры для страницы мониторинга: без cookies и без паролей прокси."""

from __future__ import annotations

from avito_monitor.metrics import METRICS, public_text
from avito_monitor.net.proxies import PROXY_POOL


def test_public_text_drops_proxy_password() -> None:
    assert public_text("сбой http://user:secret@mproxy.site:17733") == "сбой http://mproxy.site:17733"
    assert "secret" not in public_text("user:secret@mproxy.site:17733")


def test_request_counts_keep_only_the_proxy_label() -> None:
    METRICS.reset()
    METRICS.note_request("user:secret@mproxy.site:17733", "429", status=429, cookie_id=7)
    METRICS.note_request("user:secret@mproxy.site:17733", "json", status=200)

    snapshot = METRICS.snapshot()
    assert snapshot["requests"]["429"] == 1
    assert snapshot["requests"]["json"] == 1
    assert snapshot["events"][0]["proxy"] == "mproxy.site:17733"
    assert "secret" not in str(snapshot)


def test_proxy_snapshot_has_no_credentials() -> None:
    METRICS.reset()
    PROXY_POOL.configure((("user:secret@mproxy.site:17733", "https://rotate.example/secret"),))
    METRICS.note_request("mproxy.site:17733", "timeout")

    snapshot = METRICS.snapshot()
    blob = str(snapshot)
    assert "secret" not in blob
    assert "rotate.example" not in blob
    row = snapshot["proxies"][0]
    assert row["label"] == "mproxy.site:17733"
    assert row["requests"]["timeout"] == 1
