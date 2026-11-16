#!/usr/bin/env bash
# Kubespace read-only pre-flight for Azure. See azure/README.md.
# Runs under your own Azure CLI session, only reads, prints the exact JSON and asks before sending it.
set -euo pipefail
command -v az >/dev/null || { echo "The Azure CLI is required: https://learn.microsoft.com/cli/azure/install-azure-cli" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 (3.9 or later) is required" >&2; exit 1; }
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/preflight.py" "$@"
