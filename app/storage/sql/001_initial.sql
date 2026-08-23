CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at_epoch INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS flights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_key TEXT NOT NULL UNIQUE,
    requested_flight_iata TEXT NOT NULL,
    provider_flight_iata TEXT NOT NULL,
    flight_date TEXT NOT NULL,
    identity_departure_iata TEXT NOT NULL,
    identity_arrival_iata TEXT NOT NULL,
    identity_scheduled_departure_local TEXT,
    identity_departure_timezone TEXT,
    airline_name TEXT,
    departure_airport TEXT,
    departure_iata TEXT NOT NULL,
    departure_icao TEXT,
    departure_timezone TEXT,
    arrival_airport TEXT,
    arrival_iata TEXT NOT NULL,
    arrival_icao TEXT,
    arrival_timezone TEXT,
    scheduled_departure_local TEXT,
    scheduled_departure_utc_epoch INTEGER,
    estimated_departure_local TEXT,
    estimated_departure_utc_epoch INTEGER,
    actual_departure_local TEXT,
    actual_departure_utc_epoch INTEGER,
    scheduled_arrival_local TEXT,
    scheduled_arrival_utc_epoch INTEGER,
    estimated_arrival_local TEXT,
    estimated_arrival_utc_epoch INTEGER,
    actual_arrival_local TEXT,
    actual_arrival_utc_epoch INTEGER,
    departure_delay_minutes INTEGER,
    arrival_delay_minutes INTEGER,
    departure_terminal TEXT,
    departure_gate TEXT,
    arrival_terminal TEXT,
    arrival_gate TEXT,
    arrival_baggage TEXT,
    api_status TEXT,
    aircraft_registration TEXT,
    codeshare_json TEXT,
    normalized_state_json TEXT NOT NULL,
    normalized_schema_version INTEGER NOT NULL DEFAULT 1,
    latest_candidate_json TEXT NOT NULL,
    last_raw_flight_json TEXT NOT NULL,
    state_version INTEGER NOT NULL DEFAULT 1,
    last_checked_at_epoch INTEGER,
    last_success_at_epoch INTEGER,
    next_check_at_epoch INTEGER NOT NULL,
    polling_priority INTEGER NOT NULL DEFAULT 50,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    consecutive_not_found INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_until_epoch INTEGER,
    tracking_state TEXT NOT NULL,
    suspended_previous_state TEXT,
    finished_reason TEXT,
    finished_at_epoch INTEGER,
    landed_seen_at_epoch INTEGER,
    created_at_epoch INTEGER NOT NULL,
    updated_at_epoch INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_flights_scheduler
ON flights(tracking_state, next_check_at_epoch, polling_priority);

CREATE INDEX IF NOT EXISTS idx_flights_lookup
ON flights(requested_flight_iata, flight_date, departure_iata);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    telegram_user_id INTEGER NOT NULL,
    telegram_chat_id INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at_epoch INTEGER NOT NULL,
    stopped_at_epoch INTEGER,
    stop_reason TEXT,
    UNIQUE(flight_id, telegram_user_id, telegram_chat_id)
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user
ON subscriptions(telegram_user_id, active);

CREATE INDEX IF NOT EXISTS idx_subscriptions_flight
ON subscriptions(flight_id, active);

CREATE TABLE IF NOT EXISTS date_selection_sessions (
    token TEXT PRIMARY KEY,
    telegram_user_id INTEGER NOT NULL,
    telegram_chat_id INTEGER NOT NULL,
    flight_iata TEXT NOT NULL,
    departure_iata TEXT,
    expires_at_epoch INTEGER NOT NULL,
    used_at_epoch INTEGER,
    created_at_epoch INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_selections (
    token TEXT PRIMARY KEY,
    telegram_user_id INTEGER NOT NULL,
    telegram_chat_id INTEGER NOT NULL,
    candidate_json TEXT NOT NULL,
    expires_at_epoch INTEGER NOT NULL,
    used_at_epoch INTEGER,
    created_at_epoch INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    state_version INTEGER NOT NULL,
    event_kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL,
    UNIQUE(flight_id, state_version, event_kind)
);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES notification_events(id) ON DELETE CASCADE,
    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    telegram_chat_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at_epoch INTEGER NOT NULL,
    telegram_message_id INTEGER,
    last_error_code TEXT,
    sent_at_epoch INTEGER,
    created_at_epoch INTEGER NOT NULL,
    updated_at_epoch INTEGER NOT NULL,
    UNIQUE(event_id, subscription_id)
);

CREATE INDEX IF NOT EXISTS idx_deliveries_due
ON notification_deliveries(status, next_attempt_at_epoch);

CREATE TABLE IF NOT EXISTS api_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_started_at_epoch INTEGER NOT NULL,
    request_finished_at_epoch INTEGER,
    endpoint_name TEXT NOT NULL,
    flight_id INTEGER REFERENCES flights(id) ON DELETE SET NULL,
    trigger_type TEXT NOT NULL,
    priority INTEGER NOT NULL,
    success INTEGER,
    http_status INTEGER,
    api_error_code TEXT,
    attempted_cost INTEGER NOT NULL DEFAULT 1,
    duration_ms INTEGER,
    created_at_epoch INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_api_requests_period
ON api_requests(request_started_at_epoch, attempted_cost);

CREATE TABLE IF NOT EXISTS service_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at_epoch INTEGER NOT NULL
);
