"""PDF faktury (reportlab), generowany w pamięci."""

from __future__ import annotations

import html
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .invoice import InvoiceDoc, fmt_date, fmt_money, fmt_number
from .words import amount_words

# Czcionki z polskimi znakami: DejaVu (Ubuntu: fonts-dejavu-core), a lokalnie na Windows Arial.
_FONT_CANDIDATES = (
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
)
_FONT, _BOLD = "Faktura", "Faktura-Bold"
_GRAY = colors.HexColor("#dddddd")
_LIGHT = colors.HexColor("#f1f1f1")
_BAR = colors.HexColor("#bbbbbb")


def _register_fonts() -> None:
    if _FONT in pdfmetrics.getRegisteredFontNames():
        return
    for regular, bold in _FONT_CANDIDATES:
        if Path(regular).is_file() and Path(bold).is_file():
            pdfmetrics.registerFont(TTFont(_FONT, regular))
            pdfmetrics.registerFont(TTFont(_BOLD, bold))
            return
    raise RuntimeError("Brak czcionki TTF z polskimi znakami (na Ubuntu: apt install fonts-dejavu-core)")


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("base", fontName=_FONT, fontSize=8.5, leading=11)
    return {
        "base": base,
        "small": ParagraphStyle("small", parent=base, fontSize=8),
        "right": ParagraphStyle("right", parent=base, alignment=TA_RIGHT),
        "bold_right": ParagraphStyle("bold_right", parent=base, fontName=_BOLD, alignment=TA_RIGHT),
        "center": ParagraphStyle("center", parent=base, alignment=TA_CENTER),
        "title": ParagraphStyle("title", parent=base, fontName=_BOLD, fontSize=15, leading=19),
        "party": ParagraphStyle("party", parent=base, fontName=_BOLD, fontSize=12, leading=15),
        "section": ParagraphStyle("section", parent=base, fontName=_BOLD, fontSize=11, leading=14),
        "warn": ParagraphStyle("warn", parent=base, fontName=_BOLD, fontSize=12, textColor=colors.red, alignment=TA_CENTER),
    }


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(html.escape(text).replace("\n", "<br/>"), style)


def _section(title: str, s: dict[str, ParagraphStyle], width: float) -> Table:
    t = Table([["", _p(title, s["section"])]], colWidths=[4 * mm, width - 4 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), _BAR),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (1, 0), (1, 0), 6),
    ]))
    return t


def render(doc: InvoiceDoc, *, watermark: str | None = None) -> bytes:
    _register_fonts()
    s = _styles()
    buf = BytesIO()
    page = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=12 * mm, bottomMargin=15 * mm,
        title=f"{doc.title} {doc.number}", author=doc.issuer, creator="fakturobot",
    )
    width = page.width
    story: list = []

    if watermark:
        story += [_p(watermark, s["warn"]), Spacer(1, 4 * mm)]
    story += [_p(f"Wystawiono dnia {fmt_date(doc.issue)}, {doc.place}", s["small"]), Spacer(1, 6 * mm)]

    meta = Table(
        [[_p("Data sprzedaży:", s["base"]), _p(fmt_date(doc.sale), s["base"])],
         [_p("Sposób zapłaty:", s["base"]), _p(doc.payment_method, s["base"])],
         [_p("Termin płatności:", s["base"]), _p(fmt_date(doc.due), s["base"])]],
        colWidths=[32 * mm, 45 * mm],
    )
    meta.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0)]))
    header = Table([["", [_p(f"{doc.title} nr {doc.number}", s["title"]), meta]]], colWidths=[width / 2, width / 2])
    story += [header, Spacer(1, 6 * mm)]

    parties = Table(
        [[_p("Sprzedawca:", s["party"]), _p("Nabywca:", s["party"])],
         [_p("\n".join(doc.seller), s["base"]), _p("\n".join(doc.buyer), s["base"])]],
        colWidths=[width / 2, width / 2],
    )
    parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [parties, Spacer(1, 6 * mm), _section("POZYCJE FAKTURY", s, width), Spacer(1, 2 * mm)]

    items = Table(
        [[_p("Lp.", s["base"]), _p("Nazwa towaru lub usługi", s["base"]), _p("PKWiU", s["base"]),
          _p("Ilość", s["right"]), _p("Jedn.", s["base"]), _p("Cena jedn. netto", s["right"]),
          _p("Wartość netto", s["right"]), _p("Stawka VAT", s["right"])],
         [_p("1.", s["base"]), _p(doc.item_name, s["base"]), _p(doc.pkwiu, s["base"]),
          _p(fmt_number(doc.quantity), s["right"]), _p(doc.unit, s["base"]), _p(fmt_money(doc.unit_price), s["right"]),
          _p(fmt_money(doc.net), s["right"]), _p(doc.vat_label, s["right"])]],
        colWidths=[10 * mm, 58 * mm, 22 * mm, 14 * mm, 12 * mm, 22 * mm, 22 * mm, width - 160 * mm],
    )
    items.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _GRAY),
        ("BACKGROUND", (0, 1), (-1, 1), _LIGHT),
        ("FONTNAME", (0, 0), (-1, 0), _BOLD),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.5, _BAR),
    ]))
    story += [items, Spacer(1, 6 * mm), _section("PODSUMOWANIE", s, width), Spacer(1, 2 * mm)]

    label_w = 50 * mm
    col = (width - label_w) / 4
    summary_rows = [
        ["", _p("Stawka VAT", s["bold_right"]), _p("Wartość netto", s["bold_right"]), _p("VAT", s["bold_right"]),
         _p("Wartość brutto", s["bold_right"])],
        ["", _p(doc.vat_label, s["right"]), _p(fmt_money(doc.net), s["right"]), _p(fmt_money(doc.vat), s["right"]),
         _p(fmt_money(doc.gross), s["right"])],
        [_p("Razem:", s["bold_right"]), "", _p(fmt_money(doc.net), s["bold_right"]),
         _p(fmt_money(doc.vat), s["bold_right"]), _p(fmt_money(doc.gross), s["bold_right"])],
        [_p("Zapłacono:", s["bold_right"]), "", "", "", _p("0,00", s["right"])],
        [_p("Pozostało do zapłaty:", s["bold_right"]), "", "", "", _p(fmt_money(doc.gross), s["right"])],
        [_p("Słownie:", s["bold_right"]), _p(amount_words(doc.gross), s["bold_right"]), "", "", ""],
        [_p("Konto bankowe:", s["bold_right"]), _p(doc.bank_account, s["right"]), "", "", ""],
    ]
    if doc.notes:
        summary_rows.append([_p("Uwagi:", s["bold_right"]), _p(doc.notes, s["right"]), "", "", ""])
    summary = Table(summary_rows, colWidths=[label_w, col, col, col, col])
    summary.setStyle(TableStyle([
        ("SPAN", (1, 5), (-1, 5)), ("SPAN", (1, 6), (-1, 6)), ("SPAN", (1, 7), (-1, 7)),
        ("BACKGROUND", (0, 0), (-1, -1), _LIGHT),
        ("BACKGROUND", (0, 2), (-1, 2), colors.white),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.5, _BAR),
    ]))
    story += [summary, Spacer(1, 14 * mm)]

    signatures = Table(
        [[_p("Faktura bez podpisu odbiorcy", s["center"]),
          [_p("Osoba upoważniona do wystawienia faktury", s["center"]),
           Paragraph(f"<b>{html.escape(doc.issuer)}</b>", ParagraphStyle("sig", parent=s["center"], fontName=_BOLD))]]],
        colWidths=[width / 2, width / 2],
    )
    signatures.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(signatures)

    def footer(canvas, _doc) -> None:
        canvas.saveState()
        canvas.setFont(_FONT, 7.5)
        canvas.drawString(15 * mm, 10 * mm, f"{doc.title} nr {doc.number}")
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"strona {canvas.getPageNumber()}")
        canvas.restoreState()

    page.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
