# Underpig Bot

Telegram-бот с двумя функциями:

- локально распознает голосовые сообщения через `faster-whisper`;
- отслеживает авиарейсы через FlightAware AeroAPI.

Один физический рейс опрашивается один раз независимо от количества подписчиков. Изменения сначала сохраняются в SQLite outbox, поэтому уведомления переживают перезапуск контейнера.

Команды можно вызывать в личном чате, группе или супергруппе. Подписка привязывается к чату, где выполнена `/flight`, и статусы отправляются туда же. Несколько участников одной группы не получают дубли одного события.

## Добавление рейса

Поиск запускается только явной командой:

```text
/flight FV6106
```

Бот покажет кнопки «Сегодня», «Завтра», «Послезавтра» и календарь. Доступны даты от сегодня до 363 дней вперед. Прошлые даты отключены, потому что бот предназначен для последующего отслеживания, а не для просмотра истории.

Дату можно указать сразу:

```text
/flight FV6106 2026-08-24
```

Обычное сообщение `FV6106` не вызывает AeroAPI и не создает подписку.

## Как используется FlightAware AeroAPI

Для текущих и ближайших рейсов бот вызывает:

```text
GET https://aeroapi.flightaware.com/aeroapi/flights/FV6106
    ?ident_type=designator
    &max_pages=1
```

Для дат дальше двух дней используется опубликованное расписание:

```text
GET /schedules/{date_start}/{date_end}
```

Часовые пояса аэропортов запрашиваются через `GET /airports/{id}` и кэшируются в SQLite. После получения рейса бот сохраняет `fa_flight_id` и использует его для повторного сопоставления, чтобы не переключиться на другой рейс с тем же номером.

Ключ передается только в заголовке `x-apikey`. INFO-лог содержит метод и полный URL запроса, но не содержит ключ, поскольку в AeroAPI он не является частью URL.

Доступ к `/flights`, `/schedules` и `/airports/{id}` зависит от подписки FlightAware. Актуальный контракт: [AeroAPI Developer Portal](https://www.flightaware.com/aeroapi/portal/documentation).

## Команды

```text
/start
/help
/flight <номер> [YYYY-MM-DD]
/flights
/stop [номер подписки]
/delete_me
```

`/stop` без аргумента показывает кнопки остановки. Подписками через `/flights` и `/stop` управляет создавший их пользователь в том же чате.

## Распознавание голосовых сообщений

Отправьте боту голосовое сообщение. Он скачает его во временный каталог, распознает локально и вернет текст. Функция работает в личных чатах и группах.

По умолчанию используется модель `small`, CPU и вычисления `int8`. Модель загружается при первом голосовом сообщении и сохраняется в Docker volume `whisper_models`.

Для работы с обычными сообщениями в группах отключите Privacy Mode через `/setprivacy` в BotFather, затем повторно добавьте бота в группу. Голосовую функцию можно отключить через `VOICE_TRANSCRIPTION_ENABLED=false`.

## Запуск через Docker

Требования: Docker с Compose, Telegram bot token и ключ FlightAware AeroAPI.

```bash
cp .env.example .env
```

Заполните как минимум:

```text
TELEGRAM_BOT_TOKEN=
FLIGHTAWARE_AEROAPI_KEY=
```

Старое имя `BOT_TOKEN` также поддерживается. Для SOCKS5-прокси Telegram:

```text
TELEGRAM_PROXY=socks5://user:password@host:port
```

Запуск:

```bash
docker compose build
docker compose up -d
docker compose logs -f flight-bot
```

SQLite хранится в volume `flight_data` по пути `/data/flights.db`, модели Whisper — в `whisper_models`.

### Обновление существующей установки

После перехода с Aviationstack замените старую переменную ключа в `.env`:

```text
FLIGHTAWARE_AEROAPI_KEY=ваш_ключ
```

Затем пересоберите контейнер:

```bash
git pull --ff-only origin main
docker compose up -d --build flight-bot
docker compose logs -f --tail=200 flight-bot
```

Существующая SQLite-база и групповые подписки сохраняются. Старые активные рейсы, где еще нет `fa_flight_id`, сначала ищутся по номеру и после успешного ответа закрепляются за идентификатором FlightAware.

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

Тесты используют mock HTTP и не расходуют квоту AeroAPI.

## Настройки AeroAPI и локального лимита

```text
FLIGHTAWARE_AEROAPI_BASE_URL=https://aeroapi.flightaware.com/aeroapi
AEROAPI_MONTHLY_REQUEST_LIMIT=10000
AEROAPI_REQUEST_RESERVE=500
AEROAPI_HARD_REQUEST_CAP=10000
AEROAPI_ALLOW_OVERAGE=false
AEROAPI_BILLING_CYCLE_DAY=1
AEROAPI_MAX_CONCURRENCY=5
AEROAPI_EXTENDED_LOGGING=false
BOT_DEFAULT_TIMEZONE=Europe/Moscow
```

Лимит внутри бота считает HTTP-запросы и защищает от неконтролируемого polling. Он не моделирует стоимость разных endpoints FlightAware и не заменяет контроль расходов в кабинете AeroAPI.

Для диагностики можно включить `AEROAPI_EXTENDED_LOGGING=true`. Тогда после каждого запроса в лог записываются HTTP-статус и полный необработанный ответ FlightAware. Ключ `x-apikey` передается только в заголовке запроса и в лог не выводится. Полный ответ может быть большим, поэтому постоянно держать этот режим включенным не рекомендуется.

## Ограничения

- только один процесс приложения работает с одной SQLite-базой;
- источник может отдавать неполные или запаздывающие данные;
- дальние даты требуют доступа тарифа к endpoint расписаний;
- бот является информационным сервисом и не заменяет официальные сообщения авиакомпании или аэропорта.

Полные требования и критерии приемки находятся в [TECHNICAL_SPEC.md](TECHNICAL_SPEC.md).
