# Day-to-day operations

Lasting changes go through a pull request ([Change workflow](workflow.md)).
This page collects the commands for looking at the cluster, the few direct
actions that are part of normal work, and the usual problems.

## Access and tools

- A kubeconfig and a talosconfig for the cluster; neither is in this
  repository.
- The age private key, to read and edit SOPS files (see
  [Security and secrets](security.md#sops-and-age)).
- `mise install` installs kustomize, helm, flux, kubeconform, conftest, yq and
  the linters at the versions CI uses (`mise.toml`). kubectl, talosctl and
  sops are installed separately, at versions that match the cluster.

## Cluster status

These commands only read:

```bash
flux get kustomizations                                  # secrets, infrastructure, apps
flux get helmreleases -A --status-selector ready=false   # releases that are not ready
flux get sources all -A                                  # Git, Helm and OCI sources
flux events -A                                           # recent events of Flux objects
flux logs -A --level=error --since=1h                    # controller errors
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded
kubectl get events -A --sort-by=.lastTimestamp | tail -30
```

The status pages are Gatus at `gatus.${DOMAIN}` and Grafana at
`grafana.${DOMAIN}`; Prometheus alerts are at `prometheus.${DOMAIN}/alerts`.

## Reconciling

Reconciling asks Flux to act now instead of at its next interval. It changes
the cluster only by applying what is already on `main`.

```bash
flux reconcile source git flux-system                     # fetch main now
flux reconcile kustomization infrastructure               # apply a Kustomization now
flux reconcile kustomization apps --with-source           # fetch, then apply
flux reconcile helmrelease <name> -n <namespace>          # upgrade a release now
flux reconcile helmrelease <name> -n <namespace> --reset  # clear the retry counter of a stalled release
```

A HelmRelease whose upgrade failed more times than its remediation allows is
`Stalled` and waits for a change. After the fix is merged, `--reset` lets
helm-controller try again; `--force` runs a one-off install or upgrade.

## Troubleshooting

| Symptom | Where to look |
|---|---|
| A Kustomization is not ready | `kubectl -n flux-system describe kustomization <name>`: the message names the object that failed. One invalid object stops the whole Kustomization. |
| `apps` reports that a dependency is not ready | `infrastructure` has not applied the latest revision yet: reconcile it, or fix it first. |
| A HelmRelease is not ready | `kubectl -n <namespace> describe helmrelease <name>`, `helm -n <namespace> history <release>`, then the pods of the release. |
| An upgrade times out although the pod comes up | The app starts slowly: raise `spec.timeout` in the HelmRelease, as for Home Assistant. |
| A pod is in `CrashLoopBackOff` | `kubectl -n <namespace> logs <pod> --previous` and `kubectl -n <namespace> describe pod <pod>`. |
| A PVC is `Pending` | Normal until a pod uses it (`WaitForFirstConsumer`). Otherwise `kubectl -n <namespace> describe pvc <name>`: class name, NFS server reachable. |
| A hostname answers 404 | No route matches it: `kubectl -n <namespace> describe httproute <name>`, check the hostname and the `Accepted` and `ResolvedRefs` conditions. |
| A forward-auth app loops on login or answers 500 | The middleware must exist in the namespace of the route, and the `authentik-outpost` route must exist; then `kubectl -n authentik logs deploy/authentik-server`, which hosts the embedded outpost. |
| The certificate is not renewed | `kubectl -n traefik describe certificate wildcard-apps`, `kubectl -n cert-manager logs deploy/cert-manager`, the Cloudflare token. |
| kube-controller-manager or kube-scheduler restart often | Slow etcd writes on the `flash` pool: look for heavy writers on `nfs-flash` volumes and on the VM disks. |
| Flux does not pick up new commits | `flux get sources git -A`, `flux logs --kind GitRepository --level error`, the credentials in `flux-system/flux-system`. |
| No Telegram message arrives | `kubectl -n gatus logs deploy/gatus`, `kubectl -n prometheus logs statefulset/alertmanager-kube-prometheus-stack-alertmanager`, `flux events --for Alert/flux-failures -n flux-system`. |

## Nodes

### Draining a node

```bash
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
# maintenance
kubectl uncordon <node>
```

- Some pods are pinned and cannot move: the Immich server to
  `talos-homelab-worker-1` (iGPU), Zigbee2MQTT to `talos-homelab-worker-2`
  (USB adapter). Those apps are down until the node is back.
- The PodDisruptionBudgets of the CloudNativePG primaries allow no disruption,
  so draining the node that runs a primary blocks. Plan the maintenance with
  CloudNativePG (a switchover, or its node maintenance window) first.
- There is one control plane node: draining or rebooting it stops the
  Kubernetes API until it is back.

### Talos and Kubernetes upgrades

Talos, Kubernetes and the VMs are managed from the Terraform repository, not
from this one.

- A Talos upgrade uses the Image Factory installer of the node's own
  schematic: `talos-homelab-worker-1` carries the `i915` and `intel-ucode`
  extensions, which the plain `ghcr.io/siderolabs/installer` image would drop.
- Take an etcd snapshot first (`talosctl etcd snapshot <file>`), upgrade the
  workers before the control plane, one node at a time.
- Kubernetes moves one minor version at a time. After an upgrade, refresh
  `.github/ci/api-versions.txt` and `kubernetesVersion` in
  `.github/ci/config.yaml` (see the
  [CI README](https://github.com/lucaam/k8s-iannamo/blob/main/.github/ci/README.md#configuration)).

## Data

- Nothing is ever deleted to tidy up: `Released` PVs, NFS directories and
  database volumes may hold the only copy of some data. See
  [Storage](storage.md#volume-directories).
- `hack/inventory-released-pvs.py` produces a read-only inventory of the PVs
  that are not `Bound`.
- Removing an app and, much later, its data follows
  [Retiring an app](retiring-an-app.md).
