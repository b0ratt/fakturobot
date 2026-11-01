"""Stan i archiwum faktur w SQLite.

Na każdy miesiąc jeden rekord: liczba godzin, status, wersja przycisków (nonce),
a po wystawieniu: numer, dane z faktury (JSON) i PDF. Bot jest jedynym miejscem wystawiania
faktur, więc archiwum musi tu być (faktury przechowuje się 5 lat). Kopia idzie na Gmail.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .months import Month

PENDING = "oczekuje"
APPROVED = "zatwierdzona"
ISSUED = "wystawiona"
SENT = "wyslana"
DONE = frozenset({ISSUED, SENT})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    month      TEXT PRIMARY KEY,
    hours      TEXT,
    status     TEXT NOT NULL CHECK (status IN ('oczekuje', 'zatwierdzona', 'wystawiona', 'wyslana')),
    nonce      INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    year       INTEGER,
    seq        INTEGER,
    number     TEXT UNIQUE,
    data       TEXT,
    pdf        BLOB,
    UNIQUE (year, seq)
)
"""
_COLUMNS = {"hours", "status"}


@dataclass(frozen=True)
class Record:
    month: Month
    hours: Decimal | None
    status: str
    nonce: int
    number: str | None
    data: dict[str, Any] | None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class Storage:
    def __init__(self, path: Path | str) -> None:
        self._db = sqlite3.connect(path, isolation_level=None)  # autocommit; transakcje jawnie przez BEGIN
        self._db.row_factory = sqlite3.Row
        self._db.execute(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _record(row: sqlite3.Row | None) -> Record | None:
        if row is None:
            return None
        return Record(
            month=Month.parse(row["month"]),
            hours=Decimal(row["hours"]) if row["hours"] is not None else None,
            status=row["status"],
            nonce=row["nonce"],
            number=row["number"],
            data=json.loads(row["data"]) if row["data"] else None,
        )

    _FIELDS = "month, hours, status, nonce, number, data"

    def get(self, month: Month) -> Record | None:
        return self._record(self._db.execute(f"SELECT {self._FIELDS} FROM invoices WHERE month = ?", (month.key,)).fetchone())

    def create(self, month: Month) -> Record:
        """Tworzy rekord „oczekuje”, jeśli go nie ma. Istniejącego nie zmienia."""
        self._db.execute(
            "INSERT OR IGNORE INTO invoices (month, status, updated_at) VALUES (?, ?, ?)", (month.key, PENDING, _now())
        )
        rec = self.get(month)
        assert rec is not None
        return rec

    def update(self, month: Month, *, expect: Collection[str], nonce: int | None = None, **changes: object) -> Record | None:
        """Atomowa zmiana warunkowa: tylko gdy status ∈ expect (i nonce się zgadza).

        Zawsze podbija nonce, więc wcześniejsze przyciski przestają działać.
        Zwraca None, gdy warunek nie był spełniony (np. drugie kliknięcie „Zatwierdź”).
        """
        unknown = set(changes) - _COLUMNS
        if unknown:
            raise ValueError(f"Nieznane kolumny: {unknown}")
        sets = "".join(f"{col} = ?, " for col in changes)
        params: list[object] = [str(v) if isinstance(v, Decimal) else v for v in changes.values()]
        statuses = list(expect)
        sql = (
            f"UPDATE invoices SET {sets}nonce = nonce + 1, updated_at = ? "
            f"WHERE month = ? AND status IN ({', '.join('?' * len(statuses))})"
        )
        params += [_now(), month.key, *statuses]
        if nonce is not None:
            sql += " AND nonce = ?"
            params.append(nonce)
        if self._db.execute(sql, params).rowcount != 1:
            return None
        return self.get(month)

    def delete(self, month: Month, *, expect: Collection[str], nonce: int) -> bool:
        statuses = list(expect)
        cur = self._db.execute(
            f"DELETE FROM invoices WHERE month = ? AND nonce = ? AND status IN ({', '.join('?' * len(statuses))})",
            (month.key, nonce, *statuses),
        )
        return cur.rowcount == 1

    def next_seq(self, year: int, first: int) -> int:
        """Kolejny numer w roku: po ostatnim wystawionym albo `first`, gdy to pierwsza faktura w tym roku."""
        last = self._db.execute("SELECT MAX(seq) FROM invoices WHERE year = ?", (year,)).fetchone()[0]
        return first if last is None else last + 1

    def issue(self, month: Month, *, year: int, first: int,
              render: Callable[[int], tuple[str, dict[str, Any], bytes]]) -> Record | None:
        """Nadaje numer i zapisuje fakturę w jednej transakcji, tylko ze statusu „zatwierdzona”.

        render(seq) → (numer, dane, pdf). Wyjątek w render() wycofuje wszystko, więc numer nie przepada.
        """
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute("SELECT status FROM invoices WHERE month = ?", (month.key,)).fetchone()
            if row is None or row["status"] != APPROVED:
                self._db.execute("ROLLBACK")
                return None
            seq = self.next_seq(year, first)
            number, data, pdf = render(seq)
            self._db.execute(
                "UPDATE invoices SET status = ?, year = ?, seq = ?, number = ?, data = ?, pdf = ?, "
                "nonce = nonce + 1, updated_at = ? WHERE month = ?",
                (ISSUED, year, seq, number, json.dumps(data, ensure_ascii=False), pdf, _now(), month.key),
            )
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        return self.get(month)

    def pdf(self, month: Month) -> bytes | None:
        row = self._db.execute("SELECT pdf FROM invoices WHERE month = ?", (month.key,)).fetchone()
        return row["pdf"] if row else None

    def latest_pending(self) -> Record | None:
        """Rekord, na który czekamy z godzinami (ostatnio pytany)."""
        row = self._db.execute(
            f"SELECT {self._FIELDS} FROM invoices WHERE status = ? ORDER BY updated_at DESC, month DESC LIMIT 1",
            (PENDING,),
        ).fetchone()
        return self._record(row)

    def recent(self, limit: int = 6) -> list[Record]:
        rows = self._db.execute(f"SELECT {self._FIELDS} FROM invoices ORDER BY month DESC LIMIT ?", (limit,)).fetchall()
        return [r for r in map(self._record, rows) if r is not None]

    def issued(self, *, year: int | None = None, limit: int | None = None) -> list[Record]:
        """Wystawione faktury, od najnowszej."""
        sql = f"SELECT {self._FIELDS} FROM invoices WHERE status IN (?, ?)"
        params: list[object] = [ISSUED, SENT]
        if year is not None:
            sql += " AND year = ?"
            params.append(year)
        sql += " ORDER BY year DESC, seq DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [r for r in map(self._record, self._db.execute(sql, params).fetchall()) if r is not None]
