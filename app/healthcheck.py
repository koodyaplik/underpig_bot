from __future__ import annotations

import sqlite3
import time

from app.config import Settings


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    now = int(time.time())
    with sqlite3.connect(settings.database_path, timeout=5) as connection:
        connection.execute("SELECT 1")
        migration = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        if not migration or migration[0] is None:
            raise SystemExit("database migrations are missing")
        states = dict(
            connection.execute(
                "SELECT key, value FROM service_state WHERE key IN (?, ?)",
                ("heartbeat:scheduler", "heartbeat:notification_worker"),
            ).fetchall()
        )
        scheduler = int(states.get("heartbeat:scheduler", "0"))
        notifier = int(states.get("heartbeat:notification_worker", "0"))
        if scheduler < now - max(120, settings.scheduler_tick_seconds * 4):
            raise SystemExit("scheduler heartbeat is stale")
        if notifier < now - 60:
            raise SystemExit("notification worker heartbeat is stale")


if __name__ == "__main__":
    main()
