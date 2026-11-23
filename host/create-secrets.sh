#!/bin/bash
# Create or rotate the podman secrets from the files named in enabler.conf.
set -euo pipefail
source "$(dirname "$0")/common.sh"
exec python3 "$here/host/create-secrets.py"
