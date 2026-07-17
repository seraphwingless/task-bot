"""Планировщик: напоминания к дедлайну и 'пинания' при просрочке.
Настройки (интервал пинков, напоминать заранее, тихие часы) читаются из листа
'Settings' в таблице — их задаёт пользователь в Mini App."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from aiogram import Bot

from keyboards import task_actions_kb
from storage import Storage
from utils import fmt_task, now_tz

log = logging.getLogger("scheduler")


def _int(v, default: int) -> int:
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return default


def _parse(iso: str):
    try:
        return datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None


def _in_quiet(now: datetime, st: dict) -> bool:
    if st.get("quiet_on") != "1":
        return False
    try:
        sh, sm = map(int, st.get("quiet_start", "23:00").split(":"))
        eh, em = map(int, st.get("quiet_end", "08:00").split(":"))
    except ValueError:
        return False
    cur = now.hour * 60 + now.minute
    start, end = sh * 60 + sm, eh * 60 + em
    if start == end:
        return False
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end   # окно через полночь


class Reminders:
    def __init__(self, bot: Bot, storage: Storage, owner_id: int,
                 tz: str, nag_interval_min: int):
        self.bot = bot
        self.storage = storage
        self.owner_id = owner_id
        self.tz = tz
        self.nag_interval_min = nag_interval_min

    async def tick(self) -> None:
        try:
            tasks = await self.storage.open_tasks()
            st = await self.storage.settings()
        except Exception as e:  # noqa: BLE001
            log.warning("Не смог прочитать задачи/настройки: %s", e)
            return

        now = now_tz(self.tz).replace(tzinfo=None)
        if _in_quiet(now, st):          # тихие часы — молчим
            return

        for t in tasks:
            due = t.due_dt()
            if not due:
                continue

            # 1. Напоминания задачи (каждое «за N минут до срока» срабатывает один раз)
            offsets = t.reminder_offsets()
            fired = t.fired_offsets()
            new_fired = list(fired)
            for off in offsets:
                if off not in fired and now >= (due - timedelta(minutes=off)):
                    await self._send(f"⏰ <b>Напоминание</b>\n\n{fmt_task(t)}", t.id)
                    new_fired.append(off)
            if new_fired != fired:
                t.reminded = json.dumps(new_fired)
                await self.storage.update(t)
                continue

            # 2. Пинание по просрочке — интервал берётся из самой задачи
            per_nag = 60 if t.nag_on == "1" else _int(t.nag_on, 0)
            if now > due and per_nag > 0:
                last = _parse(t.last_nagged_at)
                mins = (now - (last or due)).total_seconds() / 60
                if last is not None and mins >= per_nag:
                    overdue_h = int((now - due).total_seconds() // 3600)
                    tail = f" (просрочено на {overdue_h} ч)" if overdue_h else ""
                    await self._send(f"❗️ <b>Просрочено{tail}</b>\n\n{fmt_task(t)}", t.id)
                    t.last_nagged_at = now.isoformat(timespec="seconds")
                    await self.storage.update(t)
                elif last is None:
                    t.last_nagged_at = now.isoformat(timespec="seconds")
                    await self.storage.update(t)

    async def _send(self, text: str, task_id: str) -> None:
        try:
            await self.bot.send_message(self.owner_id, text, reply_markup=task_actions_kb(task_id))
        except Exception as e:  # noqa: BLE001
            log.warning("Не смог отправить напоминание: %s", e)
