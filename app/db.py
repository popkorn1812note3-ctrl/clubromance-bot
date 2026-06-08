"""Хранилище на SQLite (aiosqlite).

Игровые переменные истории храним JSON-блобом `vars_json` в `progress` —
это позволяет добавлять новые истории с новыми переменными без миграций схемы.

Кристальную экономику ведём журналом `ledger` (источник правды по операциям),
баланс кешируем в `users.crystals`.

Замена на Postgres (как в Podpiski/GramMax) — отдельной итерацией; слой запросов
изолирован в этом модуле.
"""
from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite

START_CRYSTALS = 50            # стартовый баланс нового игрока
DAILY_COOLDOWN = 24 * 3600     # ежедневная награда раз в 24ч

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id        INTEGER PRIMARY KEY,
    name           TEXT,
    username       TEXT,
    language       TEXT    NOT NULL DEFAULT 'ru',
    crystals       INTEGER NOT NULL DEFAULT 0,
    notifications  INTEGER NOT NULL DEFAULT 1,
    last_daily     INTEGER NOT NULL DEFAULT 0,
    subscribed     INTEGER NOT NULL DEFAULT 0,
    referred_by    INTEGER,
    pending        TEXT,
    created_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS unlocks (
    user_id   INTEGER NOT NULL,
    story_id  TEXT    NOT NULL,
    at        INTEGER NOT NULL,
    PRIMARY KEY (user_id, story_id)
);

CREATE TABLE IF NOT EXISTS progress (
    user_id        INTEGER NOT NULL,
    story_id       TEXT    NOT NULL,
    current_scene  TEXT    NOT NULL,
    current_chapter TEXT,
    vars_json      TEXT    NOT NULL DEFAULT '{}',
    status         TEXT    NOT NULL DEFAULT 'in_progress',  -- in_progress | completed
    updated_at     INTEGER NOT NULL,
    PRIMARY KEY (user_id, story_id)
);

CREATE TABLE IF NOT EXISTS completions (
    user_id      INTEGER NOT NULL,
    story_id     TEXT    NOT NULL,
    ending_code  TEXT,
    ending_title TEXT,
    secret       INTEGER NOT NULL DEFAULT 0,
    plays        INTEGER NOT NULL DEFAULT 1,
    completed_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, story_id)
);

CREATE TABLE IF NOT EXISTS achievements (
    user_id     INTEGER NOT NULL,
    code        TEXT    NOT NULL,
    unlocked_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, code)
);

CREATE TABLE IF NOT EXISTS ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    type       TEXT    NOT NULL,   -- daily | subscribe | invite | spend | buy | grant
    amount     INTEGER NOT NULL,   -- +начисление / -списание (в кристаллах)
    meta       TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS referrals (
    inviter_id INTEGER NOT NULL,
    invited_id INTEGER NOT NULL,
    at         INTEGER NOT NULL,
    PRIMARY KEY (inviter_id, invited_id)
);
"""


def _now() -> int:
    return int(time.time())


class DB:
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Лёгкие миграции для уже существующих БД (без Alembic)."""
        cur = await self._conn.execute("PRAGMA table_info(users)")
        cols = {r["name"] for r in await cur.fetchall()}
        if "pending" not in cols:
            await self._conn.execute("ALTER TABLE users ADD COLUMN pending TEXT")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "DB не инициализирована (вызовите connect())"
        return self._conn

    # ── Пользователи ──────────────────────────────────────────
    async def ensure_user(self, user_id: int, name: str = "", username: str = "") -> dict[str, Any]:
        row = await self.get_user(user_id)
        if row is None:
            await self.conn.execute(
                "INSERT INTO users (user_id, name, username, crystals, created_at) VALUES (?,?,?,?,?)",
                (user_id, name, username, START_CRYSTALS, _now()),
            )
            await self.conn.execute(
                "INSERT INTO ledger (user_id, type, amount, meta, created_at) VALUES (?,?,?,?,?)",
                (user_id, "grant", START_CRYSTALS, "welcome", _now()),
            )
            await self.conn.commit()
            return await self.get_user(user_id)  # type: ignore[return-value]
        # Обновим имя/юзернейм, если изменились и пришли непустыми.
        updates: dict[str, Any] = {}
        if name and name != row["name"]:
            updates["name"] = name
        if username and username != row["username"]:
            updates["username"] = username
        if updates:
            await self.update_user(user_id, **updates)
            return await self.get_user(user_id)  # type: ignore[return-value]
        return row

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def update_user(self, user_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        await self.conn.execute(f"UPDATE users SET {cols} WHERE user_id=?", (*fields.values(), user_id))
        await self.conn.commit()

    async def add_crystals(self, user_id: int, delta: int, type_: str, meta: str = "") -> int:
        await self.conn.execute("UPDATE users SET crystals = crystals + ? WHERE user_id=?", (delta, user_id))
        await self.conn.execute(
            "INSERT INTO ledger (user_id, type, amount, meta, created_at) VALUES (?,?,?,?,?)",
            (user_id, type_, delta, meta, _now()),
        )
        await self.conn.commit()
        u = await self.get_user(user_id)
        return int(u["crystals"]) if u else 0

    async def spend_crystals(self, user_id: int, amount: int, type_: str = "spend", meta: str = "") -> bool:
        """Атомарное списание. False, если не хватает баланса."""
        u = await self.get_user(user_id)
        if not u or u["crystals"] < amount:
            return False
        await self.add_crystals(user_id, -abs(amount), type_, meta)
        return True

    # ── Награды ───────────────────────────────────────────────
    async def claim_daily(self, user_id: int, reward: int) -> tuple[bool, int, int]:
        """(успех, баланс, секунд_до_следующей). Если рано — успех=False."""
        u = await self.ensure_user(user_id)
        elapsed = _now() - int(u["last_daily"])
        if u["last_daily"] and elapsed < DAILY_COOLDOWN:
            return False, int(u["crystals"]), DAILY_COOLDOWN - elapsed
        await self.update_user(user_id, last_daily=_now())
        bal = await self.add_crystals(user_id, reward, "daily", "daily_reward")
        return True, bal, 0

    async def claim_subscribe(self, user_id: int, reward: int) -> tuple[bool, int]:
        """Одноразовая награда за подписку. (успех, баланс)."""
        u = await self.ensure_user(user_id)
        if u["subscribed"]:
            return False, int(u["crystals"])
        await self.update_user(user_id, subscribed=1)
        bal = await self.add_crystals(user_id, reward, "subscribe", "channel_subscribe")
        return True, bal

    async def add_referral(self, inviter_id: int, invited_id: int, reward: int) -> bool:
        """Засчитать приглашённого (если ещё не считали и это не сам себя). True — начислено."""
        if inviter_id == invited_id:
            return False
        invited = await self.get_user(invited_id)
        if invited and invited["referred_by"]:
            return False  # уже привязан к кому-то
        try:
            await self.conn.execute(
                "INSERT INTO referrals (inviter_id, invited_id, at) VALUES (?,?,?)",
                (inviter_id, invited_id, _now()),
            )
        except aiosqlite.IntegrityError:
            return False
        await self.conn.execute("UPDATE users SET referred_by=? WHERE user_id=?", (inviter_id, invited_id))
        await self.conn.commit()
        await self.add_crystals(inviter_id, reward, "invite", f"ref:{invited_id}")
        return True

    async def count_referrals(self, user_id: int) -> int:
        cur = await self.conn.execute("SELECT COUNT(*) AS c FROM referrals WHERE inviter_id=?", (user_id,))
        row = await cur.fetchone()
        return int(row["c"]) if row else 0

    # ── Разблокировка историй ─────────────────────────────────
    async def is_unlocked(self, user_id: int, story_id: str) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM unlocks WHERE user_id=? AND story_id=?", (user_id, story_id)
        )
        return await cur.fetchone() is not None

    async def unlock_story(self, user_id: int, story_id: str) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO unlocks (user_id, story_id, at) VALUES (?,?,?)",
            (user_id, story_id, _now()),
        )
        await self.conn.commit()

    # ── Прогресс ──────────────────────────────────────────────
    async def get_progress(self, user_id: int, story_id: str) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            "SELECT * FROM progress WHERE user_id=? AND story_id=?", (user_id, story_id)
        )
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["vars"] = json.loads(d.pop("vars_json") or "{}")
        return d

    async def save_progress(
        self,
        user_id: int,
        story_id: str,
        scene: str,
        chapter: str | None,
        variables: dict[str, Any],
        status: str = "in_progress",
    ) -> None:
        await self.conn.execute(
            """INSERT INTO progress (user_id, story_id, current_scene, current_chapter, vars_json, status, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(user_id, story_id) DO UPDATE SET
                   current_scene=excluded.current_scene,
                   current_chapter=excluded.current_chapter,
                   vars_json=excluded.vars_json,
                   status=excluded.status,
                   updated_at=excluded.updated_at""",
            (user_id, story_id, scene, chapter, json.dumps(variables, ensure_ascii=False), status, _now()),
        )
        await self.conn.commit()

    async def delete_progress(self, user_id: int, story_id: str | None = None) -> None:
        if story_id:
            await self.conn.execute("DELETE FROM progress WHERE user_id=? AND story_id=?", (user_id, story_id))
        else:
            await self.conn.execute("DELETE FROM progress WHERE user_id=?", (user_id,))
        await self.conn.commit()

    # ── Завершения историй ────────────────────────────────────
    async def mark_completed(
        self, user_id: int, story_id: str, ending_code: str, ending_title: str, secret: bool = False
    ) -> None:
        await self.conn.execute(
            """INSERT INTO completions (user_id, story_id, ending_code, ending_title, secret, plays, completed_at)
               VALUES (?,?,?,?,?,1,?)
               ON CONFLICT(user_id, story_id) DO UPDATE SET
                   ending_code=excluded.ending_code,
                   ending_title=excluded.ending_title,
                   secret=MAX(completions.secret, excluded.secret),
                   plays=completions.plays + 1,
                   completed_at=excluded.completed_at""",
            (user_id, story_id, ending_code, ending_title, 1 if secret else 0, _now()),
        )
        await self.conn.commit()

    async def get_completion(self, user_id: int, story_id: str) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            "SELECT * FROM completions WHERE user_id=? AND story_id=?", (user_id, story_id)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def mark_secret(self, user_id: int, story_id: str) -> None:
        await self.conn.execute(
            "UPDATE completions SET secret=1 WHERE user_id=? AND story_id=?", (user_id, story_id)
        )
        await self.conn.commit()

    async def stats(self, user_id: int) -> dict[str, int]:
        c = self.conn
        completed = (await (await c.execute(
            "SELECT COUNT(*) AS x FROM completions WHERE user_id=?", (user_id,))).fetchone())["x"]
        secret = (await (await c.execute(
            "SELECT COUNT(*) AS x FROM completions WHERE user_id=? AND secret=1", (user_id,))).fetchone())["x"]
        ach = (await (await c.execute(
            "SELECT COUNT(*) AS x FROM achievements WHERE user_id=?", (user_id,))).fetchone())["x"]
        return {"completed": int(completed), "secret": int(secret), "achievements": int(ach)}

    # ── Достижения ────────────────────────────────────────────
    async def award_achievement(self, user_id: int, code: str) -> bool:
        try:
            await self.conn.execute(
                "INSERT INTO achievements (user_id, code, unlocked_at) VALUES (?,?,?)",
                (user_id, code, _now()),
            )
            await self.conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def list_achievements(self, user_id: int) -> set[str]:
        cur = await self.conn.execute("SELECT code FROM achievements WHERE user_id=?", (user_id,))
        return {r["code"] for r in await cur.fetchall()}

    async def recent_ledger(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT type, amount, meta, created_at FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]
