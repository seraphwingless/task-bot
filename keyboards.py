"""Инлайн-клавиатуры. callback_data формата 'action:payload'."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from storage import CATEGORIES, PRIORITIES, RECURRENCES
from utils import RECUR_LABEL, CATEGORY_EMOJI, PRIORITY_EMOJI


def task_actions_kb(task_id: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Готово", callback_data=f"done:{task_id}")
    b.button(text="⏰ +1ч", callback_data=f"snooze:{task_id}")
    b.button(text="🗑", callback_data=f"delask:{task_id}")
    b.adjust(3)
    return b.as_markup()


def delete_confirm_kb(task_id: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🗑 Точно удалить", callback_data=f"del:{task_id}")
    b.button(text="‹ Отмена", callback_data=f"delno:{task_id}")
    b.adjust(2)
    return b.as_markup()


def draft_kb(task_id: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📂 Категория", callback_data=f"d_cat:{task_id}")
    b.button(text="⚡ Приоритет", callback_data=f"d_prio:{task_id}")
    b.button(text="⏰ Дедлайн", callback_data=f"d_due:{task_id}")
    b.button(text="🔁 Повтор", callback_data=f"d_rec:{task_id}")
    b.button(text="✅ Сохранить", callback_data=f"d_save:{task_id}")
    b.button(text="❌ Отмена", callback_data=f"d_cancel:{task_id}")
    b.adjust(2, 2, 2)
    return b.as_markup()


def category_kb(task_id: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in CATEGORIES:
        b.button(text=f"{CATEGORY_EMOJI.get(c,'')} {c}", callback_data=f"set_cat:{task_id}:{c}")
    b.button(text="‹ назад", callback_data=f"d_back:{task_id}")
    b.adjust(3, 1)
    return b.as_markup()


def priority_kb(task_id: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in PRIORITIES:
        b.button(text=f"{PRIORITY_EMOJI.get(p,'')} {p}", callback_data=f"set_prio:{task_id}:{p}")
    b.button(text="‹ назад", callback_data=f"d_back:{task_id}")
    b.adjust(4, 1)
    return b.as_markup()


def recurrence_kb(task_id: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for r in RECURRENCES:
        b.button(text=RECUR_LABEL[r], callback_data=f"set_rec:{task_id}:{r}")
    b.button(text="‹ назад", callback_data=f"d_back:{task_id}")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()


def due_quick_kb(task_id: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Через час", callback_data=f"set_due:{task_id}:+1ч")
    b.button(text="Сегодня 18:00", callback_data=f"set_due:{task_id}:сегодня 18:00")
    b.button(text="Завтра 9:00", callback_data=f"set_due:{task_id}:завтра 9:00")
    b.button(text="Завтра 18:00", callback_data=f"set_due:{task_id}:завтра 18:00")
    b.button(text="✍️ Ввести вручную", callback_data=f"due_manual:{task_id}")
    b.button(text="Убрать дедлайн", callback_data=f"set_due:{task_id}:none")
    b.button(text="‹ назад", callback_data=f"d_back:{task_id}")
    b.adjust(2, 2, 1, 1, 1)
    return b.as_markup()
