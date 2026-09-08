"""Память о том, какие объявления уже были в выдаче.

Без неё платно продвинутые объявления всплывали бы в ленте повторно: Avito
поднимает их в выдаче спустя время, и по дате публикации они выглядят как
новые.

Файл на диске нужен, чтобы перезапуск парсера не заваливал ленту тем, что
пользователь уже видел. Запись троттлится: за сутки набирается несколько
тысяч ID, и писать их на каждом цикле незачем.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from avito_monitor.paths import SEEN_PATH

SAVE_THROTTLE_SEC = 30.0


class SeenStore:
    """Множество ID объявлений с ленивой записью на диск."""

    def __init__(self, path: Path | None = None, throttle: float = SAVE_THROTTLE_SEC) -> None:
        self._path = path if path is not None else SEEN_PATH
        self._throttle = throttle
        self._saved_at = 0.0
        self.ids: set[int] = set()

    def load(self) -> set[int]:
        """Прочитать сохранённые ID. Битый файл считаем пустым."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self.ids = {int(value) for value in raw}
        except (OSError, ValueError, TypeError):
            self.ids = set()
        return self.ids

    def reset(self) -> None:
        """Забыть всё и сразу зафиксировать это на диске.

        Вызывается при старте нового поиска: объявления прошлого запроса
        к новому отношения не имеют.
        """
        self.ids = set()
        self.save(force=True)

    def save(self, *, force: bool = False) -> None:
        """Записать ID на диск, не чаще одного раза в ``throttle`` секунд."""
        now = time.time()
        if not force and self._throttle and now - self._saved_at < self._throttle:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(sorted(self.ids)), encoding="utf-8")
        self._saved_at = now

    def __len__(self) -> int:
        return len(self.ids)

    def __contains__(self, ad_id: int) -> bool:
        return ad_id in self.ids
