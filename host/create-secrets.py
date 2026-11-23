#!/usr/bin/env python3
"""Create the podman secrets the test-enabler containers need.

Run through host/create-secrets.sh, which loads enabler.conf. Reads the
credential files named there, rewrites paths for the container user
(/home/agent) and pipes each value into `podman secret create` on stdin,
so nothing is written to disk. Rerun to rotate. Never prints values.
"""
import configparser
import json
import os
import subprocess
import sys

C_HOME = "/home/agent"
E = os.environ


def path(var):
    p = os.path.expanduser(os.path.expandvars(E.get(var, "")))
    if not p or not os.path.isfile(p):
        sys.exit(f"{var}: file {p!r} not found (set it in enabler.conf)")
    return p


def read(var):
    with open(path(var), "rb") as f:
        return f.read()


def put(name, data):
    if isinstance(data, str):
        data = data.encode()
    subprocess.run(["podman", "secret", "rm", name],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["podman", "secret", "create", name, "-"],
                   input=data, check=True, stdout=subprocess.DEVNULL)
    print(f"created secret {name} ({len(data)} bytes)")


def oscrc():
    src = configparser.RawConfigParser()
    src.read(path("OSCRC"))
    out = ["[general]",
           "no_verify = 1",
           "build-type = kvm",
           "build-memory = 8192",
           "build-jobs = 4",
           f"build-vmdisk-rootsize = {E.get('BUILD_DISK_MB', '20000')}",
           "build-vmdisk-swapsize = 2048",
           "build-vmdisk-filesystem = ext4",
           "build-root = /work/buildroot/%(repo)s-%(arch)s",
           "packagecachedir = /work/osbuild-cache",
           "check_filelist = 0",
           ""]
    ibs = E["IBS_API"].rstrip("/")
    for api, alias in (("https://api.opensuse.org", "obs"), (ibs, "ibs")):
        if not src.has_section(api):
            sys.exit(f"OSCRC has no section [{api}]")
        out.append(f"[{api}]")
        for k, v in src.items(api):
            if k in ("trusted_prj", "aliases"):
                continue
            if k == "sshkey":
                v = "id_ibs_api"           # installed under this name in the container
            out.append(f"{k} = {v}")
        out.append(f"aliases = {alias}")   # the prompts use osc -A obs / -A ibs
        if not src.has_option(api, "pass") and not src.has_option(api, "sshkey"):
            sys.exit(f"OSCRC [{api}] has neither pass nor sshkey; the container "
                     "cannot use a keyring")
        out.append("")
    return "\n".join(out)


def tea():
    t = read("TEA_CONFIG").decode()
    # The entrypoint installs the keys under fixed names.
    import re
    t = re.sub(r"(ssh_key:\s*)\S+", r"\1" + C_HOME + "/.ssh/id_ibs_gitea", t)
    return t


def providers():
    with open(path("OPENCODE_CONFIG")) as f:
        host = json.load(f)
    prov = {}
    shadows = [m.strip() for m in E.get("SHADOW_REVIEWERS", "").split(",") if m.strip()]
    for model in [E.get("WORKER_MODEL", ""), E.get("REVIEWER_MODEL", "")] + shadows:
        name = model.split("/", 1)[0]
        if name not in host.get("provider", {}):
            sys.exit(f"provider {name!r} (from {model}) not in OPENCODE_CONFIG")
        prov[name] = json.loads(json.dumps(host["provider"][name]))
    g = prov.get("google-vertex", {}).get("options", {})
    if "googleAuthOptions" in g:
        g["googleAuthOptions"] = {
            "keyFilename": f"{C_HOME}/.config/gcloud/application_default_credentials.json"}
    return json.dumps({"provider": prov}, indent=2)


def main():
    put("te_oscrc", oscrc())
    put("te_ssh_ibs", read("SSH_KEY_IBS"))
    put("te_ssh_ibs_gitea", read("SSH_KEY_IBS_GITEA"))
    put("te_ssh_obs_gitea", read("SSH_KEY_OBS_GITEA"))
    put("te_tea_config", tea())
    put("te_opencode_providers", providers())
    put("te_vertex_adc", read("VERTEX_ADC"))
    put("te_ibs_gitea_token", read("IBS_GITEA_TOKEN").strip())
    put("te_bugzilla_key", read("BUGZILLA_KEY").strip())


if __name__ == "__main__":
    main()
