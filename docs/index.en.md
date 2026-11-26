# k8s-iannamo

GitOps repository of a homelab Kubernetes cluster. Three Talos Linux virtual
machines run on one Proxmox VE host, and Flux keeps the cluster in line with
the `main` branch of [lucaam/k8s-iannamo](https://github.com/lucaam/k8s-iannamo).
This site explains how the cluster is built and how to change it safely.

## At a glance

| Layer | Technology |
|---|---|
| Hypervisor | Proxmox VE on one host, with the ZFS pools `flash` (SSD) and `spacex` (HDD) |
| Operating system | Talos Linux v1.11 |
| Kubernetes | v1.31: one control plane node and two workers |
| GitOps | Flux v2.8; secrets encrypted with SOPS and age |
| Load balancing | MetalLB in layer 2 mode |
| Ingress | Traefik v3 with the Gateway API |
| Certificates | cert-manager: Let's Encrypt wildcard certificate, Cloudflare DNS-01 |
| Identity | Authentik: OIDC and forward authentication |
| Storage | NFS CSI driver backed by the Proxmox host, `Retain` reclaim policy |
| Databases | CloudNativePG for Immich, a bundled PostgreSQL for Authentik |
| Monitoring | kube-prometheus-stack, Grafana, Gatus |
| Alerting | Alertmanager, Gatus and Flux, all to Telegram |
| Automation | GitHub Actions pull request gate (`ci-gate`), Renovate |

## Where to start

<div class="grid cards" markdown>

-   :material-sitemap-outline:{ .lg .middle } **[Architecture](architecture.md)**

    ---

    Proxmox, Talos, the three nodes and how Flux applies this repository.

-   :material-apps:{ .lg .middle } **[Applications](apps.md)**

    ---

    What runs in the cluster, where it is reachable and how users log in.

-   :material-server-network:{ .lg .middle } **[Platform](infrastructure.md)**

    ---

    MetalLB, Traefik, cert-manager, storage, security and monitoring.

-   :material-source-pull:{ .lg .middle } **[Change workflow](workflow.md)**

    ---

    Branch, pull request, CI gate, merge, reconciliation and Renovate.

</div>

## Ground rules

- **Git is the source of truth.** Flux applies what is on `main`, and `main`
  only accepts squash merges of pull requests with a green `ci-gate`. A change
  made by hand drifts from Git and is overwritten by the next reconciliation
  or upgrade. See [Change workflow](workflow.md).
- **Data is never deleted automatically.** Volumes use the `Retain` reclaim
  policy, PVCs and the other stateful objects are protected from Flux pruning,
  and the CI data guard stops pull requests that remove them. Apps are retired
  with [a runbook](retiring-an-app.md), never by deleting their directory.
- **Secrets are committed only encrypted.** Secrets are SOPS files encrypted
  with age, and the domain name is the `${DOMAIN}` variable, filled in by Flux.
  See [Security and secrets](security.md).

!!! danger "There are no backups"
    Everything, from the photo library to the databases and the Home Assistant
    configuration, exists only on the ZFS pools of the single Proxmox host.
    ZFS redundancy covers a failed disk and `Retain` covers a deleted PVC;
    nothing covers a wrong `rm`, a lost pool or a lost host. See
    [Storage](storage.md#backups).

## Limits

- **No high availability.** There is one physical host and one control plane
  node with a single etcd member: when either is down, the cluster is down.
- **Fixed capacity.** Three VMs of fixed size, no autoscaling.
- **One owner.** It is a homelab, not a multi-tenant or production cluster.
