#!/usr/bin/env bash
# Kubespace read-only pre-flight for Google Cloud. See gcp/README.md.
# Runs under your own gcloud identity, only reads, prints the exact JSON and asks before sending it.
set -euo pipefail
command -v gcloud >/dev/null || { echo "gcloud is required: https://cloud.google.com/sdk/docs/install" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 (3.9 or later) is required" >&2; exit 1; }
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/preflight.py" "$@"
