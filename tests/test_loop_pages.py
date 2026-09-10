"""Настройки цикла: один запрос выдачи, без ``p=1`` / ``p=2``."""

from avito_monitor.config import Settings
from avito_monitor.monitor.loop import _runtime_settings


def test_runtime_settings_strip_paid_serp() -> None:
    """Старый адрес из spfa чистим на каждом цикле, без перезапуска поиска."""
    runtime = _runtime_settings(
        Settings(),
        {
            "web_url": "https://www.avito.ru/moskva/telefony?s=1",
            "api_url": (
                "https://www.avito.ru/web/1/js/items?locationId=637640"
                "&presentationType=serp&sort=date&s=1&owner[]=private"
            ),
            "category": {},
        },
    )
    assert "presentationType" not in runtime.api_url
    assert "sort=" not in runtime.api_url
    assert "s=104" in runtime.api_url
    assert "s=1" not in runtime.api_url.replace("s=104", "")
    assert "s=104" in runtime.web_url
    assert "owner%5B%5D=private" in runtime.api_url
    assert "privateOnly=1" in runtime.api_url
    assert "p=" not in runtime.api_url
    assert "page=" not in runtime.api_url
