**Music Stack (first iteration)**

- **Scope:** scaffold manifests for a music-focused *arr stack: `lidarr`, `musicseerr`, `prowlarr`, `transmission`, `soulsync`, `slskd`, `aurral`, `flaresolverr`, `profilarr`.
- **Goal:** provide GitOps-friendly scaffolds (one folder per app, kustomize + `HelmRelease` stubs), placeholders for secrets encrypted with SOPS, and HTTPRoute entries using `app.${DOMAIN}` hostnames.

What I created
- Per-app folder under `apps/<app>` with the files: `kustomization.yaml`, `namespace.yaml`, `helm-release.yaml`, `pvc.yaml` (template), `secret-<app>.sops.yaml` (placeholders), `httproute.yaml`.
- `httproute.yaml` uses the existing Traefik Gateway (`traefik-gateway` in namespace `traefik`) and hostnames in the form `app.${DOMAIN}` (lowercase). TLS is expected to be handled by the Gateway TLS secret already in the cluster.

Important notes / next actions
- I did NOT populate chart names or repository references — per your request I left chart fields blank where I couldn't be 100% certain. Run the chart-discovery step and fill `chart.spec.chart` and `chart.spec.sourceRef.name` in each `helm-release.yaml`. Do not run `helm template` against a Kustomize directory (e.g. `./apps/lidarr`); instead validate Flux resources. After you populate a HelmRelease's `chart` and `sourceRef`, use Flux CLI commands (for example `flux diff helmrelease <name> -n <namespace>` or `flux diff kustomization <name>`) to preview changes.
- Secrets are placeholders in `secret-*.sops.yaml`. Please encrypt them with SOPS and add the `sops` metadata (or I can do this if you provide the keys).
- PVCs: for DB-like PVCs the files default to `storageClassName: nfs-flash`; for large media/downloads use `nfs-spacex` (adjust sizes as needed).
- MusicSeerr: `httproute` is configured to use the existing Traefik forward-auth middleware `authentik-forward-auth` because MusicSeerr currently lacks OIDC. For apps that support OIDC, populate the OIDC values in their `helm-release.yaml` and add client secrets to the corresponding `secret-*.sops.yaml`.

How to proceed (suggested)
1. Chart discovery: decide the Helm chart (official or community) to use per app and update `helm-release.yaml` accordingly.
2. Populate secrets and encrypt with SOPS.
3. Locally validate each app:

```bash
# render kustomize
kustomize build ./apps/lidarr

# Validate with Flux (HelmRelease/Kustomization):
# - For HelmRelease objects, after configuring `chart.spec.chart` and `sourceRef`,
#   use `flux diff helmrelease <name> -n <namespace>` to preview what Flux will apply.
# - For Kustomization objects, `flux diff kustomization <name>` is appropriate.
flux diff helmrelease lidarr -n lidarr

# kubeval / yamllint as needed
```

4. Open a PR with the populated charts/secrets (secrets encrypted) for review.

Files created
- See `apps/` subfolders for each app and `docs/music-stack.md` for details.

**Current state (2026-05-16)**

- **Storage classes:** `nfs-flash` (fast/small, for DB/config) and `nfs-spacex` (large-media). Both use the `nfs.csi.k8s.io` provisioner and create a `subDir` per PVC on the same export (see `infrastructure/nfs-csi/storageclass-flash.yaml` and `infrastructure/nfs-csi/storageclass-spacex.yaml`).

- **Per-app status and notes:**
	- **apps/aurral/**: PVCs present (`aurral-config` on `nfs-flash`, `aurral-media` on `nfs-spacex`). Deployment already uses `securityContext`/`podSecurityContext` (fsGroup/runAsUser set).
	- **apps/flaresolverr/**: `HelmRelease` present, no persistence enabled by default.
	- **apps/k8s-at-home/**: chart repository helper; no direct app manifests here.
	- **apps/lidarr/**: added `lidarr-media` PVC (200Gi, `nfs-spacex`) in `apps/lidarr/pvc.yaml`; `apps/lidarr/helm-release.yaml` was updated to enable `persistence.media` (existingClaim: `lidarr-media`) and to set `podSecurityContext.fsGroup: 1000` and `securityContext.runAsUser: 1000` in the Helm values so pods share sane NFS permissions.
	- **apps/media-servarr/**: GitRepository used as chart source for several charts (e.g. `lidarr`, `prowlarr`, `flaresolverr`).
	- **apps/musicseerr/**: PVCs present (`musicseerr-media` on `nfs-spacex`); Deployment already sets `fsGroup/runAsUser`.
	- **apps/profilarr/**: HelmRelease present; no media PVC required by default.
	- **apps/prowlarr/**: `prowlarr-storage` PVC exists (currently `nfs-flash`, 5Gi) for indexer metadata; HelmRelease values were updated to include podSecurityContext/securityContext for NFS permission alignment. Consider an additional `prowlarr-media` on `nfs-spacex` if the app will store larger files.
	- **apps/transmission/**: `transmission-storage` PVC on `nfs-spacex` (200Gi); downloads enabled and mapped to the same PVC. HelmRelease values were updated with podSecurityContext/securityContext to align file permissions.
	- **apps/slskd/**: `slskd-storage` PVC on `nfs-spacex`; HelmRelease values were updated with podSecurityContext/securityContext.
	- **apps/soulsync/**: PVCs present (`soulsync-data` on `nfs-spacex`); deployment includes `securityContext` with `fsGroup/runAsUser`.

**Changes applied in this branch/workspace**

- Added `apps/lidarr/pvc.yaml` entry `lidarr-media` (nfs-spacex, 200Gi).
- Updated HelmRelease values to add NFS-friendly security contexts and enable media persistence where appropriate:
	- `apps/lidarr/helm-release.yaml` (enabled `persistence.media`, set `podSecurityContext.fsGroup: 1000` and `securityContext.runAsUser: 1000`).
	- `apps/transmission/helm-release.yaml` (added `podSecurityContext`/`securityContext`).
	- `apps/slskd/helm-release.yaml` (added `podSecurityContext`/`securityContext`).
	- `apps/prowlarr/helm-release.yaml` (added `podSecurityContext`/`securityContext`).

**Recommendations / best-practices applied and suggested**

- Keep DB/config PVCs on `nfs-flash` and large media/downloads on `nfs-spacex`.
- Ensure all pods that will write to the same NFS share set matching `podSecurityContext.fsGroup` and `securityContext.runAsUser` (we used `1000` as a safe default here). This avoids permission issues and makes file sharing/hardlinking more reliable.
- Prefer using a single PVC (or the same StorageClass + share) for media/downloads when apps need to hardlink or share files. If using separate PVCs, confirm they are on the same exported filesystem (this repo's `StorageClass` uses `subDir` per PVC on the same server which is suitable).
- Avoid `subPath` where possible — it can complicate atomic renames and hardlinks.
- Audit each chart's volume mounts to ensure they don't use `subPath` or mount the same directories in conflicting ways.

**Next steps**

1. Commit the changes locally and open a PR. From your repo root run:

```bash
git add apps/lidarr/pvc.yaml \
	apps/lidarr/helm-release.yaml \
	apps/transmission/helm-release.yaml \
	apps/slskd/helm-release.yaml \
	apps/prowlarr/helm-release.yaml

git commit -m "arr: add lidarr-media PVC; align NFS permissions (fsGroup/runAsUser=1000)"
```

2. Decide whether to create `*-media` PVCs for other apps (e.g. `prowlarr-media`) or to reuse `transmission-storage` for downloads.
3. Optionally, apply the `podSecurityContext`/`securityContext` pattern across all HelmReleases that mount NFS to make permissions consistent cluster-wide.

If you want, I can now:
- create additional `*-media` PVCs and update HelmRelease values for other apps, or
- run a repo-wide scan and add `podSecurityContext` to every HelmRelease that references an NFS-backed PVC.

If you want, next I can:
- perform chart-discovery (I will only check upstream repos and report chart names/URLs; I will NOT add chart values without confirmation), or
- open a branch and commit these scaffolds (I will stage only the created files and commit with a clear message).
