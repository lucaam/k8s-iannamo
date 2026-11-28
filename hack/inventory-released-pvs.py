#!/usr/bin/env python3
"""Read-only inventory of PersistentVolumes that no longer have a claim.

Every StorageClass is Retain, so a deleted PVC leaves its PV Released and its
directory on the NFS host untouched. Over time many PVs pile up. Most of them
point at the same directory as a live PV, because a PVC recreated with the
same name maps to the same "<namespace>/<pvc>" sub-directory. Such an alias
must never have its directory deleted: that would destroy live data.

For every PV that is not Bound, this script reports:
  - alias:  another, Bound PV uses the same directory -> the PV object can go,
            the directory must stay;
  - orphan: no Bound PV uses the directory -> candidate for archive and
            deletion, with its size and last modification time;
  - unparsable: the directory cannot be derived from the volume handle.

It only reads: `kubectl get pv -o json`, then one SSH call that runs `du` and
`stat` on the orphan directories. Nothing is changed. See
docs/retiring-an-app.en.md for what to do with the result.

Usage:
  hack/inventory-released-pvs.py [--host root@proxmox] [--pv-json FILE] [--stat-file FILE]
"""

import argparse
import json
import shlex
import subprocess
import sys
import time

TEMPLATE_MARKERS = ("${", "{{")


def directory(pv: dict) -> str | None:
    """Host directory of an nfs.csi.k8s.io volume: <share>/<subdir>."""
    csi = (pv.get("spec") or {}).get("csi") or {}
    if csi.get("driver") != "nfs.csi.k8s.io":
        return None
    parts = str(csi.get("volumeHandle") or "").split("#")
    if len(parts) < 3:
        return None
    share, subdir = parts[1].strip("/"), parts[2].strip("/")
    if not subdir or any(m in subdir for m in TEMPLATE_MARKERS):
        return None
    return f"/{share}/{subdir}"


def load_pvs(path: str | None) -> list[dict]:
    if path:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)["items"]
    out = subprocess.run(["kubectl", "get", "pv", "-o", "json"], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)["items"]


def load_stats(host: str, dirs: list[str], path: str | None) -> dict[str, tuple[int, int]]:
    """{dir: (size_mib, mtime_epoch)} from `du -sm` and `stat -c %Y`."""
    if path:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    elif dirs:
        quoted = " ".join(shlex.quote(d) for d in dirs)
        remote = f"for d in {quoted}; do [ -d \"$d\" ] && echo \"$(du -sm \"$d\" | cut -f1) $(stat -c %Y \"$d\") $d\"; done"
        text = subprocess.run(["ssh", "-o", "BatchMode=yes", host, remote], capture_output=True, text=True,
                              check=True).stdout
    else:
        text = ""
    stats = {}
    for line in text.splitlines():
        size, mtime, d = line.split(" ", 2)
        stats[d] = (int(size), int(mtime))
    return stats


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--host", default="root@proxmox", help="NFS host, for the read-only du/stat call")
    p.add_argument("--pv-json", help="use this `kubectl get pv -o json` output instead of calling kubectl")
    p.add_argument("--stat-file", help="use this '<MiB> <mtime> <dir>' output instead of calling ssh")
    args = p.parse_args()

    pvs = load_pvs(args.pv_json)
    live: dict[str, list[str]] = {}
    for pv in pvs:
        d = directory(pv)
        if d and (pv.get("status") or {}).get("phase") == "Bound":
            ref = pv["spec"]["claimRef"]
            live.setdefault(d, []).append(f"{ref['namespace']}/{ref['name']}")

    rows = []
    for pv in sorted(pvs, key=lambda x: x["metadata"]["name"]):
        phase = (pv.get("status") or {}).get("phase")
        if phase == "Bound":
            continue
        ref = (pv.get("spec") or {}).get("claimRef") or {}
        claim = f"{ref.get('namespace', '?')}/{ref.get('name', '?')}"
        d = directory(pv)
        kind = "unparsable" if d is None else ("alias" if d in live else "orphan")
        rows.append({"pv": pv["metadata"]["name"], "phase": phase, "claim": claim, "dir": d, "kind": kind,
                     "policy": pv["spec"].get("persistentVolumeReclaimPolicy"),
                     "created": pv["metadata"]["creationTimestamp"][:10]})

    orphan_dirs = sorted({r["dir"] for r in rows if r["kind"] == "orphan"})
    stats = load_stats(args.host, orphan_dirs, args.stat_file)

    print(f"# Released PersistentVolumes ({len(rows)} of {len(pvs)})\n")
    print("Generated", time.strftime("%Y-%m-%d %H:%M"), "- read-only, nothing was changed.\n")
    for policy_delete in [r for r in rows if r["policy"] == "Delete"]:
        print(f"**Warning:** {policy_delete['pv']} ({policy_delete['claim']}) has reclaimPolicy Delete: the "
              f"provisioner keeps trying to delete its directory.\n")

    print("## Orphans: no Bound PV uses the directory\n")
    print("Candidates for archive and deletion (decision and commands: docs/retiring-an-app.en.md).\n")
    print("| Directory | MiB | Last modified | PVs | Claim |")
    print("|---|---:|---|---:|---|")
    for d in orphan_dirs:
        group = [r for r in rows if r["dir"] == d]
        size, mtime = stats.get(d, (None, None))
        when = time.strftime("%Y-%m-%d", time.localtime(mtime)) if mtime else "missing on host"
        print(f"| `{d}` | {size if size is not None else '-'} | {when} | {len(group)} | {group[0]['claim']} |")

    print("\n## Aliases: the directory belongs to a live volume\n")
    print("The PV objects can be deleted (Retain: the directory is not touched). "
          "**Never delete these directories.**\n")
    print("| Directory | Released PVs | Live claim |")
    print("|---|---:|---|")
    for d in sorted({r["dir"] for r in rows if r["kind"] == "alias"}):
        n = sum(1 for r in rows if r["dir"] == d)
        print(f"| `{d}` | {n} | {', '.join(live[d])} |")

    odd = [r for r in rows if r["kind"] == "unparsable"]
    if odd:
        print("\n## Unparsable\n")
        for r in odd:
            print(f"- {r['pv']} ({r['claim']}, {r['policy']}): directory not derivable from the volume handle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
