#!/bin/bash
# Install runtime credentials from podman secrets, then run the
# orchestrator (or a shell / command for debugging).
set -euo pipefail

: "${OBS_USER:?OBS_USER must be set (host/run.sh passes it from enabler.conf)}"
: "${GIT_NAME:?GIT_NAME must be set}" "${GIT_EMAIL:?GIT_EMAIL must be set}"
: "${IBS_API:?IBS_API must be set (internal build service API URL)}"
: "${IBS_GITEA:?IBS_GITEA must be set (internal Gitea host)}"
: "${IBS_TEA_LOGIN:?IBS_TEA_LOGIN must be set (tea login name of IBS_GITEA)}"
: "${OBS_TEA_LOGIN:=opensuse}"
export OBS_TEA_LOGIN

S=/run/secrets
need() { [ -r "$S/$1" ] || { echo "missing podman secret $1" >&2; exit 64; }; }
for s in te_oscrc te_ssh_ibs te_ssh_ibs_gitea te_ssh_obs_gitea te_tea_config \
         te_opencode_providers te_vertex_adc te_ibs_gitea_token te_bugzilla_key; do need "$s"; done

umask 077
mkdir -p ~/.config/osc ~/.ssh ~/.config/tea ~/.config/gcloud ~/.config/opencode \
         ~/.config/enabler /work/osbuild-cache /work/buildroot
install -m 600 "$S/te_oscrc" ~/.config/osc/oscrc
install -m 600 "$S/te_ssh_ibs" ~/.ssh/id_ibs_api
install -m 600 "$S/te_ssh_ibs_gitea" ~/.ssh/id_ibs_gitea
install -m 600 "$S/te_ssh_obs_gitea" ~/.ssh/id_obs_gitea
ssh-keygen -y -f ~/.ssh/id_ibs_api > ~/.ssh/id_ibs_api.pub
ssh-keygen -y -f ~/.ssh/id_ibs_gitea > ~/.ssh/id_ibs_gitea.pub
install -m 600 "$S/te_tea_config" ~/.config/tea/config.yml
install -m 600 "$S/te_vertex_adc" ~/.config/gcloud/application_default_credentials.json
install -m 600 "$S/te_ibs_gitea_token" ~/.config/enabler/ibs_gitea.token
install -m 600 "$S/te_bugzilla_key" ~/.config/enabler/bugzilla.key

cat > ~/.ssh/config <<EOF
Host $IBS_GITEA
    User gitea
    IdentityFile ~/.ssh/id_ibs_gitea
    IdentitiesOnly yes
Host src.opensuse.org
    User gitea
    IdentityFile ~/.ssh/id_obs_gitea
    IdentitiesOnly yes
Host *
    StrictHostKeyChecking accept-new
    BatchMode yes
    ServerAliveInterval 30
EOF
chmod 600 ~/.ssh/config

git config --global user.name "$GIT_NAME"
git config --global user.email "$GIT_EMAIL"
git config --global init.defaultBranch main
git config --global advice.detachedHead false

python3 /opt/enabler/bin/make_opencode_config.py "$S/te_opencode_providers" \
    > ~/.config/opencode/opencode.json

# AGENTS.md, the master prompt and the lessons may use @PLACEHOLDERS@ for
# the deployment-specific values; render them for this deployment.
render() {
  sed -e "s|@OBS_USER@|$OBS_USER|g" -e "s|@IBS_API@|$IBS_API|g" \
      -e "s|@IBS_GITEA@|$IBS_GITEA|g" -e "s|@IBS_TEA_LOGIN@|$IBS_TEA_LOGIN|g" \
      -e "s|@OBS_TEA_LOGIN@|$OBS_TEA_LOGIN|g" "$1"
}
A=~/.config/enabler/agent
mkdir -p "$A/prompts" "$A/lessons"
render /opt/enabler/agent/AGENTS.md > ~/.config/opencode/AGENTS.md
for f in /opt/enabler/agent/prompts/*.md /opt/enabler/agent/lessons/*.md; do
  [ -e "$f" ] && render "$f" > "$A/$(basename "$(dirname "$f")")/$(basename "$f")"
done
export ENABLER_AGENT_DIR=$A

# Nothing may prompt: git, ssh, pagers and editors run non-interactively.
export GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/false SSH_ASKPASS=/bin/false \
       GIT_PAGER=cat PAGER=cat EDITOR=true VISUAL=true GIT_EDITOR=true \
       OSC_PAGER=cat DEBIAN_FRONTEND=noninteractive
git config --global core.pager cat
git config --global credential.helper ""

# Guard wrappers (submission gates) come first in PATH.
export PATH=/opt/enabler/guard:/opt/enabler/bin:$PATH
umask 022

case "${1:-run}" in
  shell) exec bash -l ;;
  exec) shift; exec "$@" ;;
  run) shift || true; exec python3 /opt/enabler/bin/orchestrate.py "$@" ;;
  *) exec python3 /opt/enabler/bin/orchestrate.py "$@" ;;
esac
