#!/usr/bin/env bash
# Kubespace keyless read-only access for Google Cloud: applies the blueprint from your setup code.
# Runs under your own gcloud identity, prints what is missing, asks, then creates only that. See gcp/README.md.
set -euo pipefail
command -v gcloud >/dev/null || { echo "gcloud is required: https://cloud.google.com/sdk/docs/install" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 (3.9 or later) is required" >&2; exit 1; }
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/apply.py" "$@"
