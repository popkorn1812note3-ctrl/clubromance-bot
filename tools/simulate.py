# -*- coding: utf-8 -*-
"""Оффлайн-симулятор: прогоняет истории через реальный диспетчер с фейковым
MAX-клиентом. Проверяет движок, меню, БД, ввод имени, UI-шапку и целостность
сюжета без живого токена.

  python tools/simulate.py            # авто-прогон + ассерты по обеим историям
  python tools/simulate.py --print    # ещё и печать транскрипта
"""
import asyncio
import sys
import tempfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import DB
from app.dispatcher import dispatch
from app.engine import visible_choices
from app.runtime import Context
from app.stories import registry


class FakeApi:
    def __init__(self):
        self.sent: list[dict] = []      # новые сообщения
        self.edits: list[dict] = []     # правки на месте
        self.shown: list[dict] = []     # всё показанное (sends + edits) — для проверок
        self.answers: list[dict] = []
        self.deleted: list[str] = []
        self._n = 0

    async def send_message(self, *, user_id=None, chat_id=None, text="", keyboard=None, fmt="markdown", image_url=None):
        self._n += 1
        mid = f"m{self._n}"
        rec = {"user_id": user_id, "text": text, "kb": keyboard, "mid": mid}
        self.sent.append(rec)
        self.shown.append(rec)
        return {"message": {"body": {"mid": mid}}}

    async def edit_message(self, message_id, *, text="", keyboard=None, fmt="markdown", image_url=None):
        rec = {"mid": message_id, "text": text, "kb": keyboard}
        self.edits.append(rec)
        self.shown.append(rec)
        return {"success": True}

    @staticmethod
    def extract_mid(resp):
        return ((resp or {}).get("message") or {}).get("body", {}).get("mid")

    async def delete_message(self, message_id):
        self.deleted.append(message_id)
        return {"success": True}

    async def answer_callback(self, callback_id, *, notification=None, text=None):
        self.answers.append({"cb": callback_id, "notification": notification, "text": text})
        return {"ok": True}

    async def close(self):
        pass


def cb_update(uid, payload):
    return {"update_type": "message_callback",
            "callback": {"callback_id": "cb", "payload": payload, "user": {"user_id": uid}}}


def text_update(uid, text):
    return {"update_type": "message_created",
            "message": {"sender": {"user_id": uid}, "body": {"text": text}}}


async def run_strategy(story_id, pick, *, hero_name=None, grant=3000, verbose=False):
    tmp = Path(tempfile.gettempdir()) / f"cr_sim_{story_id}_{id(pick)}.db"
    tmp.unlink(missing_ok=True)
    db = DB(str(tmp)); await db.connect(); registry.load()
    api = FakeApi()
    ctx = Context(db=db, api=api, registry=registry, bot_username="clubromance_bot")
    uid = 777
    story = registry.get(story_id)
    assert story, f"история {story_id} не загрузилась"

    await dispatch(ctx, {"update_type": "bot_started", "user": {"user_id": uid, "first_name": "Тест"}})
    await db.add_crystals(uid, grant, "grant", "sim")

    # старт → вводный экран → выбор имени
    await dispatch(ctx, cb_update(uid, f"st:start:{story_id}"))
    if hero_name:
        await dispatch(ctx, cb_update(uid, f"st:name:{story_id}"))   # просим имя
        await dispatch(ctx, text_update(uid, hero_name))             # вводим имя
    else:
        await dispatch(ctx, cb_update(uid, f"st:noname:{story_id}"))

    steps = 0
    while steps < 400:
        steps += 1
        prog = await db.get_progress(uid, story_id)
        assert prog, "прогресс пропал"
        scene = story.scene(prog["current_scene"])
        vchoices = visible_choices(scene, prog["vars"])
        if scene.get("final"):
            break
        if vchoices:
            i = max(0, min(pick(vchoices), len(vchoices) - 1))
            await dispatch(ctx, cb_update(uid, f"pl:{story_id}:c:{i}"))
        else:
            await dispatch(ctx, cb_update(uid, f"pl:{story_id}:n"))
    else:
        raise AssertionError(f"[{story_id}] не достигли финала за 400 шагов")

    completion = await db.get_completion(uid, story_id)
    achievements = await db.list_achievements(uid)
    # Проверки UI: шапка с балансом, выделение имён, аннотации выбора.
    gameplay = [m for m in api.shown if "💎" in m["text"] and "┄" in m["text"]]
    has_header = bool(gameplay)
    has_annotation = any(
        ("+" in b["text"] and "·" in b["text"])
        for m in api.shown if m["kb"]
        for row in m["kb"] for b in row
    )
    hero_shown = (hero_name is None) or any(hero_name in m["text"] for m in api.shown)
    if verbose:
        print(f"\n===== ТРАНСКРИПТ [{story_id}] (всего новых сообщений={len(api.sent)}, правок={len(api.edits)}) =====")
        for m in api.shown[:6]:
            print("  ·", m["text"].replace("\n", " ⏎ ")[:120])
    await db.close(); tmp.unlink(missing_ok=True)
    return dict(completion=completion, ach=achievements, steps=steps, vars=prog["vars"],
                has_header=has_header, has_annotation=has_annotation, hero_shown=hero_shown)


async def main():
    verbose = "--print" in sys.argv
    strategies = {"first": lambda ch: 0, "last": lambda ch: len(ch) - 1, "middle": lambda ch: len(ch) // 2}

    for story_id in ("heirs", "vampire"):
        print(f"\n=== История «{story_id}» ===")
        for name, pick in strategies.items():
            r = await run_strategy(story_id, pick, verbose=(verbose and name == "first"))
            assert r["completion"] is not None, f"[{story_id}/{name}] не отмечена пройденной"
            assert r["ach"], f"[{story_id}/{name}] нет достижений"
            assert r["has_header"], f"[{story_id}/{name}] нет UI-шапки (баланс+статы)"
            assert r["has_annotation"], f"[{story_id}/{name}] нет аннотаций (+N эмодзи) на кнопках"
            print(f"  [{name:6}] шагов={r['steps']:3} концовка='{r['completion']['ending_title']}'"
                  f" секрет={'да' if r['completion']['secret'] else 'нет'} достижений={len(r['ach'])}")

    # Ввод имени героини
    r = await run_strategy("heirs", lambda ch: 0, hero_name="Анна")
    assert r["hero_shown"], "имя героини «Анна» не подставилось в тексты"
    assert r["vars"].get("_hero") == "Анна", "_hero не сохранён"
    print(f"\n  [имя] подстановка «Анна» ✅  (_hero={r['vars']['_hero']})")

    # Секретная концовка (heirs, премиум-выбор)
    r = await run_strategy("heirs", lambda ch: 0)
    assert r["completion"]["secret"] == 1 and "secret_letter" in r["ach"], "секретная концовка не открылась"
    print(f"  [секрет] секретная концовка + достижение ✅")

    print("\n✅ Обе истории целостны; UI-шапка, аннотации, имя героини и достижения работают.")


if __name__ == "__main__":
    asyncio.run(main())
