"""Kiedy pytać o godziny i kiedy wystawiać. Czyste funkcje bez I/O."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, time

from .months import Month


@dataclass(frozen=True)
class ScheduleConfig:
    days_before_end: tuple[int, ...]  # np. (3, 2, 1): pytania przed ostatnim dniem
    time: time  # codzienne pytanie, a ostatniego dnia też wystawienie
    evening_time: time  # drugie przypomnienie w ostatni dzień miesiąca
    remind_until_day: int  # w następnym miesiącu przypominaj do tego dnia


def morning_months(today: date, cfg: ScheduleConfig) -> list[Month]:
    """Miesiące, którymi poranne zadanie ma się dziś zająć (zapytać albo wystawić)."""
    cur = Month.of(today)
    months = []
    if today.day <= cfg.remind_until_day:
        months.append(cur.prev())
    days_left = (cur.last_day - today).days
    if days_left == 0 or days_left in cfg.days_before_end:
        months.append(cur)
    return months


def evening_months(today: date) -> list[Month]:
    cur = Month.of(today)
    return [cur] if today == cur.last_day else []


def default_target(today: date, cfg: ScheduleConfig, is_done: Callable[[Month], bool]) -> Month:
    """Miesiąc dla /faktura bez argumentu: zaległy poprzedni, a jeśli go nie ma, to bieżący."""
    cur = Month.of(today)
    if today.day <= cfg.remind_until_day and not is_done(cur.prev()):
        return cur.prev()
    return cur
