import argparse
import json

from app.core.config import get_settings
from app.core.readiness import ReadinessReport, build_readiness_report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Print first-version runtime readiness diagnostics."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with status 1 when required runtime configuration is missing.",
    )
    args = parser.parse_args(argv)

    report = build_readiness_report(get_settings())
    print(_report_json(report))
    if args.strict and _has_blocking_issue(report):
        raise SystemExit(1)


def _report_json(report: ReadinessReport) -> str:
    payload = {
        "status": report.status,
        "checks": report.checks,
        "details": [detail.model_dump(mode="json") for detail in report.details],
        "missing_required": report.missing_required,
        "next_steps": report.next_steps,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _has_blocking_issue(report: ReadinessReport) -> bool:
    if report.missing_required:
        return True
    return any(detail.status == "error" for detail in report.details)


if __name__ == "__main__":
    main()
