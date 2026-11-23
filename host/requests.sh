#!/bin/bash
# Status of the agents' requests and PRs (see host/requests.py).
source "$(dirname "$0")/common.sh"
exec python3 "$here/host/requests.py" "$@"
