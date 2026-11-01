"""Konfiguracja: sekrety ze zmiennych środowiskowych (.env wczytuje systemd), dane faktury z config.yaml.

Komunikaty błędów podają nazwy zmiennych i kluczy, nigdy ich wartości.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from .schedule import ScheduleConfig

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class GmailSettings:
    client_id: str
    client_secret: str
    refresh_token: str
    sender: str


@dataclass(frozen=True)
class InvoiceTemplate:
    """Stałe dane faktury (z wzorcowej faktury)."""

    title: str
    place: str
    issuer: str
    seller: tuple[str, ...]
    buyer: tuple[str, ...]
    item_name: str
    pkwiu: str
    unit: str
    vat_rate: Decimal | None  # None = zwolnienie („zw.”)
    payment_method: str
    bank_account: str
    notes: str


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    chat_id: int
    dry_run: bool
    gmail: GmailSettings | None
    accountant_email: str | None
    accountant_cc: tuple[str, ...]  # do wiadomości (DW) przy wysyłce faktury
    data_dir: Path
    rate: Decimal
    position_mode: str  # „kwota” albo „godziny”
    payment_days: int
    number_format: str  # np. „{numer}/{rok}”
    first_number: int  # pierwszy numer nadany przez bota…
    first_number_year: int  # …w tym roku; w kolejnych latach od 1
    schedule: ScheduleConfig
    mail_subject: str
    mail_body: str
    invoice: InvoiceTemplate

    def secrets(self) -> list[str]:
        """Wartości do zamaskowania w logach."""
        values = [self.telegram_token]
        if self.gmail:
            values += [self.gmail.client_secret, self.gmail.refresh_token]
        return [v for v in values if v]

    def first_seq(self, year: int) -> int:
        return self.first_number if year == self.first_number_year else 1

    def format_number(self, seq: int, year: int) -> str:
        return self.number_format.format(numer=seq, rok=year)


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "tak", "on"}:
        return True
    if raw in {"0", "false", "no", "nie", "off"}:
        return False
    raise ConfigError(f"{name} musi mieć wartość true albo false")


def _env(env: Mapping[str, str], name: str) -> str:
    return env.get(name, "").strip()


def _required(env: Mapping[str, str], name: str) -> str:
    value = _env(env, name)
    if not value:
        raise ConfigError(f"Brak zmiennej {name} w .env")
    return value


def _time(value: Any, key: str) -> time:
    try:
        hh, mm = str(value).split(":")
        return time(int(hh), int(mm))
    except ValueError:
        raise ConfigError(f"{key}: oczekuję godziny w formacie HH:MM") from None


def _load_secrets(env: Mapping[str, str]) -> dict[str, Any]:
    dry_run = _env_bool(env, "DRY_RUN", default=True)
    token = _required(env, "TELEGRAM_BOT_TOKEN")
    try:
        chat_id = int(_required(env, "TELEGRAM_CHAT_ID"))
    except ValueError:
        raise ConfigError("TELEGRAM_CHAT_ID musi być liczbą") from None

    gmail_vars = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "GMAIL_SENDER"]
    present = [n for n in gmail_vars if _env(env, n)]
    gmail = None
    if present and len(present) != len(gmail_vars):
        missing = ", ".join(n for n in gmail_vars if n not in present)
        raise ConfigError(f"Konfiguracja Gmaila niepełna, brakuje: {missing}")
    if present:
        gmail = GmailSettings(*(_env(env, n) for n in gmail_vars))
        if not _EMAIL_RE.match(gmail.sender):
            raise ConfigError("GMAIL_SENDER nie wygląda na adres e-mail")

    accountant = _env(env, "ACCOUNTANT_EMAIL") or None
    if accountant and not _EMAIL_RE.match(accountant):
        raise ConfigError("ACCOUNTANT_EMAIL nie wygląda na adres e-mail")
    if gmail and not accountant:
        raise ConfigError("Skonfigurowano Gmaila, ale brakuje ACCOUNTANT_EMAIL")
    cc = tuple(a.strip() for a in _env(env, "ACCOUNTANT_CC").split(",") if a.strip())
    if not all(_EMAIL_RE.match(a) for a in cc):
        raise ConfigError("ACCOUNTANT_CC: adresy e-mail oddzielone przecinkami")

    return dict(telegram_token=token, chat_id=chat_id, dry_run=dry_run, gmail=gmail,
                accountant_email=accountant, accountant_cc=cc)


def _str(section: Mapping[str, Any], key: str, path: str, *, required: bool = True) -> str:
    value = section.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, (str, int)) or (required and not str(value).strip()):
        raise ConfigError(f"{path}.{key}: wymagany tekst")
    return str(value).strip()


def _lines(section: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = section.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(x, (str, int)) for x in value):
        raise ConfigError(f"faktura.{key}: lista linii tekstu")
    return tuple(str(x) for x in value)


def _invoice(raw: Any) -> InvoiceTemplate:
    if not isinstance(raw, dict):
        raise ConfigError("Brak sekcji faktura")
    item = raw.get("pozycja")
    if not isinstance(item, dict):
        raise ConfigError("Brak sekcji faktura.pozycja")
    vat = raw.get("vat")
    if str(vat).strip().lower() == "zw":
        vat_rate = None
    else:
        try:
            vat_rate = Decimal(str(vat)) / 100
        except InvalidOperation:
            raise ConfigError("faktura.vat: zw albo stawka w procentach, np. 23") from None
        if not 0 <= vat_rate < 1:
            raise ConfigError("faktura.vat: stawka 0–99")
    return InvoiceTemplate(
        title=_str(raw, "tytul", "faktura"),
        place=_str(raw, "miejsce_wystawienia", "faktura"),
        issuer=_str(raw, "wystawil", "faktura"),
        seller=_lines(raw, "sprzedawca"),
        buyer=_lines(raw, "nabywca"),
        item_name=_str(item, "nazwa", "faktura.pozycja"),
        pkwiu=_str(item, "pkwiu", "faktura.pozycja", required=False),
        unit=_str(item, "jednostka", "faktura.pozycja"),
        vat_rate=vat_rate,
        payment_method=_str(raw, "sposob_zaplaty", "faktura"),
        bank_account=_str(raw, "konto", "faktura"),
        notes=str(raw.get("uwagi") or "").strip(),
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"Nie znaleziono pliku konfiguracji {path}") from None
    except yaml.YAMLError as e:
        raise ConfigError(f"Błąd składni w {path}: {e}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: oczekuję słownika na najwyższym poziomie")

    try:
        rate = Decimal(str(raw.get("stawka_godzinowa")))
    except InvalidOperation:
        raise ConfigError("stawka_godzinowa musi być liczbą, np. \"100.00\"") from None
    if not rate.is_finite() or rate <= 0:
        raise ConfigError("stawka_godzinowa musi być większa od 0")

    mode = raw.get("pozycja_jako", "kwota")
    if mode not in {"kwota", "godziny"}:
        raise ConfigError("pozycja_jako: dozwolone wartości to kwota albo godziny")

    payment_days = raw.get("termin_platnosci_dni", 14)
    if not isinstance(payment_days, int) or not 0 <= payment_days <= 120:
        raise ConfigError("termin_platnosci_dni: liczba całkowita 0–120")

    n = raw.get("numeracja") or {}
    number_format = str(n.get("format", "{numer}/{rok}"))
    try:
        if "{numer}" not in number_format:
            raise KeyError
        number_format.format(numer=1, rok=2026)
    except (KeyError, IndexError, ValueError):
        raise ConfigError("numeracja.format: musi zawierać {numer}, opcjonalnie {rok}") from None
    first_number, first_year = n.get("pierwszy_numer", 1), n.get("rok_pierwszego_numeru", 0)
    if not isinstance(first_number, int) or first_number < 1 or not isinstance(first_year, int):
        raise ConfigError("numeracja.pierwszy_numer i rok_pierwszego_numeru: liczby całkowite")

    h = raw.get("harmonogram") or {}
    days = tuple(h.get("dni_przed_koncem", (3, 2, 1)))
    if not all(isinstance(d, int) and 1 <= d <= 27 for d in days):
        raise ConfigError("harmonogram.dni_przed_koncem: lista liczb 1–27")
    until = h.get("przypominaj_do_dnia", 15)
    if not isinstance(until, int) or not 0 <= until <= 28:
        raise ConfigError("harmonogram.przypominaj_do_dnia: liczba 0–28")
    schedule = ScheduleConfig(
        days_before_end=days,
        time=_time(h.get("godzina", "10:00"), "harmonogram.godzina"),
        evening_time=_time(h.get("przypomnienie_wieczorne", "18:00"), "harmonogram.przypomnienie_wieczorne"),
        remind_until_day=until,
    )

    mail = raw.get("mail") or {}
    subject, body = mail.get("temat"), mail.get("tresc")
    if not (isinstance(subject, str) and isinstance(body, str)):
        raise ConfigError("mail.temat i mail.tresc są wymagane")
    try:
        subject.format(miesiac="x", numer="x"), body.format(miesiac="x", numer="x")
    except (KeyError, IndexError, ValueError):
        raise ConfigError("mail.temat / mail.tresc: dozwolone znaczniki to {miesiac} i {numer}") from None

    return dict(
        rate=rate, position_mode=mode, payment_days=payment_days, number_format=number_format,
        first_number=first_number, first_number_year=first_year, schedule=schedule,
        mail_subject=subject, mail_body=body, invoice=_invoice(raw.get("faktura")),
    )


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    config_path = Path(env.get("FAKTUROBOT_CONFIG", "config.yaml"))
    data_dir = Path(env.get("FAKTUROBOT_DATA_DIR", "data"))
    return Settings(data_dir=data_dir, **_load_secrets(env), **_load_yaml(config_path))
