"""Background sender for Telegram reports.

Reports used to be sent synchronously from inside API requests (creating a user waited for
Telegram, one request per admin, and a Telegram outage made the API slow). Now ``submit`` only
puts the message into a queue; a single daemon worker delivers it, keeps a small gap between
messages to the same chat, honours ``429 retry_after`` and retries transient failures.
"""
import logging
import queue
import threading
import time
from typing import Any, Dict, Optional

import requests
from telebot.apihelper import ApiTelegramException

logger = logging.getLogger("uvicorn.error")

PER_CHAT_INTERVAL = 0.35  # seconds between two messages to the same chat
MAX_TRIES = 4
MAX_QUEUE = 2000


class Sender:
    def __init__(self) -> None:
        self._q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=MAX_QUEUE)
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_sent: Dict[Any, float] = {}
        self.sleep = time.sleep  # replaced in tests

    # ------------------------------------------------------------------ public
    def submit(self, chat_id, text: str, parse_mode: Optional[str] = "html", reply_markup=None) -> bool:
        """Queue a message. Never blocks; returns False if it had to be dropped."""
        self._ensure_worker()
        try:
            self._q.put_nowait({"chat_id": chat_id, "text": text, "parse_mode": parse_mode, "reply_markup": reply_markup})
            return True
        except queue.Full:
            logger.error("Telegram report queue is full, dropping a message")
            return False

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until everything queued so far was handled (used on shutdown)."""
        deadline = time.monotonic() + timeout
        while self._q.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.05)
        return not self._q.unfinished_tasks

    # ------------------------------------------------------------------ worker
    def _ensure_worker(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="telegram-sender", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._q.get()
            try:
                self._deliver(item)
            except Exception:  # never let the worker die
                logger.exception("Telegram sender failed")
            finally:
                self._q.task_done()

    def _wait_turn(self, chat_id) -> None:
        gap = PER_CHAT_INTERVAL - (time.monotonic() - self._last_sent.get(chat_id, 0.0))
        if gap > 0:
            self.sleep(gap)

    def _deliver(self, item: Dict[str, Any]) -> None:
        from app.telegram import bot  # late import: this module is imported by the handlers

        chat_id = item["chat_id"]
        for attempt in range(MAX_TRIES):
            try:
                self._wait_turn(chat_id)
                self._last_sent[chat_id] = time.monotonic()
                bot.send_message(chat_id, item["text"], parse_mode=item["parse_mode"],
                                 reply_markup=item["reply_markup"])
                return
            except ApiTelegramException as err:
                code = getattr(err, "error_code", None)
                if code == 429:
                    params = (getattr(err, "result_json", None) or {}).get("parameters") or {}
                    self.sleep(min(float(params.get("retry_after", 1)), 60.0) + 0.1)
                    continue
                if code and code >= 500:
                    self.sleep(2 ** attempt)
                    continue
                # 400/401/403/404...: wrong chat id, bot kicked, blocked by user -> retrying is pointless
                logger.error("Telegram report to %s failed: %s", chat_id, getattr(err, "description", err))
                return
            except requests.RequestException as err:  # network hiccup
                logger.warning("Telegram unreachable (%s), retry %d/%d", type(err).__name__, attempt + 1, MAX_TRIES)
                self.sleep(2 ** attempt)
        logger.error("Giving up on a Telegram report to %s after %d tries", chat_id, MAX_TRIES)


sender = Sender()
