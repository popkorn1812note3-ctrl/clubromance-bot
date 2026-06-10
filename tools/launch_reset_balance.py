#!/usr/bin/env python
"""Одноразовый срез балансов перед запуском: всем, у кого больше LAUNCH_BALANCE
кристаллов, ставим ровно LAUNCH_BALANCE (стартовые 50 были слишком щедрыми —
пропадал стимул выполнять задания). У кого меньше — не трогаем.

Запуск на сервере:
  sudo -u clubromance /opt/clubromance/.venv/bin/python tools/launch_reset_balance.py

Каждый срез пишется в ledger (type=spend, meta=launch:reset_to_10) — видно в
журнале операций юзера в админке.
"""
from __future__ import annotations

import asyncio
import sys
import time

sys.path.insert(0, ".")

from app.config import load_config  # noqa: E402
from app.db import DB  # noqa: E402

LAUNCH_BALANCE = 10


async def main() -> None:
    cfg = load_config()
    db = DB(cfg.db_abspath)
    await db.connect()
    cur = await db.conn.execute(
        "SELECT user_id, crystals FROM users WHERE crystals > ?", (LAUNCH_BALANCE,)
    )
    rows = [(int(r["user_id"]), int(r["crystals"])) for r in await cur.fetchall()]
    now = int(time.time())
    for uid, bal in rows:
        delta = LAUNCH_BALANCE - bal  # отрицательное
        await db.conn.execute("UPDATE users SET crystals=? WHERE user_id=?", (LAUNCH_BALANCE, uid))
        await db.conn.execute(
            "INSERT INTO ledger (user_id, type, amount, meta, created_at) VALUES (?,?,?,?,?)",
            (uid, "spend", delta, "launch:reset_to_10", now),
        )
    await db.conn.commit()
    total = (await (await db.conn.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
    print(f"Готово: срезано {len(rows)} юзеров до {LAUNCH_BALANCE} кристаллов (всего юзеров {total}).")
    if rows:
        preview = ", ".join(f"{uid}:{bal}->{LAUNCH_BALANCE}" for uid, bal in rows[:10])
        print(f"Примеры: {preview}{' …' if len(rows) > 10 else ''}")
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
