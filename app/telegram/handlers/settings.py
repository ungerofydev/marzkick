"""Bot menu for notification settings (stored in the database, see app/settings.py).

Everything is edited in place with inline keyboards; values that need typing (thresholds, IPs,
URLs, channel id) are asked with a prompt that can be cancelled. Callback data: ``st:<action>``.
"""
import ipaddress
import re
from typing import Callable, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from telebot import types
from telebot.apihelper import ApiTelegramException
from telebot.formatting import escape_html

from app import logger
from app.settings import settings, SPECS
from app.telegram import bot
from app.telegram.utils.custom_filters import cb_query_startswith
from config import TELEGRAM_ADMIN_ID

# (settings key, short label) in the order they are shown
EVENTS: List[Tuple[str, str]] = [
    ("NOTIFY_USER_CREATED", "🆕 User created"),
    ("NOTIFY_USER_UPDATED", "✏️ User updated"),
    ("NOTIFY_USER_DELETED", "🗑 User deleted"),
    ("NOTIFY_STATUS_CHANGE", "🔄 Status changes"),
    ("NOTIFY_USER_DATA_USED_RESET", "♻️ Usage reset"),
    ("NOTIFY_USER_SUB_REVOKED", "🚫 Sub revoked"),
    ("NOTIFY_IF_DATA_USAGE_PERCENT_REACHED", "🪫 Usage threshold"),
    ("NOTIFY_IF_DAYS_LEFT_REACHED", "⏳ Days-left threshold"),
    ("NOTIFY_LOGIN", "🔐 Panel logins"),
]

MAX_THRESHOLDS = 8
BTN = types.InlineKeyboardButton


# ----------------------------------------------------------------------------------- helpers
def _on(value: bool) -> str:
    return "✅" if value else "❌"


def _mask(value: str, head: int = 28, tail: int = 4) -> str:
    if not value:
        return "—"
    return value if len(value) <= head + tail else value[:head] + "…" + value[-tail:]


def _mask_discord(url: str) -> str:
    """Keep the webhook id visible, hide most of the token."""
    if not url:
        return "—"
    return re.sub(r"(/webhooks/\d+/)(.{4}).*(.{2})$", r"\1\2…\3", url)


def _code(value) -> str:
    return f"<code>{escape_html(str(value))}</code>"


def _fmt_list(values, suffix: str = "") -> str:
    return ", ".join(f"{v}{suffix}" for v in values) if values else "off"


def _kb(*rows: List[types.InlineKeyboardButton]) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    for row in rows:
        kb.row(*row)
    return kb


def _back(to: str = "st:home") -> List[types.InlineKeyboardButton]:
    return [BTN("🔙 Back", callback_data=to)]


def _targets_line() -> str:
    channel = settings.TELEGRAM_LOGGER_CHANNEL_ID
    if channel:
        return f"channel {_code(channel)}"
    return f"admin chats ({len(TELEGRAM_ADMIN_ID)})" if TELEGRAM_ADMIN_ID else "nobody (set TELEGRAM_ADMIN_ID)"


# ----------------------------------------------------------------------------------- screens
def screen_home():
    events_on = sum(1 for key, _ in EVENTS if settings.get(key))
    hooks = settings.WEBHOOK_ADDRESS
    text = (
        "⚙️ <b>Settings</b>\n"
        "➖➖➖➖➖➖➖➖➖\n"
        f"🔔 <b>Events:</b> {events_on}/{len(EVENTS)} on\n"
        f"📊 <b>Usage alerts at:</b> {_code(_fmt_list(settings.NOTIFY_REACHED_USAGE_PERCENT, '%'))}\n"
        f"⏳ <b>Expiry alerts at:</b> {_code(_fmt_list(settings.NOTIFY_DAYS_LEFT, ' d'))}\n"
        f"🛡 <b>Login whitelist:</b> {len(settings.LOGIN_NOTIFY_WHITE_LIST)} address(es)\n"
        f"📨 <b>Telegram reports go to:</b> {_targets_line()}\n"
        f"👾 <b>Discord:</b> {'on' if settings.DISCORD_WEBHOOK_URL else 'off'}\n"
        f"🌐 <b>Webhooks:</b> {len(hooks)} endpoint(s)"
    )
    kb = _kb(
        [BTN("🔔 Events", callback_data="st:events"), BTN("📊 Thresholds", callback_data="st:thr")],
        [BTN("🛡 Login whitelist", callback_data="st:wl"), BTN("📨 Destinations", callback_data="st:dst")],
        [BTN("♻️ Reset all", callback_data="st:reset")],
        [BTN("🔙 Back", callback_data="cancel")],
    )
    return text, kb


def screen_events():
    text = (
        "🔔 <b>Events</b>\n"
        "Choose what is reported (Telegram, Discord and webhooks). Tap to toggle.\n\n"
        "ℹ️ <i>Threshold events fire when a user passes a value from “Thresholds”.</i>"
    )
    buttons = [BTN(f"{_on(settings.get(key))} {label}", callback_data=f"st:t:{i}") for i, (key, label) in enumerate(EVENTS)]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([BTN("✅ All on", callback_data="st:all:1"), BTN("❌ All off", callback_data="st:all:0")])
    rows.append(_back())
    return text, _kb(*rows)


def screen_thresholds():
    usage_on = settings.NOTIFY_IF_DATA_USAGE_PERCENT_REACHED
    days_on = settings.NOTIFY_IF_DAYS_LEFT_REACHED
    text = (
        "📊 <b>Thresholds</b>\n"
        "➖➖➖➖➖➖➖➖➖\n"
        f"🪫 <b>Data usage:</b> {_code(_fmt_list(settings.NOTIFY_REACHED_USAGE_PERCENT, '%'))}  {_on(usage_on)}\n"
        f"⏳ <b>Days left:</b> {_code(_fmt_list(settings.NOTIFY_DAYS_LEFT, ' d'))}  {_on(days_on)}\n\n"
        "A user is reported once for every value they pass. The ✅/❌ shows whether the event is switched "
        "on in “Events”."
    )
    kb = _kb(
        [BTN("✏️ Usage %", callback_data="st:set:usage"), BTN("✏️ Days left", callback_data="st:set:days")],
        _back(),
    )
    return text, kb


def screen_whitelist():
    ips = settings.LOGIN_NOTIFY_WHITE_LIST
    text = (
        "🛡 <b>Login whitelist</b>\n"
        "Panel logins from these addresses are not reported.\n\n"
        + ("\n".join(f"• {_code(ip)}" for ip in ips) if ips else "<i>empty — every login is reported</i>")
    )
    rows = [[BTN("➕ Add address", callback_data="st:set:ip")]]
    for i, ip in enumerate(ips[:20]):
        rows.append([BTN(f"🗑 {ip}", callback_data=f"st:wld:{i}")])
    if ips:
        rows.append([BTN("🧹 Clear all", callback_data="st:wlc")])
    rows.append(_back())
    return text, _kb(*rows)


def screen_destinations():
    text = (
        "📨 <b>Destinations</b>\n"
        "➖➖➖➖➖➖➖➖➖\n"
        f"📣 <b>Telegram:</b> {_targets_line()}\n"
        f"👾 <b>Discord:</b> {_code(_mask_discord(settings.DISCORD_WEBHOOK_URL))}\n"
        f"🌐 <b>Webhooks:</b> {len(settings.WEBHOOK_ADDRESS)} endpoint(s)"
    )
    kb = _kb(
        [BTN("📣 Log channel", callback_data="st:ch")],
        [BTN("👾 Discord", callback_data="st:dc"), BTN("🌐 Webhooks", callback_data="st:wh")],
        _back(),
    )
    return text, kb


def screen_channel():
    channel = settings.TELEGRAM_LOGGER_CHANNEL_ID
    text = (
        "📣 <b>Telegram log channel</b>\n"
        "With a channel (or group) set, reports are posted there; otherwise they are sent to every "
        "bot admin in private.\n\n"
        f"<b>Current:</b> {_code(channel) if channel else '<i>not set — admin chats</i>'}\n\n"
        "ℹ️ <i>Add the bot to the channel as an administrator, then send its numeric id "
        "(starts with -100).</i>"
    )
    rows = [[BTN("✏️ Set channel", callback_data="st:set:chan")]]
    if channel:
        rows[0].append(BTN("🧹 Clear", callback_data="st:clr:chan"))
    rows.append([BTN("📨 Send test", callback_data="st:test:chan")])
    rows.append(_back("st:dst"))
    return text, _kb(*rows)


def screen_discord():
    url = settings.DISCORD_WEBHOOK_URL
    text = (
        "👾 <b>Discord webhook</b>\n"
        f"<b>Current:</b> {_code(_mask_discord(url)) if url else '<i>not set</i>'}"
    )
    rows = [[BTN("✏️ Set URL", callback_data="st:set:discord")]]
    if url:
        rows[0].append(BTN("🧹 Clear", callback_data="st:clr:discord"))
        rows.append([BTN("📨 Send test", callback_data="st:test:discord")])
    rows.append(_back("st:dst"))
    return text, _kb(*rows)


def screen_webhooks():
    hooks = settings.WEBHOOK_ADDRESS
    secret = settings.WEBHOOK_SECRET
    text = (
        "🌐 <b>Webhooks</b>\n"
        "Notifications are POSTed as JSON to every address (with retries).\n\n"
        + ("\n".join(f"• {_code(_mask(h, 40, 6))}" for h in hooks) if hooks else "<i>no endpoints</i>")
        + f"\n\n🔑 <b>Secret</b> (sent as <code>x-webhook-secret</code>): "
        + (_code("•" * 6 + secret[-2:]) if secret else "<i>not set</i>")
    )
    rows = [[BTN("➕ Add URL", callback_data="st:set:hook"), BTN("🔑 Set secret", callback_data="st:set:secret")]]
    for i, h in enumerate(hooks[:10]):
        rows.append([BTN(f"🗑 {h[:40]}", callback_data=f"st:hkd:{i}")])
    extra = []
    if secret:
        extra.append(BTN("🧹 Clear secret", callback_data="st:clr:secret"))
    if hooks:
        extra.append(BTN("📨 Send test", callback_data="st:test:hook"))
    if extra:
        rows.append(extra)
    rows.append(_back("st:dst"))
    return text, _kb(*rows)


def screen_reset():
    text = "⚠️ <b>Reset all notification settings to defaults?</b>\nValues set in the bot are forgotten."
    return text, _kb([BTN("Yes", callback_data="st:reset:yes"), BTN("No", callback_data="st:home")])


SCREENS = {
    "home": screen_home,
    "events": screen_events,
    "thr": screen_thresholds,
    "wl": screen_whitelist,
    "dst": screen_destinations,
    "ch": screen_channel,
    "dc": screen_discord,
    "wh": screen_webhooks,
    "reset": screen_reset,
}


def render(chat_id: int, message_id: int, screen: str, note: Optional[str] = None) -> None:
    text, kb = SCREENS[screen]()
    if note:
        text = f"{note}\n\n{text}"
    try:
        bot.edit_message_text(text, chat_id, message_id, parse_mode="HTML", reply_markup=kb,
                              disable_web_page_preview=True)
    except ApiTelegramException as err:
        if "message is not modified" not in str(err):
            raise


# ----------------------------------------------------------------------------------- input parsing
class InputError(ValueError):
    pass


def parse_int_list(text: str, low: int, high: int, what: str) -> List[int]:
    text = text.strip().lower()
    if text in ("off", "none", "-", "0", ""):
        return []
    parts = [p for p in re.split(r"[\s,;]+", text) if p]
    try:
        values = sorted({int(p.rstrip("%d")) for p in parts})
    except ValueError:
        raise InputError("Send whole numbers separated by commas, e.g. 80, 90")
    if len(values) > MAX_THRESHOLDS:
        raise InputError(f"At most {MAX_THRESHOLDS} values.")
    if any(v < low or v > high for v in values):
        raise InputError(f"{what} must be between {low} and {high}.")
    return values


def parse_ip(text: str) -> str:
    try:
        return str(ipaddress.ip_address(text.strip()))
    except ValueError:
        raise InputError("That is not a valid IPv4/IPv6 address.")


def parse_channel(text: str) -> int:
    text = text.strip().lower()
    if text in ("0", "off", "none", "-"):
        return 0
    if not re.fullmatch(r"-?\d{5,20}", text):
        raise InputError("Send the numeric channel id, e.g. -1001234567890 (or 0 to clear).")
    return int(text)


def parse_discord(text: str) -> str:
    text = text.strip()
    if not re.match(r"^https://(?:[\w-]+\.)?discord(?:app)?\.com/api/webhooks/\S+$", text):
        raise InputError("Send a Discord webhook URL (https://discord.com/api/webhooks/…).")
    return text


def parse_webhook(text: str) -> str:
    text = text.strip()
    url = urlparse(text)
    if url.scheme not in ("http", "https") or not url.netloc or " " in text:
        raise InputError("Send a full http(s) URL, e.g. https://example.com/hook")
    return text


def parse_secret(text: str) -> str:
    text = text.strip()
    if not text or len(text) > 128 or re.search(r"\s", text):
        raise InputError("Secret must be 1–128 characters without spaces.")
    return text


# prompt kinds: key -> (screen to return to, question, secret input?, apply(text) -> note)
def _apply_usage(text):
    settings.set("NOTIFY_REACHED_USAGE_PERCENT", parse_int_list(text, 1, 100, "Percent"))
    return "✅ Usage thresholds saved."


def _apply_days(text):
    settings.set("NOTIFY_DAYS_LEFT", parse_int_list(text, 1, 365, "Days"))
    return "✅ Days-left thresholds saved."


def _apply_ip(text):
    ip = parse_ip(text)
    settings.set("LOGIN_NOTIFY_WHITE_LIST", settings.LOGIN_NOTIFY_WHITE_LIST + [ip])
    return f"✅ {escape_html(ip)} added."


def _apply_chan(text):
    settings.set("TELEGRAM_LOGGER_CHANNEL_ID", parse_channel(text))
    return "✅ Log channel saved. Use “Send test” to verify the bot can post there."


def _apply_discord(text):
    settings.set("DISCORD_WEBHOOK_URL", parse_discord(text))
    return "✅ Discord webhook saved."


def _apply_hook(text):
    settings.set("WEBHOOK_ADDRESS", settings.WEBHOOK_ADDRESS + [parse_webhook(text)])
    return "✅ Webhook added."


def _apply_secret(text):
    settings.set("WEBHOOK_SECRET", parse_secret(text))
    return "✅ Secret saved."


PROMPTS = {
    "usage": ("thr", "Send the data-usage percentages that trigger a warning, separated by commas.\n"
                     "Example: <code>80, 90, 100</code> — or <code>off</code>.", False, _apply_usage),
    "days": ("thr", "Send the number of days left that trigger a warning, separated by commas.\n"
                    "Example: <code>3, 7</code> — or <code>off</code>.", False, _apply_days),
    "ip": ("wl", "Send an IP address to add to the whitelist.\nExample: <code>203.0.113.7</code>", False, _apply_ip),
    "chan": ("ch", "Send the numeric id of the channel/group, e.g. <code>-1001234567890</code>.\n"
                   "Send <code>0</code> to clear.", False, _apply_chan),
    "discord": ("dc", "Send the Discord webhook URL.\n<i>Your message is deleted right away.</i>", True, _apply_discord),
    "hook": ("wh", "Send the webhook URL (http/https) to add.", False, _apply_hook),
    "secret": ("wh", "Send the secret to put in the <code>x-webhook-secret</code> header.\n"
                     "<i>Your message is deleted right away.</i>", True, _apply_secret),
}


def _ask(chat_id: int, message_id: int, kind: str) -> None:
    back, question, secret, _ = PROMPTS[kind]
    kb = _kb([BTN("❌ Cancel", callback_data=f"st:{back}")])
    bot.edit_message_text(f"✏️ {question}", chat_id, message_id, parse_mode="HTML", reply_markup=kb)
    bot.register_next_step_handler_by_chat_id(chat_id, _answer, message_id, kind)


def _answer(message: types.Message, prompt_id: int, kind: str) -> None:
    chat_id = message.chat.id
    back, _question, _secret, apply = PROMPTS[kind]
    text = message.text or ""
    try:  # never leave typed values (urls, ids, secrets) lying in the chat
        bot.delete_message(chat_id, message.message_id)
    except ApiTelegramException:
        pass
    try:
        note = apply(text)
    except InputError as err:
        render_prompt_error(chat_id, prompt_id, kind, str(err))
        return
    render(chat_id, prompt_id, back, note=note)


def render_prompt_error(chat_id: int, prompt_id: int, kind: str, error: str) -> None:
    back, question, _secret, _ = PROMPTS[kind]
    kb = _kb([BTN("❌ Cancel", callback_data=f"st:{back}")])
    bot.edit_message_text(f"❌ {escape_html(error)}\n\n✏️ {question}", chat_id, prompt_id, parse_mode="HTML",
                          reply_markup=kb)
    bot.register_next_step_handler_by_chat_id(chat_id, _answer, prompt_id, kind)


# ----------------------------------------------------------------------------------- tests
def test_channel() -> str:
    channel = settings.TELEGRAM_LOGGER_CHANNEL_ID
    if not channel:
        return "ℹ️ No channel is set — reports go to the admin chats."
    try:
        bot.send_message(channel, "✅ <b>Marzban</b>: test message", parse_mode="HTML")
        return "✅ Test message sent."
    except ApiTelegramException as err:
        return f"❌ Telegram refused: {escape_html(str(err.description))}"


def test_discord() -> str:
    try:
        r = requests.post(settings.DISCORD_WEBHOOK_URL, json={"content": "✅ Marzban: test message"}, timeout=10)
        return "✅ Test message sent." if r.ok else f"❌ Discord answered HTTP {r.status_code}."
    except requests.RequestException as err:
        return f"❌ Request failed: {escape_html(type(err).__name__)}"


def test_hooks() -> str:
    secret = settings.WEBHOOK_SECRET
    headers = {"x-webhook-secret": secret} if secret else None
    lines = []
    for url in settings.WEBHOOK_ADDRESS:
        try:
            r = requests.post(url, json=[{"action": "test", "message": "Marzban webhook test"}],
                              headers=headers, timeout=10)
            lines.append(f"{'✅' if r.ok else '❌'} {escape_html(_mask(url, 30, 6))} → HTTP {r.status_code}")
        except requests.RequestException as err:
            lines.append(f"❌ {escape_html(_mask(url, 30, 6))} → {escape_html(type(err).__name__)}")
    return "\n".join(lines) or "ℹ️ No webhooks configured."


# ----------------------------------------------------------------------------------- handlers
@bot.message_handler(commands=["settings"], is_admin=True)
def settings_command(message: types.Message):
    bot.clear_step_handler_by_chat_id(message.chat.id)
    text, kb = screen_home()
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=kb)


@bot.callback_query_handler(lambda q: q.data == "settings", is_admin=True)
def open_settings(call: types.CallbackQuery):
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    bot.answer_callback_query(call.id)
    render(call.message.chat.id, call.message.message_id, "home")


@bot.callback_query_handler(cb_query_startswith("st:"), is_admin=True)
def settings_callback(call: types.CallbackQuery):
    chat_id, mid = call.message.chat.id, call.message.message_id
    parts = call.data.split(":")
    action = parts[1]
    arg = parts[2] if len(parts) > 2 else None
    bot.clear_step_handler_by_chat_id(chat_id)  # a pending prompt is abandoned by any button
    note = None
    toast = None

    try:
        if action in SCREENS and arg is None:
            screen = action
        elif action == "t" and arg is not None:          # toggle one event
            key = EVENTS[int(arg)][0]
            settings.set(key, not settings.get(key))
            screen = "events"
        elif action == "all":                            # all events on/off
            for key, _ in EVENTS:
                settings.set(key, arg == "1")
            screen = "events"
        elif action == "set" and arg in PROMPTS:
            bot.answer_callback_query(call.id)
            return _ask(chat_id, mid, arg)
        elif action == "clr":
            key = {"chan": "TELEGRAM_LOGGER_CHANNEL_ID", "discord": "DISCORD_WEBHOOK_URL", "secret": "WEBHOOK_SECRET"}[arg]
            settings.set(key, 0 if arg == "chan" else "")
            screen = {"chan": "ch", "discord": "dc", "secret": "wh"}[arg]
            note = "🧹 Cleared."
        elif action == "wld":                            # remove one whitelisted address
            ips = settings.LOGIN_NOTIFY_WHITE_LIST
            if int(arg) < len(ips):
                removed = ips.pop(int(arg))
                settings.set("LOGIN_NOTIFY_WHITE_LIST", ips)
                note = f"🗑 {escape_html(removed)} removed."
            screen = "wl"
        elif action == "wlc":
            settings.set("LOGIN_NOTIFY_WHITE_LIST", [])
            screen, note = "wl", "🧹 Whitelist cleared."
        elif action == "hkd":                            # remove one webhook
            hooks = settings.WEBHOOK_ADDRESS
            if int(arg) < len(hooks):
                hooks.pop(int(arg))
                settings.set("WEBHOOK_ADDRESS", hooks)
                note = "🗑 Webhook removed."
            screen = "wh"
        elif action == "test":
            bot.answer_callback_query(call.id, "Sending…")
            result = {"chan": test_channel, "discord": test_discord, "hook": test_hooks}[arg]()
            return render(chat_id, mid, {"chan": "ch", "discord": "dc", "hook": "wh"}[arg], note=result)
        elif action == "reset" and arg == "yes":
            settings.reset()
            screen, note = "home", "♻️ All notification settings were reset to defaults."
        else:
            bot.answer_callback_query(call.id)
            return
    except (ValueError, IndexError, KeyError) as err:
        logger.warning("settings callback %r failed: %s", call.data, err)
        bot.answer_callback_query(call.id, "Invalid action")
        return

    bot.answer_callback_query(call.id, toast)
    render(chat_id, mid, screen, note=note)
