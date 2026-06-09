from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

RUNTIME_REQUIRED_ENV = [
    "INTERNAL_ADMIN_API_KEY",
    "POSTGRES_PASSWORD",
    "MODEL_SECRET_ENCRYPTION_KEY",
]

FEISHU_REQUIRED_ENV = [
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_VERIFICATION_TOKEN",
    "FEISHU_ENCRYPT_KEY",
    "FEISHU_DEFAULT_CHAT_ID",
    "FEISHU_BITABLE_APP_TOKEN",
    "FEISHU_BITABLE_REQUISITION_TABLE_ID",
    "FEISHU_BITABLE_CANDIDATE_TABLE_ID",
    "FEISHU_BITABLE_TALENT_MAP_TABLE_ID",
    "FEISHU_BITABLE_REPORT_TABLE_ID",
]

RECOMMENDED_ENV = [
    "EMBEDDING_API_KEY",
]

GENERIC_PLACEHOLDER_VALUES = {
    "",
    "change-me",
    "changeme",
    "replace-me",
    "todo",
    "your-domain.com",
    "your-domain.example.com",
}

PLACEHOLDER_VALUES_BY_ENV = {
    "INTERNAL_ADMIN_API_KEY": {"change-this-admin-key"},
    "POSTGRES_PASSWORD": {"change-this-password"},
    "MODEL_SECRET_ENCRYPTION_KEY": {"change-this-32-byte-or-base64-secret"},
}

GENERATED_LOCAL_SECRET_ENV = {
    "INTERNAL_ADMIN_API_KEY",
    "POSTGRES_PASSWORD",
    "MODEL_SECRET_ENCRYPTION_KEY",
}
ONECLICK_PREFLIGHT_COMMAND = (
    "powershell -ExecutionPolicy Bypass -File scripts\\lietou-oneclick.ps1"
)
ONECLICK_START_COMMAND = f"{ONECLICK_PREFLIGHT_COMMAND} -Start"
POSIX_ONECLICK_PREFLIGHT_COMMAND = "bash scripts/lietou-oneclick.sh"
POSIX_ONECLICK_START_COMMAND = f"{POSIX_ONECLICK_PREFLIGHT_COMMAND} --start"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Check whether the local Feishu-first deployment can be started."
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root to inspect. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Environment file path, relative to repo root unless absolute.",
    )
    parser.add_argument(
        "--init-env",
        action="store_true",
        help=(
            "Create .env from .env.example when .env is missing. "
            "Existing .env is never overwritten."
        ),
    )
    parser.add_argument(
        "--generate-local-secrets",
        action="store_true",
        help=(
            "Replace local runtime placeholders in .env with strong random values. "
            "Feishu, Bitable, model, and embedding credentials are never generated."
        ),
    )
    parser.add_argument(
        "--skip-docker",
        action="store_true",
        help="Skip Docker and docker compose probing.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with status 1 unless the local deployment is ready for real Feishu use.",
    )
    args = parser.parse_args(argv)

    report = build_local_doctor_report(
        repo_root=Path(args.repo_root),
        env_file=Path(args.env_file),
        init_env=args.init_env,
        generate_local_secrets=args.generate_local_secrets,
        check_docker=not args.skip_docker,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and report["status"] != "ok":
        raise SystemExit(1)


def build_local_doctor_report(
    *,
    repo_root: Path,
    env_file: Path = Path(".env"),
    init_env: bool = False,
    generate_local_secrets: bool = False,
    check_docker: bool = True,
    docker_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, Any]:
    root = repo_root.resolve()
    resolved_env_file = _resolve_env_file(root, env_file)
    env_example = root / ".env.example"
    env_created = False
    generated_env_vars: list[str] = []
    init_error: str | None = None

    if init_env and not resolved_env_file.exists():
        if env_example.exists():
            shutil.copyfile(env_example, resolved_env_file)
            env_created = True
        else:
            init_error = ".env.example not found; cannot initialize .env."

    if generate_local_secrets and resolved_env_file.exists():
        generated_env_vars = generate_local_runtime_secrets(resolved_env_file)

    env_values = load_env_file(resolved_env_file) if resolved_env_file.exists() else {}
    env_report = _build_env_report(
        env_values=env_values,
        env_file=resolved_env_file,
        env_created=env_created,
        generated_env_vars=generated_env_vars,
        init_error=init_error,
    )
    docker_report = (
        check_docker_runtime(runner=docker_runner, which=which)
        if check_docker
        else {"checked": False, "installed": None, "compose_available": None}
    )

    blocking = _blocking_issues(env_report, docker_report)
    warnings = _warning_issues(env_report, docker_report)
    status = "ok" if not blocking else "not_ready"
    return {
        "status": status,
        "repo_root": str(root),
        "env": env_report,
        "docker": docker_report,
        "blocking_issues": blocking,
        "warnings": warnings,
        "commands": _commands(),
        "next_steps": _next_steps(blocking=blocking, warnings=warnings),
    }


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def generate_local_runtime_secrets(path: Path) -> list[str]:
    env_values = load_env_file(path)
    generated: dict[str, str] = {}
    for key in sorted(GENERATED_LOCAL_SECRET_ENV):
        value = env_values.get(key)
        if value is None or _is_placeholder(key, value):
            generated[key] = _new_local_secret(key)
    if not generated:
        return []

    lines = path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    updated_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            updated_lines.append(line)
            continue
        key, _, value = line.partition("=")
        normalized_key = key.strip()
        if normalized_key in generated:
            seen.add(normalized_key)
            updated_lines.append(f"{normalized_key}={generated[normalized_key]}")
            continue
        updated_lines.append(line)

    missing_generated = [key for key in sorted(generated) if key not in seen]
    if missing_generated:
        if updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        updated_lines.append("# Generated local runtime secrets")
        for key in missing_generated:
            updated_lines.append(f"{key}={generated[key]}")

    path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
    return sorted(generated)


def check_docker_runtime(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, Any]:
    docker_path = which("docker")
    if not docker_path:
        return {
            "checked": True,
            "installed": False,
            "compose_available": False,
            "version": None,
            "error": "docker executable not found on PATH.",
        }

    try:
        result = runner(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "checked": True,
            "installed": True,
            "compose_available": False,
            "version": None,
            "error": str(exc),
        }

    output = (result.stdout or result.stderr or "").strip()
    return {
        "checked": True,
        "installed": True,
        "compose_available": result.returncode == 0,
        "version": output if result.returncode == 0 else None,
        "error": None if result.returncode == 0 else output,
    }


def _resolve_env_file(repo_root: Path, env_file: Path) -> Path:
    if env_file.is_absolute():
        return env_file
    return repo_root / env_file


def _build_env_report(
    *,
    env_values: dict[str, str],
    env_file: Path,
    env_created: bool,
    generated_env_vars: list[str],
    init_error: str | None,
) -> dict[str, Any]:
    runtime_missing, runtime_placeholder = _classify_env(env_values, RUNTIME_REQUIRED_ENV)
    feishu_missing, feishu_placeholder = _classify_env(env_values, FEISHU_REQUIRED_ENV)
    recommended_missing, recommended_placeholder = _classify_env(env_values, RECOMMENDED_ENV)
    return {
        "path": str(env_file),
        "exists": env_file.exists(),
        "created": env_created,
        "generated_local_secrets": generated_env_vars,
        "init_error": init_error,
        "runtime_required": {
            "missing": runtime_missing,
            "placeholder": runtime_placeholder,
        },
        "feishu_required": {
            "missing": feishu_missing,
            "placeholder": feishu_placeholder,
        },
        "recommended": {
            "missing": recommended_missing,
            "placeholder": recommended_placeholder,
        },
    }


def _classify_env(env_values: dict[str, str], keys: list[str]) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    placeholder: list[str] = []
    for key in keys:
        if key not in env_values:
            missing.append(key)
            continue
        if _is_placeholder(key, env_values[key]):
            placeholder.append(key)
    return missing, placeholder


def _is_placeholder(key: str, value: str) -> bool:
    normalized = value.strip()
    if normalized.lower() in GENERIC_PLACEHOLDER_VALUES:
        return True
    return normalized in PLACEHOLDER_VALUES_BY_ENV.get(key, set())


def _new_local_secret(key: str) -> str:
    if key == "MODEL_SECRET_ENCRYPTION_KEY":
        return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    return secrets.token_urlsafe(32)


def _blocking_issues(env_report: dict[str, Any], docker_report: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if env_report["init_error"]:
        issues.append(env_report["init_error"])
    if not env_report["exists"]:
        issues.append("Create .env from .env.example and fill deployment values.")
    for section_name in ["runtime_required", "feishu_required"]:
        section = env_report[section_name]
        if section["missing"]:
            issues.append(f"{section_name} missing: {', '.join(section['missing'])}")
        if section["placeholder"]:
            issues.append(f"{section_name} still placeholder: {', '.join(section['placeholder'])}")
    if docker_report.get("checked") and not docker_report.get("installed"):
        issues.append("Install Docker Desktop or add docker to PATH.")
    elif docker_report.get("checked") and not docker_report.get("compose_available"):
        issues.append("Install/enable Docker Compose v2 so `docker compose` works.")
    return issues


def _warning_issues(env_report: dict[str, Any], docker_report: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    recommended = env_report["recommended"]
    recommended_unset = [*recommended["missing"], *recommended["placeholder"]]
    if recommended_unset:
        warnings.append(
            "Optional memory vectorization is not ready: "
            + ", ".join(sorted(set(recommended_unset)))
        )
    if docker_report.get("error") and docker_report.get("installed"):
        warnings.append(f"Docker Compose probe returned: {docker_report['error']}")
    return warnings


def _next_steps(*, blocking: list[str], warnings: list[str]) -> list[str]:
    preflight_command = _oneclick_preflight_command()
    start_command = "docker compose up -d --build"
    if not blocking:
        steps = [
            f"Run `{start_command}`.",
            "Run `docker compose exec api lietou-config-check --strict` after containers start.",
            (
                "Run `docker compose exec worker lietou-outbox-worker --once` "
                "for a one-shot worker check."
            ),
        ]
        if warnings:
            steps.append("Address warnings before relying on long-term memory retrieval.")
        return steps

    steps: list[str] = []
    if _has_blocking_prefix(blocking, "Create .env") or _has_blocking_prefix(
        blocking, "runtime_required"
    ):
        steps.append(
            f"Run `{preflight_command}` from the repo root to initialize .env "
            "and generate local runtime secrets."
        )
    if _has_blocking_prefix(blocking, "feishu_required"):
        steps.append(
            "Before running strict preflight again, edit `.env` and fill the "
            "Feishu/Bitable values listed in `docs/manual/飞书接入操作手册.md` "
            "section `获取 .env 里的飞书 ID`."
        )
    if any("Docker" in issue or "docker compose" in issue for issue in blocking):
        steps.append(
            "Install Docker Engine/Compose or Docker Desktop and confirm "
            "`docker compose version` works."
        )
    steps.extend(
        [
            "Run `python -m app.runtime.local_doctor --strict` again.",
            f"After `status` is `ok`, run `{start_command}`.",
        ]
    )
    return steps


def _has_blocking_prefix(blocking: list[str], prefix: str) -> bool:
    return any(issue.startswith(prefix) for issue in blocking)


def _commands() -> dict[str, str]:
    preflight_command = _oneclick_preflight_command()
    start_command = _oneclick_start_command()
    return {
        "oneclick_preflight": preflight_command,
        "oneclick_start": start_command,
        "oneclick_preflight_windows": ONECLICK_PREFLIGHT_COMMAND,
        "oneclick_start_windows": ONECLICK_START_COMMAND,
        "oneclick_preflight_posix": POSIX_ONECLICK_PREFLIGHT_COMMAND,
        "oneclick_start_posix": POSIX_ONECLICK_START_COMMAND,
        "doctor": "lietou-local-doctor --strict",
        "doctor_module": "python -m app.runtime.local_doctor --strict",
        "start": "docker compose up -d --build",
        "docker_compose_start": "docker compose up -d --build",
        "readiness": "docker compose exec api lietou-config-check --strict",
        "worker_once": "docker compose exec worker lietou-outbox-worker --once",
    }


def _oneclick_preflight_command() -> str:
    if os.name == "nt":
        return ONECLICK_PREFLIGHT_COMMAND
    return POSIX_ONECLICK_PREFLIGHT_COMMAND


def _oneclick_start_command() -> str:
    if os.name == "nt":
        return ONECLICK_START_COMMAND
    return POSIX_ONECLICK_START_COMMAND


if __name__ == "__main__":
    main()
