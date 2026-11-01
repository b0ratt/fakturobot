"""Przepływ end-to-end na atrapach Telegrama i Gmaila (PDF generowany naprawdę)."""

from __future__ import annotations

import asyncio
import csv
import dataclasses
import io
from datetime import date
from decimal import Decimal

import pytest

from fakturobot.engine import Engine
from fakturobot.gmail import GmailError
from fakturobot.months import Month
from fakturobot.storage import APPROVED, ISSUED, PENDING, SENT, Storage

OCT, NOV = Month(2026, 10), Month(2026, 11)


class FakeOutbox:
    def __init__(self):
        self.messages: list[tuple[str, list]] = []
        self.documents: list[tuple[str, bytes, str, list]] = []
        self.buttons: list[str] = []  # wszystkie przyciski w kolejności wysłania

    async def text(self, text, buttons=None):
        self.messages.append((text, buttons or []))
        self.buttons += [d for _, d in buttons or []]

    async def document(self, data, filename, caption, buttons=None):
        self.documents.append((filename, data, caption, buttons or []))
        self.buttons += [d for _, d in buttons or []]

    def button(self, prefix: str) -> str:
        """Najnowszy przycisk danego typu."""
        found = [d for d in self.buttons if d.startswith(prefix + "|")]
        assert found, f"brak przycisku {prefix}"
        return found[-1]


class FakeGmail:
    sender = "ja@example.com"

    def __init__(self):
        self.sent: list[tuple] = []
        self.fail = False

    async def send(self, to, subject, body, attachment=None, filename=None, cc=()):
        if self.fail:
            raise GmailError("Gmail leży")
        self.sent.append((to, subject, body, attachment, filename))
        self.cc = tuple(cc)


@pytest.fixture
def world(settings, tmp_path):
    today = {"d": date(2026, 11, 27)}
    out, gmail = FakeOutbox(), FakeGmail()
    settings = dataclasses.replace(settings, rate=Decimal("70"), first_number=10, first_number_year=2026,
                                   accountant_cc=("szef@example.com",))
    engine = Engine(settings, Storage(tmp_path / "e.db"), out, gmail=gmail, today=lambda: today["d"])
    return engine, out, gmail, today


def run(coro):
    return asyncio.run(coro)


def click(engine, data: str) -> str:
    action, month, nonce = data.split("|")
    return run(engine.on_action(action, month, int(nonce)))


def test_full_flow_issue_on_last_day(world):
    engine, out, gmail, today = world
    run(engine.morning())
    assert "Ile godzin fakturujemy za <b>listopad 2026</b>" in out.messages[-1][0]

    run(engine.on_text("152,5"))
    summary = out.messages[-1][0]
    assert "Numer: 10/2026" in summary
    assert "10 675,00 zł" in summary and "30-11-2026" in summary and "14-12-2026" in summary

    assert click(engine, out.button("prev")) == "keep"
    assert out.documents[-1][0] == "podglad_2026-11.pdf" and out.documents[-1][1].startswith(b"%PDF")

    ok = out.button("ok")
    assert click(engine, ok) == "done"
    assert engine.db.get(NOV).status == APPROVED
    assert click(engine, ok) == "stale"  # drugie kliknięcie

    today["d"] = date(2026, 11, 28)
    run(engine.morning())  # zatwierdzona: nie pyta ponownie i jeszcze nie wystawia
    assert engine.db.get(NOV).status == APPROVED

    today["d"] = date(2026, 11, 30)
    run(engine.morning())
    rec = engine.db.get(NOV)
    assert rec.status == ISSUED and rec.number == "10/2026"
    filename, pdf_bytes, caption, buttons = out.documents[-1]
    assert filename == "faktura_10_2026_30-11-2026.pdf" and pdf_bytes.startswith(b"%PDF")
    assert rec.data["issue"] == "2026-11-30" and rec.data["due"] == "2026-12-14" and rec.data["gross"] == "10675.00"
    # kopia archiwalna na własny adres
    assert gmail.sent[-1][0] == "ja@example.com" and gmail.sent[-1][3] == pdf_bytes

    run(engine.morning())
    run(engine.catch_up())
    assert len(engine.db.issued()) == 1  # nigdy drugi raz

    sent_before = len(gmail.sent)
    mail = out.button("mail")
    assert click(engine, mail) == "keep"  # najpierw tylko podgląd
    preview = out.messages[-1][0]
    assert len(gmail.sent) == sent_before
    assert "Do: ksiegowa@example.com" in preview and "DW: szef@example.com" in preview
    assert "Temat: <b>Faktura listopad 2026 - Jan Kowalski</b>" in preview
    assert "Załącznik: faktura_10_2026_30-11-2026.pdf" in preview
    assert "Wysyłam fakturę sprzedażową za listopad 2026." in preview

    send = out.button("send")
    assert click(engine, send) == "done"
    assert click(engine, send) == "stale"
    assert click(engine, mail) == "stale"
    to, subject, body, attachment, fname = gmail.sent[-1]
    assert to == "ksiegowa@example.com" and subject == "Faktura listopad 2026 - Jan Kowalski"
    assert gmail.cc == ("szef@example.com",)
    assert "DW: szef@example.com" in out.messages[-1][0]
    assert "Wysyłam fakturę sprzedażową za listopad 2026." in body and attachment == pdf_bytes
    assert engine.db.get(NOV).status == SENT
    assert len([s for s in gmail.sent if s[0] == "ksiegowa@example.com"]) == 1


def test_late_october_then_november_numbering(world):
    engine, out, _, today = world
    today["d"] = date(2026, 11, 2)
    run(engine.morning())
    assert "październik 2026" in out.messages[-1][0]
    run(engine.on_text("100"))
    assert "Data wystawienia: 31-10-2026" in out.messages[-1][0]
    assert "Data sprzedaży: 31-10-2026" in out.messages[-1][0]
    assert "Termin płatności: 14-11-2026" in out.messages[-1][0]
    click(engine, out.button("ok"))
    assert engine.db.get(OCT).number == "10/2026"
    assert engine.db.get(OCT).data["issue"] == "2026-10-31"
    assert engine.db.get(OCT).data["due"] == "2026-11-14"
    assert out.documents[-1][0] == "faktura_10_2026_31-10-2026.pdf"

    today["d"] = date(2026, 11, 27)
    run(engine.morning())
    run(engine.on_text("100"))
    assert "Numer: 11/2026" in out.messages[-1][0]


def test_faktury_list_and_pdf_download(world):
    engine, out, _, today = world
    today["d"] = date(2026, 11, 30)
    run(engine.cmd_faktura(None))
    run(engine.on_text("100"))
    click(engine, out.button("ok"))
    run(engine.cmd_faktury())
    text, buttons = out.messages[-1]
    assert "10/2026" in text and "7 000,00 zł" in text
    assert click(engine, buttons[0][1]) == "keep"
    assert out.documents[-1][0] == "faktura_10_2026_30-11-2026.pdf"


def test_eksport_csv(world):
    engine, out, _, today = world
    today["d"] = date(2026, 11, 30)
    run(engine.cmd_faktura(None))
    run(engine.on_text("100"))
    click(engine, out.button("ok"))
    run(engine.cmd_eksport(None))
    filename, data, _, _ = out.documents[-1]
    assert filename == "faktury-2026.csv"
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig")), delimiter=";"))
    assert rows[0][0] == "numer" and rows[1][0] == "10/2026" and rows[1][8] == "7 000,00"
    run(engine.cmd_eksport("2025"))
    assert "Brak faktur" in out.messages[-1][0]


def test_fix_and_cancel(world):
    engine, out, _, _ = world
    run(engine.cmd_faktura(None))
    run(engine.on_text("100"))
    old_ok = out.button("ok")
    assert click(engine, out.button("fix")) == "done"
    assert engine.db.get(NOV).hours is None
    assert click(engine, old_ok) == "stale"  # podsumowanie sprzed poprawki jest nieaktualne
    run(engine.on_text("120"))
    assert "8 400,00" in out.messages[-1][0]
    assert click(engine, out.button("cancel")) == "done"
    assert engine.db.get(NOV) is None
    run(engine.on_text("120"))
    assert "Nie czekam" in out.messages[-1][0]


def test_invalid_hours(world):
    engine, out, _, _ = world
    run(engine.cmd_faktura(None))
    run(engine.on_text("dużo"))
    assert out.messages[-1][0].startswith("⚠️")
    run(engine.on_text("500"))
    assert "400" in out.messages[-1][0]
    assert engine.db.get(NOV).hours is None


def test_render_failure_keeps_approval_and_number(world, monkeypatch):
    engine, out, _, today = world
    today["d"] = date(2026, 11, 30)
    run(engine.cmd_faktura(None))
    run(engine.on_text("100"))
    from fakturobot import pdf

    def broken(*a, **k):
        raise RuntimeError("Brak czcionki")

    monkeypatch.setattr(pdf, "render", broken)
    click(engine, out.button("ok"))
    assert "Nie udało się wystawić" in out.messages[-1][0]
    assert engine.db.get(NOV).status == APPROVED
    monkeypatch.undo()
    assert click(engine, out.button("retry")) == "done"
    assert engine.db.get(NOV).number == "10/2026"


def test_mail_failure_restores_button(world):
    engine, out, gmail, today = world
    today["d"] = date(2026, 11, 30)
    run(engine.cmd_faktura(None))
    run(engine.on_text("100"))
    click(engine, out.button("ok"))
    gmail.fail = True
    click(engine, out.button("mail"))
    assert click(engine, out.button("send")) == "done"
    assert engine.db.get(NOV).status == ISSUED
    gmail.fail = False
    click(engine, out.button("mail"))
    assert click(engine, out.button("send")) == "done"
    assert engine.db.get(NOV).status == SENT


def test_undo_approval(world):
    engine, out, _, today = world
    run(engine.cmd_faktura(None))
    run(engine.on_text("100"))
    click(engine, out.button("ok"))
    assert click(engine, out.button("undo")) == "done"
    assert engine.db.get(NOV).status == PENDING
    today["d"] = date(2026, 11, 30)
    run(engine.morning())
    assert engine.db.get(NOV).status == PENDING


def test_dry_run_marks_pdf_and_sends_no_mail(world):
    engine, out, gmail, today = world
    engine.s = dataclasses.replace(engine.s, dry_run=True)
    today["d"] = date(2026, 11, 30)
    run(engine.cmd_faktura(None))
    run(engine.on_text("100"))
    click(engine, out.button("ok"))
    assert "DRY_RUN" in out.documents[-1][2]
    click(engine, out.button("mail"))
    assert "Podgląd maila" in out.messages[-1][0]
    click(engine, out.button("send"))
    assert not gmail.sent
    assert "DRY_RUN: mail nie został wysłany" in out.messages[-1][0]


def test_evening_reminder_only_without_answer(world):
    engine, out, _, today = world
    today["d"] = date(2026, 11, 30)
    run(engine.evening())
    assert "ostatni dzień" in out.messages[-1][0]
    run(engine.on_text("100"))
    click(engine, out.button("ok"))
    count = len(out.messages)
    run(engine.evening())
    assert len(out.messages) == count


def test_faktura_on_issued_month_offers_pdf(world):
    engine, out, _, today = world
    today["d"] = date(2026, 11, 30)
    run(engine.cmd_faktura(None))
    run(engine.on_text("100"))
    click(engine, out.button("ok"))
    run(engine.cmd_faktura("2026-11"))
    assert "już wystawiona (nr 10/2026)" in out.messages[-1][0]


def test_faktura_rejects_old_month(world):
    engine, out, _, _ = world
    run(engine.cmd_faktura("2026-01"))
    assert "bieżący albo poprzedni" in out.messages[-1][0]
