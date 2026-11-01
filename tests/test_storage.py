from decimal import Decimal

import pytest

from fakturobot.months import Month
from fakturobot.storage import APPROVED, ISSUED, PENDING, Storage

M = Month(2026, 11)


def _approved(db: Storage, month: Month) -> None:
    db.create(month)
    db.update(month, expect={PENDING}, hours=Decimal("10"))
    db.update(month, expect={PENDING}, status=APPROVED)


def _renderer(year: int):
    return lambda seq: (f"{seq}/{year}", {"n": seq}, b"%PDF-" + str(seq).encode())


_render = _renderer(2026)


def test_conditional_update_and_nonce(tmp_path):
    db = Storage(tmp_path / "t.db")
    rec = db.create(M)
    assert rec.status == PENDING and rec.hours is None and rec.nonce == 0

    rec = db.update(M, expect={PENDING}, hours=Decimal("152.5"))
    assert rec.hours == Decimal("152.5") and rec.nonce == 1

    assert db.update(M, expect={PENDING}, nonce=1, status=APPROVED) is not None
    # drugie kliknięcie tego samego przycisku: ani status, ani nonce się nie zgadzają
    assert db.update(M, expect={PENDING}, nonce=1, status=APPROVED) is None
    assert db.update(M, expect={APPROVED}, nonce=1, status=PENDING) is None
    assert db.get(M).status == APPROVED


def test_numbering_continues_and_resets_per_year(tmp_path):
    db = Storage(tmp_path / "t.db")
    oct_, nov, jan = Month(2026, 10), Month(2026, 11), Month(2027, 1)
    for m in (oct_, nov, jan):
        _approved(db, m)
    assert db.issue(oct_, year=2026, first=10, render=_render).number == "10/2026"
    assert db.issue(nov, year=2026, first=10, render=_render).number == "11/2026"
    assert db.issue(jan, year=2027, first=1, render=_renderer(2027)).number == "1/2027"
    assert db.pdf(nov) == b"%PDF-11"
    assert db.get(nov).data == {"n": 11}
    assert [r.number for r in db.issued()] == ["1/2027", "11/2026", "10/2026"]
    assert [r.number for r in db.issued(year=2026)] == ["11/2026", "10/2026"]


def test_issue_only_once(tmp_path):
    db = Storage(tmp_path / "t.db")
    _approved(db, M)
    assert db.issue(M, year=2026, first=1, render=_render).status == ISSUED
    assert db.issue(M, year=2026, first=1, render=_render) is None
    assert db.next_seq(2026, 1) == 2


def test_failed_render_rolls_back_and_keeps_number(tmp_path):
    db = Storage(tmp_path / "t.db")
    _approved(db, M)

    def broken(seq):
        raise RuntimeError("brak czcionki")

    with pytest.raises(RuntimeError):
        db.issue(M, year=2026, first=10, render=broken)
    assert db.get(M).status == APPROVED and db.get(M).number is None
    assert db.issue(M, year=2026, first=10, render=_render).number == "10/2026"


def test_delete_requires_nonce_and_status(tmp_path):
    db = Storage(tmp_path / "t.db")
    rec = db.create(M)
    assert not db.delete(M, expect={PENDING}, nonce=rec.nonce + 1)
    assert db.delete(M, expect={PENDING}, nonce=rec.nonce)
    assert db.get(M) is None


def test_latest_pending(tmp_path):
    db = Storage(tmp_path / "t.db")
    db.create(Month(2026, 10))
    db.create(M)
    assert db.latest_pending().month == M
    db.update(Month(2026, 10), expect={PENDING})
    assert db.latest_pending().month == Month(2026, 10)
