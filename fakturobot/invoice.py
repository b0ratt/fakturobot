"""Wyliczenia faktury: godziny, kwoty (Decimal), daty i dane dokumentu."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .months import Month

HOURS_MAX = Decimal("400")
CENTS = Decimal("0.01")
_HOURS_RE = re.compile(r"^\d{1,3}(?:[.,]\d{1,2})?$")


class HoursError(ValueError):
    pass


def parse_hours(text: str) -> Decimal:
    """„152”, „152,5”, „152.25” → Decimal. Tylko 0 < h ≤ 400, maks. 2 miejsca po przecinku."""
    t = text.strip().lower().removesuffix("h").strip()
    if not _HOURS_RE.match(t):
        raise HoursError("Podaj samą liczbę godzin, np. 152 albo 152,5 (maks. 2 miejsca po przecinku).")
    hours = Decimal(t.replace(",", "."))
    if not (0 < hours <= HOURS_MAX):
        raise HoursError(f"Liczba godzin musi być większa od 0 i nie większa niż {HOURS_MAX}.")
    return hours


def money(x: Decimal) -> Decimal:
    return x.quantize(CENTS, rounding=ROUND_HALF_UP)


def fmt_money(x: Decimal) -> str:
    """10675.5 → „10 675,50”."""
    return f"{money(x):,.2f}".replace(",", " ").replace(".", ",")


def fmt_number(x: Decimal) -> str:
    """152.50 → „152,5”."""
    return format(x.normalize(), "f").replace(".", ",")


def fmt_date(d: date) -> str:
    return d.strftime("%d-%m-%Y")


@dataclass(frozen=True)
class InvoiceDates:
    issue: date
    sale: date
    due: date


def invoice_dates(month: Month, payment_days: int) -> InvoiceDates:
    """Wystawienie i sprzedaż: ostatni dzień fakturowanego miesiąca (także gdy zatwierdzasz później).
    Termin płatności: wystawienie + N dni."""
    last = month.last_day
    return InvoiceDates(issue=last, sale=last, due=last + timedelta(days=payment_days))


@dataclass(frozen=True)
class Totals:
    quantity: Decimal
    unit_price: Decimal
    net: Decimal
    vat: Decimal
    gross: Decimal
    vat_label: str


def compute_totals(hours: Decimal, rate: Decimal, vat_rate: Decimal | None, mode: str) -> Totals:
    """mode „kwota”: 1 × (godziny × stawka); „godziny”: godziny × stawka. vat_rate None = zwolnienie."""
    if mode == "godziny":
        quantity, unit_price = hours, rate
    else:
        quantity, unit_price = Decimal(1), money(hours * rate)
    net = money(quantity * unit_price)
    if vat_rate is None:
        return Totals(quantity, unit_price, net, Decimal("0.00"), net, "zw.")
    vat = money(net * vat_rate)
    return Totals(quantity, unit_price, net, vat, net + vat, f"{fmt_number(vat_rate * 100)}%")


@dataclass(frozen=True)
class InvoiceDoc:
    """Wszystko, co jest na fakturze. Zapisywane w archiwum razem z PDF-em."""

    number: str
    title: str
    place: str
    issue: date
    sale: date
    due: date
    seller: tuple[str, ...]
    buyer: tuple[str, ...]
    item_name: str
    pkwiu: str
    unit: str
    quantity: Decimal
    unit_price: Decimal
    net: Decimal
    vat: Decimal
    gross: Decimal
    vat_label: str
    payment_method: str
    bank_account: str
    notes: str
    issuer: str

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in asdict(self).items():
            if isinstance(v, (date, Decimal)):
                v = v.isoformat() if isinstance(v, date) else str(v)
            elif isinstance(v, tuple):
                v = list(v)
            out[k] = v
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InvoiceDoc:
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            v = data[f.name]
            if f.name in {"issue", "sale", "due"}:
                v = date.fromisoformat(v)
            elif f.name in {"quantity", "unit_price", "net", "vat", "gross"}:
                v = Decimal(v)
            elif f.name in {"seller", "buyer"}:
                v = tuple(v)
            kwargs[f.name] = v
        return cls(**kwargs)
