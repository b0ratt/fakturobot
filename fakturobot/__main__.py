"""Uruchomienie: python -m fakturobot (zmienne z .env podaje systemd)."""

from __future__ import annotations

import logging
import sys

from .config import ConfigError, load_settings
from .logs import setup_logging
from .storage import Storage

log = logging.getLogger("fakturobot")

# Kod wyjścia 2 = błąd konfiguracji; usługa systemd go nie restartuje (RestartPreventExitStatus=2).
EXIT_CONFIG = 2


def main() -> int:
    setup_logging()
    try:
        settings = load_settings()
    except ConfigError as e:
        log.error("Błąd konfiguracji: %s", e)
        return EXIT_CONFIG
    setup_logging(settings.secrets())

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # Osobna baza dla DRY_RUN, żeby testowe „wystawienia” nie blokowały prawdziwych.
    db_name = "fakturobot-dryrun.db" if settings.dry_run else "fakturobot.db"
    storage = Storage(settings.data_dir / db_name)

    from .bot import run  # import tutaj: błąd konfiguracji nie wymaga ładowania Telegrama

    run(settings, storage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
