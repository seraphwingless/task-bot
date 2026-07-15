"""Планировщик: напоминания к дедлайну и 'пинания' при просрочке.

Тик каждую минуту сканирует открытые задачи:
  1. remind_at наступил и напоминание ещё не слали -> шлём напоминание.
  2. дедлайн прошёл, задача открыта -> пинаем раз в NAG_INTERVAL_MIN минут.
Повторяющиеся задачи пересоздаются при отметке 'Готово' (см. bot.py).
"""
from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot

from keyboards import task_actions_kb
from storage import Storage
from utils import fmt_task, now_tz

log = logging.getLogger("scheduler")


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
        except Exception as e:  # noqa: BLE001 — не роняем планировщик из-за сбоя сети
            log.warning("Не смог прочитать задачи: %s", e)
            return

        now = now_tz(self.tz).replace(tzinfo=None)

        for t in tasks:
            due = t.due_dt()
            remind = t.remind_dt()

            # 1. Напоминание к сроку
            if remind and not t.reminded and remind <= now:
                await self._send(f"⏰ <b>Напоминание</b>\n\n{fmt_task(t)}", t.id)
                t.reminded = "1"
                await self.storage.update(t)
                continue

            # 2. Пинание по просрочке
            if due and now > due:
                last = _parse(t.last_nagged_at)
                due_minutes = (now - (last or due)).total_seconds() / 60
                if last is None or due_minutes >= self.nag_interval_min:
                    overdue_h = int((now - due).total_seconds() // 3600)
                    tail = f" (просрочено на {overdue_h} ч)" if overdue_h else ""
                    await self._send(f"❗️ <b>Просрочено{tail}</b>\n\n{fmt_task(t)}", t.id)
                    t.last_nagged_at = now.isoformat(timespec="seconds")
                    await self.storage.update(t)

    async def _send(self, text: str, task_id: str) -> None:
        try:
            await self.bot.send_message(
                self.owner_id, text, reply_markup=task_actions_kb(task_id)
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Не смог отправить напоминание: %s", e)


def _parse(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None
