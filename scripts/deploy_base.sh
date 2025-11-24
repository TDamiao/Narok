#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -f .env ]; then
  # shellcheck disable=SC1091
  set -a
  source .env
  set +a
fi

if [ -z "${BASE_RPC_URL:-}" ]; then
  echo "BASE_RPC_URL is not set in .env." >&2
  exit 1
fi

export WEB3_HTTP_PROVIDER_URI="${WEB3_HTTP_PROVIDER_URI:-$BASE_RPC_URL}"

if [ -z "${PRIVATE_KEY:-}" ] && [ -z "${APE_ACCOUNT_ALIAS:-}" ]; then
  echo "Define PRIVATE_KEY or APE_ACCOUNT_ALIAS in .env." >&2
  exit 1
fi

if [ -z "${ADMIN_WALLET:-}" ]; then
  echo "ADMIN_WALLET is not set in .env." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Python virtualenv .venv not found. Create it before running this script." >&2
  exit 1
fi

source .venv/bin/activate
exec ape run scripts/deploy.py --network base:mainnet:geth "$@"
