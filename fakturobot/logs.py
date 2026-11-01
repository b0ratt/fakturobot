"""Logowanie na stdout (journald) z maskowaniem sekretów."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable

_FORMAT = "%(levelname)s %(name)s: %(message)s"  # znacznik czasu dodaje journald


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__(_FORMAT)
        self._secrets = sorted({s for s in secrets if len(s) >= 6}, key=len, reverse=True)

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


def setup_logging(secrets: Iterable[str] = ()) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingFormatter(secrets))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    # httpx na poziomie INFO loguje pełne URL-e, a URL Telegrama zawiera token bota
    for noisy in ("httpx", "httpcore", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
