import importlib.util
from os.path import dirname
from threading import Thread
from config import TELEGRAM_API_TOKEN, TELEGRAM_PROXY_URL
from app import app
from telebot import TeleBot, apihelper


bot = None
if TELEGRAM_API_TOKEN:
    if TELEGRAM_PROXY_URL:
        apihelper.proxy = {'http': TELEGRAM_PROXY_URL, 'https': TELEGRAM_PROXY_URL}
    # 4 worker threads: a slow handler (bulk edit, db) must not block every other button press
    bot = TeleBot(TELEGRAM_API_TOKEN, num_threads=4)

handler_names = ["admin", "report", "settings", "user"]

@app.on_event("startup")
def start_bot():
    if bot:
        handler_dir = dirname(__file__) + "/handlers/"
        for name in handler_names:
            spec = importlib.util.spec_from_file_location(name, f"{handler_dir}{name}.py")
            spec.loader.exec_module(importlib.util.module_from_spec(spec))

        from app.telegram import utils # setup custom handlers
        utils.setup()

        # skip_pending: do not replay button presses made while the panel was down;
        # allowed_updates: the bot only uses messages and button callbacks.
        thread = Thread(target=bot.infinity_polling, daemon=True, kwargs=dict(
            skip_pending=True, timeout=30, long_polling_timeout=25,
            allowed_updates=["message", "callback_query"]))
        thread.start()


@app.on_event("shutdown")
def flush_reports():
    if bot:
        from app.telegram.utils.sender import sender
        sender.flush(5)


from .handlers.report import (  # noqa
    has_targets,
    report_usage_percent_reached,
    report_days_left_reached,
    report,
    report_new_user,
    report_user_modification,
    report_user_deletion,
    report_status_change,
    report_user_usage_reset,
    report_user_data_reset_by_next,
    report_user_subscription_revoked,
    report_login
)

__all__ = [
    "bot",
    "has_targets",
    "report_usage_percent_reached",
    "report_days_left_reached",
    "report",
    "report_new_user",
    "report_user_modification",
    "report_user_deletion",
    "report_status_change",
    "report_user_usage_reset",
    "report_user_data_reset_by_next",
    "report_user_subscription_revoked",
    "report_login"
]
