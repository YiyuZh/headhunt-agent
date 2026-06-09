#!/usr/bin/env sh
set -eu

START=0
DOCTOR_ONLY=0
SKIP_DOCKER=0
NO_INIT=0
NO_BUILD=0
REPO_ROOT=""
PYTHON_BIN=""

usage() {
  cat <<'EOF'
Usage: bash scripts/lietou-oneclick.sh [options]

Options:
  --start          Run strict preflight, then docker compose up -d --build.
  --doctor-only    Run preflight only. This is also the default when --start is absent.
  --skip-docker    Skip Docker probing during preflight. Cannot be used with --start.
  --no-init        Do not create .env and do not generate local runtime secrets.
  --no-build       With --start, run docker compose up -d without --build.
  --repo-root DIR  Repository root. Defaults to the parent of this script directory.
  --python PATH    Python interpreter. Defaults to .venv/bin/python, then python3, then python.
  -h, --help       Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --start)
      START=1
      ;;
    --doctor-only)
      DOCTOR_ONLY=1
      ;;
    --skip-docker)
      SKIP_DOCKER=1
      ;;
    --no-init)
      NO_INIT=1
      ;;
    --no-build)
      NO_BUILD=1
      ;;
    --repo-root)
      shift
      if [ "$#" -eq 0 ]; then
        echo "--repo-root requires a value" >&2
        exit 2
      fi
      REPO_ROOT=$1
      ;;
    --python)
      shift
      if [ "$#" -eq 0 ]; then
        echo "--python requires a value" >&2
        exit 2
      fi
      PYTHON_BIN=$1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [ "$START" -eq 1 ] && [ "$DOCTOR_ONLY" -eq 1 ]; then
  echo "Use either --start or --doctor-only, not both." >&2
  exit 2
fi
if [ "$START" -eq 1 ] && [ "$SKIP_DOCKER" -eq 1 ]; then
  echo "Cannot use --skip-docker with --start because Docker Compose is required." >&2
  exit 2
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -n "$REPO_ROOT" ]; then
  ROOT=$(CDPATH= cd -- "$REPO_ROOT" && pwd)
else
  ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
fi

cd "$ROOT"

if [ -z "$PYTHON_BIN" ]; then
  if [ -x "$ROOT/.venv/bin/python" ]; then
    PYTHON_BIN="$ROOT/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN=$(command -v python3)
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN=$(command -v python)
  else
    echo "Python was not found. Create .venv or pass --python /path/to/python." >&2
    exit 127
  fi
fi

echo
echo "==> Running local deployment doctor"
set -- -m app.runtime.local_doctor --repo-root "$ROOT"
if [ "$NO_INIT" -eq 0 ]; then
  set -- "$@" --init-env --generate-local-secrets
fi
if [ "$SKIP_DOCKER" -eq 1 ]; then
  set -- "$@" --skip-docker
fi
"$PYTHON_BIN" "$@"

if [ "$DOCTOR_ONLY" -eq 1 ] || [ "$START" -eq 0 ]; then
  echo
  echo "Preflight finished. Fill .env, then run: bash scripts/lietou-oneclick.sh --start"
  exit 0
fi

echo
echo "==> Verifying strict readiness before Docker start"
"$PYTHON_BIN" -m app.runtime.local_doctor --repo-root "$ROOT" --strict

echo
echo "==> Starting Docker Compose stack"
if [ "$NO_BUILD" -eq 1 ]; then
  docker compose up -d
else
  docker compose up -d --build
fi

echo
echo "==> Current Docker Compose services"
docker compose ps
