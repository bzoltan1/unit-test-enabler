#!/bin/bash
# Refuse to commit deployment-specific or secret content.
#   host/leak-check.sh            check the staged changes (pre-commit hook)
#   host/leak-check.sh --all      check every tracked file
# Forbidden: the values from enabler.conf (OBS_USER, GIT_NAME, GIT_EMAIL,
# SANITIZE_EXTRA_IDS, the IBS_API / IBS_GITEA / INTERNAL_CA_REPO hosts,
# ENABLER_DATA, $HOME), private keys, token-shaped strings.
# Install as hook:  ln -sf ../../host/leak-check.sh .git/hooks/pre-commit
set -uo pipefail
top=$(git rev-parse --show-toplevel)
conf=$top/enabler.conf
[ -r "$conf" ] || { echo "leak-check: no enabler.conf, only generic checks" >&2; }
pats=()
if [ -r "$conf" ]; then
  # shellcheck disable=SC1090
  set -a; source "$conf"; set +a
  host() { local h=${1#*://}; echo "${h%%/*}"; }
  for v in "${OBS_USER:-}" "${GIT_EMAIL:-}" $(echo "${GIT_NAME:-}") \
           $(echo "${SANITIZE_EXTRA_IDS:-}" | tr ',' ' ') \
           "$(host "${IBS_API:-}")" "${IBS_GITEA:-}" "$(host "${INTERNAL_CA_REPO:-}")" \
           "${ENABLER_DATA:-}" "$HOME"; do
    [ ${#v} -ge 4 ] && pats+=("$(printf '%s' "$v" | sed 's/[.[\*^$+?(){}|]/\\&/g')")
  done
fi
pats+=('-----BEGIN [A-Z ]*PRIVATE KEY' 'ghp_[A-Za-z0-9]{30,}' 'glpat-[A-Za-z0-9_-]{20}'
       'AKIA[0-9A-Z]{16}' 'AIza[0-9A-Za-z_-]{35}' '"private_key"' 'sk-[A-Za-z0-9]{32,}')
re=$(IFS='|'; echo "${pats[*]}")
# Allowed lines: the public clone URL, plus the regexes in .leak-allow
# (intentional mentions such as the credit to a skill's author).
allow='github\.com/[A-Za-z0-9-]+/unit-test-enabler'
[ -r "$top/.leak-allow" ] && while IFS= read -r l; do
  [ -n "$l" ] && [ "${l#\#}" = "$l" ] && allow="$allow|$l"
done < "$top/.leak-allow"
# The files that define the patterns are not checked against them.
self=(':!host/leak-check.sh' ':!.leak-allow')
if [ "${1:-}" = --all ]; then
  hits=$(git -C "$top" grep -nIiE "$re" -- . "${self[@]}" | grep -vE "$allow")
else
  hits=$(git -C "$top" diff --cached -U0 --no-color -- . "${self[@]}" | grep -E '^\+[^+]' | grep -iE "$re" | grep -vE "$allow")
fi
if [ -n "$hits" ]; then
  echo "leak-check: refusing, deployment-specific or secret content:" >&2
  echo "$hits" | cut -c1-200 >&2
  exit 1
fi
exit 0
