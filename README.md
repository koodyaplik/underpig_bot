# Underpig Bot

Единый Telegram-бот с двумя функциями:

- распознает голосовые сообщения локально через `faster-whisper`;
- отслеживает авиарейсы через Aviationstack.

Один физический рейс опрашивается один раз, даже если на него подписано несколько пользователей. Изменения доставляются через SQLite outbox и переживают перезапуск контейнера.

Команды отслеживания можно вызывать непосредственно в группе. Подписка привязывается к тому чату, где выполнена `/flight`, и дальнейшие статусы отправляются туда же. Если несколько участников одной группы подпишутся на тот же рейс, группа получит одно уведомление об изменении, а не несколько одинаковых.

## Распознавание голосовых сообщений

Отправьте боту обычное голосовое сообщение. Бот скачает его во временный каталог, распознает локально и вернет текст. Голосовые поддерживаются в личных чатах и группах.

По умолчанию используется модель `small`, CPU и вычисления `int8`. Модель загружается при первом голосовом сообщении и сохраняется в Docker volume `whisper_models`.

Для работы в группах отключите Privacy Mode через `/setprivacy` в BotFather, затем повторно добавьте бота в группу.

Ограничение длительности задается через `MAX_DURATION`, по умолчанию 900 секунд. Голосовую функцию можно отключить через `VOICE_TRANSCRIPTION_ENABLED=false`.

## Как пользователь выбирает дату

Поиск запускается только явной командой:

```text
/flight FV6106
```

После нее бот показывает кнопки:

- «Сегодня»;
- «Завтра»;
- «Послезавтра»;
- «Другая дата» — открывает inline-календарь с перелистыванием месяцев;
- «Не знаю дату» — выполняет один экономный поиск без `flight_date`.

До нажатия кнопки Aviationstack не вызывается. Обычное сообщение `FV6106` не запускает поиск.

Дату можно указать сразу:

```text
/flight FV6106 2026-08-23
```

Если дата более чем через семь дней, Aviationstack `flightsFuture` требует аэропорт отправления:

```text
/flight FV6106 2026-09-15 GOJ
```

Также можно заранее указать аэропорт и затем выбрать дату кнопкой:

```text
/flight FV6106 GOJ
```

## Команды

```text
/start
/help
/flight <номер> [дата] [аэропорт отправления]
/flights
/stop [номер подписки]
/delete_me
```

`/stop` без аргумента показывает кнопки остановки. Остановка по одному номеру рейса не используется, потому что у одного номера могут быть разные даты и маршруты.

## Требования

- Python 3.12 или новее;
- Docker и Docker Compose для штатного запуска;
- Telegram bot token;
- Aviationstack Basic или более высокий тариф;
- один экземпляр приложения и одна SQLite-база.

Для распознавания голоса контейнеру рекомендуется не менее 2,5 ГБ памяти. В Compose уже установлен соответствующий лимит.

Free-тариф Aviationstack не рассчитан на постоянный polling. Приложение имеет локальный hard cap и по умолчанию запрещает overage.

## Запуск через Docker

```bash
cp .env.example .env
```

Заполните как минимум:

```text
TELEGRAM_BOT_TOKEN=
AVIATIONSTACK_API_KEY=
```

Старое имя `BOT_TOKEN` из исходной версии voice-бота также поддерживается. Если Telegram доступен только через SOCKS5-прокси:

```text
TELEGRAM_PROXY=socks5://user:password@host:port
```

Логин и пароль с зарезервированными URL-символами должны быть URL-кодированы.

Затем:

```bash
docker compose build
docker compose up -d
docker compose logs -f
```

SQLite хранится в named volume `flight_data` по пути `/data/flights.db`, модели Whisper — в named volume `whisper_models`.

## Локальный запуск

```bash
python -m venv .venv
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -e ".[dev,voice]"
cp .env.example .env
python -m app.main
```

PowerShell:

```powershell
.venv\Scripts\Activate.ps1
pip install -e ".[dev,voice]"
Copy-Item .env.example .env
python -m app.main
```

## Проверки

```bash
pytest
ruff check .
ruff format --check .
```

Тесты используют mock HTTP и не расходуют Aviationstack quota.

## Важные настройки

```text
AVIATIONSTACK_TIME_MODE=wall_clock
AVIATIONSTACK_MONTHLY_REQUEST_LIMIT=10000
AVIATIONSTACK_REQUEST_RESERVE=500
AVIATIONSTACK_HARD_REQUEST_CAP=10000
AVIATIONSTACK_ALLOW_OVERAGE=false
BOT_DEFAULT_TIMEZONE=Europe/Moscow
WHISPER_MODEL=small
MAX_DURATION=900
TELEGRAM_PROXY=
```

«Сегодня» в календаре вычисляется в `BOT_DEFAULT_TIMEZONE`. После получения рейса время вылета и прилета отображается в timezone соответствующего аэропорта.

## Ограничения первой версии

- подписками через `/flights` и `/stop` управляет создавший их пользователь в том же чате;
- только один процесс приложения;
- источник может отдавать неполные или запаздывающие данные;
- для дальнего будущего рейса нужен IATA-код аэропорта отправления;
- pagination автоматически не обходится, чтобы не расходовать квоту без явного решения пользователя.

Полные требования и критерии приемки находятся в [TECHNICAL_SPEC.md](TECHNICAL_SPEC.md).
