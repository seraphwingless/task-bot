"""Планировщик: напоминания к сроку и пинки при просрочке — для всех пользователей.
Настройки напоминаний (кол-во, «за N до», частота пинков) — в самой задаче;
тихие часы — в настройках каждого пользователя."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from keyboards import task_actions_kb
from storage import Storage
from utils import fmt_task, now_tz

log = logging.getLogger("scheduler")

WEBAPP_URL = os.getenv("WEBAPP_URL", "")
RU_WD = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
RU_MON = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]
MAX_OVERDUE, MAX_TODAY, MAX_CHECK = 5, 7, 7


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _overdue_label(minutes: float) -> str:
    d = int(minutes // 1440)
    if d:
        return f"на {d} дн"
    h = int((minutes % 1440) // 60)
    if h:
        return f"на {h} ч"
    return f"на {max(1, int(minutes))} мин"


def _line(t, emoji: str, with_time: bool) -> str:
    due = t.due_dt()
    when = (due.strftime("%H:%M") + " ") if (with_time and due) else ""
    cat = (emoji + " ") if emoji else ""
    return f"  {when}{t.priority or 'P3'} {cat}{_esc(t.title)}"


def _tail(items: list, shown: int) -> str:
    left = len(items) - shown
    return f"\n  <i>и ещё {left}</i>" if left > 0 else ""


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
    return cur >= start or cur < end


class Reminders:
    def __init__(self, bot: Bot, storage: Storage, owner_id: int,
                 tz: str, nag_interval_min: int):
        self.bot = bot
        self.storage = storage
        self.owner_id = owner_id
        self.tz = tz
        self.nag_interval_min = nag_interval_min

    def _now(self, st: dict) -> datetime:
        """Текущее время в поясе пользователя (у каждого свой)."""
        for tzname in ((st or {}).get("tz"), self.tz):
            if tzname:
                try:
                    return now_tz(tzname).replace(tzinfo=None)
                except Exception:  # noqa: BLE001
                    continue
        return datetime.now()

    async def tick(self) -> None:
        try:
            await self.digests()
        except Exception as e:  # noqa: BLE001
            log.warning("Дайджест не отработал: %s", e)

        try:
            tasks = await self.storage.open_tasks_all()
        except Exception as e:  # noqa: BLE001
            log.warning("Не смог прочитать задачи: %s", e)
            return

        settings_cache: dict[int, dict] = {}

        for t in tasks:
            uid = t.user_id or self.owner_id
            if uid not in settings_cache:
                try:
                    settings_cache[uid] = await self.storage.settings(uid)
                except Exception:  # noqa: BLE001
                    settings_cache[uid] = {}
            now = self._now(settings_cache[uid])
            if _in_quiet(now, settings_cache[uid]):
                continue

            due = t.due_dt()
            if not due:
                continue

            offsets = t.reminder_offsets()
            fired = t.fired_offsets()
            new_fired = list(fired)
            for off in offsets:
                if off not in fired and now >= (due - timedelta(minutes=off)):
                    await self._send(uid, f"⏰ <b>Напоминание</b>\n\n{fmt_task(t)}", t.id)
                    new_fired.append(off)
            if new_fired != fired:
                t.reminded = json.dumps(new_fired)
                await self.storage.update(t)
                continue

            per_nag = 60 if t.nag_on == "1" else _int(t.nag_on, 0)
            if now > due and per_nag > 0:
                last = _parse(t.last_nagged_at)
                mins = (now - (last or due)).total_seconds() / 60
                if last is not None and mins >= per_nag:
                    overdue_h = int((now - due).total_seconds() // 3600)
                    tail = f" (просрочено на {overdue_h} ч)" if overdue_h else ""
                    await self._send(uid, f"❗️ <b>Просрочено{tail}</b>\n\n{fmt_task(t)}", t.id)
                    t.last_nagged_at = now.isoformat(timespec="seconds")
                    await self.storage.update(t)
                elif last is None:
                    t.last_nagged_at = now.isoformat(timespec="seconds")
                    await self.storage.update(t)

    async def _send(self, uid: int, text: str, task_id: str) -> None:
        try:
            await self.bot.send_message(uid, text, reply_markup=task_actions_kb(task_id))
            log.info("НАПОМИНАНИЕ отправлено uid=%s task=%s", uid, task_id)
        except Exception as e:  # noqa: BLE001
            log.warning("НАПОМИНАНИЕ не ушло uid=%s task=%s: %s", uid, task_id, e)

    # ---------------- дайджесты ----------------
    async def digests(self) -> None:
        """Утренний дайджест и вечернее превью. Вызывается каждую минуту."""
        try:
            users = await self.storage.list_users()
        except Exception as e:  # noqa: BLE001
            log.warning("Не смог получить список пользователей: %s", e)
            return
        for uid in users:
            try:
                st = await self.storage.settings(uid)
            except Exception:  # noqa: BLE001
                continue
            now = self._now(st)
            today = now.strftime("%Y-%m-%d")
            await self._maybe(uid, st, now, today, "digest",
                              st.get("digest_time", "08:00"), st.get("digest_on", "1"), self._morning)
            await self._maybe(uid, st, now, today, "evening",
                              st.get("evening_time", "20:00"), st.get("evening_on", "1"), self._evening)

    async def _maybe(self, uid, st, now, today, key, hhmm, enabled, builder) -> None:
        if enabled != "1" or st.get(key + "_last", "") == today:
            return
        try:
            hh, mm = map(int, str(hhmm).split(":"))
        except ValueError:
            return
        sched = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < sched:
            return
        # Если бот лежал и окно упущено — помечаем день отправленным, но не шлём задним числом.
        if (now - sched).total_seconds() > 7200:
            await self.storage.set_setting(uid, key + "_last", today)
            return
        try:
            text = await builder(uid, now)
        except Exception as e:  # noqa: BLE001
            log.warning("Не смог собрать %s для %s: %s", key, uid, e)
            return
        await self.storage.set_setting(uid, key + "_last", today)
        if text:
            await self._send_digest(uid, text)

    async def _morning(self, uid: int, now: datetime) -> str:
        tasks = await self.storage.open_all(uid)
        emo = await self.storage.cat_emoji(uid)
        today, yday = now.date(), (now - timedelta(days=1)).strftime("%Y-%m-%d")

        overdue, plan, check = [], [], []
        for t in tasks:
            if t.checklist == "1":
                check.append(t)
                continue
            due = t.due_dt()
            if not due:
                continue
            if due < now:
                overdue.append(t)
            elif due.date() == today:
                plan.append(t)
        if not overdue and not plan and not check:
            return ""

        overdue.sort(key=lambda x: x.due_at)
        plan.sort(key=lambda x: x.due_at)
        wd, d, mon = RU_WD[now.weekday()], now.day, RU_MON[now.month - 1]
        out = [f"☀️ <b>Доброе утро.</b> {wd.capitalize()}, {d} {mon}"]

        if overdue:
            out.append(f"\n🔥 <b>Просрочено — {len(overdue)}</b>")
            for t in overdue[:MAX_OVERDUE]:
                late = _overdue_label((now - t.due_dt()).total_seconds() / 60)
                out.append(_line(t, emo.get(t.category, ""), False) + f" — {late}")
            out[-1] += _tail(overdue, MAX_OVERDUE)
        if plan:
            out.append(f"\n📌 <b>Сегодня — {len(plan)}</b>")
            for t in plan[:MAX_TODAY]:
                out.append(_line(t, emo.get(t.category, ""), True))
            out[-1] += _tail(plan, MAX_TODAY)
        if check:
            done_yday = sum(1 for t in check if t.checked_date == yday)
            out.append(f"\n✅ <b>Чеклист — {len(check)}</b> <i>(вчера {done_yday} из {len(check)})</i>")
            names = " · ".join(_esc(t.title) for t in check[:MAX_CHECK])
            out.append("  " + names + (f" <i>и ещё {len(check) - MAX_CHECK}</i>" if len(check) > MAX_CHECK else ""))
        return "\n".join(out)

    async def _evening(self, uid: int, now: datetime) -> str:
        tasks = await self.storage.open_all(uid)
        emo = await self.storage.cat_emoji(uid)
        tmw = (now + timedelta(days=1)).date()
        plan = [t for t in tasks if t.checklist != "1" and t.due_dt() and t.due_dt().date() == tmw]
        if not plan:
            return ""
        plan.sort(key=lambda x: x.due_at)
        out = [f"🌙 <b>Завтра — {len(plan)}</b>"]
        for t in plan[:MAX_TODAY]:
            out.append(_line(t, emo.get(t.category, ""), True))
        out[-1] += _tail(plan, MAX_TODAY)
        return "\n".join(out)

    async def _send_digest(self, uid: int, text: str) -> None:
        kb = None
        if WEBAPP_URL:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text="Открыть приложение", web_app=WebAppInfo(url=WEBAPP_URL))]])
        try:
            await self.bot.send_message(uid, text, reply_markup=kb)
        except Exception as e:  # noqa: BLE001
            log.warning("Не смог отправить дайджест пользователю %s: %s", uid, e)
