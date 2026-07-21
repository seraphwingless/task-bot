"""Разбор дат, повторы, форматирование."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from storage import Task

PRIORITY_EMOJI = {"P1": "🔴", "P2": "🟠", "P3": "🟡", "P4": "⚪"}
class _CatEmoji(dict):
    """Регистронезависимый доступ: 'Личное' и 'личное' — одно и то же."""

    def get(self, key, default=""):
        return dict.get(self, (key or "").strip().lower(), default)


CATEGORY_EMOJI = _CatEmoji({
    "личное": "🙋‍♂️", "бизнес": "💼", "семья": "👨‍👩‍👧", "спорт": "⚽",
})
RECUR_LABEL = {
    "none": "разово", "daily": "каждый день", "weekly": "каждую неделю",
    "monthly": "каждый месяц", "yearly": "каждый год",
}


def now_tz(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def parse_due(text: str, tz: str) -> datetime | None:
    """Понимает: 'завтра 18:00', 'сегодня 9:00', '16.07', '16.07 18:30',
    '16.07.2026 18:30', '+2ч', '+30м', '18:00'. Возвращает naive datetime
    (в локальной зоне tz, без tzinfo — так удобнее сравнивать с due_at в Sheets)."""
    if not text:
        return None
    t = text.strip().lower()
    base = now_tz(tz).replace(tzinfo=None)

    # относительное: +2ч / +30м / +1д
    m = re.fullmatch(r"\+(\d+)\s*([мmчhдd])", t)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit in ("м", "m"):
            return base + timedelta(minutes=n)
        if unit in ("ч", "h"):
            return base + timedelta(hours=n)
        return base + timedelta(days=n)

    def hm(s: str) -> tuple[int, int] | None:
        mm = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
        return (int(mm.group(1)), int(mm.group(2))) if mm else None

    parts = t.split()

    if parts[0] == "сегодня":
        h, mi = (hm(parts[1]) or (9, 0)) if len(parts) > 1 else (9, 0)
        return base.replace(hour=h, minute=mi, second=0, microsecond=0)

    if parts[0] == "завтра":
        h, mi = (hm(parts[1]) or (9, 0)) if len(parts) > 1 else (9, 0)
        d = base + timedelta(days=1)
        return d.replace(hour=h, minute=mi, second=0, microsecond=0)

    # только время -> сегодня, а если уже прошло -> завтра
    only = hm(parts[0]) if len(parts) == 1 else None
    if only:
        cand = base.replace(hour=only[0], minute=only[1], second=0, microsecond=0)
        return cand if cand > base else cand + timedelta(days=1)

    # дата DD.MM[.YYYY] [HH:MM]
    dm = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?", parts[0])
    if dm:
        day, month = int(dm.group(1)), int(dm.group(2))
        year = int(dm.group(3)) if dm.group(3) else base.year
        if year < 100:
            year += 2000
        h, mi = (hm(parts[1]) or (9, 0)) if len(parts) > 1 else (9, 0)
        try:
            return datetime(year, month, day, h, mi)
        except ValueError:
            return None
    return None


def _add_months(dt: datetime, months: int) -> datetime:
    m = dt.month - 1 + months
    year = dt.year + m // 12
    month = m % 12 + 1
    # аккуратно с концом месяца
    day = min(dt.day, [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0)
                       else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return dt.replace(year=year, month=month, day=day)


def next_occurrence(dt: datetime, recurrence: str) -> datetime | None:
    if recurrence == "daily":
        return dt + timedelta(days=1)
    if recurrence == "weekly":
        return dt + timedelta(weeks=1)
    if recurrence == "monthly":
        return _add_months(dt, 1)
    if recurrence == "yearly":
        return _add_months(dt, 12)
    return None


def human_due(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return ""
    return dt.strftime("%d.%m %H:%M")


def fmt_task(t: Task, with_id: bool = False) -> str:
    parts = [PRIORITY_EMOJI.get(t.priority, "")]
    if t.category:
        parts.append(CATEGORY_EMOJI.get(t.category, ""))
    line = " ".join(p for p in parts if p)
    head = f"{line} <b>{_esc(t.title)}</b>"
    meta = []
    if t.due_at:
        meta.append(f"⏰ {human_due(t.due_at)}")
    if t.recurrence and t.recurrence != "none":
        meta.append(f"🔁 {RECUR_LABEL.get(t.recurrence, t.recurrence)}")
    if t.attachments_list():
        meta.append(f"📎 {len(t.attachments_list())}")
    if with_id:
        meta.append(f"<code>{t.id}</code>")
    if meta:
        head += "\n   " + "  ".join(meta)
    if t.notes:
        head += f"\n   <i>{_esc(t.notes)}</i>"
    return head


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
