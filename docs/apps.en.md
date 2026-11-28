# Applications

Each application has its own directory under
[`apps/`](https://github.com/lucaam/k8s-iannamo/tree/main/apps) and runs only
if it is listed in `apps/kustomization.yaml`, which the `apps` Kustomization
applies. Most of them are HelmReleases: the chart version is pinned in each
`helm-release.yaml` and Renovate proposes the updates, so this page does not
repeat version numbers.

## Running applications

| App | Namespace | Address | Login |
|---|---|---|---|
| [Authentik](#authentik) | `authentik` | `auth.${DOMAIN}` | its own: it is the identity provider |
| [Mosquitto](#mosquitto) | `mqtt` | LAN IP, port 1883 | MQTT users from a password file |
| [Zigbee2MQTT](#zigbee2mqtt) | `zigbee2mqtt` | LAN IP, port 8080 | none |
| [Home Assistant](#home-assistant) | `home-assistant` | `home.${DOMAIN}`, LAN IP | its own |
| [code-server](#home-assistant) for Home Assistant | `home-assistant` | `code-home.${DOMAIN}` | Authentik forward auth |
| [Matter Server](#matter-server) | `home-assistant-matter-server` | LAN IP, port 5580 | none |
| [Immich](#immich) | `immich` | `immich.${DOMAIN}`, LAN IP | its own |
| [Gatus](#gatus) | `gatus` | `gatus.${DOMAIN}` | Authentik OIDC |
| [kube-prometheus-stack](#kube-prometheus-stack) | `prometheus` | `prometheus.${DOMAIN}` | Authentik forward auth |
| [Grafana](#grafana) | `grafana` | `grafana.${DOMAIN}` | Authentik OIDC |
| [External services](#external-services) | `external-services` | `homelab.${DOMAIN}` | Authentik forward auth |
| [TREK](#trek) | `trek` | `trek.${DOMAIN}` | Authentik OIDC |
| [social-media-reuploader](#social-media-reuploader) | `social-media-reuploader` | `social-media-reuploader.${DOMAIN}` | OAuth |
| [Merlo Fiorillo shop](#merlofiorillo-shop) | `merlofiorillo-shop` | `shop.merlofiorillo.it` | WordPress |

Hostnames end with `${DOMAIN}`, which Flux replaces with the real domain. A LAN
IP is a MetalLB address that the home network reaches directly, without
Traefik: the list is in [Networking](networking.md#lan-addresses). Login types
are explained in [Security and secrets](security.md#authentication).

## Identity

### Authentik { #authentik }

- Chart `authentik` from `charts.goauthentik.io`: one server and one worker
  replica. The chart also creates the HTTPRoute for `auth.${DOMAIN}`.
- The embedded outpost answers the forward-auth requests of Traefik. The
  route `authentik-outpost` sends the `/outpost.goauthentik.io` path of every
  host under `${DOMAIN}` to it, so that the login redirect works on each app.
- Database: the PostgreSQL bundled with the chart (Bitnami subchart running
  the official `postgres` 17 image), on `nfs-flash`.
- Secrets: `secret-authentik.sops.yaml` holds the secret key and the database
  and e-mail credentials.
- The PVCs `authentik-media`, `authentik-certs` and `authentik-templates` are
  never mounted by the release, so they stay `Pending`.

## Home automation

### Mosquitto { #mosquitto }

- Chart `mosquitto` from `bdclark.github.io/helm-charts`, MQTT on port 1883.
  Its LAN IP is shared with Home Assistant.
- Clients authenticate with the password file of the `mqtt-auth` Secret. The
  chart turns anonymous access off when a password file is set, even though
  the values say `allowAnonymous: true`.
- Data: `mosquitto-data` on `nfs-spacex`, created by the chart with
  `helm.sh/resource-policy: keep`.

### Zigbee2MQTT { #zigbee2mqtt }

- Chart `zigbee2mqtt` from `charts.zigbee2mqtt.io`. The pod is pinned to
  `talos-homelab-worker-2`, where the USB adapter is passed through
  (`/dev/ttyUSB0`, `ember` driver); the container is privileged to reach the
  serial device.
- Publishes to `mosquitto.mqtt.svc.cluster.local:1883`, with Home Assistant
  discovery; the MQTT credentials come from the `mqtt-auth` Secret.
- The web frontend has no authentication and is reachable on its LAN IP.
- Data: `nfs-spacex`, 1 GiB.

### Home Assistant { #home-assistant }

- Chart `home-assistant` (pajikos) from `blog.pavelsklenar.com`. One pod
  (StatefulSet) with host networking for device discovery and the
  `homelab-critical` priority.
- Reachable at `home.${DOMAIN}` through Traefik and on a LAN IP (port 8080),
  with its own login.
- The release is tuned for its slow start: 15 minute timeout, remediation
  retries, and a 240 second grace period so that the SQLite recorder can close
  the database cleanly.
- `/config` lives on `home-assistant-home-assistant-0` (`nfs-spacex`,
  ReadWriteMany).
- **code-server** ([`code-server.yaml`](https://github.com/lucaam/k8s-iannamo/blob/main/apps/home-assistant/code-server.yaml))
  edits the configuration from the browser. It is a separate Deployment that
  mounts the same `/config` volume, opens no port on the nodes and is
  reachable only at `code-home.${DOMAIN}`, behind Authentik forward auth. The
  code-server addon of the chart is disabled.
- The chart's ServiceMonitor scrapes `/api/prometheus`, but the Prometheus
  integration is not enabled in Home Assistant, so that target is down.

### Matter Server { #matter-server }

- Chart `home-assistant-matter-server` from `charts.derwitt.dev`, running the
  matter.js server image `matter-js/matterjs-server`.
- Home Assistant talks to its WebSocket API on port 5580; the API has no
  authentication and is reachable on its LAN IP.
- Data: `nfs-spacex`, 2 GiB.

## Media

### Immich { #immich }

- Chart `immich` from `immich-app.github.io/immich-charts`: server, machine
  learning and Valkey.
- The server is pinned to `talos-homelab-worker-1` and uses the iGPU
  (`/dev/dri`, privileged container) for hardware transcoding.
- `immich.${DOMAIN}` goes through the `immich-buffering` middleware, which
  limits request bodies to 100 MiB. The internal names `immich` and
  `immich.lan` are routed too, and the server also has a LAN IP for the API
  (port 2283) and its metrics ports.
- Database: the CloudNativePG cluster `immich-pg` (two instances of the
  VectorChord PostgreSQL image, 32 GiB each on `nfs-flash`) and its `Database`
  object, in `database.yaml`.
- Library: `immich-library` on `nfs-spacex` (1 TiB requested); machine
  learning cache and Valkey on `nfs-flash`.
- Immich writes a nightly database dump into the library, on the same pool as
  the photos: it is not a backup.

## Monitoring

### Gatus { #gatus }

Chart `gatus` from `twin.github.io/helm-charts`. Status page at
`gatus.${DOMAIN}` with Authentik OIDC, health checks with Telegram alerts,
history in SQLite on `gatus-data`. See [Monitoring](monitoring.md#gatus).

### kube-prometheus-stack { #kube-prometheus-stack }

Chart `kube-prometheus-stack` from `prometheus-community.github.io/helm-charts`:
Prometheus, Alertmanager, the Prometheus Operator, kube-state-metrics and
node-exporter. The Prometheus UI is at `prometheus.${DOMAIN}`, behind Authentik
forward auth. See [Monitoring](monitoring.md#prometheus).

### Grafana { #grafana }

Chart `grafana` from `grafana.github.io/helm-charts`, at `grafana.${DOMAIN}`
with Authentik generic OAuth. The Authentik group `monitoring admins` maps to
the Admin role, `monitoring editors` to Editor, everyone else gets Viewer. See
[Monitoring](monitoring.md#grafana).

## Other apps

### External services { #external-services }

Publishes a web service that runs on another LAN host at `homelab.${DOMAIN}`,
behind Authentik forward auth. There is no chart: a Service without selector
and a hand-written `Endpoints` object point Traefik at that host.

### TREK { #trek }

- Travel planner. The chart comes from the upstream Git repository
  `liketrek/TREK`, pinned to a release tag; Renovate proposes new tags.
- The GitRepository checks out only `charts/trek` (`sparseCheckout`): the full
  repository is about 195 MB, and source-controller unpacked it on every chart
  reconciliation, with heavy writes on the `flash` pool.
- `trek.${DOMAIN}` with Authentik OIDC; members of the `trip admins` group are
  TREK administrators. Data and uploads on `nfs-flash`.

### social-media-reuploader { #social-media-reuploader }

- A Telegram bot, in polling mode, that downloads the media linked in a chat
  (any site supported by yt-dlp) and posts the files back, plus an admin GUI
  at `social-media-reuploader.${DOMAIN}` with Authentik OAuth login (redirect
  URI `https://social-media-reuploader.${DOMAIN}/auth`).
- The chart is the signed OCI chart that every release of
  [lucaam/social-media-reuploader](https://github.com/lucaam/social-media-reuploader)
  publishes (`oci://ghcr.io/lucaam/charts/social-media-reuploader`), pinned to
  the release that matches the image tag; the `OCIRepository` accepts only
  charts signed by the release workflow. Renovate proposes chart and image
  together.
- Secrets: `social-media-reuploader-secret` (bot token) and
  `social-media-reuploader-gui-secret` (`SECRET_KEY` and the `OAUTH_*`
  settings), both encrypted with SOPS.
- Data: the SQLite database `social-media-reuploader-data` on `nfs-flash`,
  created by the chart with `helm.sh/resource-policy: keep`. Bot and GUI run
  on the same node. The database uses a rollback journal
  (`SQLITE_JOURNAL_MODE: delete`, `synchronous=FULL`): WAL, the app default
  since v0.9.0, needs local storage, and the cluster has only NFS.
- Upgrades use client-side apply (`upgrade.serverSideApply: disabled`): the
  bot Deployment uses the Recreate strategy, which server-side apply cannot
  switch to from the defaulted RollingUpdate.

### Merlo Fiorillo shop { #merlofiorillo-shop }

- WordPress with WooCommerce at `shop.merlofiorillo.it`. The name is in the
  separate `merlofiorillo.it` zone, so it has its own certificate and its own
  Gateway listener (see [Networking](networking.md#traefik-and-the-gateway-api)).
  Its DNS record is not proxied by Cloudflare: it points at the home address
  through `ddns.merlofiorillo.it`.
- Chart `wordpress` from `groundhog2k.github.io/helm-charts` with the official
  `wordpress` image; MariaDB is a StatefulSet in `database.yaml`. Both pods run
  as non-root users with a read-only root filesystem, and the namespace
  enforces the `restricted` Pod Security level.
- The database password comes from the SOPS Secret `wordpress-secrets`.
  `wp-config.php` is the template of the official image, which reads the
  database settings from the environment. The restored site keeps its own
  copy of that template, with its original salts, and still takes the
  credentials from the Secret.
- Data: the WordPress files on `wordpress-data` (`nfs-spacex`), the database
  on `mariadb-data` (`nfs-flash`).
- The HTTPRoute answers 403 to `/wp-admin/install.php` and
  `/wp-admin/setup-config.php`: while the database is empty, anyone reaching
  the installer could create the first administrator. A fresh install goes
  through `kubectl port-forward`.
- As on every route of the `websecure` entry point, Traefik replaces the
  bodies of the 404, 500 and 503 responses with its own error pages.

## Disabled and retired applications { #disabled }

`ollama`, `open-webui`, `paperless-ngx` and `tado-api-proxy` still have a
directory under `apps/`, but they are commented out in
`apps/kustomization.yaml`. Their namespaces no longer exist; the files of
their volumes are still on the NFS host, behind `Released` PVs. CI reports
their manifests as orphans, because no Kustomization applies them, and
Renovate ignores them. Re-enabling one of them recreates its PVCs with the old
names, which map to the old directories on the NFS host: the old data comes
back. See [Storage](storage.md#volume-directories).

**TeslaMate** was retired as described in
[Retiring an app](retiring-an-app.md): parked by
[#415](https://github.com/lucaam/k8s-iannamo/pull/415), then removed from Git
by [#431](https://github.com/lucaam/k8s-iannamo/pull/431). Its namespace, the
hibernated CloudNativePG cluster `teslamate-db` and the Grafana PVC stay in
the cluster until 2027-02-26, as recorded in `.github/ci/retention.yaml`. Its
chart source no longer serves the chart, so bringing it back needs a new
chart.

!!! warning "Do not disable an app by commenting it out"
    Removing an app from `apps/kustomization.yaml` deletes its HelmRelease,
    and Helm then deletes the PVCs that the chart created, unless they carry
    `helm.sh/resource-policy: keep`. Follow [Retiring an app](retiring-an-app.md)
    instead.

## Layout of an app directory { #app-layout }

```text
apps/<app>/
├── kustomization.yaml      resources of the app
├── namespace.yaml          namespace; stateful apps carry prune: disabled
├── helm-repository.yaml    chart source in flux-system (HelmRepository or GitRepository)
├── helm-release.yaml       HelmRelease: pinned chart version and values
├── httproute.yaml          Gateway API route on traefik-gateway (websecure)
├── pvc.yaml                PVCs created by Flux, with prune: disabled (optional)
├── secret-*.sops.yaml      Secrets encrypted with SOPS (optional)
└── middleware-*.yaml       Traefik middlewares, e.g. Authentik forward auth (optional)
```

## Adding an app

1. Create `apps/<app>/` with the layout above. Pin the chart version, which
   CI requires, and give every image a tag or a digest.
2. Annotate the Namespace and every PVC with
   `kustomize.toolkit.fluxcd.io/prune: disabled`, and set the Pod Security
   level on the Namespace if the app needs more than `baseline`.
3. Name the StorageClass explicitly: there is no default one. See
   [Storage](storage.md#storageclasses).
4. Set CPU and memory requests and a memory limit, but no CPU limit, and a
   PriorityClass when the app is critical or can wait. See
   [Infrastructure](infrastructure.md#priority-classes).
5. Put credentials in `secret-<name>.sops.yaml` and encrypt the file before
   the first commit. See [Security and secrets](security.md#sops-and-age).
6. Publish it with an HTTPRoute on `traefik-gateway`, listener `websecure`,
   with a hostname covered by the wildcard certificate. Put the forward-auth
   middleware in front of apps without their own login.
7. Add the directory to `apps/kustomization.yaml`, add a check to Gatus, and
   open a pull request as described in [Change workflow](workflow.md).
