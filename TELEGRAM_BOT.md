# Telegram bot

Set `TELEGRAM_API_TOKEN` (from @BotFather) and `TELEGRAM_ADMIN_ID` (numeric ids, comma separated) in
`.env`, then `marzban restart`. Send `/start` to the bot.

## Settings menu (⚙️ Settings, or `/settings`)

Notification options are **not** environment variables any more. They are edited in the bot and stored in
the database (`app_settings` table, see `app/settings.py`), so they apply immediately, no restart:

| Screen | What you can change |
| --- | --- |
| 🔔 Events | Toggle each event: user created / updated / deleted, status changes, usage reset, subscription revoked, usage threshold, days-left threshold, panel logins |
| 📊 Thresholds | Data-usage percentages and days-left values that trigger a warning (e.g. `80, 90` / `3, 7`) |
| 🛡 Login whitelist | IP addresses whose panel logins are not reported |
| 📨 Destinations | Telegram log channel/group, Discord webhook, webhook URLs + secret, each with a **Send test** button |
| ♻️ Reset all | Forget stored values and fall back to the defaults |

Values you type (URLs, channel id, secret) are deleted from the chat right after they are read, and
secrets are only shown masked.

Usage / days-left warnings are delivered to Telegram (log channel, or every bot admin) as well as to
webhooks; each threshold is reported once per user.

### Upgrading from environment variables

`NOTIFY_*`, `LOGIN_NOTIFY_WHITE_LIST`, `TELEGRAM_LOGGER_CHANNEL_ID`, `DISCORD_WEBHOOK_URL`,
`WEBHOOK_ADDRESS` and `WEBHOOK_SECRET` are no longer documented in `.env.example`. If they are still set,
they are used as the initial defaults until the matching option is changed in the bot. Webhook retry
tuning (`NUMBER_OF_RECURRENT_NOTIFICATIONS`, `RECURRENT_NOTIFICATIONS_TIMEOUT`) stays in `.env`.

## Performance notes

* Reports are queued and sent by a background worker with rate limiting and retries (`429`, 5xx, network
  errors); API requests never wait for Telegram or Discord.
* Bulk actions in *Edit All Users* run as a single database transaction (data/expiry changes on 5000
  users: minutes → well under a second).
* The bot uses 4 handler threads and only subscribes to `message` and `callback_query` updates; updates
  that arrived while the panel was down are skipped instead of replayed.
