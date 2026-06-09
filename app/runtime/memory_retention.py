import argparse
import json
from dataclasses import asdict
from datetime import datetime

from app.memory.retention import MemoryRetentionService, MemoryRetentionSummary
from app.storage.database import SessionLocal


def run_memory_retention_once(
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> MemoryRetentionSummary:
    with SessionLocal() as session:
        with session.begin():
            return MemoryRetentionService(session).run(now=now, dry_run=dry_run)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run memory retention defaults and expiry processing once."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report changes without mutating rows.",
    )
    parser.add_argument(
        "--now",
        help="Override current time with an ISO-8601 timestamp, for audits and tests.",
    )
    args = parser.parse_args(argv)

    summary = run_memory_retention_once(
        dry_run=args.dry_run,
        now=_parse_datetime(args.now) if args.now else None,
    )
    print(json.dumps(asdict(summary), ensure_ascii=False, default=str))


def _parse_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    return datetime.fromisoformat(normalized)


if __name__ == "__main__":
    main()
