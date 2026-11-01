"""Miesiące rozliczeniowe i bieżąca data w strefie Europe/Warsaw."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")

_NAMES = (
    "styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec",
    "lipiec", "sierpień", "wrzesień", "październik", "listopad", "grudzień",
)


def today_warsaw() -> date:
    return datetime.now(WARSAW).date()


@dataclass(frozen=True, order=True)
class Month:
    year: int
    month: int

    def __post_init__(self) -> None:
        if not (2000 <= self.year <= 2100 and 1 <= self.month <= 12):
            raise ValueError(f"Niepoprawny miesiąc: {self.year}-{self.month}")

    @classmethod
    def of(cls, d: date) -> Month:
        return cls(d.year, d.month)

    @classmethod
    def parse(cls, text: str) -> Month:
        try:
            year, month = text.strip().split("-")
            return cls(int(year), int(month))
        except ValueError as e:
            raise ValueError(f"Niepoprawny miesiąc {text!r}, oczekuję RRRR-MM, np. 2026-11.") from e

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def label(self) -> str:
        """Np. „listopad 2026” (biernik = mianownik dla nazw miesięcy)."""
        return f"{_NAMES[self.month - 1]} {self.year}"

    @property
    def first_day(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def last_day(self) -> date:
        return date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])

    def prev(self) -> Month:
        return Month(self.year - 1, 12) if self.month == 1 else Month(self.year, self.month - 1)

    def next(self) -> Month:
        return Month(self.year + 1, 1) if self.month == 12 else Month(self.year, self.month + 1)
