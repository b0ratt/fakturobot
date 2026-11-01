"""Logika bota niezależna od Telegrama: przepływ faktury, harmonogram, przyciski i archiwum.

Każda zmiana stanu przechodzi przez Storage z oczekiwanym statusem i nonce, pod jednym
asyncio.Lock, więc ponowne lub stare kliknięcie niczego nie wystawi ani nie wyśle drugi raz.
"""

from __future__ import annotations

import asyncio
import csv
import html
import io
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any, Literal, Protocol

from . import pdf
from .config import Settings
from .gmail import GmailClient, GmailError
from .invoice import (
    HoursError, InvoiceDates, InvoiceDoc, Totals, compute_totals, fmt_date, fmt_money, fmt_number, invoice_dates,
    parse_hours,
)
from .months import Month, today_warsaw
from .schedule import default_target, evening_months, morning_months
from .storage import APPROVED, DONE, ISSUED, PENDING, SENT, Record, Storage

log = logging.getLogger(__name__)

Buttons = list[tuple[str, str]]  # (etykieta, callback_data)
# stale: przycisk nieaktualny; done: obsłużony, zdejmij przyciski z wiadomości; keep: obsłużony, zostaw przyciski
ActionResult = Literal["stale", "done", "keep"]

DRY_RUN_MARK = "DRY_RUN: dokument testowy, nie jest fakturą"
PREVIEW_MARK = "PODGLĄD: dokument nie jest fakturą"


class Outbox(Protocol):
    async def text(self, text: str, buttons: Buttons | None = None) -> None: ...
    async def document(self, data: bytes, filename: str, caption: str, buttons: Buttons | None = None) -> None: ...


def _cb(action: str, rec: Record) -> str:
    return f"{action}|{rec.month.key}|{rec.nonce}"


def _e(value: object) -> str:
    return html.escape(str(value))


def _filename(number: str, issue: date) -> str:
    return re.sub(r"[^\w.-]", "_", f"faktura_{number}_{fmt_date(issue)}") + ".pdf"


class Engine:
    def __init__(self, settings: Settings, storage: Storage, outbox: Outbox, *,
                 gmail: GmailClient | None = None, today: Callable[[], date] = today_warsaw) -> None:
        self.s = settings
        self.db = storage
        self.out = outbox
        self.gmail = gmail
        self._today = today
        self._lock = asyncio.Lock()
        self._actions: dict[str, Callable[[Month, int], Awaitable[ActionResult]]] = {
            "ok": self._act_approve,
            "fix": self._act_fix,
            "cancel": self._act_cancel,
            "undo": self._act_undo,
            "retry": self._act_retry,
            "prev": self._act_preview,
            "pdf": self._act_pdf,
            "mail": self._act_mail,
            "send": self._act_send,
        }

    @property
    def dry_run(self) -> bool:
        return self.s.dry_run

    # ------------------------------------------------------------------ wejście od użytkownika

    async def on_text(self, text: str) -> None:
        async with self._lock:
            rec = self.db.latest_pending()
            if rec is None:
                await self.out.text("Nie czekam teraz na liczbę godzin. Wpisz /faktura, żeby zacząć.")
                return
            try:
                hours = parse_hours(text)
            except HoursError as e:
                await self.out.text(f"⚠️ {_e(e)}")
                return
            rec = self.db.update(rec.month, expect={PENDING}, hours=hours)
            if rec is not None:
                await self._send_summary(rec)

    async def on_action(self, action: str, month_key: str, nonce: int) -> ActionResult:
        handler = self._actions.get(action)
        if handler is None:
            return "stale"
        try:
            month = Month.parse(month_key)
        except ValueError:
            return "stale"
        async with self._lock:
            return await handler(month, nonce)

    async def cmd_faktura(self, arg: str | None) -> None:
        today = self._today()
        cur = Month.of(today)
        if arg:
            try:
                month = Month.parse(arg)
            except ValueError as e:
                await self.out.text(f"⚠️ {_e(e)}")
                return
            if not cur.prev() <= month <= cur:
                await self.out.text("⚠️ Mogę wystawić fakturę tylko za bieżący albo poprzedni miesiąc.")
                return
        else:
            month = default_target(today, self.s.schedule, self._is_done)
        async with self._lock:
            await self._prompt(month, manual=True)

    async def cmd_faktury(self) -> None:
        records = self.db.issued(limit=12)
        if not records:
            await self.out.text("Nie ma jeszcze wystawionych faktur.")
            return
        lines = ["🗂 <b>Wystawione faktury</b> (ostatnie 12)", ""]
        buttons: Buttons = []
        for r in records:
            doc = InvoiceDoc.from_dict(r.data or {})
            sent = "wysłana do księgowej" if r.status == SENT else "niewysłana"
            lines.append(f"<b>{_e(r.number)}</b> · {_e(r.month.label)} · {fmt_money(doc.gross)} zł · {sent}")
            buttons.append((f"📄 {r.number} ({r.month.label})", _cb("pdf", r)))
        lines += ["", "Kliknij, żeby pobrać PDF. Zestawienie za rok: /eksport"]
        await self.out.text("\n".join(lines), buttons)

    async def cmd_eksport(self, arg: str | None) -> None:
        year = self._today().year
        if arg:
            if not arg.isdigit() or len(arg) != 4:
                await self.out.text("⚠️ Podaj rok, np. /eksport 2026")
                return
            year = int(arg)
        records = sorted(self.db.issued(year=year), key=lambda r: (r.data or {}).get("issue", ""))
        if not records:
            await self.out.text(f"Brak faktur wystawionych w {year}.")
            return
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";")
        writer.writerow(["numer", "miesiac", "data_wystawienia", "data_sprzedazy", "termin_platnosci",
                         "nabywca", "netto", "vat", "brutto", "wyslana_do_ksiegowej"])
        for r in records:
            doc = InvoiceDoc.from_dict(r.data or {})
            writer.writerow([doc.number, r.month.key, doc.issue.isoformat(), doc.sale.isoformat(), doc.due.isoformat(),
                             doc.buyer[0], fmt_money(doc.net), fmt_money(doc.vat), fmt_money(doc.gross),
                             "tak" if r.status == SENT else "nie"])
        await self.out.document(buf.getvalue().encode("utf-8-sig"), f"faktury-{year}.csv",
                                f"📊 Faktury wystawione w {year}: {len(records)}")

    async def cmd_status(self) -> None:
        today = self._today()
        mode = "🧪 DRY_RUN (faktury testowe, bez maili)" if self.dry_run else "🟢 produkcja"
        next_number = self.s.format_number(self.db.next_seq(today.year, self.s.first_seq(today.year)), today.year)
        lines = [f"Tryb: {mode}", f"Stawka: {fmt_money(self.s.rate)} zł/h", f"Następny numer: {_e(next_number)}", ""]
        records = self.db.recent()
        for r in records:
            extra = f" · {fmt_number(r.hours)} h" if r.hours is not None else ""
            extra += f" · nr {_e(r.number)}" if r.number else ""
            lines.append(f"{_e(r.month.label)}: <b>{r.status}</b>{extra}")
        if not records:
            lines.append("Brak zapisanych faktur.")
        await self.out.text("\n".join(lines))

    async def cmd_testmail(self) -> None:
        if self.gmail is None:
            await self.out.text("⚠️ Gmail nie jest skonfigurowany (GOOGLE_* i GMAIL_SENDER w .env).")
            return
        try:
            await self.gmail.send(
                to=self.gmail.sender,
                subject="fakturobot: test Gmail API",
                body="Test wysyłki z fakturobota. Jeśli to czytasz, Gmail API działa.",
            )
        except GmailError as e:
            await self.out.text(f"❌ Test Gmaila nieudany: {_e(e)}")
            return
        await self.out.text(f"📧 Wysłano testowego maila na {_e(self.gmail.sender)}.")

    # ------------------------------------------------------------------ harmonogram

    async def morning(self) -> None:
        today = self._today()
        async with self._lock:
            for month in morning_months(today, self.s.schedule):
                rec = self.db.get(month)
                if rec is not None and rec.status == APPROVED:
                    if today >= month.last_day:
                        await self._issue(month)
                    continue
                if month < Month.of(today):
                    prefix = f"⏰ Faktura za {_e(month.label)} nie jest jeszcze wystawiona."
                elif today == month.last_day:
                    prefix = "⏰ Dziś ostatni dzień miesiąca."
                else:
                    prefix = ""
                await self._prompt(month, prefix=prefix)

    async def evening(self) -> None:
        today = self._today()
        async with self._lock:
            for month in evening_months(today):
                rec = self.db.get(month)
                if rec is None or rec.status == PENDING:
                    await self._prompt(month, prefix="⏰ Dziś ostatni dzień miesiąca, przypominam.")

    async def catch_up(self) -> None:
        """Po starcie: wystaw zatwierdzone faktury, których termin minął, gdy bot nie działał."""
        today = self._today()
        async with self._lock:
            cur = Month.of(today)
            for month in (cur.prev(), cur):
                rec = self.db.get(month)
                if rec is not None and rec.status == APPROVED and today >= month.last_day:
                    await self._issue(month)

    # ------------------------------------------------------------------ przyciski

    async def _act_approve(self, month: Month, nonce: int) -> ActionResult:
        rec = self.db.get(month)
        if rec is None or rec.hours is None:
            return "stale"
        rec = self.db.update(month, expect={PENDING}, nonce=nonce, status=APPROVED)
        if rec is None:
            return "stale"
        log.info("Zatwierdzono fakturę za %s", month.key)
        if self._today() >= month.last_day:
            await self.out.text(f"✅ Zatwierdzone. Wystawiam fakturę za {_e(month.label)}…")
            await self._issue(month)
        else:
            await self.out.text(
                f"✅ Zatwierdzone. Wystawię fakturę za {_e(month.label)} "
                f"{fmt_date(month.last_day)} o {self.s.schedule.time:%H:%M} i przyślę PDF.",
                [("↩️ Cofnij zatwierdzenie", _cb("undo", rec))],
            )
        return "done"

    async def _act_fix(self, month: Month, nonce: int) -> ActionResult:
        if self.db.update(month, expect={PENDING}, nonce=nonce, hours=None) is None:
            return "stale"
        await self.out.text(f"✏️ Podaj poprawną liczbę godzin za {_e(month.label)}.")
        return "done"

    async def _act_cancel(self, month: Month, nonce: int) -> ActionResult:
        if not self.db.delete(month, expect={PENDING}, nonce=nonce):
            return "stale"
        await self.out.text("🗑️ Anulowano. Zapytam ponownie zgodnie z harmonogramem albo wpisz /faktura.")
        return "done"

    async def _act_undo(self, month: Month, nonce: int) -> ActionResult:
        rec = self.db.update(month, expect={APPROVED}, nonce=nonce, status=PENDING)
        if rec is None:
            return "stale"
        log.info("Cofnięto zatwierdzenie za %s", month.key)
        await self.out.text("↩️ Cofnięto zatwierdzenie.")
        await self._send_summary(rec)
        return "done"

    async def _act_retry(self, month: Month, nonce: int) -> ActionResult:
        rec = self.db.get(month)
        if rec is None or rec.status != APPROVED or rec.nonce != nonce:
            return "stale"
        await self._issue(month)
        return "done"

    async def _act_preview(self, month: Month, nonce: int) -> ActionResult:
        rec = self.db.get(month)
        if rec is None or rec.status != PENDING or rec.hours is None or rec.nonce != nonce:
            return "stale"
        dates = invoice_dates(month, self.s.payment_days)
        doc = self._doc(rec, self._totals(rec), dates, number="PODGLĄD")
        data = await asyncio.to_thread(pdf.render, doc, watermark=PREVIEW_MARK)
        await self.out.document(data, f"podglad_{month.key}.pdf", f"👁 Podgląd faktury za {_e(month.label)}")
        return "keep"

    async def _act_pdf(self, month: Month, nonce: int) -> ActionResult:
        rec = self.db.get(month)
        data = self.db.pdf(month)
        if rec is None or rec.status not in DONE or data is None or rec.number is None:
            return "stale"
        doc = InvoiceDoc.from_dict(rec.data or {})
        await self.out.document(data, _filename(rec.number, doc.issue), f"📄 Faktura {_e(rec.number)}",
                                self._issued_buttons(rec))
        return "keep"

    async def _act_mail(self, month: Month, nonce: int) -> ActionResult:
        """Podgląd maila do księgowej. Wysyłka dopiero po „Wyślij teraz”."""
        rec = self.db.get(month)
        if rec is None or rec.status != ISSUED or rec.nonce != nonce or rec.number is None:
            return "stale"
        if not self.dry_run and (self.gmail is None or not self.s.accountant_email):
            await self.out.text("⚠️ Gmail albo ACCOUNTANT_EMAIL nie są skonfigurowane w .env.")
            return "keep"
        subject, body = self._mail_content(month, rec.number)
        doc = InvoiceDoc.from_dict(rec.data or {})
        lines = [
            "📧 <b>Podgląd maila do księgowej</b>" + (" 🧪 DRY_RUN" if self.dry_run else ""),
            "",
            f"Od: {_e(self.gmail.sender if self.gmail else '(Gmail nieskonfigurowany)')}",
            f"Do: {_e(self.s.accountant_email or '(brak ACCOUNTANT_EMAIL)')}",
        ]
        if self.s.accountant_cc:
            lines.append(f"DW: {_e(', '.join(self.s.accountant_cc))}")
        lines += [
            f"Temat: <b>{_e(subject)}</b>",
            f"Załącznik: {_e(_filename(rec.number, doc.issue))}",
            "",
            _e(body.strip()),
        ]
        await self.out.text("\n".join(lines), [("✅ Wyślij teraz", _cb("send", rec))])
        return "keep"

    async def _act_send(self, month: Month, nonce: int) -> ActionResult:
        rec = self.db.get(month)
        if rec is None or rec.status != ISSUED or rec.nonce != nonce or rec.number is None:
            return "stale"
        if not self.dry_run and (self.gmail is None or not self.s.accountant_email):
            await self.out.text("⚠️ Gmail albo ACCOUNTANT_EMAIL nie są skonfigurowane w .env.")
            return "keep"
        number = rec.number
        rec = self.db.update(month, expect={ISSUED}, nonce=nonce, status=SENT)  # rezerwacja przed wysyłką
        if rec is None:
            return "stale"
        subject, body = self._mail_content(month, number)
        recipients = self.s.accountant_email or "(brak ACCOUNTANT_EMAIL)"
        if self.s.accountant_cc:
            recipients += f", DW: {', '.join(self.s.accountant_cc)}"
        try:
            if self.dry_run:
                await self.out.text(f"🧪 DRY_RUN: mail nie został wysłany (adresaci: {_e(recipients)}).")
                return "done"
            assert self.gmail is not None and self.s.accountant_email
            doc = InvoiceDoc.from_dict(rec.data or {})
            await self.gmail.send(self.s.accountant_email, subject, body, attachment=self.db.pdf(month),
                                  filename=_filename(number, doc.issue), cc=self.s.accountant_cc)
        except GmailError as e:
            rec = self.db.update(month, expect={SENT}, status=ISSUED) or rec
            log.warning("Wysyłka maila za %s nieudana: %s", month.key, type(e).__name__)
            await self.out.text(f"❌ Nie wysłano maila: {_e(e)}", self._issued_buttons(rec))
            return "done"
        log.info("Wysłano fakturę %s do księgowej", number)
        await self.out.text(f"📧 Wysłano fakturę {_e(number)}: {_e(recipients)}.")
        return "done"

    # ------------------------------------------------------------------ wewnętrzne

    def _is_done(self, month: Month) -> bool:
        rec = self.db.get(month)
        return rec is not None and rec.status in DONE

    def _totals(self, rec: Record) -> Totals:
        assert rec.hours is not None
        return compute_totals(rec.hours, self.s.rate, self.s.invoice.vat_rate, self.s.position_mode)

    def _doc(self, rec: Record, totals: Totals, dates: InvoiceDates, number: str) -> InvoiceDoc:
        t = self.s.invoice
        return InvoiceDoc(
            number=number, title=t.title, place=t.place, issue=dates.issue, sale=dates.sale, due=dates.due,
            seller=t.seller, buyer=t.buyer, item_name=t.item_name, pkwiu=t.pkwiu, unit=t.unit,
            quantity=totals.quantity, unit_price=totals.unit_price, net=totals.net, vat=totals.vat, gross=totals.gross,
            vat_label=totals.vat_label, payment_method=t.payment_method, bank_account=t.bank_account,
            notes=t.notes, issuer=t.issuer,
        )

    def _mail_content(self, month: Month, number: str) -> tuple[str, str]:
        values = {"miesiac": month.label, "numer": number}
        return self.s.mail_subject.format(**values), self.s.mail_body.format(**values)

    def _issued_buttons(self, rec: Record) -> Buttons:
        return [("📧 Wyślij do księgowej", _cb("mail", rec))] if rec.status == ISSUED else []

    async def _prompt(self, month: Month, *, prefix: str = "", manual: bool = False) -> None:
        """Zapytaj o godziny, przypomnij podsumowanie albo powiedz, że faktura już jest."""
        rec = self.db.get(month)
        if rec is not None and rec.status in DONE:
            if manual:
                await self.out.text(
                    f"ℹ️ Faktura za {_e(month.label)} jest już wystawiona (nr {_e(rec.number)}).",
                    [("📄 Pobierz PDF", _cb("pdf", rec)), *self._issued_buttons(rec)],
                )
            return
        if rec is not None and rec.status == APPROVED:
            if manual:
                await self.out.text(
                    f"✅ Faktura za {_e(month.label)} jest zatwierdzona. Wystawię ją "
                    f"{fmt_date(max(month.last_day, self._today()))} o {self.s.schedule.time:%H:%M}.",
                    [("↩️ Cofnij zatwierdzenie", _cb("undo", rec))],
                )
            return
        if rec is not None and rec.hours is not None:
            await self._send_summary(rec, prefix=prefix)
            return
        if rec is None:
            self.db.create(month)
        else:
            self.db.update(month, expect={PENDING})  # odświeża updated_at: na ten miesiąc czekamy
        lead = f"{prefix}\n" if prefix else ""
        await self.out.text(f"{lead}🕐 Ile godzin fakturujemy za <b>{_e(month.label)}</b>?\nOdpisz liczbą, np. 152 albo 152,5.")

    async def _send_summary(self, rec: Record, *, prefix: str = "") -> None:
        today = self._today()
        t = self._totals(rec)
        d = invoice_dates(rec.month, self.s.payment_days)
        inv = self.s.invoice
        number = self.s.format_number(self.db.next_seq(d.issue.year, self.s.first_seq(d.issue.year)), d.issue.year)
        pkwiu = f" (PKWiU {inv.pkwiu})" if inv.pkwiu else ""
        if today >= rec.month.last_day:
            when = "Wystawię od razu po zatwierdzeniu."
        else:
            when = f"Po zatwierdzeniu wystawię ją automatycznie {fmt_date(d.issue)} o {self.s.schedule.time:%H:%M}."
        lines = [
            prefix,
            f"🧾 <b>Faktura za {_e(rec.month.label)}: do zatwierdzenia</b>" + (" 🧪 DRY_RUN" if self.dry_run else ""),
            "",
            f"Numer: {_e(number)}",
            f"Pozycja: {_e(inv.item_name)}{_e(pkwiu)}",
            f"Godziny: {fmt_number(rec.hours or 0)} h × {fmt_money(self.s.rate)} zł",
            f"Ilość: {fmt_number(t.quantity)} {_e(inv.unit)} × {fmt_money(t.unit_price)} zł",
            f"Netto: <b>{fmt_money(t.net)} zł</b>",
            f"VAT: {_e(t.vat_label)}, {fmt_money(t.vat)} zł",
            f"Brutto: <b>{fmt_money(t.gross)} zł</b>",
            "",
            f"Data wystawienia: {fmt_date(d.issue)}",
            f"Data sprzedaży: {fmt_date(d.sale)}",
            f"Termin płatności: {fmt_date(d.due)} ({_e(inv.payment_method.lower())})",
            f"Nabywca: {_e(', '.join(inv.buyer))}",
            "",
            when,
        ]
        await self.out.text(
            "\n".join(lines).strip(),
            [("✅ Zatwierdź", _cb("ok", rec)), ("👁 Podgląd PDF", _cb("prev", rec)),
             ("✏️ Popraw godziny", _cb("fix", rec)), ("❌ Anuluj", _cb("cancel", rec))],
        )

    async def _issue(self, month: Month) -> None:
        """Nadaje numer, generuje PDF i archiwizuje fakturę. Wywoływać pod self._lock."""
        rec = self.db.get(month)
        if rec is None or rec.status != APPROVED:
            return
        dates = invoice_dates(month, self.s.payment_days)
        totals = self._totals(rec)
        year = dates.issue.year

        def render(seq: int) -> tuple[str, dict[str, Any], bytes]:
            number = self.s.format_number(seq, year)
            doc = self._doc(rec, totals, dates, number)
            return number, doc.to_dict(), pdf.render(doc, watermark=DRY_RUN_MARK if self.dry_run else None)

        try:
            issued = self.db.issue(month, year=year, first=self.s.first_seq(year), render=render)
        except Exception as e:  # noqa: BLE001 — transakcja wycofana, numer nie przepadł
            log.error("Wystawienie faktury za %s nieudane: %s", month.key, type(e).__name__, exc_info=e)
            await self.out.text(
                f"❌ Nie udało się wystawić faktury za {_e(month.label)} ({_e(type(e).__name__)}: {_e(e)}).\n"
                "Nic nie zostało zapisane, numer nie przepadł.",
                [("🔁 Spróbuj ponownie", _cb("retry", rec)), ("↩️ Cofnij zatwierdzenie", _cb("undo", rec))],
            )
            return
        if issued is None or issued.number is None:
            return
        log.info("Wystawiono fakturę %s za %s", issued.number, month.key)
        data = self.db.pdf(month) or b""
        filename = _filename(issued.number, dates.issue)
        header = "🧪 DRY_RUN: testowa faktura" if self.dry_run else "✅ Wystawiono fakturę"
        await self.out.document(data, filename, f"{header} <b>{_e(issued.number)}</b> za {_e(month.label)}.",
                                self._issued_buttons(issued))
        await self._archive_copy(issued.number, data, filename)

    async def _archive_copy(self, number: str, data: bytes, filename: str) -> None:
        """Kopia zapasowa każdej faktury na własny adres Gmail (archiwum poza serwerem)."""
        if self.dry_run or self.gmail is None:
            return
        try:
            await self.gmail.send(
                self.gmail.sender, f"[fakturobot] Archiwum: faktura {number}",
                f"Kopia archiwalna faktury {number} wystawionej przez fakturobota.",
                attachment=data, filename=filename,
            )
        except GmailError as e:
            log.warning("Kopia archiwalna %s nieudana: %s", number, type(e).__name__)
            await self.out.text(f"⚠️ Faktura jest zapisana na serwerze, ale kopia na Gmail się nie udała: {_e(e)}")
