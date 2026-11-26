# k8s-iannamo

GitOps repository of a homelab Kubernetes cluster: Talos Linux on Proxmox,
reconciled by Flux from this repository.

📖 **[Documentation](https://lucaam.github.io/k8s-iannamo/)**: architecture,
apps, storage, networking, security, monitoring and operations, in English and
Italian.

## At a glance

| | |
|---|---|
| **Nodes** | 3 Talos Linux v1.11 VMs on one Proxmox host: 1 control plane, 2 workers |
| **Kubernetes** | v1.31 |
| **GitOps** | Flux v2.8 with the Kustomizations `secrets` → `infrastructure` → `apps`; secrets encrypted with SOPS and age |
| **Networking** | MetalLB (L2), Traefik with the Gateway API, cert-manager, Authentik forward auth |
| **Storage** | NFS CSI on ZFS: `flash` (SSD mirror) and `spacex` (HDD raidz); reclaim policy `Retain` |
| **Monitoring** | kube-prometheus-stack, Grafana, Gatus; alerts to Telegram |
| **CI** | `ci-gate` on every pull request: offline Flux build, Helm render, schema and policy checks, data guard |
| **Updates** | Renovate |

## Apps

Enabled in `apps/kustomization.yaml`: Authentik, Home Assistant (with
code-server), Matter Server, Mosquitto, Zigbee2MQTT, Immich, Prometheus,
Grafana, Gatus, Trek, Social Media Reuploader, and routes to services outside
the cluster (`external-services`). See
[Applications](https://lucaam.github.io/k8s-iannamo/apps/).

## Repository layout

| Path | Content |
|---|---|
| `clusters/production/` | Flux entry point: the three Kustomizations |
| `infrastructure/` | Platform: MetalLB, Traefik, cert-manager, NFS CSI, CRDs, PriorityClasses, shared Secrets, notifications |
| `apps/` | One directory per app |
| `policy/` | conftest policies used by CI |
| `.github/ci/` | `fluxci`, its configuration and the data retention registry ([README](.github/ci/README.md)) |
| `hack/` | Read-only helper scripts |
| `docs/` | The documentation site (MkDocs Material) |

## Changing the cluster

Nothing is applied by hand. Every change is a pull request: `ci-gate` must
pass, the branch must be up to date with `main`, and merges are squash only.
Flux then applies `main`. Pull requests that remove PVCs, namespaces,
databases or HelmReleases need the `allow-destructive` label. See
[Change workflow](https://lucaam.github.io/k8s-iannamo/workflow/) and
[Retiring an app](https://lucaam.github.io/k8s-iannamo/retiring-an-app/).

## Rebuilding from scratch

Flux was bootstrapped with the CLI and does not manage itself:
`clusters/production/flux-system/` is not included in
`clusters/production/kustomization.yaml`, so the controllers keep the version
that was installed (v2.8.7) whatever Git says. Flux 2.8 requires Kubernetes
1.33 or later: on this cluster (1.31) `flux check --pre` fails, and so would a
new bootstrap with that version. A rebuild therefore needs Kubernetes 1.33 or
later (the planned upgrade) or a Flux release that still supports 1.31. A
rebuild from this repository alone has not been tested; the outline is:

1. Create the Talos cluster (repository `tf-talos-cluster`).
2. Create the age key Secret that decrypts the SOPS files, before Flux starts:

   ```bash
   kubectl create namespace flux-system
   kubectl -n flux-system create secret generic sops-age --from-file=age.agekey=/path/to/age.agekey
   ```

3. Bootstrap Flux with a GitHub token (the cluster pulls this repository over
   HTTPS), at a version that supports the cluster's Kubernetes version:

   ```bash
   flux bootstrap github --token-auth --owner=lucaam --repository=k8s-iannamo \
     --branch=main --path=clusters/production --version=<flux version>
   ```

Details and caveats: [Architecture](https://lucaam.github.io/k8s-iannamo/architecture/)
and [Day-to-day operations](https://lucaam.github.io/k8s-iannamo/operations/).
