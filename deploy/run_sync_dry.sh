#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_DIR}"

if [[ ! -f .env ]]; then
  echo ".env not found in ${PROJECT_DIR}" >&2
  exit 1
fi

docker compose run --rm -e DRY_RUN=true xrxs2ldap xrxs2ldap --dry-run --once
