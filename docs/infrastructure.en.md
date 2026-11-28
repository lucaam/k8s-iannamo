# Infrastructure

The platform components live in
[`infrastructure/`](https://github.com/lucaam/k8s-iannamo/tree/main/infrastructure)
and are applied by the `infrastructure` Kustomization, which `apps` waits for.
There is no ordering inside it: Flux applies all the directories listed in
`infrastructure/kustomization.yaml` as one set, and the controllers converge.
Chart versions are pinned in each `helm-release.yaml`.

| Directory | What it provides | Runs in | Source |
|---|---|---|---|
| `crds/` | Gateway API CRDs, CloudNativePG operator and CRDs | `cnpg-system` | upstream release manifests |
| `priority-classes/` | `homelab-critical`, `homelab-standard`, `homelab-low` | cluster-wide | manifests |
| `metallb/` | load balancer, address pool, L2 advertisement | `metallb` | chart `metallb` |
| `cert-manager/` | ClusterIssuer and wildcard certificate | `cert-manager` | chart `cert-manager` |
| `nfs-csi/` | NFS CSI driver, StorageClasses `nfs-flash` and `nfs-spacex` | `kube-system` | chart `csi-driver-nfs` |
| `traefik/` | Traefik, Gateway, TLS store, middlewares, error pages | `traefik` | chart `traefik` |
| `kube-system/` | CoreDNS configuration | `kube-system` | manifest |
| `notifications/` | Flux alerts to Telegram | `flux-system` | manifests |
| `secrets/` | Secrets for postBuild substitution | `flux-system` | applied by the `secrets` Kustomization |

## CRDs and CloudNativePG { #crds }

- **Gateway API**: the standard-channel CRDs of release v1.5.1 (GatewayClass,
  Gateway, HTTPRoute, GRPCRoute, ReferenceGrant, BackendTLSPolicy,
  ListenerSet, TLSRoute) and the ValidatingAdmissionPolicy that blocks unsafe
  CRD upgrades, pulled from the upstream release URLs.
- **CloudNativePG**: the release manifest `cnpg-1.28.0.yaml`, which contains
  both the CRDs and the operator Deployment in `cnpg-system`.
- Both are pinned in their URLs; Renovate proposes new releases through regex
  managers.
- A kustomize patch annotates every CRD with
  `kustomize.toolkit.fluxcd.io/prune: disabled`: deleting a CRD deletes every
  object of that kind, such as CloudNativePG clusters with their PVCs, or all
  the HTTPRoutes.
- The CRDs of Traefik and of the Prometheus Operator are not here. Both
  HelmReleases use `crds: Skip`, so those CRDs are not upgraded with the
  charts and stay at the version of an earlier install.
- The namespace `crd-ns` is created here and stays empty.

## PriorityClasses { #priority-classes }

| Class | Value | Use |
|---|---|---|
| `homelab-critical` | 100000 | home automation, ingress, identity and their databases |
| `homelab-standard` | 10000, global default | every pod that sets no class |
| `homelab-low` | 1000, never preempts | batch and tooling |

- `homelab-critical`: Traefik, Home Assistant, Matter Server, Zigbee2MQTT,
  Mosquitto, Authentik (server, worker and PostgreSQL), the CloudNativePG
  operator and the Immich database.
- `homelab-low`: code-server, Immich machine learning and
  social-media-reuploader.
- The global default applies when a pod is created, so older pods keep
  priority 0 until they are recreated.
- `value` and `preemptionPolicy` cannot be changed in place: changing them
  means recreating the class.

Resources follow one convention, applied app by app: CPU and memory requests
from the observed usage, a memory limit with headroom above the observed
peak, and no CPU limit, because CPU throttling made probes fail during
bursts.

## MetalLB { #metallb }

- Layer 2 mode: the pool `external-pool` (`192.168.178.10`–`192.168.178.40`)
  is announced on `eth0`, the interface name of the Talos images used here.
- Services pick a fixed address with the `metallb.io/loadBalancerIPs`
  annotation; the addresses in use are listed in
  [Networking](networking.md#lan-addresses).
- The namespace is `privileged`: the speakers need host networking. The chart
  also runs FRR-K8s, its default, which layer 2 mode does not use.

## cert-manager { #cert-manager }

- The chart installs its own CRDs (`installCRDs: true`).
- The ClusterIssuer `cloudflare` uses Let's Encrypt production with ACME
  DNS-01 challenges, through a Cloudflare API token stored in
  `secret-cloudflare.sops.yaml`.
- The Certificate `wildcard-apps`, in the `traefik` namespace, covers
  `*.${DOMAIN}` and `*.home.${DOMAIN}` and is stored in the Secret
  `wildcard-apps-tls`, used by the Gateway and by the default Traefik TLS
  store.
- The Certificate `merlofiorillo-shop`, also in `traefik`, covers
  `shop.merlofiorillo.it`, in the separate `merlofiorillo.it` zone that the
  same API token can edit. Its Secret `merlofiorillo-shop-tls` is used by the
  Gateway listener `websecure-merlofiorillo-shop` only.
- cert-manager renews both 30 days before expiry. The `CertManagerCert*` alerts
  and the Gatus checks fire when less than 14 days are left, that is when the
  renewal is stuck.

## NFS CSI driver { #nfs-csi }

- Chart `csi-driver-nfs` in `kube-system`: two controller replicas and a node
  plugin on every node, control plane included. The chart comes from the
  `charts` directory of the upstream `master` branch, which publishes the
  released versions.
- The StorageClasses are defined in this directory, not by the chart. See
  [Storage](storage.md).
- The namespace `nfs-csi` is created here and stays empty.

## Traefik { #traefik }

- Two replicas, behind the LoadBalancer IP `192.168.178.10`.
- Providers: the Gateway API, for the HTTPRoutes of the apps, and the Traefik
  CRDs, for middlewares and the dashboard IngressRoute. The Kubernetes Ingress
  provider is off.
- Entry points `web` (80) and `websecure` (443): every HTTP request is
  redirected to HTTPS, and `websecure` serves the custom error pages of the
  `traefik-errors` middleware.
- The dashboard is published at `traefik.${DOMAIN}` behind Authentik forward
  auth.

Routing, middlewares and error pages are described in
[Networking](networking.md#traefik-and-the-gateway-api).

## CoreDNS configuration { #coredns }

`kube-system/coredns-config.yaml` replaces the `coredns` ConfigMap that Talos
installs. See [Networking](networking.md#dns).

!!! warning "Do not delete this file"
    The ConfigMap has no prune protection: removing the file deletes the
    ConfigMap, and CoreDNS fails at its next restart, taking the cluster DNS
    with it.

## Flux notifications { #notifications }

- The Alert `flux-failures` sends the `error` events of every GitRepository,
  Kustomization, HelmRepository and HelmRelease to the Provider `telegram`,
  except the routine `waiting.*retrying` and `Health check passed` messages.
- The whole `spec` of the Provider is encrypted with SOPS.
- The Secret `telegram-bot` holds only `${TELEGRAM_BOT_TOKEN}`, which Flux
  fills in from `telegram-credentials`.

See [Monitoring](monitoring.md#flux-notifications).

## Substitution secrets { #substitution-secrets }

The `secrets` Kustomization applies `infrastructure/secrets/` with
`prune: false`, so these Secrets are never deleted by Flux:

| Secret | Keys | Used for |
|---|---|---|
| `cluster-vars` | `DOMAIN` | every hostname and URL in the manifests |
| `telegram-credentials` | `TELEGRAM_BOT_TOKEN` | the Flux Telegram provider |

See [Security and secrets](security.md#postbuild-variables).
