"""Runtime settings that are edited from the Telegram bot and stored in the database.

Notification related options used to live in environment variables. They are now kept in the
``app_settings`` table; the environment variables (see ``config.py``) are only used as the
*initial default* so existing installations keep working until the value is changed in the bot.

Usage::

    from app.settings import settings

    if settings.NOTIFY_USER_CREATED: ...
    settings.set("NOTIFY_USER_CREATED", False)

Values are cached in memory and the cache is updated on every ``set``/``reset`` (the panel runs
as a single process).
"""
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import config

logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class Spec:
    kind: str  # bool | int | str | int_list | str_list
    default: Any


def _s(kind: str, default: Any) -> Spec:
    return Spec(kind, default)


SPECS: Dict[str, Spec] = {
    # --- which events are reported -------------------------------------------------
    "NOTIFY_STATUS_CHANGE": _s("bool", config.NOTIFY_STATUS_CHANGE),
    "NOTIFY_USER_CREATED": _s("bool", config.NOTIFY_USER_CREATED),
    "NOTIFY_USER_UPDATED": _s("bool", config.NOTIFY_USER_UPDATED),
    "NOTIFY_USER_DELETED": _s("bool", config.NOTIFY_USER_DELETED),
    "NOTIFY_USER_DATA_USED_RESET": _s("bool", config.NOTIFY_USER_DATA_USED_RESET),
    "NOTIFY_USER_SUB_REVOKED": _s("bool", config.NOTIFY_USER_SUB_REVOKED),
    "NOTIFY_IF_DATA_USAGE_PERCENT_REACHED": _s("bool", config.NOTIFY_IF_DATA_USAGE_PERCENT_REACHED),
    "NOTIFY_IF_DAYS_LEFT_REACHED": _s("bool", config.NOTIFY_IF_DAYS_LEFT_REACHED),
    "NOTIFY_LOGIN": _s("bool", config.NOTIFY_LOGIN),
    # --- thresholds ------------------------------------------------------------------
    "NOTIFY_REACHED_USAGE_PERCENT": _s("int_list", list(config.NOTIFY_REACHED_USAGE_PERCENT)),
    "NOTIFY_DAYS_LEFT": _s("int_list", list(config.NOTIFY_DAYS_LEFT)),
    # --- login alerts ----------------------------------------------------------------
    "LOGIN_NOTIFY_WHITE_LIST": _s("str_list", list(config.LOGIN_NOTIFY_WHITE_LIST)),
    # --- destinations ----------------------------------------------------------------
    "TELEGRAM_LOGGER_CHANNEL_ID": _s("int", config.TELEGRAM_LOGGER_CHANNEL_ID),
    "DISCORD_WEBHOOK_URL": _s("str", config.DISCORD_WEBHOOK_URL or ""),
    "WEBHOOK_ADDRESS": _s("str_list", list(config.WEBHOOK_ADDRESS)),
    "WEBHOOK_SECRET": _s("str", config.WEBHOOK_SECRET or ""),
}

_RETRY_AFTER = 30  # seconds, when the table is not available yet


def coerce(kind: str, value: Any) -> Any:
    """Validate/normalise a value for the given kind. Raises ValueError."""
    if kind == "bool":
        if isinstance(value, bool):
            return value
        raise ValueError("expected true/false")
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("expected an integer")
        return value
    if kind == "str":
        if not isinstance(value, str):
            raise ValueError("expected text")
        return value.strip()
    if kind == "int_list":
        if not isinstance(value, (list, tuple)) or any(isinstance(i, bool) or not isinstance(i, int) for i in value):
            raise ValueError("expected a list of integers")
        return sorted(set(int(i) for i in value))
    if kind == "str_list":
        if not isinstance(value, (list, tuple)) or any(not isinstance(i, str) for i in value):
            raise ValueError("expected a list of strings")
        seen: List[str] = []
        for item in value:
            item = item.strip()
            if item and item not in seen:
                seen.append(item)
        return seen
    raise ValueError(f"unknown kind {kind}")


def _copy(value: Any) -> Any:
    return list(value) if isinstance(value, list) else value


class Settings:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._overrides: Optional[Dict[str, Any]] = None
        self._retry_at = 0.0

    # ------------------------------------------------------------------ storage
    def _fresh(self) -> bool:
        """True while the cached overrides can be used (loaded, or a failed load is still backing off)."""
        return self._overrides is not None and not (self._retry_at and time.monotonic() >= self._retry_at)

    def _load(self) -> Dict[str, Any]:
        if self._fresh():
            return self._overrides
        with self._lock:
            if self._fresh():
                return self._overrides
            values: Dict[str, Any] = {}
            try:
                from app.db import GetDB
                from app.db.models import AppSetting

                with GetDB() as db:
                    for row in db.query(AppSetting).all():
                        spec = SPECS.get(row.key)
                        if not spec:
                            continue
                        try:
                            values[row.key] = coerce(spec.kind, row.value)
                        except ValueError:
                            logger.warning("Ignoring invalid stored setting %s", row.key)
                self._retry_at = 0.0
            except Exception as err:  # e.g. migrations were not applied yet
                logger.warning("Settings table is not available (%s), using defaults", err)
                self._retry_at = time.monotonic() + _RETRY_AFTER
            self._overrides = values
            return values

    # ------------------------------------------------------------------ public API
    def get(self, key: str) -> Any:
        spec = SPECS[key]
        overrides = self._load()
        return _copy(overrides[key]) if key in overrides else _copy(spec.default)

    def is_default(self, key: str) -> bool:
        return key not in self._load()

    def set(self, key: str, value: Any) -> Any:
        spec = SPECS[key]
        value = coerce(spec.kind, value)
        from app.db import GetDB
        from app.db.models import AppSetting

        with self._lock:
            with GetDB() as db:
                db.merge(AppSetting(key=key, value=value))
                db.commit()
            self._load()
            self._overrides[key] = value
            self._retry_at = 0.0
        return _copy(value)

    def reset(self, keys: Optional[Iterable[str]] = None) -> None:
        """Forget stored values so the defaults (environment) apply again."""
        keys = list(SPECS) if keys is None else list(keys)
        from app.db import GetDB
        from app.db.models import AppSetting

        with self._lock:
            with GetDB() as db:
                db.query(AppSetting).filter(AppSetting.key.in_(keys)).delete(synchronize_session=False)
                db.commit()
            self._load()
            for key in keys:
                self._overrides.pop(key, None)

    def invalidate(self) -> None:
        with self._lock:
            self._overrides = None
            self._retry_at = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {key: self.get(key) for key in SPECS}

    def __getattr__(self, name: str) -> Any:
        if name in SPECS:
            return self.get(name)
        raise AttributeError(name)


settings = Settings()
