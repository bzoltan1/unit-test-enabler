# Sourced by host scripts: loads enabler.conf and defines SECRET_ARGS.
here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
conf=${ENABLER_CONF:-$here/enabler.conf}
if [ ! -r "$conf" ]; then
  echo "missing $conf (copy enabler.conf.example and edit it)" >&2
  exit 64
fi
# Variables set in the environment win over enabler.conf for these.
_override=(SHADOW_REVIEWERS WORKER_MODEL REVIEWER_MODEL MODEL_LIMITS SHADOW_MAX_STEPS BUILD_WAIT_HOURS TARGET_GRACE_HOURS BUILD_CHURN_LIMIT)
declare -A _env
for v in "${_override[@]}"; do [ -n "${!v+x}" ] && _env[$v]=${!v}; done
set -a
# shellcheck disable=SC1090
source "$conf"
for v in "${!_env[@]}"; do printf -v "$v" '%s' "${_env[$v]}"; done
set +a
: "${OBS_USER:?OBS_USER not set in $conf}" "${ENABLER_DATA:?ENABLER_DATA not set in $conf}"
: "${IBS_API:?IBS_API not set in $conf}" "${IBS_GITEA:?IBS_GITEA not set in $conf}"
: "${IBS_TEA_LOGIN:?IBS_TEA_LOGIN not set in $conf}"
OBS_TEA_LOGIN=${OBS_TEA_LOGIN:-opensuse}
# Deployment values every container gets.
ENV_ARGS=(-e OBS_USER="$OBS_USER" -e GIT_NAME="$GIT_NAME" -e GIT_EMAIL="$GIT_EMAIL"
          -e IBS_API="$IBS_API" -e IBS_GITEA="$IBS_GITEA" -e IBS_TEA_LOGIN="$IBS_TEA_LOGIN"
          -e OBS_TEA_LOGIN="$OBS_TEA_LOGIN" -e WORKER_MODEL="$WORKER_MODEL"
          -e REVIEWER_MODEL="$REVIEWER_MODEL" -e BUGZILLA_URL="${BUGZILLA_URL:-https://bugzilla.suse.com}"
          -e SHADOW_REVIEWERS="${SHADOW_REVIEWERS:-}" -e MODEL_LIMITS="${MODEL_LIMITS:-}"
          -e BUILD_WAIT_HOURS="${BUILD_WAIT_HOURS:-6}" -e TARGET_GRACE_HOURS="${TARGET_GRACE_HOURS:-3}"
          -e BUILD_CHURN_LIMIT="${BUILD_CHURN_LIMIT:-3}"
          -e SHADOW_MAX_STEPS="${SHADOW_MAX_STEPS:-120}" -e REVIEWER_MAX_STEPS="${REVIEWER_MAX_STEPS:-200}")
data=$ENABLER_DATA

SECRETS=(te_oscrc te_ssh_ibs te_ssh_ibs_gitea te_ssh_obs_gitea te_tea_config
         te_opencode_providers te_vertex_adc te_ibs_gitea_token te_bugzilla_key)
SECRET_ARGS=()
for s in "${SECRETS[@]}"; do
  SECRET_ARGS+=(--secret "$s,mode=0400,uid=1000")
done
