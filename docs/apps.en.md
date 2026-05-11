# Applications

All apps are deployed as HelmRelease managed by Flux, in the `apps/` folder.

## Service List

| App | Namespace | Chart | Description |
|-----|-----------|-------|-------------|
| **Authentik** | `authentik` | authentik/authentik | Identity Provider SSO (OIDC, SAML, LDAP) |
| **Home Assistant** | `home-assistant` | home-assistant | Home automation |
| **Home Assistant Matter** | `home-assistant` | matter-server | Matter/Thread protocol bridge |
| **Immich** | `immich` | immich | Self-hosted photo/video management (Google Photos alternative) |
| **Grafana** | `grafana` | grafana | Metrics dashboard and visualization |
| **Prometheus** | `prometheus` | kube-prometheus-stack | Metrics collection + Alertmanager |
| **Gatus** | `gatus` | gatus | Uptime monitoring with status page |
| **Kubernetes Dashboard** | `kubernetes-dashboard` | kubernetes-dashboard | Web UI for cluster management |
| **Mosquitto** | `mosquitto` | mosquitto | MQTT broker for IoT |
| **Zigbee2MQTT** | `zigbee2mqtt` | zigbee2mqtt | Zigbee → MQTT bridge |
| **Tado API Proxy** | `tado-api-proxy` | custom | Proxy for Tado thermostats |
| **Trek** | `trek` | trek | Travel planner |
| **External Services** | `external-services` | — | Proxy for services external to the network |

## Common pattern

Each app follows this structure:

```
apps/<app-name>/
├── namespace.yaml           # Dedicated namespace
├── helm-repository.yaml     # Helm chart source
├── helm-release.yaml        # Deployment configuration
├── httproute.yaml           # HTTP routing (Gateway API)
├── kustomization.yaml       # Kustomize resource list
├── pvc.yaml                 # (optional) PersistentVolumeClaim
├── secret-*.sops.yaml       # (optional) SOPS-encrypted secrets
└── middleware*.yaml         # (optional) Traefik/Authentik middleware
```

## Service access

All exposed services use **Gateway API HTTPRoute** with hostname `<service>.${DOMAIN}`:

- `auth.${DOMAIN}` → Authentik
- `ha.${DOMAIN}` → Home Assistant
- `photos.${DOMAIN}` → Immich
- `grafana.${DOMAIN}` → Grafana
- `status.${DOMAIN}` → Gatus
- `kubernetes.${DOMAIN}` → Kubernetes Dashboard

!!! note "Authentication"
    Services without native auth are protected by Authentik's forward-auth middleware on Traefik.
