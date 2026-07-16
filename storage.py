"""Слой хранения задач в Google Sheets.

Таблица (лист "Tasks") хранит по одной задаче в строке.
Файл gspread синхронный, поэтому все обращения оборачиваем в asyncio.to_thread,
чтобы не блокировать event loop бота.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Порядок колонок в таблице. Менять только добавлением в конец.
HEADER = [
    "id", "title", "notes", "category", "priority",
    "due_at", "remind_at", "recurrence", "status",
    "created_at", "completed_at", "attachments",
    "reminded", "last_nagged_at",
]

PRIORITIES = ["P1", "P2", "P3", "P4"]
CATEGORIES = ["личное", "бизнес", "семья"]
RECURRENCES = ["none", "daily", "weekly", "monthly", "yearly"]


@dataclass
class Task:
    id: str = ""
    title: str = ""
    notes: str = ""
    category: str = ""
    priority: str = "P3"
    due_at: str = ""          # ISO 8601, напр. 2026-07-16T18:00:00
    remind_at: str = ""       # когда напомнить (по умолч. = due_at)
    recurrence: str = "none"
    status: str = "open"      # open | done
    created_at: str = ""
    completed_at: str = ""
    attachments: str = ""     # JSON-список [{"type","file_id","name"}]
    reminded: str = ""        # "1" если напоминание уже отправлено
    last_nagged_at: str = ""  # ISO последнего "пинка" по просрочке

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


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class Storage:
    def __init__(self, sheet_id: str):
        self._sheet_id = sheet_id
        self._ws: Optional[gspread.Worksheet] = None

    # --- подключение ---
    def _connect(self) -> gspread.Worksheet:
        if self._ws is not None:
            return self._ws
        raw = os.getenv("GOOGLE_CREDENTIALS_JSON")
        if raw:
            info = json.loads(raw)
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        else:
            path = os.getenv("GOOGLE_CREDENTIALS_FILE", "service_account.json")
            creds = Credentials.from_service_account_file(path, scopes=SCOPES)
        client = gspread.authorize(creds)
        sh = client.open_by_key(self._sheet_id)
        try:
            ws = sh.worksheet("Tasks")
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title="Tasks", rows=1000, cols=len(HEADER))
        # Гарантируем строку заголовков.
        first_row = ws.row_values(1)
        if first_row != HEADER:
            ws.update([HEADER], "A1")
        self._ws = ws
        return ws

    def _all(self) -> list[Task]:
        ws = self._connect()
        records = ws.get_all_records(expected_headers=HEADER, numericise_ignore=["all"])
        tasks = []
        for r in records:
            tasks.append(Task(**{k: str(r.get(k, "")) for k in HEADER}))
        return tasks

    def _row_index(self, task_id: str) -> Optional[int]:
        ws = self._connect()
        ids = ws.col_values(1)  # включает заголовок в позиции 1
        for i, val in enumerate(ids, start=1):
            if val == task_id:
                return i
        return None

    def _add(self, task: Task) -> Task:
        ws = self._connect()
        if not task.id:
            task.id = "t" + uuid.uuid4().hex[:7]
        if not task.created_at:
            task.created_at = datetime.now().isoformat(timespec="seconds")
        row = [getattr(task, k) for k in HEADER]
        ws.append_row(row, value_input_option="RAW")
        return task

    def _update(self, task: Task) -> None:
        ws = self._connect()
        idx = self._row_index(task.id)
        if idx is None:
            return
        row = [getattr(task, k) for k in HEADER]
        ws.update([row], f"A{idx}")

    def _delete(self, task_id: str) -> None:
        ws = self._connect()
        idx = self._row_index(task_id)
        if idx and idx > 1:
            ws.delete_rows(idx)

    # --- async-обёртки (публичный API) ---
    async def add(self, task: Task) -> Task:
        return await asyncio.to_thread(self._add, task)

    async def update(self, task: Task) -> None:
        await asyncio.to_thread(self._update, task)

    async def delete(self, task_id: str) -> None:
        await asyncio.to_thread(self._delete, task_id)

    async def all(self) -> list[Task]:
        return await asyncio.to_thread(self._all)

    async def get(self, task_id: str) -> Optional[Task]:
        tasks = await self.all()
        return next((t for t in tasks if t.id == task_id), None)

    async def open_tasks(self) -> list[Task]:
        return [t for t in await self.all() if t.status == "open"]
