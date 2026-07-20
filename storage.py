"""Хранилище бота на Postgres (asyncpg), многопользовательское.
Общая база с Mini App. Данные привязаны к user_id; доступ по allowed_users."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import asyncpg

TASK_FIELDS = [
    "id", "title", "notes", "category", "priority",
    "due_at", "remind_at", "recurrence", "status",
    "created_at", "completed_at", "attachments",
    "reminded", "last_nagged_at", "reminders", "nag_on",
    "checklist", "checked_date",
]

PRIORITIES = ["P1", "P2", "P3", "P4", "P5"]
CATEGORIES = ["Личное", "Бизнес", "Семья", "Спорт"]
RECURRENCES = ["none", "daily", "weekly", "monthly", "yearly"]

DDL = """
CREATE TABLE IF NOT EXISTS tasks(
  seq bigserial, id text PRIMARY KEY, title text DEFAULT '', notes text DEFAULT '',
  category text DEFAULT '', priority text DEFAULT 'P3', due_at text DEFAULT '',
  remind_at text DEFAULT '', recurrence text DEFAULT 'none', status text DEFAULT 'open',
  created_at text DEFAULT '', completed_at text DEFAULT '', attachments text DEFAULT '',
  reminded text DEFAULT '', last_nagged_at text DEFAULT '',
  reminders text DEFAULT '', nag_on text DEFAULT '1');
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS reminders text DEFAULT '';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS nag_on text DEFAULT '1';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS user_id bigint;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS checklist text DEFAULT '0';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS checked_date text DEFAULT '';
CREATE TABLE IF NOT EXISTS allowed_users(user_id bigint PRIMARY KEY, name text DEFAULT '', added_at text DEFAULT '');
CREATE TABLE IF NOT EXISTS user_settings(user_id bigint, key text, value text, PRIMARY KEY(user_id, key));
"""


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass
class Task:
    id: str = ""
    title: str = ""
    notes: str = ""
    category: str = ""
    priority: str = "P3"
    due_at: str = ""
    remind_at: str = ""
    recurrence: str = "none"
    status: str = "open"
    created_at: str = ""
    completed_at: str = ""
    attachments: str = ""
    reminded: str = ""
    last_nagged_at: str = ""
    reminders: str = ""
    nag_on: str = "1"
    checklist: str = "0"
    checked_date: str = ""
    user_id: int = 0

    def reminder_offsets(self) -> list[int]:
        try:
            return [int(x) for x in (json.loads(self.reminders) if self.reminders else [])]
        except (ValueError, TypeError):
            return []

    def fired_offsets(self) -> list[int]:
        try:
            return [int(x) for x in (json.loads(self.reminded) if self.reminded.startswith("[") else [])]
        except (ValueError, TypeError, AttributeError):
            return []

    def attachments_list(self) -> list[dict]:
        if not self.attachments:
            return []
        try:
            return json.loads(self.attachments)
        except json.JSONDecodeError:
            return []

    def due_dt(self) -> Optional[datetime]:
        return _parse_iso(self.due_at)

    def remind_dt(self) -> Optional[datetime]:
        return _parse_iso(self.remind_at or self.due_at)


class Storage:
    def __init__(self, dsn: str, owner_id: int):
        self._dsn = dsn
        self._owner = int(owner_id)
        self._pool = None

    async def _p(self):
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, ssl=False, min_size=1, max_size=5)
            async with self._pool.acquire() as c:
                await c.execute(DDL)
                await c.execute("UPDATE tasks SET user_id=$1 WHERE user_id IS NULL", self._owner)
                await c.execute("INSERT INTO allowed_users(user_id,name,added_at) VALUES($1,'Владелец',$2) "
                                "ON CONFLICT DO NOTHING", self._owner, datetime.now().isoformat(timespec="seconds"))
        return self._pool

    def _mk(self, r) -> Task:
        d = {k: (str(r[k]) if r[k] is not None else "") for k in TASK_FIELDS}
        d["user_id"] = int(r["user_id"]) if r["user_id"] is not None else 0
        return Task(**d)

    async def is_allowed(self, uid: int) -> bool:
        if int(uid) == self._owner:
            return True
        p = await self._p()
        return bool(await p.fetchval("SELECT 1 FROM allowed_users WHERE user_id=$1", int(uid)))

    async def add(self, uid: int, task: Task) -> Task:
        if not task.id:
            task.id = "t" + uuid.uuid4().hex[:7]
        if not task.created_at:
            task.created_at = datetime.now().isoformat(timespec="seconds")
        task.user_id = int(uid)
        p = await self._p()
        cols = TASK_FIELDS + ["user_id"]
        vals = [str(getattr(task, k) or "") for k in TASK_FIELDS] + [int(uid)]
        ph = ",".join("$" + str(i + 1) for i in range(len(cols)))
        await p.execute(f"INSERT INTO tasks({','.join(cols)}) VALUES({ph}) ON CONFLICT(id) DO NOTHING", *vals)
        return task

    async def update(self, task: Task) -> None:
        p = await self._p()
        fields = [k for k in TASK_FIELDS if k != "id"]
        sets = ",".join(f"{k}=${i + 2}" for i, k in enumerate(fields))
        await p.execute(f"UPDATE tasks SET {sets} WHERE id=$1", task.id,
                        *[str(getattr(task, k) or "") for k in fields])

    async def delete(self, uid: int, task_id: str) -> None:
        p = await self._p()
        await p.execute("DELETE FROM tasks WHERE id=$1 AND user_id=$2", task_id, int(uid))

    async def get(self, uid: int, task_id: str) -> Optional[Task]:
        p = await self._p()
        r = await p.fetchrow("SELECT * FROM tasks WHERE id=$1 AND user_id=$2", task_id, int(uid))
        return self._mk(r) if r else None

    async def open_tasks(self, uid: int) -> list[Task]:
        p = await self._p()
        rows = await p.fetch("SELECT * FROM tasks WHERE status='open' AND coalesce(checklist,'0')<>'1' "
                             "AND user_id=$1 ORDER BY seq", int(uid))
        return [self._mk(r) for r in rows]

    async def open_tasks_all(self) -> list[Task]:
        p = await self._p()
        rows = await p.fetch("SELECT * FROM tasks WHERE status='open' AND coalesce(checklist,'0')<>'1' ORDER BY seq")
        return [self._mk(r) for r in rows]

    async def settings(self, uid: int) -> dict:
        p = await self._p()
        rows = await p.fetch("SELECT key,value FROM user_settings WHERE user_id=$1", int(uid))
        return {r["key"]: r["value"] for r in rows}

    # ---- для дайджеста ----
    async def set_setting(self, uid: int, key: str, value: str) -> None:
        p = await self._p()
        await p.execute("INSERT INTO user_settings(user_id,key,value) VALUES($1,$2,$3) "
                        "ON CONFLICT(user_id,key) DO UPDATE SET value=$3", int(uid), key, str(value))

    async def list_users(self) -> list[int]:
        p = await self._p()
        rows = await p.fetch("SELECT user_id FROM allowed_users")
        return [int(r["user_id"]) for r in rows]

    async def open_all(self, uid: int) -> list[Task]:
        """Все незакрытые задачи пользователя, включая чеклист."""
        p = await self._p()
        rows = await p.fetch("SELECT * FROM tasks WHERE status='open' AND user_id=$1 ORDER BY seq", int(uid))
        return [self._mk(r) for r in rows]

    async def cat_emoji(self, uid: int) -> dict:
        p = await self._p()
        try:
            rows = await p.fetch("SELECT name,emoji FROM user_categories WHERE user_id=$1", int(uid))
        except Exception:  # noqa: BLE001
            return {}
        return {r["name"]: (r["emoji"] or "") for r in rows}
