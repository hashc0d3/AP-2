"""Запрос номера телефона объявления."""

from __future__ import annotations

from avito_monitor.avito import phone
from avito_monitor.avito.user_session import normalize_import


class _Client:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}


def test_extract_phone_from_json() -> None:
    assert phone.extract_phone({"phone": "+79001234567"}) == "+79001234567"


def test_extract_phone_from_action_uri_tel() -> None:
    payload = {"status": "ok", "result": {"action": {"uri": "tel:+79001112233"}}}
    assert phone.extract_phone(payload) == "+79001112233"


def test_extract_phone_from_action_uri_number() -> None:
    payload = {
        "status": "ok",
        "result": {"action": {"uri": "avito://ru.avito://call?number=%2B79001112233"}},
    }
    assert phone.extract_phone(payload) == "+79001112233"


def test_extract_phone_ignores_scheme_version_in_uri() -> None:
    """``ru.avito://1/...`` не должен превращать +7 в +1."""
    payload = {
        "status": "ok",
        "result": {
            "action": {"uri": "ru.avito://1/call?number=%2B79161234567"},
        },
    }
    assert phone.extract_phone(payload) == "+79161234567"


def test_extract_phone_normalizes_eight() -> None:
    assert phone.extract_phone({"phone": "89001112233"}) == "+79001112233"


def test_needs_auth_detects_status() -> None:
    assert phone.needs_auth({"status": "unauthorized"}) is True
    assert phone.needs_auth({"status": "ok"}) is False


def test_fetch_phone_asks_number_before_item_card(monkeypatch) -> None:
    """Ключ карточки не должен блокировать быстрый путь к номеру."""
    calls: list[str] = []

    def fake_get(_client, url: str, params: dict | None = None):
        calls.append(url)
        return 200, {"phone": "+79001234567"}

    monkeypatch.setattr(phone, "_get", fake_get)
    result = phone.fetch_phone(_Client(), 8301859285)
    assert result == {"ok": True, "phone": "+79001234567"}
    assert "phone" in calls[0]
    assert all("/items/" not in url or "/phone" in url for url in calls)


def test_fetch_phone_uses_supplied_key_first(monkeypatch) -> None:
    seen: list[dict | None] = []

    def fake_get(_client, url: str, params: dict | None = None):
        seen.append(params)
        return 200, {"result": {"action": {"uri": "tel:+79001112233"}}}

    monkeypatch.setattr(phone, "_get", fake_get)
    result = phone.fetch_phone(_Client(), 1, phone_key="abc")
    assert result["ok"] is True
    assert seen[0] == {"key": "abc"}


def test_fetch_phone_rejects_non_numeric_id() -> None:
    result = phone.fetch_phone(_Client(), "abc")
    assert result["ok"] is False
    assert result["code"] == "bad_id"


def test_cookie_editor_accepts_capitalized_keys() -> None:
    parsed = normalize_import([{"Name": "sessid", "Value": "abc"}])
    assert parsed["cookies"]["sessid"] == "abc"
