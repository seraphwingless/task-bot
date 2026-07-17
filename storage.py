"""Хранилище задач бота на Postgres (asyncpg). Общая база с Mini App."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import asyncpg

TASK_FIELDS = [
    "id", "title", "notes", "category", "priority",
    "due_at", "remind_at", "recurrence", "status",
    "created_at", "completed_at", "attachments",
    "reminded", "last_nagged_at",
]

PRIORITIES = ["P1", "P2", "P3", "P4", "P5"]
CATEGORIES = ["личное", "бизнес", "семья", "спорт"]
RECURRENCES = ["none", "daily", "weekly", "monthly", "yearly"]

DDL = """
CREATE TABLE IF NOT EXISTS tasks(
  seq bigserial, id text PRIMARY KEY, title text DEFAULT '', notes text DEFAULT '',
  category text DEFAULT '', priority text DEFAULT 'P3', due_at text DEFAULT '',
  remind_at text DEFAULT '', recurrence text DEFAULT 'none', status text DEFAULT 'open',
  created_at text DEFAULT '', completed_at text DEFAULT '', attachments text DEFAULT '',
  reminded text DEFAULT '', last_nagged_at text DEFAULT '');
CREATE TABLE IF NOT EXISTS categories(
  seq bigserial, name text PRIMARY KEY, emoji text DEFAULT '', color text DEFAULT '#888780');
CREATE TABLE IF NOT EXISTS comments(id text PRIMARY KEY, task_id text, body text, created_at text);
CREATE TABLE IF NOT EXISTS settings(key text PRIMARY KEY, value text);
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
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool = None

    async def _p(self):
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
            async with self._pool.acquire() as c:
                await c.execute(DDL)
        return self._pool

    def _mk(self, r) -> Task:
        return Task(**{k: (str(r[k]) if r[k] is not None else "") for k in TASK_FIELDS})

    async def add(self, task: Task) -> Task:
        if not task.id:
            task.id = "t" + uuid.uuid4().hex[:7]
        if not task.created_at:
            task.created_at = datetime.now().isoformat(timespec="seconds")
        p = await self._p()
        ph = ",".join("$" + str(i + 1) for i in range(len(TASK_FIELDS)))
        await p.execute(f"INSERT INTO tasks({','.join(TASK_FIELDS)}) VALUES({ph}) "
                        "ON CONFLICT(id) DO NOTHING",
                        *[str(getattr(task, k) or "") for k in TASK_FIELDS])
        return task

    async def update(self, task: Task) -> None:
        p = await self._p()
        fields = [k for k in TASK_FIELDS if k != "id"]
        sets = ",".join(f"{k}=${i + 2}" for i, k in enumerate(fields))
        await p.execute(f"UPDATE tasks SET {sets} WHERE id=$1",
                        task.id, *[str(getattr(task, k) or "") for k in fields])

    async def delete(self, task_id: str) -> None:
        p = await self._p()
        await p.execute("DELETE FROM tasks WHERE id=$1", task_id)

    async def all(self) -> list[Task]:
        p = await self._p()
        rows = await p.fetch("SELECT * FROM tasks ORDER BY seq")
        return [self._mk(r) for r in rows]

    async def get(self, task_id: str) -> Optional[Task]:
        p = await self._p()
        r = await p.fetchrow("SELECT * FROM tasks WHERE id=$1", task_id)
        return self._mk(r) if r else None

    async def open_tasks(self) -> list[Task]:
        p = await self._p()
        rows = await p.fetch("SELECT * FROM tasks WHERE status='open' ORDER BY seq")
        return [self._mk(r) for r in rows]

    async def settings(self) -> dict:
        p = await self._p()
        rows = await p.fetch("SELECT key,value FROM settings")
        return {r["key"]: r["value"] for r in rows}
