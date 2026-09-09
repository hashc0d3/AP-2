"""Запрос номера телефона объявления."""

from __future__ import annotations

from avito_monitor.avito import phone


class _Client:
    headers: dict[str, str] = {}


def test_extract_phone_from_json() -> None:
    assert phone.extract_phone({"phone": "+79001234567"}) == "+79001234567"


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


def test_fetch_phone_rejects_non_numeric_id() -> None:
    result = phone.fetch_phone(_Client(), "abc")
    assert result["ok"] is False
    assert result["code"] == "bad_id"
