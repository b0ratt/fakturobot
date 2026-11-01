from datetime import date
from decimal import Decimal

import pytest

from fakturobot import pdf
from fakturobot.invoice import (
    HoursError, InvoiceDoc, compute_totals, fmt_money, fmt_number, invoice_dates, parse_hours,
)
from fakturobot.months import Month
from fakturobot.words import amount_words, number_words


@pytest.mark.parametrize("text, expected", [
    ("152", "152"), ("152,5", "152.5"), ("152.25", "152.25"), (" 160 ", "160"), ("152,5h", "152.5"),
    ("0,01", "0.01"), ("400", "400"),
])
def test_parse_hours_ok(text, expected):
    assert parse_hours(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["0", "0,00", "400,01", "401", "-5", "1,234", "abc", "1e3", "", "1 000", "152,5,5", "nan"])
def test_parse_hours_rejects(text):
    with pytest.raises(HoursError):
        parse_hours(text)


def test_dates_last_day_of_month():
    d = invoice_dates(Month(2026, 9), 14)
    assert (d.issue, d.sale, d.due) == (date(2026, 9, 30), date(2026, 9, 30), date(2026, 10, 14))  # jak wzór 9/2026
    d = invoice_dates(Month(2026, 10), 14)
    assert (d.issue, d.sale, d.due) == (date(2026, 10, 31), date(2026, 10, 31), date(2026, 11, 14))
    assert invoice_dates(Month(2028, 2), 14).issue == date(2028, 2, 29)
    assert invoice_dates(Month(2026, 12), 14).due == date(2027, 1, 14)


def test_totals_lump_sum_exempt():
    t = compute_totals(Decimal("152.5"), Decimal("70"), None, "kwota")
    assert (t.quantity, t.unit_price, t.net, t.vat, t.gross) == (1, Decimal("10675.00"), Decimal("10675.00"), 0, Decimal("10675.00"))
    assert t.vat_label == "zw."


def test_totals_rounding_half_up():
    assert compute_totals(Decimal("0.33"), Decimal("33.33"), None, "kwota").gross == Decimal("11.00")  # 10.9989


def test_totals_vat_payer_hours_mode():
    t = compute_totals(Decimal("10"), Decimal("100"), Decimal("0.23"), "godziny")
    assert (t.quantity, t.unit_price, t.net, t.vat, t.gross) == (10, 100, Decimal("1000.00"), Decimal("230.00"), Decimal("1230.00"))
    assert t.vat_label == "23%"


@pytest.mark.parametrize("n, words", [
    (0, "zero"), (1, "jeden"), (12, "dwanaście"), (21, "dwadzieścia jeden"), (100, "sto"),
    (1000, "tysiąc"), (1001, "tysiąc jeden"), (2000, "dwa tysiące"), (5000, "pięć tysięcy"),
    (12000, "dwanaście tysięcy"), (22000, "dwadzieścia dwa tysiące"), (12345, "dwanaście tysięcy trzysta czterdzieści pięć"),
    (1_000_000, "milion"), (2_500_000, "dwa miliony pięćset tysięcy"),
])
def test_number_words(n, words):
    assert number_words(n) == words


def test_amount_words():
    assert amount_words(Decimal("12345.00")) == "dwanaście tysięcy trzysta czterdzieści pięć PLN"
    assert amount_words(Decimal("101.05")) == "sto jeden PLN 05/100"


def test_formatting():
    assert fmt_money(Decimal("12345")) == "12 345,00"
    assert fmt_money(Decimal("1234567.5")) == "1 234 567,50"
    assert fmt_number(Decimal("152.50")) == "152,5"
    assert fmt_number(Decimal("160")) == "160"


def _doc(**overrides) -> InvoiceDoc:
    values = dict(
        number="1/2026", title="Faktura", place="Przykładowo", issue=date(2026, 11, 30), sale=date(2026, 11, 30),
        due=date(2026, 12, 14), seller=("Firma Ąę", "NIP 0000000000"), buyer=("Kupiec GmbH", "Niemcy"),
        item_name="Usługi programistyczne", pkwiu="62.01.11.0", unit="szt", quantity=Decimal(1),
        unit_price=Decimal("12345.00"), net=Decimal("12345.00"), vat=Decimal("0.00"), gross=Decimal("12345.00"),
        vat_label="zw.", payment_method="Przelew", bank_account="00 0000", notes="Zażółć gęślą jaźń\nlinia 2",
        issuer="JAN KOWALSKI",
    )
    values.update(overrides)
    return InvoiceDoc(**values)


def test_doc_roundtrip():
    doc = _doc()
    assert InvoiceDoc.from_dict(doc.to_dict()) == doc


def test_pdf_renders():
    data = pdf.render(_doc())
    assert data.startswith(b"%PDF") and len(data) > 5000
    assert pdf.render(_doc(), watermark="TEST").startswith(b"%PDF")
