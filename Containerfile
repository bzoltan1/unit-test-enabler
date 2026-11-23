# Unit test enabler agent image. Contains no credentials; those come
# in at runtime as podman secrets (see host/create-secrets.sh).
FROM registry.opensuse.org/opensuse/tumbleweed:latest

# Optional internal CA for the internal build service and Gitea
# (INTERNAL_CA_REPO: zypper repository URL, INTERNAL_CA_PACKAGE: package).
ARG INTERNAL_CA_REPO=
ARG INTERNAL_CA_PACKAGE=
RUN if [ -n "$INTERNAL_CA_REPO" ]; then \
      zypper -n ar -f -G "$INTERNAL_CA_REPO" internal_ca \
      && zypper -n --gpg-auto-import-keys ref \
      && zypper -n in --no-recommends "$INTERNAL_CA_PACKAGE"; \
    fi

RUN zypper -n in --no-recommends \
      osc build obs-service-source_validator obs-service-format_spec_file \
      obs-service-download_files obs-service-obs_scm obs-service-tar_scm \
      obs-service-set_version obs-service-recompress obs-service-go_modules \
      obs-service-cargo_vendor obs-service-verify_file \
      qemu-x86 qemu-kvm qemu-tools e2fsprogs cpio rpm-build rpmlint \
      git-core openssh-clients tea python3 python3-PyYAML python3-requests \
      jq curl wget ripgrep findutils diffutils patch quilt xz zstd bzip2 \
      gzip tar unzip less vim-small which procps util-linux shadow \
      sudo bsdtar file gawk sed grep hostname timezone glibc-locale-base \
      spec-cleaner bugwarden obs-scm-bridge \
 && zypper clean -a

# git-obs ships with osc on Tumbleweed; fail the build if it is missing.
RUN command -v git-obs && command -v osc && command -v tea

# The default podman seccomp profile blocks io_uring, which build-vm-kvm
# would pass to qemu (aio=io_uring). Use qemu's default aio instead.
RUN sed -i "s/if ldd \$kvm_bin | grep -E -q 'liburing.so.\[2-9\]' ; then/if false ; then/" /usr/lib/build/build-vm-kvm \
 && ! grep -q 'liburing.so' /usr/lib/build/build-vm-kvm

# opencode: pinned official release, checksum from the GitHub release.
ARG OPENCODE_VERSION=1.19.6
ARG OPENCODE_SHA256=47734e08968b30cc22a0bffb6d9feeed76ad4aa743213a12122040392d09a057
RUN curl -fsSL -o /tmp/opencode.tar.gz \
      "https://github.com/anomalyco/opencode/releases/download/v${OPENCODE_VERSION}/opencode-linux-x64.tar.gz" \
 && echo "${OPENCODE_SHA256}  /tmp/opencode.tar.gz" | sha256sum -c - \
 && tar -xzf /tmp/opencode.tar.gz -C /usr/local/bin opencode \
 && rm /tmp/opencode.tar.gz \
 && /usr/local/bin/opencode --version | grep -qx "${OPENCODE_VERSION}"

# Unprivileged user: build-vm only uses mkfs copyin (no loop mounts)
# when not running as uid 0, which is what rootless podman allows.
RUN useradd -m -u 1000 -s /bin/bash agent \
 && echo 'agent ALL=(root) NOPASSWD: /usr/bin/zypper' > /etc/sudoers.d/agent-zypper \
 && chmod 440 /etc/sudoers.d/agent-zypper \
 && mkdir -p /work /results /opt/enabler \
 && chown agent:agent /work /results

COPY --chown=agent:agent skills/ /home/agent/.config/opencode/skills/
COPY agent/ /opt/enabler/agent/
COPY bin/ /opt/enabler/bin/
COPY prices.json /opt/enabler/prices.json
COPY guard/ /opt/enabler/guard/
RUN chmod +x /opt/enabler/bin/* /opt/enabler/guard/guard.py \
 && cd /opt/enabler/guard && for p in osc git git-obs tea; do ln -sf guard.py $p; done

USER agent
WORKDIR /work
ENV HOME=/home/agent LANG=C.UTF-8 TZ=UTC
ENTRYPOINT ["/opt/enabler/bin/entrypoint.sh"]
