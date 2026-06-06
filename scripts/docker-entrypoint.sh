#!/usr/bin/env sh
set -eu

if [ -n "${DATABASE_URL:-}" ]; then
  python - <<'PY'
import os
import time

from sqlalchemy import create_engine, text

database_url = os.environ["DATABASE_URL"]
deadline = time.monotonic() + float(os.environ.get("DATABASE_WAIT_SECONDS", "60"))
last_error = None

while time.monotonic() < deadline:
    try:
        engine = create_engine(database_url, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        raise SystemExit(0)
    except Exception as exc:
        last_error = exc
        time.sleep(1)

raise SystemExit(f"database not ready before timeout: {last_error}")
PY
fi

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
  alembic upgrade head
fi

exec "$@"
