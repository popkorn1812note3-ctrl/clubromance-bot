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
    gate_passed    INTEGER NOT NULL DEFAULT 0,
    gate_checked_at INTEGER NOT NULL DEFAULT 0,
    created_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    chat_id   INTEGER PRIMARY KEY,
    title     TEXT,
    link      TEXT,
    required  INTEGER NOT NULL DEFAULT 1,   -- 1 = канал обязательной подписки (гейт)
    reward    INTEGER NOT NULL DEFAULT 0,   -- >0 = канал-задание «подписка за награду»
    hold_days INTEGER NOT NULL DEFAULT 0,   -- сколько дней держать подписку, иначе отзыв награды
    added_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS images (
    key        TEXT PRIMARY KEY,   -- 'bg:<story>:<bgkey>' | 'scene:<story>:<sceneid>'
    photos     TEXT NOT NULL,      -- JSON dict 'photos' из MAX upload
    updated_at INTEGER NOT NULL
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

CREATE TABLE IF NOT EXISTS subscription_claims (
    user_id    INTEGER NOT NULL,
    chat_id    INTEGER NOT NULL,
    reward     INTEGER NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'active',   -- active | revoked
    claimed_at INTEGER NOT NULL,
    check_at   INTEGER NOT NULL DEFAULT 0,          -- когда проверить удержание (0 = не проверять)
    revoked_at INTEGER,
    PRIMARY KEY (user_id, chat_id)
);

CREATE TABLE IF NOT EXISTS link_tasks (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,    -- задания «по ссылке»: старт бота, гс-чат, раздача
    title    TEXT    NOT NULL,
    link     TEXT    NOT NULL,
    reward   INTEGER NOT NULL DEFAULT 0,
    added_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS link_task_claims (
    user_id    INTEGER NOT NULL,
    task_id    INTEGER NOT NULL,
    reward     INTEGER NOT NULL,
    claimed_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, task_id)
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
        """Лёгкие миграции для уже существующих БД (без Alembic): доливаем недостающие колонки."""
        async def table_cols(table: str) -> set[str]:
            cur = await self._conn.execute(f"PRAGMA table_info({table})")
            return {r["name"] for r in await cur.fetchall()}

        ucols = await table_cols("users")
        if "pending" not in ucols:
            await self._conn.execute("ALTER TABLE users ADD COLUMN pending TEXT")
        if "gate_passed" not in ucols:
            await self._conn.execute("ALTER TABLE users ADD COLUMN gate_passed INTEGER NOT NULL DEFAULT 0")
        if "gate_checked_at" not in ucols:
            await self._conn.execute("ALTER TABLE users ADD COLUMN gate_checked_at INTEGER NOT NULL DEFAULT 0")

        chcols = await table_cols("channels")
        if "reward" not in chcols:
            await self._conn.execute("ALTER TABLE channels ADD COLUMN reward INTEGER NOT NULL DEFAULT 0")
        if "hold_days" not in chcols:
            await self._conn.execute("ALTER TABLE channels ADD COLUMN hold_days INTEGER NOT NULL DEFAULT 0")

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
        """Атомарное списание одним условным UPDATE (без гонки check-then-act).
        False, если не хватает баланса."""
        amount = abs(int(amount))
        cur = await self.conn.execute(
            "UPDATE users SET crystals = crystals - ? WHERE user_id=? AND crystals >= ?",
            (amount, user_id, amount),
        )
        if cur.rowcount == 0:  # юзера нет или не хватило баланса
            await self.conn.commit()
            return False
        await self.conn.execute(
            "INSERT INTO ledger (user_id, type, amount, meta, created_at) VALUES (?,?,?,?,?)",
            (user_id, type_, -amount, meta, _now()),
        )
        await self.conn.commit()
        return True

    async def adjust_crystals(self, user_id: int, delta: int, meta: str = "admin") -> int:
        """Админская правка баланса на ±delta (не опуская ниже нуля). Пишет в журнал.
        Возвращает новый баланс."""
        u = await self.get_user(user_id)
        if not u:
            return 0
        delta = max(int(delta), -int(u["crystals"]))  # в минус не уходим
        if delta == 0:
            return int(u["crystals"])
        return await self.add_crystals(user_id, delta, "grant" if delta > 0 else "spend", meta)

    async def set_crystals(self, user_id: int, value: int, meta: str = "admin:set") -> int:
        """Установить точный баланс (через журнал — разницей). Возвращает новый баланс."""
        u = await self.get_user(user_id)
        if not u:
            return 0
        delta = max(0, int(value)) - int(u["crystals"])
        if delta == 0:
            return int(u["crystals"])
        return await self.add_crystals(user_id, delta, "grant" if delta > 0 else "spend", meta)

    # ── Управление пользователями (админка) ───────────────────
    @staticmethod
    def _user_filter(query: str) -> tuple[str, list[Any]]:
        """WHERE-хвост для поиска: числовой запрос — по user_id, иначе LIKE по имени/юзернейму."""
        q = (query or "").strip().lstrip("@")
        if not q:
            return "", []
        if q.lstrip("-").isdigit():
            return " WHERE user_id=?", [int(q)]
        like = f"%{q}%"
        return " WHERE name LIKE ? OR username LIKE ?", [like, like]

    async def list_users(self, query: str = "", limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        where, args = self._user_filter(query)
        cur = await self.conn.execute(
            f"SELECT * FROM users{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*args, limit, max(0, offset)),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def count_users(self, query: str = "") -> int:
        where, args = self._user_filter(query)
        cur = await self.conn.execute(f"SELECT COUNT(*) FROM users{where}", args)
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def list_progress(self, user_id: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT * FROM progress WHERE user_id=? ORDER BY updated_at DESC", (user_id,)
        )
        out: list[dict[str, Any]] = []
        for r in await cur.fetchall():
            d = dict(r)
            d["vars"] = json.loads(d.pop("vars_json") or "{}")
            out.append(d)
        return out

    async def list_completions(self, user_id: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT * FROM completions WHERE user_id=? ORDER BY completed_at DESC", (user_id,)
        )
        return [dict(r) for r in await cur.fetchall()]

    async def reset_user_game(self, user_id: int) -> None:
        """Сбросить игровой прогресс (истории, завершения, достижения, разблокировки).
        Профиль и баланс не трогаем."""
        for tbl in ("progress", "completions", "achievements", "unlocks"):
            await self.conn.execute(f"DELETE FROM {tbl} WHERE user_id=?", (user_id,))
        await self.conn.commit()

    async def delete_user(self, user_id: int) -> None:
        """Полное удаление пользователя и всех связанных записей."""
        for tbl in ("users", "unlocks", "progress", "completions", "achievements", "ledger"):
            await self.conn.execute(f"DELETE FROM {tbl} WHERE user_id=?", (user_id,))
        await self.conn.execute(
            "DELETE FROM referrals WHERE inviter_id=? OR invited_id=?", (user_id, user_id)
        )
        await self.conn.commit()

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

    # ── Каналы обязательной подписки (ОП / гейт) ──────────────
    async def upsert_channel(self, chat_id: int, title: str = "", link: str = "") -> None:
        await self.conn.execute(
            """INSERT INTO channels (chat_id, title, link, required, added_at) VALUES (?,?,?,1,?)
               ON CONFLICT(chat_id) DO UPDATE SET
                   title=COALESCE(NULLIF(excluded.title,''), channels.title),
                   link=COALESCE(NULLIF(excluded.link,''), channels.link)""",
            (chat_id, title, link, _now()),
        )
        await self.conn.commit()
        # Новый канал в гейте → все юзеры проходят гейт заново.
        await self.conn.execute("UPDATE users SET gate_passed=0")
        await self.conn.commit()

    async def remove_channel(self, chat_id: int) -> None:
        await self.conn.execute("DELETE FROM channels WHERE chat_id=?", (chat_id,))
        await self.conn.commit()

    async def list_required_channels(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute("SELECT * FROM channels WHERE required=1 ORDER BY added_at")
        return [dict(r) for r in await cur.fetchall()]

    async def set_gate_passed(self, user_id: int, value: int = 1) -> None:
        """Зафиксировать результат проверки ОП + момент проверки (для TTL-кеша)."""
        await self.conn.execute(
            "UPDATE users SET gate_passed=?, gate_checked_at=? WHERE user_id=?", (value, _now(), user_id)
        )
        await self.conn.commit()

    async def gate_stats(self) -> dict[str, int]:
        """Сводка по обязательной подписке: всего юзеров, прошли гейт, новые за день/неделю."""
        async def one(q: str, *a: Any) -> int:
            cur = await self.conn.execute(q, a)
            row = await cur.fetchone()
            return int(row[0]) if row else 0
        now = _now()
        total = await one("SELECT COUNT(*) FROM users")
        passed = await one("SELECT COUNT(*) FROM users WHERE gate_passed=1")
        return {
            "total": total,
            "passed": passed,
            "stuck": max(0, total - passed),
            "today": await one("SELECT COUNT(*) FROM users WHERE created_at>=?", now - 86400),
            "week": await one("SELECT COUNT(*) FROM users WHERE created_at>=?", now - 7 * 86400),
            "today_passed": await one(
                "SELECT COUNT(*) FROM users WHERE gate_passed=1 AND created_at>=?", now - 86400),
            "week_passed": await one(
                "SELECT COUNT(*) FROM users WHERE gate_passed=1 AND created_at>=?", now - 7 * 86400),
        }

    # ── Каналы-задания «подписка за награду» ──────────────────
    async def upsert_reward_channel(
        self, chat_id: int, title: str = "", link: str = "", reward: int = 0, hold_days: int = 0
    ) -> None:
        """Добавить/обновить канал-задание (required=0, не трогает гейт ОП)."""
        await self.conn.execute(
            """INSERT INTO channels (chat_id, title, link, required, reward, hold_days, added_at)
               VALUES (?,?,?,0,?,?,?)
               ON CONFLICT(chat_id) DO UPDATE SET
                   title=COALESCE(NULLIF(excluded.title,''), channels.title),
                   link=COALESCE(NULLIF(excluded.link,''), channels.link),
                   required=0,
                   reward=excluded.reward,
                   hold_days=excluded.hold_days""",
            (chat_id, title, link, max(0, int(reward)), max(0, int(hold_days)), _now()),
        )
        await self.conn.commit()

    async def list_reward_channels(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute("SELECT * FROM channels WHERE reward>0 ORDER BY added_at")
        return [dict(r) for r in await cur.fetchall()]

    async def get_channel(self, chat_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute("SELECT * FROM channels WHERE chat_id=?", (chat_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def subscription_claim(self, user_id: int, chat_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            "SELECT * FROM subscription_claims WHERE user_id=? AND chat_id=?", (user_id, chat_id)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_user_claims(self, user_id: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT * FROM subscription_claims WHERE user_id=? ORDER BY claimed_at DESC", (user_id,)
        )
        return [dict(r) for r in await cur.fetchall()]

    async def grant_subscription(self, user_id: int, chat_id: int, reward: int, hold_days: int) -> tuple[bool, int]:
        """Начислить награду за подписку + записать claim. (начислено?, баланс).
        Идемпотентно: если выдача уже была (active|revoked) — повторно не даёт."""
        if await self.subscription_claim(user_id, chat_id):
            u = await self.get_user(user_id)
            return False, int(u["crystals"]) if u else 0
        check_at = _now() + int(hold_days) * 86400 if hold_days and hold_days > 0 else 0
        # ON CONFLICT DO NOTHING + rowcount: при гонке (двойной тап) второй вызов
        # не вставит дубль и НЕ начислит повторно.
        cur = await self.conn.execute(
            """INSERT INTO subscription_claims (user_id, chat_id, reward, status, claimed_at, check_at)
               VALUES (?,?,?, 'active', ?, ?)
               ON CONFLICT(user_id, chat_id) DO NOTHING""",
            (user_id, chat_id, int(reward), _now(), check_at),
        )
        if cur.rowcount == 0:  # кто-то успел раньше — награду уже выдали
            await self.conn.commit()
            u = await self.get_user(user_id)
            return False, int(u["crystals"]) if u else 0
        bal = await self.add_crystals(user_id, int(reward), "subscribe", f"sub:{chat_id}")
        return True, bal

    async def list_due_claims(self, now: int, limit: int = 500) -> list[dict[str, Any]]:
        """Активные выдачи, которым пора проверить удержание (check_at наступил)."""
        cur = await self.conn.execute(
            """SELECT * FROM subscription_claims
               WHERE status='active' AND check_at>0 AND check_at<=?
               ORDER BY check_at LIMIT ?""",
            (now, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def mark_claim_kept(self, user_id: int, chat_id: int) -> None:
        """Удержал срок подписки → больше не проверяем (награда закреплена)."""
        await self.conn.execute(
            "UPDATE subscription_claims SET check_at=0 WHERE user_id=? AND chat_id=?", (user_id, chat_id)
        )
        await self.conn.commit()

    async def defer_claim_check(self, user_id: int, chat_id: int, next_at: int) -> None:
        """Отложить проверку удержания (когда подписку нельзя проверить — бот не админ/
        канал удалён). Бэк-офф, чтобы не дёргать API каждый проход."""
        await self.conn.execute(
            "UPDATE subscription_claims SET check_at=? WHERE user_id=? AND chat_id=? AND status='active'",
            (int(next_at), user_id, chat_id),
        )
        await self.conn.commit()

    async def revoke_subscription(self, user_id: int, chat_id: int) -> int:
        """Отозвать награду (юзер отписался). Списывает не ниже нуля. Возвращает списанное."""
        claim = await self.subscription_claim(user_id, chat_id)
        if not claim or claim["status"] != "active":
            return 0
        u = await self.get_user(user_id)
        bal = int(u["crystals"]) if u else 0
        take = min(int(claim["reward"]), bal)  # в минус не уводим
        if take > 0:
            await self.add_crystals(user_id, -take, "revoke", f"sub_revoke:{chat_id}")
        await self.conn.execute(
            "UPDATE subscription_claims SET status='revoked', revoked_at=?, check_at=0 WHERE user_id=? AND chat_id=?",
            (_now(), user_id, chat_id),
        )
        await self.conn.commit()
        return take

    # ── Задания «по ссылке» (старт бота / гс-чат / раздача) ───
    async def add_link_task(self, title: str, link: str, reward: int) -> int:
        cur = await self.conn.execute(
            "INSERT INTO link_tasks (title, link, reward, added_at) VALUES (?,?,?,?)",
            (title.strip(), link.strip(), max(0, int(reward)), _now()),
        )
        await self.conn.commit()
        return int(cur.lastrowid or 0)

    async def update_link_task(self, task_id: int, title: str, link: str, reward: int) -> None:
        await self.conn.execute(
            "UPDATE link_tasks SET title=?, link=?, reward=? WHERE id=?",
            (title.strip(), link.strip(), max(0, int(reward)), task_id),
        )
        await self.conn.commit()

    async def delete_link_task(self, task_id: int) -> None:
        await self.conn.execute("DELETE FROM link_tasks WHERE id=?", (task_id,))
        await self.conn.commit()

    async def list_link_tasks(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute("SELECT * FROM link_tasks WHERE reward>0 ORDER BY added_at")
        return [dict(r) for r in await cur.fetchall()]

    async def get_link_task(self, task_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute("SELECT * FROM link_tasks WHERE id=?", (task_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def claim_link_task(self, user_id: int, task_id: int, reward: int) -> tuple[bool, int]:
        """Выдать награду за ссылочное задание (один раз; проверки нет — на доверии).
        Гонко-безопасно: ON CONFLICT DO NOTHING + rowcount. (выдано?, баланс)."""
        cur = await self.conn.execute(
            """INSERT INTO link_task_claims (user_id, task_id, reward, claimed_at)
               VALUES (?,?,?,?) ON CONFLICT(user_id, task_id) DO NOTHING""",
            (user_id, task_id, int(reward), _now()),
        )
        if cur.rowcount == 0:
            await self.conn.commit()
            u = await self.get_user(user_id)
            return False, int(u["crystals"]) if u else 0
        bal = await self.add_crystals(user_id, int(reward), "task", f"task:{task_id}")
        return True, bal

    async def list_user_link_claims(self, user_id: int) -> set[int]:
        cur = await self.conn.execute(
            "SELECT task_id FROM link_task_claims WHERE user_id=?", (user_id,)
        )
        return {int(r["task_id"]) for r in await cur.fetchall()}

    async def link_task_summary(self) -> dict[int, int]:
        """task_id -> сколько раз выдана награда (для админки)."""
        cur = await self.conn.execute(
            "SELECT task_id, COUNT(*) AS c FROM link_task_claims GROUP BY task_id"
        )
        return {int(r["task_id"]): int(r["c"]) for r in await cur.fetchall()}

    async def subscription_summary(self) -> dict[int, dict[str, int]]:
        """По каждому каналу-заданию: сколько выдач active / revoked (для админки)."""
        cur = await self.conn.execute(
            """SELECT chat_id,
                      SUM(CASE WHEN status='active'  THEN 1 ELSE 0 END) AS active,
                      SUM(CASE WHEN status='revoked' THEN 1 ELSE 0 END) AS revoked
               FROM subscription_claims GROUP BY chat_id"""
        )
        return {
            int(r["chat_id"]): {"active": int(r["active"] or 0), "revoked": int(r["revoked"] or 0)}
            for r in await cur.fetchall()
        }

    # ── Картинки сцен (фоны по локациям + override сцен) ──────
    async def set_image(self, key: str, photos: dict[str, Any]) -> None:
        await self.conn.execute(
            """INSERT INTO images (key, photos, updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET photos=excluded.photos, updated_at=excluded.updated_at""",
            (key, json.dumps(photos, ensure_ascii=False), _now()),
        )
        await self.conn.commit()

    async def get_image(self, key: str) -> dict[str, Any] | None:
        cur = await self.conn.execute("SELECT photos FROM images WHERE key=?", (key,))
        row = await cur.fetchone()
        return json.loads(row["photos"]) if row else None

    async def delete_image(self, key: str) -> None:
        await self.conn.execute("DELETE FROM images WHERE key=?", (key,))
        await self.conn.commit()

    async def image_keys(self) -> set[str]:
        cur = await self.conn.execute("SELECT key FROM images")
        return {r["key"] for r in await cur.fetchall()}

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
