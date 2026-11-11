<h1 align="center">Marzkick</h1>

<p align="center">
  Форк <a href="https://github.com/Gozargah/Marzban">Marzban</a> — панели управления прокси на ядре
  <a href="https://github.com/XTLS/Xray-core">Xray</a>: быстрее, с Hysteria 2, настройкой уведомлений в Telegram-боте
  и установкой одной командой.
</p>

<p align="center">
  <b>Русский</b> · <a href="./README.en.md">English</a>
</p>

---

## Установка одной командой

На чистом сервере с **Debian 12 / Ubuntu 22.04+** (systemd, root):

```bash
sudo bash -c "$(curl -sL https://raw.githubusercontent.com/ungerofydev/marzkick/main/install.sh)"
```

Установщик ставит зависимости и Xray (с проверкой контрольной суммы), скачивает код в `/opt/marzban`,
создаёт виртуальное окружение, `.env`, systemd-сервис и администратора, затем запускает панель. Через
минуту в конце вы увидите адрес и логин/пароль. По пути он спросит токен Telegram-бота (можно пропустить).

Всё можно задать заранее переменными окружения, тогда вопросов не будет:

```bash
sudo env PORT=8080 ADMIN_USERNAME=root ADMIN_PASSWORD='секрет' \
  TELEGRAM_API_TOKEN=123456:ABC... TELEGRAM_ADMIN_ID=111222333 \
  bash -c "$(curl -sL https://raw.githubusercontent.com/ungerofydev/marzkick/main/install.sh)"
```

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `PORT` | `8000` | порт панели |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / случайный | первый администратор |
| `TELEGRAM_API_TOKEN` / `TELEGRAM_ADMIN_ID` | — | включить бота (токен проверяется через `getMe`) |
| `CERT_FILE` / `KEY_FILE` | — | HTTPS для самой панели (самоподписанный сертификат определяется автоматически) |
| `XRAY_PORT` | `1080` | порт встроенного Shadowsocks-инбаунда |
| `APP_NAME` | `marzban` | имя установки (каталог `/opt/<имя>`, сервис, команда) — можно ставить несколько копий |
| `REPO` / `BRANCH` | этот репозиторий / `main` | откуда брать код |
| `GITHUB_TOKEN` | — | если репозиторий приватный |
| `UPDATE_XRAY` | — | `1` — заново скачать последний Xray |
| `ASSUME_YES` | — | `1` — не задавать вопросов |

### Доступ к панели

Без HTTPS панель слушает только `127.0.0.1` (так устроен Marzban). Откройте её через SSH-туннель:

```bash
ssh -L 8000:127.0.0.1:8000 root@<ip-сервера>      # затем http://localhost:8000/dashboard/
```

Для постоянного доступа получите сертификат для своего домена (например, `certbot`) и поставьте панель
с `CERT_FILE=/путь/fullchain.pem KEY_FILE=/путь/privkey.pem`, либо впишите их в `.env`
(`UVICORN_SSL_CERTFILE`, `UVICORN_SSL_KEYFILE`) через `marzban edit-env`.

### Обновление и удаление

```bash
marzban update                      # git pull + зависимости + миграции БД + перезапуск
marzban uninstall                   # удалить (данные в /var/lib/marzban спросит отдельно)
```

То же самое можно сделать, запустив установщик снова: `... install.sh)" @ update` или `@ uninstall`.
Повторный запуск на установленной системе **ничего не затирает**: `.env`, база и администраторы
остаются на месте.

## Чем отличается от Marzban

**Hysteria 2.** Нативный inbound Xray (`protocol: hysteria`) как полноценный тип прокси: пользователи,
подписки (ссылка `hysteria2://`, Xray JSON, Clash Meta, sing-box), Host Settings, учёт трафика.
Подробности и пример inbound — в [HYSTERIA2.md](./HYSTERIA2.md).

**Telegram-бот с настройками внутри.** Уведомления больше не настраиваются через `.env`: меню
**⚙️ Settings** (`/settings`) управляет событиями, порогами трафика и срока, белым списком IP для входов,
лог-каналом, Discord и вебхуками, с кнопками проверки. Предупреждения о пороге теперь приходят и в
Telegram. Подробности — [TELEGRAM_BOT.md](./TELEGRAM_BOT.md).

**Скорость** (тестовый VPS 2 vCPU / 2 ГБ, 5000 пользователей):

| | было | стало |
| --- | --- | --- |
| любая команда `marzban cli` | 3,7 с | ~0,2 с |
| `GET /api/users?limit=50` (страница дашборда) | 470 мс | ~40 мс |
| `GET /api/users/usage` | 290 мс | ~11 мс |
| бот: «+ГБ всем» / «+дни всем» | 151 с / 120 с | 0,2 с / 0,2 с |
| бот: удалить истёкших | 11 с | 1,9 с |
| уведомления | блокируют запрос API | очередь с повторами и лимитами |

Это достигнуто быстрым CLI-демоном (запускается сам, см. `cli/README.md`), исправлением N+1 запросов,
SQL-агрегацией и пакетными транзакциями.

**Прочее.** Полный `.env.example` со всеми переменными; команда `marzban` (`up`, `down`, `restart`, `logs`,
`cli`, `backup`, `core-update`, `edit-env`, …) работает для установки из исходников; код проверен на
Python 3.11 (в апстриме часть Telegram-кода запускалась только на 3.12); исправлены ошибки апстрима
(пользователь без явных `inbounds` оставался без ссылок, уведомления об окончании срока уходили при
выключенной опции).

## Управление

```text
marzban up | down | restart | status | logs      запуск, остановка, состояние, журнал
marzban cli <команда>                             администраторы, пользователи, подписки (marzban cli --help)
marzban edit-env                                  редактировать .env      (затем marzban restart)
marzban edit                                      редактировать systemd-юнит
marzban update                                    обновиться до последнего коммита
marzban backup | backup-service                   резервная копия вручную / по расписанию в Telegram
marzban core-update                               выбрать другую версию Xray
```

Каталоги: код — `/opt/marzban`, данные (база, конфиг Xray, сертификаты) — `/var/lib/marzban`.
Конфиг Xray редактируется в дашборде (*Core Settings*) или в `/var/lib/marzban/xray_config.json`.

## Требования и ограничения

- Установщик рассчитан на Debian/Ubuntu с systemd и Python ≥ 3.10; проверялся на **Debian 12 (Python 3.11)**.
  Ubuntu 22.04/24.04 должны работать, но не проверялись. На других системах — ручная установка
  (`git clone`, `python3 -m venv venv`, `pip install -r requirements.txt`, `alembic upgrade head`, `python main.py`).
- Xray не умеет разрывать уже открытую сессию Hysteria 2: отключённый или истёкший пользователь теряет
  доступ при следующем подключении, а не мгновенно.
- Ноды ([marzban-node](https://github.com/Gozargah/Marzban-node)) должны быть на ядре Xray с поддержкой
  Hysteria, если вы используете этот протокол. Работа форка с нодами не проверялась.
- Файлы Docker остались от апстрима и в этом форке не поддерживаются и не тестировались.

## Разработка

```bash
git clone https://github.com/ungerofydev/marzkick.git && cd marzkick
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env && ./venv/bin/alembic upgrade head && ./venv/bin/python main.py
bash build_dashboard.sh        # пересобрать дашборд (нужен Node.js ≥ 18)
```

## Лицензия и авторы

Проект основан на [Marzban](https://github.com/Gozargah/Marzban) (© Gozargah) и распространяется на тех же
условиях — **GNU AGPL-3.0**, см. [LICENSE](./LICENSE). Документация оригинального проекта:
<https://gozargah.github.io/marzban/>.
