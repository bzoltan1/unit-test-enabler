#!/bin/bash
# Keep evidence jobs running within a resource budget.
#   host/evidence-batch.sh [concurrency] [hours]
# Picks packages by priority with host/evidence-next.sh, keeps at most
# <concurrency> containers up, lowers the cap when memory is tight, retries
# incomplete runs twice, and stops at the time budget.
set -uo pipefail
source "$(dirname "$0")/common.sh"
conc=${1:-8}
hours=${2:-10}
end=$(( $(date +%s) + hours * 3600 ))
log="$here/results/evidence-batch.log"
att="$here/results/.evidence-attempts"
mkdir -p "$att" "$here/results/evidence"
say() { echo "[$(date -u +%FT%TZ)] $*" >> "$log"; }
say "batch start conc=$conc hours=$hours"

while [ "$(date +%s)" -lt "$end" ]; do
  # retry runs that ended without evidence.json, at most twice
  for d in "$here"/results/evidence/*/; do
    [ -d "$d" ] || continue
    p=$(basename "$d")
    [ -s "$d/evidence.json" ] && continue
    podman ps --format '{{.Names}}' | grep -qx "te-evidence-$p" && continue
    a=$(cat "$att/$p" 2>/dev/null || echo 0)
    [ "$a" -ge 2 ] && continue
    say "retry $p (attempt $((a+1)))"
    podman rm -f "te-evidence-$p" >/dev/null 2>&1
    rm -rf "$d"
    echo $(( a + 1 )) > "$att/$p"
  done

  running=$(podman ps --filter label=evidence=1 --format '{{.Names}}' | wc -l)
  avail=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)
  cap=$conc
  [ "$avail" -lt 20 ] && cap=$(( conc / 2 ))
  [ "$avail" -lt 12 ] && cap=0
  [ "$cap" -lt 0 ] && cap=0
  slots=$(( cap - running ))
  [ "$slots" -lt 0 ] && slots=0
  started=0
  if [ "$slots" -gt 0 ]; then
    pkgs=$(STATUSES="${STATUSES:-valid weak_reference no_distro_reference}" \
           "$here/host/evidence-next.sh" "$slots")
    for p in $pkgs; do
      podman container exists "te-evidence-$p" && continue
      "$here/host/run-evidence.sh" "$p" >> "$log" 2>&1 && started=$(( started + 1 ))
      sleep 2
    done
  fi
  say "running=$running avail=${avail}G cap=$cap started=$started slots=$slots pending=$(STATUSES="${STATUSES:-valid weak_reference no_distro_reference}" "$here/host/evidence-next.sh" 1)"
  if [ "$running" -eq 0 ] && [ "$started" -eq 0 ]; then
    left=$(STATUSES="${STATUSES:-valid weak_reference no_distro_reference}" "$here/host/evidence-next.sh" 1)
    [ -z "$left" ] && { say "no packages left"; break; }
  fi
  sleep 300
done
say "batch end"
