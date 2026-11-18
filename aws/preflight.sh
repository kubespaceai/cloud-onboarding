#!/usr/bin/env bash
# Kubespace read-only pre-flight for AWS. See aws/README.md.
# Runs under your own AWS CLI session, only reads, prints the exact JSON and asks before sending it.
set -euo pipefail
command -v aws >/dev/null || { echo "The AWS CLI v2 is required: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 (3.9 or later) is required" >&2; exit 1; }
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/preflight.py" "$@"
