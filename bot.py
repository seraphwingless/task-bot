"""Личный таск-менеджер в Telegram.

Как пользоваться:
  • просто напиши текст  -> создастся задача, дальше кнопками задаёшь
    категорию / приоритет / дедлайн / повтор и жмёшь «Сохранить»;
  • пришли фото/файл/видео (можно с подписью) -> задача с вложением;
  • /list — все открытые задачи, /today — на сегодня, /overdue — просроченные.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

import keyboards as kb
from scheduler import Reminders
from storage import Storage, Task
from utils import fmt_task, next_occurrence, now_tz, parse_due

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("bot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
DATABASE_URL = os.environ["DATABASE_URL"]
TZ = os.getenv("TIMEZONE", "Europe/Lisbon")
NAG_INTERVAL_MIN = int(os.getenv("NAG_INTERVAL_MIN", "60"))

storage = Storage(DATABASE_URL)
router = Router()

# Черновики задач (ещё не сохранённые в таблицу), ключ — id черновика.
DRAFTS: dict[str, Task] = {}
# Кого ждём ручной ввод дедлайна: user_id -> draft_id.
AWAITING_DUE: dict[int, str] = {}

HELP = (
    "🗂 <b>Твой таск-менеджер</b>\n\n"
    "• Напиши текст — создам задачу, дальше настроишь кнопками.\n"
    "• Пришли фото/файл/видео — задача с вложением.\n\n"
    "<b>Команды</b>\n"
    "/list — открытые задачи\n"
    "/today — на сегодня\n"
    "/overdue — просроченные\n"
    "/help — помощь"
)


# --- owner-only middleware ---
@router.message.outer_middleware
async def only_owner_msg(handler, event: Message, data):
    if event.from_user and event.from_user.id == OWNER_ID:
        return await handler(event, data)
    return None


@router.callback_query.outer_middleware
async def only_owner_cb(handler, event: CallbackQuery, data):
    if event.from_user and event.from_user.id == OWNER_ID:
        return await handler(event, data)
    return await event.answer("Это личный бот.", show_alert=True)


# --- команды ---
@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(message: Message):
    await message.answer(HELP)


@router.message(Command("list"))
async def cmd_list(message: Message):
    await _show_list(message, scope="all")


@router.message(Command("today"))
async def cmd_today(message: Message):
    await _show_list(message, scope="today")


@router.message(Command("overdue"))
async def cmd_overdue(message: Message):
    await _show_list(message, scope="overdue")


async def _show_list(message: Message, scope: str):
    tasks = await storage.open_tasks()
    now = now_tz(TZ).replace(tzinfo=None)
    if scope == "today":
        end = now.replace(hour=23, minute=59, second=59, microsecond=0)
        tasks = [t for t in tasks if t.due_dt() and t.due_dt() <= end]
        title = "📅 <b>На сегодня</b>"
    elif scope == "overdue":
        tasks = [t for t in tasks if t.due_dt() and t.due_dt() < now]
        title = "❗️ <b>Просроченные</b>"
    else:
        title = "🗂 <b>Открытые задачи</b>"

    if not tasks:
        await message.answer(f"{title}\n\nПусто ✨")
        return

    # Сортировка: приоритет, затем дедлайн.
    tasks.sort(key=lambda t: (t.priority, t.due_at or "9999"))
    await message.answer(title)
    for t in tasks:
        await message.answer(fmt_task(t), reply_markup=kb.task_actions_kb(t.id))


# --- создание задачи из вложения ---
@router.message(F.photo | F.document | F.video | F.voice | F.audio)
async def on_attachment(message: Message):
    if message.photo:
        att = {"type": "photo", "file_id": message.photo[-1].file_id, "name": "photo"}
    elif message.document:
        att = {"type": "document", "file_id": message.document.file_id,
               "name": message.document.file_name or "file"}
    elif message.video:
        att = {"type": "video", "file_id": message.video.file_id, "name": "video"}
    elif message.voice:
        att = {"type": "voice", "file_id": message.voice.file_id, "name": "voice"}
    else:
        att = {"type": "audio", "file_id": message.audio.file_id, "name": "audio"}

    title = (message.caption or att["name"]).strip()
    draft = Task(id=_draft_id(), title=title, attachments=json.dumps([att]))
    DRAFTS[draft.id] = draft
    await message.answer(
        f"📎 Черновик задачи с вложением:\n\n{fmt_task(draft, with_id=False)}",
        reply_markup=kb.draft_kb(draft.id),
    )


# --- текст: ручной ввод дедлайна ИЛИ новый черновик ---
@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message):
    uid = message.from_user.id
    # Ждём ручной ввод дедлайна?
    if uid in AWAITING_DUE:
        draft_id = AWAITING_DUE.pop(uid)
        draft = DRAFTS.get(draft_id)
        if not draft:
            await message.answer("Черновик потерялся, начни заново.")
            return
        dt = parse_due(message.text, TZ)
        if not dt:
            AWAITING_DUE[uid] = draft_id
            await message.answer("Не понял дату. Примеры: <code>завтра 18:00</code>, "
                                 "<code>16.07 9:30</code>, <code>+2ч</code>. Попробуй ещё раз:")
            return
        draft.due_at = dt.isoformat(timespec="seconds")
        await message.answer(f"⏰ Дедлайн: {dt.strftime('%d.%m %H:%M')}\n\n"
                             f"{fmt_task(draft, with_id=False)}",
                             reply_markup=kb.draft_kb(draft.id))
        return

    # Новый черновик.
    draft = Task(id=_draft_id(), title=message.text.strip())
    DRAFTS[draft.id] = draft
    await message.answer(
        f"📝 Новая задача:\n\n{fmt_task(draft, with_id=False)}\n\nНастрой и сохрани:",
        reply_markup=kb.draft_kb(draft.id),
    )


# --- callbacks черновика ---
@router.callback_query(F.data.startswith("d_"))
async def on_draft_nav(cq: CallbackQuery):
    action, _, task_id = cq.data.partition(":")
    draft = DRAFTS.get(task_id)
    if not draft and action != "d_cancel":
        await cq.answer("Черновик потерялся.", show_alert=True)
        return

    if action == "d_cat":
        await cq.message.edit_reply_markup(reply_markup=kb.category_kb(task_id))
    elif action == "d_prio":
        await cq.message.edit_reply_markup(reply_markup=kb.priority_kb(task_id))
    elif action == "d_rec":
        await cq.message.edit_reply_markup(reply_markup=kb.recurrence_kb(task_id))
    elif action == "d_due":
        await cq.message.edit_reply_markup(reply_markup=kb.due_quick_kb(task_id))
    elif action == "d_back":
        await cq.message.edit_reply_markup(reply_markup=kb.draft_kb(task_id))
    elif action == "d_cancel":
        DRAFTS.pop(task_id, None)
        await cq.message.edit_text("❌ Отменено.")
    elif action == "d_save":
        draft.status = "open"
        if draft.due_at and not draft.remind_at:
            draft.remind_at = draft.due_at
        await storage.add(draft)
        DRAFTS.pop(task_id, None)
        await cq.message.edit_text(f"✅ Сохранено:\n\n{fmt_task(draft)}")
    await cq.answer()


@router.callback_query(F.data.startswith("set_"))
async def on_draft_set(cq: CallbackQuery):
    action, _, rest = cq.data.partition(":")
    task_id, _, value = rest.partition(":")
    draft = DRAFTS.get(task_id)
    if not draft:
        await cq.answer("Черновик потерялся.", show_alert=True)
        return

    if action == "set_cat":
        draft.category = value
    elif action == "set_prio":
        draft.priority = value
    elif action == "set_rec":
        draft.recurrence = value
    elif action == "set_due":
        if value == "none":
            draft.due_at = ""
        else:
            dt = parse_due(value, TZ)
            draft.due_at = dt.isoformat(timespec="seconds") if dt else draft.due_at

    await cq.message.edit_text(
        f"📝 {fmt_task(draft, with_id=False)}\n\nНастрой и сохрани:",
        reply_markup=kb.draft_kb(task_id),
    )
    await cq.answer("Ок")


@router.callback_query(F.data.startswith("due_manual:"))
async def on_due_manual(cq: CallbackQuery):
    _, _, task_id = cq.data.partition(":")
    AWAITING_DUE[cq.from_user.id] = task_id
    await cq.message.answer("Напиши дедлайн: <code>завтра 18:00</code>, "
                            "<code>16.07 9:30</code>, <code>сегодня 21:00</code> или <code>+90м</code>")
    await cq.answer()


# --- callbacks сохранённых задач ---
@router.callback_query(F.data.startswith("done:"))
async def on_done(cq: CallbackQuery):
    _, _, task_id = cq.data.partition(":")
    t = await storage.get(task_id)
    if not t:
        await cq.answer("Задача не найдена.", show_alert=True)
        return
    t.status = "done"
    t.completed_at = now_tz(TZ).replace(tzinfo=None).isoformat(timespec="seconds")
    await storage.update(t)

    # Повторяющиеся — создаём следующую копию.
    if t.recurrence and t.recurrence != "none" and t.due_dt():
        nxt = next_occurrence(t.due_dt(), t.recurrence)
        if nxt:
            copy = Task(
                title=t.title, notes=t.notes, category=t.category, priority=t.priority,
                due_at=nxt.isoformat(timespec="seconds"),
                remind_at=nxt.isoformat(timespec="seconds"),
                recurrence=t.recurrence, status="open", attachments=t.attachments,
            )
            await storage.add(copy)
    await cq.message.edit_text(f"✅ Выполнено: <s>{_short(t.title)}</s>")
    await cq.answer("Готово")


@router.callback_query(F.data.startswith("snooze:"))
async def on_snooze(cq: CallbackQuery):
    _, _, task_id = cq.data.partition(":")
    t = await storage.get(task_id)
    if not t:
        await cq.answer("Задача не найдена.", show_alert=True)
        return
    new_due = (now_tz(TZ).replace(tzinfo=None) + timedelta(hours=1))
    t.due_at = new_due.isoformat(timespec="seconds")
    t.remind_at = t.due_at
    t.reminded = ""
    t.last_nagged_at = ""
    await storage.update(t)
    await cq.message.edit_text(f"⏰ Отложено на час → {new_due.strftime('%H:%M')}\n\n{fmt_task(t)}",
                               reply_markup=kb.task_actions_kb(t.id))
    await cq.answer("Отложено")


@router.callback_query(F.data.startswith("del:"))
async def on_del(cq: CallbackQuery):
    _, _, task_id = cq.data.partition(":")
    await storage.delete(task_id)
    await cq.message.edit_text("🗑 Удалено.")
    await cq.answer("Удалено")


# --- helpers ---
def _draft_id() -> str:
    import uuid
    return "d" + uuid.uuid4().hex[:7]


def _short(s: str, n: int = 60) -> str:
    s = s.replace("<", "&lt;").replace(">", "&gt;")
    return s if len(s) <= n else s[:n] + "…"


async def main():
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.include_router(router)

    reminders = Reminders(bot, storage, OWNER_ID, TZ, NAG_INTERVAL_MIN)
    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(reminders.tick, "interval", minutes=1, next_run_time=datetime.now())
    scheduler.start()

    log.info("Бот запущен.")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
