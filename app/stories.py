"""Загрузка историй из stories/*.json в реестр."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import ROOT
from .engine import Story

log = logging.getLogger("stories")

STORIES_DIR = ROOT / "stories"


class Registry:
    def __init__(self) -> None:
        self._stories: dict[str, Story] = {}

    def load(self) -> None:
        self._stories.clear()
        if not STORIES_DIR.exists():
            log.warning("Каталог историй не найден: %s", STORIES_DIR)
            return
        for path in sorted(STORIES_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                log.error("Не удалось прочитать %s: %s", path.name, e)
                continue
            sid = data.get("id")
            if not sid:
                log.error("В %s нет поля id — пропуск", path.name)
                continue
            self._stories[sid] = Story(data)
            log.info("Загружена история %s (%s сцен)", sid, len(data.get("scenes", {})))

    def get(self, story_id: str) -> Story | None:
        return self._stories.get(story_id)

    def all(self) -> list[Story]:
        # Доступные первыми, дальше — по названию.
        return sorted(self._stories.values(), key=lambda s: (not s.available, s.title))


registry = Registry()
