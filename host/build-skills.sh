#!/bin/bash
# Build skills/ from SKILLS_SRC / SKILLS_GIT and skills-src/ (see host/sanitize-skills.py).
set -euo pipefail
source "$(dirname "$0")/common.sh"
exec python3 "$here/host/sanitize-skills.py"
