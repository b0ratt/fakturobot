"""Warstwa Telegrama: whitelist chat_id, komendy, przyciski i harmonogram (JobQueue)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Conflict, NetworkError, TimedOut
from telegram.ext import (
    Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler,
    TypeHandler, filters,
)

from .config import Settings
from .engine import ActionResult, Buttons, Engine
from .gmail import GmailClient
from .months import WARSAW
from .storage import Storage

log = logging.getLogger(__name__)

HELP = (
    "🤖 <b>fakturobot</b>\n\n"
    "Przed końcem miesiąca pytam o liczbę godzin, liczę kwotę i po Twoim zatwierdzeniu "
    "wystawiam fakturę (PDF) ostatniego dnia miesiąca. Wszystkie faktury trzymam w archiwum.\n\n"
    "/faktura: zacznij teraz (bieżący albo zaległy miesiąc)\n"
    "/faktura RRRR-MM: konkretny miesiąc\n"
    "/faktury: wystawione faktury i ich PDF-y\n"
    "/eksport [RRRR]: zestawienie faktur z roku (CSV)\n"
    "/status: tryb pracy i następny numer\n"
    "/testmail: testowy mail przez Gmail API na Twój adres"
)


def _markup(buttons: Buttons | None) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data)] for label, data in buttons])


class TelegramOutbox:
    def __init__(self, app: Application, chat_id: int) -> None:
        self._app = app
        self._chat_id = chat_id

    async def text(self, text: str, buttons: Buttons | None = None) -> None:
        await self._app.bot.send_message(self._chat_id, text, parse_mode=ParseMode.HTML, reply_markup=_markup(buttons))

    async def document(self, data: bytes, filename: str, caption: str, buttons: Buttons | None = None) -> None:
        await self._app.bot.send_document(
            self._chat_id, document=data, filename=filename, caption=caption,
            parse_mode=ParseMode.HTML, reply_markup=_markup(buttons),
        )


def build_application(settings: Settings, storage: Storage) -> Application:
    app = Application.builder().token(settings.telegram_token).build()
    outbox = TelegramOutbox(app, settings.chat_id)
    gmail = GmailClient(**vars(settings.gmail)) if settings.gmail else None
    engine = Engine(settings, storage, outbox, gmail=gmail)
    owner = settings.chat_id

    async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Każda aktualizacja (wiadomość, komenda, przycisk) spoza czatu właściciela jest ignorowana bez odpowiedzi."""
        chat, user = update.effective_chat, update.effective_user
        if chat is None or user is None or chat.id != owner or user.id != owner:
            log.info("Zignorowano aktualizację spoza dozwolonego czatu")
            raise ApplicationHandlerStop

    async def on_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await outbox.text(HELP)

    async def on_faktura(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await engine.cmd_faktura(context.args[0] if context.args else None)

    async def on_faktury(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await engine.cmd_faktury()

    async def on_eksport(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await engine.cmd_eksport(context.args[0] if context.args else None)

    async def on_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await engine.cmd_status()

    async def on_testmail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await engine.cmd_testmail()

    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message and update.message.text:
            await engine.on_text(update.message.text)

    async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        parts = (query.data or "").split("|")
        result: ActionResult = "stale"
        if len(parts) == 3 and parts[2].isdigit():
            result = await engine.on_action(parts[0], parts[1], int(parts[2]))
        if result == "stale":
            await query.answer("Ten przycisk jest już nieaktualny.")
            return
        await query.answer()
        if result == "done":
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except BadRequest:
                pass  # wiadomość mogła już nie mieć przycisków

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        error = context.error
        if isinstance(error, (NetworkError, TimedOut, Conflict)):
            log.warning("Problem z połączeniem z Telegramem: %s", type(error).__name__)
            return
        log.error("Nieobsłużony wyjątek: %s", type(error).__name__, exc_info=error)
        try:
            await outbox.text(f"❌ Błąd wewnętrzny bota ({type(error).__name__}). Szczegóły: journalctl -u fakturobot")
        except Exception:  # noqa: BLE001 — nie pozwól, by błąd raportowania wywrócił bota
            log.warning("Nie udało się powiadomić o błędzie")

    def job(fn: Callable[[], Awaitable[None]], name: str) -> Callable[[ContextTypes.DEFAULT_TYPE], Awaitable[None]]:
        async def run(context: ContextTypes.DEFAULT_TYPE) -> None:
            try:
                await fn()
            except Exception as e:  # noqa: BLE001
                log.error("Zadanie %s nieudane: %s", name, type(e).__name__, exc_info=e)
                try:
                    await outbox.text(f"❌ Zadanie harmonogramu „{name}” nie powiodło się ({type(e).__name__}).")
                except Exception:  # noqa: BLE001
                    pass
        return run

    async def post_init(application: Application) -> None:
        await application.bot.set_my_commands([
            BotCommand("faktura", "Wystaw fakturę (opcjonalnie RRRR-MM)"),
            BotCommand("faktury", "Wystawione faktury i PDF-y"),
            BotCommand("eksport", "Zestawienie faktur z roku (CSV)"),
            BotCommand("status", "Tryb i następny numer"),
            BotCommand("testmail", "Test wysyłki przez Gmail"),
            BotCommand("help", "Pomoc"),
        ])
        mode = "DRY_RUN" if settings.dry_run else "produkcja"
        log.info("Start fakturobota, tryb: %s, czas Warszawa: %s", mode, datetime.now(WARSAW).isoformat(timespec="minutes"))
        await outbox.text(f"🤖 fakturobot uruchomiony. Tryb: <b>{mode}</b>. /help")

    async def post_shutdown(application: Application) -> None:
        if gmail is not None:
            await gmail.aclose()
        storage.close()

    app.post_init = post_init
    app.post_shutdown = post_shutdown

    app.add_handler(TypeHandler(Update, gate), group=-1)
    app.add_handler(CommandHandler(["start", "help"], on_help))
    app.add_handler(CommandHandler("faktura", on_faktura))
    app.add_handler(CommandHandler("faktury", on_faktury))
    app.add_handler(CommandHandler("eksport", on_eksport))
    app.add_handler(CommandHandler("status", on_status))
    app.add_handler(CommandHandler("testmail", on_testmail))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(on_error)

    sched = settings.schedule
    jq = app.job_queue
    assert jq is not None, "Zainstaluj python-telegram-bot[job-queue]"
    jq.run_daily(job(engine.morning, "poranne"), time=sched.time.replace(tzinfo=WARSAW), name="morning")
    jq.run_daily(job(engine.evening, "wieczorne"), time=sched.evening_time.replace(tzinfo=WARSAW), name="evening")
    jq.run_once(job(engine.catch_up, "po starcie"), when=15, name="catch-up")
    return app


def run(settings: Settings, storage: Storage) -> None:
    app = build_application(settings, storage)
    # bootstrap_retries: chwilowy timeout Telegrama przy starcie nie kończy procesu
    app.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY], bootstrap_retries=5)
