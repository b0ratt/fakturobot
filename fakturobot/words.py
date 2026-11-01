"""Kwota słownie po polsku, np. 12345 → „dwanaście tysięcy trzysta czterdzieści pięć”."""

from __future__ import annotations

from decimal import Decimal

_UNITS = ("", "jeden", "dwa", "trzy", "cztery", "pięć", "sześć", "siedem", "osiem", "dziewięć")
_TEENS = ("dziesięć", "jedenaście", "dwanaście", "trzynaście", "czternaście",
          "piętnaście", "szesnaście", "siedemnaście", "osiemnaście", "dziewiętnaście")
_TENS = ("", "", "dwadzieścia", "trzydzieści", "czterdzieści",
         "pięćdziesiąt", "sześćdziesiąt", "siedemdziesiąt", "osiemdziesiąt", "dziewięćdziesiąt")
_HUNDREDS = ("", "sto", "dwieście", "trzysta", "czterysta", "pięćset", "sześćset", "siedemset", "osiemset", "dziewięćset")
_GROUPS = (("", "", ""), ("tysiąc", "tysiące", "tysięcy"), ("milion", "miliony", "milionów"))


def _below_thousand(n: int) -> list[str]:
    words = [_HUNDREDS[n // 100]]
    rest = n % 100
    if 10 <= rest < 20:
        words.append(_TEENS[rest - 10])
    else:
        words += [_TENS[rest // 10], _UNITS[rest % 10]]
    return [w for w in words if w]


def _form(n: int, forms: tuple[str, str, str]) -> str:
    if n == 1:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def number_words(n: int) -> str:
    if not 0 <= n < 1_000_000_000:
        raise ValueError("Obsługiwany zakres: 0–999 999 999")
    if n == 0:
        return "zero"
    words: list[str] = []
    for power in (2, 1, 0):
        group = n // 1000**power % 1000
        if not group:
            continue
        if power and group == 1:
            words.append(_GROUPS[power][0])  # „tysiąc”, nie „jeden tysiąc”
            continue
        words += _below_thousand(group)
        if power:
            words.append(_form(group, _GROUPS[power]))
    return " ".join(words)


def amount_words(amount: Decimal, currency: str = "PLN") -> str:
    """12345.00 → „dwanaście tysięcy trzysta czterdzieści pięć PLN”; z groszami: „… PLN 50/100”."""
    cents = int((amount * 100).to_integral_value())
    zloty, grosze = divmod(cents, 100)
    text = f"{number_words(zloty)} {currency}"
    return f"{text} {grosze:02d}/100" if grosze else text
